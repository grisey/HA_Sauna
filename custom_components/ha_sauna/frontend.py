"""Lokales HA-Panel. Messdaten kommen ausschließlich über authentifizierte APIs."""
from pathlib import Path
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from .const import DOMAIN


async def register(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("panel_registered"):
        return
    await hass.http.async_register_static_paths([StaticPathConfig(
        "/ha_sauna/panel.js", str(Path(__file__).with_name("panel.js")), False)])
    await async_register_panel(hass, frontend_url_path="ha-sauna",
        webcomponent_name="ha-sauna-panel", sidebar_title="Sauna",
        sidebar_icon="mdi:radiator", module_url="/ha_sauna/panel.js",
        embed_iframe=False, require_admin=False)
    data["panel_registered"] = True
