"""Explicit name and registry maintenance from the integration options flow."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from importlib import import_module

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import MAX_LENGTH_STATE_ENTITY_ID
from homeassistant.core import valid_entity_id
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import entity_sources

from .const import CONF_BINDINGS, DOMAIN
from .runtime import Configuration

_LOGGER = logging.getLogger(__name__)


class MaintenanceError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _check_idle(entry):
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and not runtime.closed:
        if runtime.reconfiguring:
            raise MaintenanceError("configuration_busy")
        if runtime.session is not None:
            raise MaintenanceError("session_exists")
        if Configuration.from_options(entry.options).as_options() != runtime.configuration.as_options():
            # The options callback may be queued but not yet have set its
            # reconfiguration flag. Do not preview the old entity set then.
            raise MaintenanceError("configuration_busy")


def expected_entities(entry):
    """Use the setup factories, including entities disabled in HA."""
    return {
        (platform, entity.unique_id)
        for platform in ("number", "sensor", "switch", "climate", "button")
        for entity in import_module(f"{__package__}.{platform}").entities_for_entry(entry)
    }


def manageable_entities(hass, entry):
    registry = er.async_get(hass)
    bound = set(entry.options.get(CONF_BINDINGS, {}).values())
    return sorted(
        (
            entity for entity in registry.entities.values()
            if (entity.config_entry_id == entry.entry_id and entity.platform == DOMAIN)
            or entity.entity_id in bound
        ),
        key=lambda entity: entity.entity_id,
    )


def _all_bindings(hass):
    return {
        entity_id
        for entry in hass.config_entries.async_entries(DOMAIN)
        for entity_id in entry.options.get(CONF_BINDINGS, {}).values()
    }


def cleanup_candidates(hass, entry):
    _check_idle(entry)
    if entry.state != ConfigEntryState.LOADED:
        raise MaintenanceError("integration_not_loaded")
    expected = expected_entities(entry)
    bound = _all_bindings(hass)
    loaded = entity_sources(hass)
    return sorted(
        (
            entity
            for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if entity.platform == DOMAIN
            and (entity.domain, entity.unique_id) not in expected
            and entity.entity_id not in bound
            and entity.entity_id not in loaded
        ),
        key=lambda entity: entity.entity_id,
    )


def _identity(entity):
    return (
        entity.id, entity.entity_id, entity.unique_id, entity.platform,
        entity.config_entry_id, entity.name, entity.original_name,
        entity.disabled_by, entity.hidden_by, entity.device_id,
    )


def remove_entities(hass, entry, preview):
    """Only delete the exact set whose preview was confirmed; never history."""
    current = cleanup_candidates(hass, entry)
    if {_identity(entity) for entity in current} != {_identity(entity) for entity in preview}:
        raise MaintenanceError("preview_changed")
    registry = er.async_get(hass)
    for entity in preview:
        registry.async_remove(entity.entity_id)


async def async_rename_sauna(hass, entry, name):
    _check_idle(entry)
    if not isinstance(name, str) or not name.strip():
        raise MaintenanceError("required")
    name = name.strip()
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    if device is not None:
        registry.async_update_device(device.id, name=name, name_by_user=name)
    hass.config_entries.async_update_entry(entry, title=name)
    from .frontend import register

    await register(hass)


def _check_rename(hass, entry, selected, new_entity_id):
    _check_idle(entry)
    if not any(_identity(entity) == _identity(selected) for entity in manageable_entities(hass, entry)):
        raise MaintenanceError("entity_changed")
    if (
        not valid_entity_id(new_entity_id)
        or len(new_entity_id) > MAX_LENGTH_STATE_ENTITY_ID
        or new_entity_id.split(".")[0] != selected.domain
    ):
        raise MaintenanceError("invalid_entity_id")
    if new_entity_id != selected.entity_id and (
        er.async_get(hass).async_get(new_entity_id) is not None
        or hass.states.get(new_entity_id) is not None
    ):
        raise MaintenanceError("entity_id_in_use")


def _affected_entries(hass, entity_id):
    return sorted(
        (
            entry for entry in hass.config_entries.async_entries(DOMAIN)
            if entity_id in entry.options.get(CONF_BINDINGS, {}).values()
        ),
        key=lambda entry: entry.entry_id,
    )


async def async_rename_entity(hass, entry, selected, new_entity_id, name):
    """Rename in one explicit workflow; no registry-wide rename listener."""
    lock = hass.data.setdefault(DOMAIN, {}).setdefault("entity_maintenance_lock", asyncio.Lock())
    async with lock:
        _check_rename(hass, entry, selected, new_entity_id)
        affected = _affected_entries(hass, selected.entity_id) if new_entity_id != selected.entity_id else []
        snapshots = {item.entry_id: dict(item.options) for item in affected}
        candidates = {
            item.entry_id: {
                **item.options,
                CONF_BINDINGS: {
                    role: new_entity_id if value == selected.entity_id else value
                    for role, value in item.options[CONF_BINDINGS].items()
                },
            }
            for item in affected
        }
        for candidate in candidates.values():
            Configuration.from_options(candidate)
        runtimes = []
        locked_runtimes = {}
        unloaded = []
        registry = er.async_get(hass)
        async with AsyncExitStack() as locks:
            for item in affected:
                runtime = getattr(item, "runtime_data", None)
                if runtime is not None and not runtime.closed:
                    await locks.enter_async_context(runtime._lock)
                    runtimes.append(runtime)
                    locked_runtimes[item.entry_id] = runtime
            _check_rename(hass, entry, selected, new_entity_id)
            for item in affected:
                if (
                    item.entry_id in locked_runtimes
                    and getattr(item, "runtime_data", None) is not locked_runtimes[item.entry_id]
                ):
                    raise MaintenanceError("configuration_busy")
                if item.options != snapshots[item.entry_id]:
                    raise MaintenanceError("entity_changed")
                _check_idle(item)
                if item.state not in (ConfigEntryState.LOADED, ConfigEntryState.NOT_LOADED):
                    raise MaintenanceError("integration_not_loaded")
            for runtime in runtimes:
                runtime.reconfiguring = True
        try:
            # The existing unload path completes output handoff while the old
            # IDs still exist. An ordinary options reload after rename cannot.
            for item in affected:
                if item.state == ConfigEntryState.LOADED:
                    if not await hass.config_entries.async_unload(item.entry_id):
                        raise MaintenanceError("reload_failed")
                    unloaded.append(item)
            current = registry.async_get(selected.entity_id)
            if current is None or _identity(current) != _identity(selected):
                raise MaintenanceError("entity_changed")
            if new_entity_id != selected.entity_id and ([item.entry_id for item in affected] != [
                item.entry_id for item in _affected_entries(hass, selected.entity_id)
            ] or any(item.options != snapshots[item.entry_id] for item in affected)):
                raise MaintenanceError("entity_changed")
            # Recheck ID occupancy after unload awaits, before any mutation.
            if new_entity_id != selected.entity_id and (
                registry.async_get(new_entity_id) is not None
                or hass.states.get(new_entity_id) is not None
            ):
                raise MaintenanceError("entity_id_in_use")
            registry.async_update_entity(
                selected.entity_id, new_entity_id=new_entity_id, name=name
            )
            for item in affected:
                hass.config_entries.async_update_entry(item, options=candidates[item.entry_id])
        except MaintenanceError:
            raise
        except Exception as error:
            _LOGGER.exception("Entität konnte nicht umbenannt werden")
            raise MaintenanceError("reload_failed") from error
        finally:
            # Successful registry mutations retain their new bindings even if
            # setup fails. Before mutation this restores the unchanged setup.
            for runtime in runtimes:
                runtime.reconfiguring = False
            setup_failed = False
            for item in unloaded:
                try:
                    loaded = await hass.config_entries.async_setup(item.entry_id)
                except Exception:
                    _LOGGER.exception("Sauna %s konnte nicht neu geladen werden", item.entry_id)
                    loaded = False
                if not loaded:
                    setup_failed = True
            if setup_failed:
                raise MaintenanceError("reload_failed")
