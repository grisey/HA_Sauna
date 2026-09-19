"""Lokales HA-Panel. Messdaten kommen ausschließlich über authentifizierte APIs."""
import asyncio
from hashlib import sha256
from pathlib import Path
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from .const import DOMAIN


async def register(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("panel_registered"):
        return
    panel = Path(__file__).with_name("panel.js")
    # Auch bei unveränderter Integrationsversion muss ein HACS-Update eine
    # neue Moduladresse bekommen; Browser halten bereits geladene Module vor.
    fingerprint = sha256(await asyncio.to_thread(panel.read_bytes)).hexdigest()[:16]
    await hass.http.async_register_static_paths([StaticPathConfig(
        "/ha_sauna/panel.js", str(panel), False)])
    await async_register_panel(hass, frontend_url_path="ha-sauna",
        webcomponent_name="ha-sauna-panel", sidebar_title="Sauna",
        sidebar_icon="mdi:radiator", module_url=f"/ha_sauna/panel.js?v={fingerprint}",
        embed_iframe=False, require_admin=False)
    data["panel_registered"] = True
