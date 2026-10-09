"""Authentifizierte Archiv-/Exportansicht; keine Dateien unter www."""

import asyncio
from dataclasses import asdict

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers import entity_registry as er

from .appearance import APPEARANCE_CATALOG
from .archive import plain
from .const import DOMAIN
from .core.defaults import section
from .core.display import phase_timer, start_availability
from .core.parameters import EDITABLE_DEFINITIONS, LIVE_TEMPERATURE_KEYS, ParameterError
from .environment import environment_snapshot
from .log import LEVELS
from .presentation import (
    decision_message, fault_message, issues, parameter_error,
    public_measurement, public_phase_projection, public_session, public_state,
    resolve_cooling_token,
)
from .settings import (
    ConfigurationLocked,
    async_reset_parameters,
    async_set_appearance,
    async_set_button_program,
    async_set_control_mode,
    async_set_parameters,
    async_set_program,
    async_set_program_catalog,
    async_set_temperature_steps,
)


def can_control(request, entry_id):
    """Use Home Assistant's normal control grant for this instance's switch."""
    hass = request.app[KEY_HASS]
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{entry_id}_operation"
    )
    return bool(
        entity_id
        and request["hass_user"].permissions.check_entity(entity_id, POLICY_CONTROL)
    )


def can_read(request, entry_id):
    """Use the operation entity as the instance's read permission boundary."""
    hass = request.app[KEY_HASS]
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{entry_id}_operation"
    )
    return bool(
        entity_id
        and request["hass_user"].permissions.check_entity(entity_id, POLICY_READ)
    )


def require_read(request, entry_id):
    if not can_read(request, entry_id):
        raise web.HTTPForbidden()


def historical_measurement_ttl(session, fallback):
    parameters = session.get("configuration", {}).get("parameters", {})
    return parameters.get("sensor_timeout_seconds", fallback)


async def json_body(request):
    """Return a consistent client error for malformed JSON on every write path."""
    try:
        return await request.json()
    except (ValueError, UnicodeError, web.HTTPBadRequest) as error:
        raise web.HTTPBadRequest(
            text='{"error":"Ungültiger JSON-Körper."}', content_type="application/json"
        ) from error


def require_control(request, entry_id):
    if not can_control(request, entry_id):
        raise web.HTTPForbidden()


def manual_controls(runtime):
    """Expose selected plans and explicit observation without device ids."""
    light = runtime.device.light_output if runtime.device else None
    return {
        "heater": {
            "observation": {
                "available": runtime.controller.contactor is not None,
                "on": runtime.controller.contactor,
            },
            "manual": runtime.controller.heater_override,
            "override_ends_at": plain(runtime.controller.heater_override_ends_at),
            "automatic": runtime.controller.automatic_decision.heat
            if runtime.controller.automatic_decision
            else None,
        },
        "light": {
            "observation": runtime.device.light_observation if runtime.device
            else {"available": False, "brightness_percent": None},
            "manual": light.manual_brightness if light else None,
            "override_ends_at": plain(light.manual_ends_at) if light else None,
            "automatic": light.last_automatic_brightness if light else None,
            "normal": runtime.device.normal_light_brightness()
            if runtime.device
            else None,
        },
    }


