"""Authentifizierte Archiv-/Exportansicht; keine Dateien unter www."""
import asyncio
from aiohttp import web
from homeassistant.components.http import HomeAssistantView, KEY_HASS
from .const import DOMAIN


def runtime_for(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise web.HTTPNotFound()
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None or runtime.closed:
        raise web.HTTPServiceUnavailable()
    return runtime


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
    data["api_registered"] = True
