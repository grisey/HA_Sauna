"""Authentifizierte Archiv-/Exportansicht; keine Dateien unter www."""

import asyncio
from dataclasses import asdict
from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from homeassistant.helpers import entity_registry as er
from .archive import plain
from .core.parameters import EDITABLE_DEFINITIONS, ParameterError, LIVE_TEMPERATURE_KEYS
from .core.display import phase_timer, start_availability
from .core.detection_parameters import SPECS
from .const import DOMAIN
from .presentation import issues, decision_message, fault_message, parameter_error
from .log import LEVELS
from .settings import (
    async_set_parameters,
    async_set_program,
    async_reset_parameters,
    ConfigurationLocked,
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


def require_control(request, entry_id):
    if not can_control(request, entry_id):
        raise web.HTTPForbidden()


def manual_controls(runtime):
    """Expose the selected and automatic plans without exposing device ids."""
    light = runtime.device.light_output if runtime.device else None
    return {
        "heater": {
            "manual": runtime.controller.heater_override,
            "automatic": runtime.controller.automatic_decision.heat
            if runtime.controller.automatic_decision
            else None,
        },
        "light": {
            "manual": light.manual_brightness if light else None,
            "automatic": light.last_automatic_brightness if light else None,
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
            ]
        )


class StateView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/state"
    name = "api:ha_sauna:state"
    requires_auth = True

    async def get(self, request, entry_id):
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        async with runtime._lock:
            controller, device, session = (
                runtime.controller,
                runtime.device,
                runtime.session,
            )
            now = runtime._clock()
            active = session.timeline.active if session else None
            experts = {s[0] for s in SPECS}
            return self.json(
                plain(
                    {
                        "now": now,
                        "phase": controller.phase,
                        "session": session,
                        "configuration": runtime.configuration.as_options(),
                        "last_session": controller.completed_sessions[-1]
                        if controller.completed_sessions
                        else None,
                        "parameters": [
                            {
                                **asdict(d),
                                "expert": d.key in experts,
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
                        "heating_limit_seconds": controller.heating_limit_seconds,
                        "readiness_target": controller.readiness_target,
                        "target_temperature": controller.target_temperature,
                        "cooling_wait_until": controller.cooling_wait_until,
                        "mechanical_timer_ends_at": controller.mechanical_timer_ends_at,
                        "mechanical_timer": controller.mechanical_timer_status,
                        "phase_timer": phase_timer(controller, now),
                        "start_availability": start_availability(
                            controller,
                            now,
                            temperature_rate=device.temperature_rate(now)
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
                            "heater": request["hass_user"].is_admin,
                        },
                    }
                )
            )


class ControlView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/control"
    name = "api:ha_sauna:control"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await request.json()
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
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) != {"purpose", "token"}
            or not isinstance(body["purpose"], str)
            or body["purpose"] not in ("after_run", "forced_cooling")
            or not isinstance(body["token"], str)
            or not body["token"]
        ):
            return self.json(
                {
                    "error": "Bitte einen laufenden Nachlauf oder eine laufende Zwangskühlung auswählen."
                },
                status_code=400,
            )
        try:
            await runtime.finish_phase(body["purpose"], body["token"])
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
        body = await request.json()
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
        body = await request.json()
        try:
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
            }
        )


class LightView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/light"
    name = "api:ha_sauna:light"
    requires_auth = True

    async def post(self, request, entry_id):
        require_control(request, entry_id)
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"value"}:
            raise web.HTTPBadRequest(text="Lichtwert fehlt oder ist ungültig")
        value = body["value"]
        preset = value is True or value is False or value is None or value == "normal"
        percentage = not isinstance(value, bool) and isinstance(value, (int, float))
        if not preset and not percentage:
            raise web.HTTPBadRequest(text="Lichtwert fehlt oder ist ungültig")
        if percentage and not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
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
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) != {"value"}
            or (body["value"] is not None and not isinstance(body["value"], bool))
        ):
            raise web.HTTPBadRequest(
                text="Heizwert muss wahr, falsch oder automatisch sein"
            )
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
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
        body = await request.json()
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
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        await runtime.archive.flush()
        session_id = request.query.get("session_id")
        try:
            after = max(0, int(request.query.get("after", "0")))
        except ValueError as error:
            raise web.HTTPBadRequest() from error
        result = await asyncio.to_thread(runtime.archive.read, session_id, after=after)
        if result is None:
            raise web.HTTPNotFound()
        if session_id:
            for record in result["records"]:
                if record["kind"] == "diagnostic":
                    record["payload"]["messages"] = [
                        fault_message(k, v)
                        for k, v in record["payload"].get("faults", {}).items()
                    ]
        return self.json(result)


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
    hass.http.register_view(ExportView)
    hass.http.register_view(InstancesView)
    hass.http.register_view(StateView)
    hass.http.register_view(ControlView)
    hass.http.register_view(FinishPhaseView)
    hass.http.register_view(ParametersView)
    hass.http.register_view(TemperatureView)
    hass.http.register_view(ResetParametersView)
    hass.http.register_view(ProgramView)
    hass.http.register_view(LightView)
    hass.http.register_view(HeaterView)
    hass.http.register_view(LoggingView)
    data["api_registered"] = True
