"""Authentifizierte Archiv-/Exportansicht; keine Dateien unter www."""
import asyncio
from dataclasses import asdict
from aiohttp import web
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from .archive import plain
from .core.parameters import DEFINITIONS, Parameters
from .core.detection_parameters import SPECS
from .const import DOMAIN


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
        return self.json([{"entry_id": entry.entry_id, "title": entry.title}
            for entry in request.app[KEY_HASS].config_entries.async_entries(DOMAIN)
            if getattr(entry, "runtime_data", None) and not entry.runtime_data.closed])


class StateView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/state"
    name = "api:ha_sauna:state"
    requires_auth = True

    async def get(self, request, entry_id):
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        async with runtime._lock:
            controller, device, session = runtime.controller, runtime.device, runtime.session
            now = runtime._clock()
            active = session.timeline.active if session else None
            experts = {s[0] for s in SPECS}
            return self.json(plain({"now": now, "phase": controller.phase,
                "session": session, "configuration": runtime.configuration.as_options(),
                "parameters": [{**asdict(d), "expert": d.key in experts} for d in DEFINITIONS],
                "configuration_locked": session is not None,
                "operation_enabled": bool(session and session.operation_enabled),
                "heating_feedback": controller.feedback,
                "heating_observation": device.heating_observation if device else None,
                "energy_kwh": session.energy.total_kwh if session else 0,
                "energy_source": session.energy.source if session else "estimated",
                "heating_limit_seconds": controller.heating_limit_seconds,
                "readiness_target": controller.readiness_target,
                "cooling_wait_until": controller.cooling_wait_until,
                "mechanical_timer_ends_at": controller.mechanical_timer_ends_at,
                "gang_count": session.timeline.gang_count if session else 0,
                "gang_confirmation": active.confirmation if active else None,
                "gang_duration_seconds": active.elapsed_seconds(now) if active else None,
                "decision": controller.last_decision,
                "measurements": list(device.measurements.values()) if device else [],
                "faults": device.faults if device else {},
                "protection": sorted(controller.protection),
                "inhibits": sorted(controller.inhibits),
                "archive_error": str(runtime.archive.failure) if runtime.archive.failure else None,
                "detection_channels": runtime.detector.active_positions if runtime.detector else [],
                "detector_trace": runtime.detector.diagnostic if runtime.detector else None,
            }))


class ControlView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/control"
    name = "api:ha_sauna:control"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
            raise web.HTTPBadRequest(text="enabled muss wahr oder falsch sein")
        await runtime.set_operation(body["enabled"])
        return self.json({"success": True})


class ParametersView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/parameters"
    name = "api:ha_sauna:parameters"
    requires_auth = True

    async def post(self, request, entry_id):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden()
        hass = request.app[KEY_HASS]
        runtime = runtime_for(hass, entry_id)
        body = await request.json()
        try:
            parameters = Parameters(body)
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        async with runtime._lock:
            if runtime.session is not None:
                return self.json({"error": "Grundkonfiguration ist während einer Session gesperrt"}, status_code=409)
            runtime.check_configuration_change()
            entry = hass.config_entries.async_get_entry(entry_id)
            hass.config_entries.async_update_entry(entry, options={
                **entry.options, "parameters": parameters.as_dict()})
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
        return self.json(result)


class ExportView(HomeAssistantView):
    url = "/api/ha_sauna/{entry_id}/export"
    name = "api:ha_sauna:export"
    requires_auth = True

    async def get(self, request, entry_id):
        runtime = runtime_for(request.app[KEY_HASS], entry_id)
        async with runtime._lock:
            if runtime.session:
                runtime.archive.save_session(runtime.session, runtime._clock(), runtime.configuration.as_options())
        path = await runtime.archive.export()
        response = web.StreamResponse(headers={"Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="ha-sauna-archive.zip"',
            "Cache-Control": "no-store"})
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
    hass.http.register_view(ParametersView)
    data["api_registered"] = True
