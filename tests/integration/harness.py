"""Echter HA-Kern, ConfigEntry-Manager und Entitätsplattformen; isolierter Pfad."""
from pathlib import Path
import tempfile
import shutil

from homeassistant import bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ha_sauna.bindings import ROLES
from custom_components.ha_sauna.core.parameters import DEFINITIONS

ROOT = Path(__file__).resolve().parents[2]


async def start_hass(directory=None):
    temp = tempfile.TemporaryDirectory(prefix="ha-sauna-test-") if directory is None else None
    path = Path(temp.name if temp else directory)
    path.mkdir(parents=True, exist_ok=True)
    (path / "custom_components").mkdir(exist_ok=True)
    shutil.copytree(ROOT / "custom_components/ha_sauna", path / "custom_components/ha_sauna", dirs_exist_ok=True)
    hass = HomeAssistant(str(path))
    hass.config.skip_pip = True
    hass.config.latitude = 0
    hass.config.longitude = 0
    hass.config.elevation = 0
    await hass.config.async_set_time_zone("UTC")
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    assert await bootstrap.async_load_base_functionality(hass)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "persistent_notification", {})
    await hass.async_start()
    return hass, temp


def seed_sources(hass):
    bindings = {r.key: f"{r.domains[0]}.test_{r.key}" for r in ROLES if not r.optional}
    for role in ROLES:
        if role.key not in bindings:
            continue
        hass.states.async_set(bindings[role.key], "off" if role.unit is None else "25", {
            "device_class": role.device_class,
            "unit_of_measurement": role.unit,
            "supported_color_modes": ["brightness"],
        })
    return bindings


async def create_sauna(hass):
    bindings = seed_sources(hass)
    flow = await hass.config_entries.flow.async_init("ha_sauna", context={"source": "user"})
    assert flow["step_id"] == "user", flow
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"name": "Testsauna", **bindings})
    assert flow["step_id"] == "parameters", flow
    values = {d.key: 2.5 for d in DEFINITIONS}
    values["heating_reduction_minutes"] = 0.5
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()
    return result["result"]