def runtime_for(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise web.HTTPNotFound()
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None or runtime.closed:
        raise web.HTTPServiceUnavailable()
    return runtime


class InstancesView(HomeAssistantView):
    url = "/api/ha_sauna"
    name = "api:ha_sauna:instances"
    requires_auth = True

    async def get(self, request):
        return self.json(
            [
                {"entry_id": entry.entry_id, "title": entry.title}
                for entry in request.app[KEY_HASS].config_entries.async_entries(DOMAIN)
                if getattr(entry, "runtime_data", None)
                and not entry.runtime_data.closed
                and can_read(request, entry.entry_id)
            ]
        )


class StateView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/state"
    name = "api:ha_sauna:state"
    requires_auth = True

    async def get(self, request, entry_id):
        require_read(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        async with runtime._lock:
            controller, device, session = (
                runtime.controller,
                runtime.device,
                runtime.session,
            )
            now = runtime._clock()
            active = session.timeline.active if session else None
            regulation_measurement = (
                device.regulation_measurement(now) if device else None
            )
            result = plain(
                    {
                        "now": now,
                        "phase": controller.phase,
                        "presence": runtime.presence_status,
                        "environment": environment_snapshot(
                            request.app[KEY_HASS], runtime.configuration.bindings.values
                        ),
                        "rule_inputs": controller.regulation_inputs,
                        "phase_projection": controller.phase_projection(now),
                        "session": session,
                        "configuration": runtime.configuration.as_options(),
                        "measurement_ttl_seconds": runtime.configuration.parameters.values[
                            "sensor_timeout_seconds"
                        ],
                        "appearance": runtime.configuration.appearance,
                        "appearance_catalog": APPEARANCE_CATALOG,
                        "frontend_defaults": section("frontend"),
                        "last_session": next((
                            item for item in reversed(controller.completed_sessions)
                            if item.timeline.gang_count
                        ), None),
                        "archive_revision": runtime.archive.revision if runtime.archive else 0,
                        "parameters": [
                            {
                                **asdict(d),
                                "minimum": runtime.configuration.parameters.minimum_for(
                                    d.key
                                ),
                                "live_editable": d.key in LIVE_TEMPERATURE_KEYS,
                            }
                            for d in EDITABLE_DEFINITIONS
                        ],
                        "configuration_locked": session is not None,
                        "operation_enabled": bool(
                            session and session.operation_enabled
                        ),
                        "heating_feedback": controller.feedback,
                        "heating_observation": device.heating_observation
                        if device
                        else None,
                        "energy_kwh": session.energy.total_kwh if session else 0,
                        "energy_source": session.energy.source
                        if session
                        else "estimated",
                        "thermostat_target": controller.thermostat_target,
                        "thermostat_restart_temperature": controller.thermostat_restart_temperature,
                        "target_temperature": controller.target_temperature,
                        "mechanical_timer_ends_at": controller.mechanical_timer_ends_at,
                        "mechanical_timer": controller.mechanical_timer_status,
                        "phase_timer": phase_timer(controller, now),
                        "start_availability": start_availability(
                            controller,
                            now,
                            estimated_ready_seconds=device.estimated_ready_seconds(now)
                            if device
                            else None,
                        ),
                        "light_after_run": controller.light_after_run,
                        "start_errors": device.start_errors() if device else [],
                        "issues": issues(runtime),
                        "decision_text": decision_message(controller.last_decision),
                        "measurement_status": device.measurement_status(now)
                        if device
                        else {},
                        "regulation_temperature_position": (
                            regulation_measurement.position.value
                            if regulation_measurement else None
                        ),
                        "measurement_positions": [
                            position
                            for position in ("upper", "lower")
                            if f"{position}_temperature"
                            in runtime.configuration.bindings.values
                        ],
                        "gang_count": session.timeline.gang_count if session else 0,
                        "gang_confirmation": active.confirmation if active else None,
                        "gang_duration_seconds": active.elapsed_seconds(now)
                        if active
                        else None,
                        "decision": controller.last_decision,
                        "measurements": list(device.measurements.values())
                        if device
                        else [],
                        "faults": device.faults if device else {},
                        "protection": sorted(controller.protection),
                        "inhibits": sorted(controller.inhibits),
                        "archive_error": str(runtime.archive.failure)
                        if runtime.archive.failure
                        else None,
                        "detection_channels": runtime.detector.active_positions
                        if runtime.detector
                        else [],
                        "detector_trace": runtime.detector.diagnostic
                        if runtime.detector
                        else None,
                        "manual_controls": manual_controls(runtime),
                        "permissions": {
                            "admin": request["hass_user"].is_admin,
                            "control": can_control(request, entry_id),
                            "temperature": can_control(request, entry_id),
                            "program": can_control(request, entry_id),
                            "light": can_control(request, entry_id),
                            "heater": can_control(request, entry_id),
                        },
                    }
                )
            if not request["hass_user"].is_admin:
                result = public_state(result)
            return self.json(result)


class ControlView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/control"
    name = "api:ha_sauna:control"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"enabled"}
            or not isinstance(body["enabled"], bool)
        ):
            raise web.HTTPBadRequest(text="enabled muss wahr oder falsch sein")
        try:
            await runtime.set_operation(body["enabled"])
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True})


class FinishPhaseView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/finish_phase"
    name = "api:ha_sauna:finish_phase"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"purpose", "token"}
            or not isinstance(body["purpose"], str)
            or body["purpose"] != "after_run"
            or not isinstance(body["token"], str)
            or not body["token"]
        ):
            return self.json(
                {
                    "error": "Bitte eine laufende Ofenkühlung auswählen."
                },
                status_code=400,
            )
        try:
            await runtime.finish_phase(
                body["purpose"], resolve_cooling_token(runtime.session, body["token"])
            )
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True})


