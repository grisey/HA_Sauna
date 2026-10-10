"""Lokales HA-Panel. Messdaten kommen ausschließlich über authentifizierte APIs."""

import asyncio
from hashlib import sha256
from pathlib import Path
from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from .const import DOMAIN


async def register(hass, *, exclude_entry_id=None):
    data = hass.data.setdefault(DOMAIN, {})
    async with data.setdefault("panel_register_lock", asyncio.Lock()):
        entries = [
            entry for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != exclude_entry_id
        ]
        if not entries and exclude_entry_id is not None:
            if data.get("panel_registered"):
                frontend.async_remove_panel(hass, "ha-sauna")
            data["panel_registered"] = False
            data.pop("panel_sidebar_title", None)
            return
        title = entries[0].title if len(entries) == 1 else "Sauna"
        if data.get("panel_registered"):
            if data.get("panel_sidebar_title") != title:
                panel = hass.data[frontend.DATA_PANELS]["ha-sauna"]
                frontend.async_register_built_in_panel(
                    hass,
                    component_name=panel.component_name,
                    frontend_url_path=panel.frontend_url_path,
                    sidebar_title=title,
                    sidebar_icon=panel.sidebar_icon,
                    config=panel.config,
                    require_admin=panel.require_admin,
                    update=True,
                )
                data["panel_sidebar_title"] = title
            return
        panel = Path(__file__).with_name("panel.js")
        # Auch bei unveränderter Integrationsversion muss ein HACS-Update eine
        # neue Moduladresse bekommen; Browser halten bereits geladene Module vor.
        fingerprint = sha256(await asyncio.to_thread(panel.read_bytes)).hexdigest()[:16]
        if not data.get("panel_static_registered"):
            await hass.http.async_register_static_paths(
                [StaticPathConfig("/ha_sauna/panel.js", str(panel), False)]
            )
            data["panel_static_registered"] = True
        await async_register_panel(
            hass,
            frontend_url_path="ha-sauna",
            webcomponent_name="ha-sauna-panel",
            sidebar_title=title,
            sidebar_icon="mdi:radiator",
            module_url=f"/ha_sauna/panel.js?v={fingerprint}",
            embed_iframe=False,
            require_admin=False,
        )
        data["panel_registered"] = True
        data["panel_sidebar_title"] = title