class FinishSessionView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/finish-session"
    name = "api:ha_sauna:finish_session"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"token"}
            or not isinstance(body["token"], str)
            or not body["token"]
        ):
            return self.json(
                {"error": "Bitte die aktuelle Unterbrechungsfrist auswählen."},
                status_code=400,
            )
        try:
            await runtime.finish_session_gap(body["token"])
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True})


class ParametersView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/parameters"
    name = "api:ha_sauna:parameters"
    requires_auth = True
    partial = False

    async def post(self, request, entry_id):
        if self.partial:
            require_control(request, entry_id)
        elif not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        hass = request.app[KEY_HASS]
        runtime_for(hass, entry_id)
        body = await json_body(request)
        try:
            if self.partial and (
                not isinstance(body, dict)
                or not body
                or set(body) - LIVE_TEMPERATURE_KEYS
            ):
                raise ParameterError("base", "invalid_parameters")
            parameters = await async_set_parameters(
                hass,
                hass.config_entries.async_get_entry(entry_id),
                body,
                partial=self.partial,
            )
        except ParameterError as error:
            return self.json({"error": parameter_error(error)}, status_code=400)
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True, "parameters": parameters})


class TemperatureView(ParametersView):
    url = "/api/ha_sauna/{entry_id}/temperature"
    name = "api:ha_sauna:temperature"
    partial = True


class ResetParametersView(HomeAssistantView):
    """Restore all software preferences, leaving hardware associations intact."""

    url = "/api/ha_sauna/{entry_id}/parameters/reset"
    name = "api:ha_sauna:parameters:reset"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_for(hass, entry_id)
        try:
            parameters = await async_reset_parameters(hass, entry)
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json(
            {
                "success": True,
                "parameters": parameters,
                "configuration": {
                    key: entry.options.get(key)
                    for key in ("log_level", "program_mode", "button_program")
                },
            }
        )


class AppearanceView(HomeAssistantView):
    """Replace one instance's appearance while leaving its operation intact."""

    url = "/api/ha_sauna/{entry_id}/appearance"
    name = "api:ha_sauna:appearance"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        hass = request.app[KEY_HASS]
        runtime_for(hass, entry_id)
        entry = hass.config_entries.async_get_entry(entry_id)
        try:
            appearance = await async_set_appearance(hass, entry, await json_body(request))
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        return self.json({"success": True, "appearance": appearance})


class ProgramView(HomeAssistantView):
    """Select a stored profile or an explicit start/end/distribution program."""

    url = "/api/ha_sauna/{entry_id}/program"
    name = "api:ha_sauna:program"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_for(hass, entry_id)
        try:
            body = await json_body(request)
            if isinstance(body, dict) and set(body) == {"profile"}:
                if not isinstance(body["profile"], str):
                    raise ValueError("Ungültiges Temperaturprogramm")
                parameters = await async_set_program(hass, entry, body["profile"])
            elif isinstance(body, dict) and set(body) == {
                "target_temperature_c",
                "final_temperature_c",
                "temperature_gangs",
            }:
                start, end, gangs = (
                    body["target_temperature_c"],
                    body["final_temperature_c"],
                    body["temperature_gangs"],
                )
                if (
                    isinstance(start, bool)
                    or not isinstance(start, (int, float))
                    or isinstance(end, bool)
                    or not isinstance(end, (int, float))
                    or isinstance(gangs, bool)
                    or not isinstance(gangs, int)
                ):
                    raise ParameterError("base", "invalid_parameters")
                parameters = await async_set_parameters(
                    hass,
                    entry,
                    body,
                    partial=True,
                    explicit_target=False,
                    program_mode="progressive",
                    new_program=True,
                )
            elif isinstance(body, dict) and set(body) == {"temperature_steps"}:
                parameters = await async_set_temperature_steps(
                    hass, entry, body["temperature_steps"]
                )
            else:
                raise ParameterError("base", "invalid_parameters")
        except ParameterError as error:
            return self.json({"error": parameter_error(error)}, status_code=400)
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        return self.json(
            {
                "success": True,
                "parameters": parameters,
                "program_mode": entry.runtime_data.configuration.program_mode,
                "selected_program_id": entry.runtime_data.configuration.selected_program_id,
                "temperature_steps": entry.runtime_data.configuration.temperature_steps,
            }
        )


class ProgramsView(HomeAssistantView):
    """Replace the named temperature-program catalog for switch controllers."""

    url = "/api/ha_sauna/{entry_id}/programs"
    name = "api:ha_sauna:programs"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_for(hass, entry_id)
        try:
            body = await json_body(request)
            if not isinstance(body, dict) or set(body) != {"programs"}:
                raise web.HTTPBadRequest(text="Programmliste fehlt oder ist ungültig")
            programs = await async_set_program_catalog(hass, entry, body["programs"])
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        return self.json({"success": True, "programs": programs})


class ButtonProgramView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/button-program"
    name = "api:ha_sauna:button_program"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_for(hass, entry_id)
        body = await json_body(request)
        if not isinstance(body, dict) or set(body) not in (
            {"profile"},
            {"profile", "temperature_c"},
        ):
            raise web.HTTPBadRequest(text="Tasterprogramm fehlt oder ist ungültig")
        if "temperature_c" in body and (
            isinstance(body["temperature_c"], bool)
            or not isinstance(body["temperature_c"], (int, float))
        ):
            raise web.HTTPBadRequest(text="Tastertemperatur fehlt oder ist ungültig")
        try:
            configuration = await async_set_button_program(
                hass, entry, body["profile"], body.get("temperature_c")
            )
        except ConfigurationLocked as error:
            return self.json({"error": str(error)}, status_code=409)
        except ParameterError:
            return self.json(
                {
                    "error": "Tastertemperatur innerhalb des eingestellten Regelbereichs wählen."
                },
                status_code=400,
            )
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        return self.json(
            {
                "success": True,
                "button_program": configuration.button_program,
                "button_temperature_c": configuration.button_temperature_c,
            }
        )


class ControlModeView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/control-mode"
    name = "api:ha_sauna:control_mode"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_for(hass, entry_id)
        body = await json_body(request)
        if not isinstance(body, dict) or set(body) != {"mode"}:
            raise web.HTTPBadRequest(text="Betriebsmodus fehlt oder ist ungültig")
        try:
            configuration = await async_set_control_mode(hass, entry, body["mode"])
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json(
            {"success": True, "control_mode": configuration.control_mode}
        )


class LightView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/light"
    name = "api:ha_sauna:light"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        body = await json_body(request)
        if not isinstance(body, dict) or set(body) != {"value"}:
            raise web.HTTPBadRequest(text="Lichtwert fehlt oder ist ungültig")
        value = body["value"]
        preset = value is True or value is False or value is None or value == "normal"
        percentage = not isinstance(value, bool) and isinstance(value, (int, float))
        if not preset and not percentage:
            raise web.HTTPBadRequest(text="Lichtwert fehlt oder ist ungültig")
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        try:
            await runtime.set_light_override(value)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        return self.json({"success": True, "manual_controls": manual_controls(runtime)})


class HeaterView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/heater"
    name = "api:ha_sauna:heater"
    requires_auth = True

    async def post(self, request, entry_id):
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        if not can_control(request, entry_id):
            raise web.HTTPForbidden()
        body = await json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"value"}
            or (body["value"] is not None and not isinstance(body["value"], bool))
        ):
            raise web.HTTPBadRequest(
                text="Heizwert muss wahr, falsch oder automatisch sein"
            )
        try:
            await runtime.set_heater_override(body["value"])
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True, "manual_controls": manual_controls(runtime)})


class LoggingView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/logging"
    name = "api:ha_sauna:logging"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        hass = request.app[KEY_HASS]
        runtime = runtime_for(hass, entry_id)
        body = await json_body(request)
        if (
            not isinstance(body, dict)
            or set(body) != {"level"}
            or body["level"] not in LEVELS
        ):
            return self.json(
                {"error": "Bitte ERROR, INFO oder DEBUG auswählen."}, status_code=400
            )
        async with runtime._lock:
            entry = hass.config_entries.async_get_entry(entry_id)
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "log_level": body["level"]}
            )
            runtime.set_log_level(body["level"])
        return self.json({"success": True})


class ArchiveView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/archive"
    name = "api:ha_sauna:archive"
    requires_auth = True

    async def get(self, request, entry_id):
        require_read(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        await runtime.archive.flush()
        session_id = request.query.get("session_id")
        try:
            after = max(0, int(request.query.get("after", "0")))
            if after > 2**63 - 1:
                raise ValueError("Archivcursor außerhalb des gültigen Bereichs")
        except ValueError as error:
            raise web.HTTPBadRequest() from error
        projection = request.query.get("projection")
        if projection not in (None, "history"):
            raise web.HTTPBadRequest(text="Unbekannte Archivansicht")
        read_options = {"after": after}
        clock = getattr(runtime, "_clock", None)
        if callable(clock):
            read_options["now"] = clock()
        if projection == "history":
            read_options.update(
                limit=5000,
                kinds=(
                    "measurement", "source_snapshot", "phase", "diagnostic",
                    "detector_trace", "detection", "presence", "presence_source",
                    "decision", "command", "light_command", "consumer_event",
                ) if request["hass_user"].is_admin else (
                    "measurement", "source_snapshot", "phase",
                ),
            )
        result = await asyncio.to_thread(runtime.archive.read, session_id, **read_options)
        if result is None:
            raise web.HTTPNotFound()
        if session_id:
            session = result["session"]
            session["measurement_ttl_seconds"] = historical_measurement_ttl(
                session, runtime.configuration.parameters.values["sensor_timeout_seconds"]
            )
            if request["hass_user"].is_admin:
                for record in result["records"]:
                    if record["kind"] == "diagnostic":
                        record["payload"]["messages"] = [
                            fault_message(k, v)
                            for k, v in record["payload"].get("faults", {}).items()
                        ]
            else:
                result["session"] = public_session(session)
                result["phase_projection"] = public_phase_projection(result["phase_projection"])
                result["records"] = [
                    {
                        **record,
                        "payload": public_measurement(record["payload"]),
                    }
                    if record["kind"] in {"measurement", "source_snapshot"}
                    else record
                    for record in result["records"]
                    if record["kind"] in {"measurement", "source_snapshot", "phase"}
                ]
        return self.json(result)


class EraseArchiveView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/archive/erase"
    name = "api:ha_sauna:archive:erase"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        body = await json_body(request)
        if not isinstance(body, dict) or not (
            (set(body) == {"reset"} and body["reset"] is True)
            or (set(body) == {"session_id"} and isinstance(body["session_id"], str)
                and bool(body["session_id"]))
        ):
            raise web.HTTPBadRequest(text="Eine Sitzungs-ID oder reset=true ist erforderlich.")
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        try:
            count = await runtime.erase_archive(body.get("session_id"), reset=body.get("reset", False))
        except KeyError as error:
            raise web.HTTPNotFound() from error
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=409)
        return self.json({"success": True, "deleted_sessions": count,
                          "archive_revision": runtime.archive.revision})


class ExportView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/export"
    name = "api:ha_sauna:export"
    requires_auth = True

    async def get(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        async with runtime._lock:
            if runtime.session:
                runtime.archive.save_session(
                    runtime.session,
                    runtime._clock(),
                    runtime.configuration.as_options(),
                )
        path = await runtime.archive.export()
        response = web.StreamResponse(
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="ha-sauna-archive.zip"',
                "Cache-Control": "no-store",
            }
        )
        try:
            await response.prepare(request)
            handle = await asyncio.to_thread(path.open, "rb")
            try:
                while chunk := await asyncio.to_thread(handle.read, 65536):
                    await response.write(chunk)
            finally:
                await asyncio.to_thread(handle.close)
            await response.write_eof()
            return response
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)


def register(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("api_registered"):
        return
    hass.http.register_view(ArchiveView)
    hass.http.register_view(EraseArchiveView)
    hass.http.register_view(ExportView)
    hass.http.register_view(InstancesView)
    hass.http.register_view(StateView)
    hass.http.register_view(ControlView)
    hass.http.register_view(FinishPhaseView)
    hass.http.register_view(FinishSessionView)
    hass.http.register_view(ParametersView)
    hass.http.register_view(TemperatureView)
    hass.http.register_view(ResetParametersView)
    hass.http.register_view(AppearanceView)
    hass.http.register_view(ProgramView)
    hass.http.register_view(ProgramsView)
    hass.http.register_view(ButtonProgramView)
    hass.http.register_view(ControlModeView)
    hass.http.register_view(LightView)
    hass.http.register_view(HeaterView)
    hass.http.register_view(LoggingView)
    data["api_registered"] = True
