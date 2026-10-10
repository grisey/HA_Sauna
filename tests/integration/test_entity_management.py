"""Registry maintenance through the real HA lifecycle and options flow."""

import unittest
from unittest.mock import patch

from aiohttp import ClientSession

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.ha_sauna.rename_selection import rename_groups
from custom_components.ha_sauna.const import DOMAIN
from custom_components.ha_sauna.maintenance import (
    MaintenanceError,
    async_rename_entity,
    cleanup_candidates,
    expected_entities,
    remove_entities,
)

from harness import create_sauna, credentials, start_hass


class MaintenanceRelay(SwitchEntity):
    _attr_name = "Maintenance relay"
    _attr_unique_id = "maintenance-relay"
    _attr_should_poll = False
    _attr_is_on = False

    def __init__(self):
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append((self.entity_id, True))
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append((self.entity_id, False))
        self._attr_is_on = False
        self.async_write_ha_state()


class MaintenanceTemperature(SensorEntity):
    _attr_name = "Maintenance temperature"
    _attr_unique_id = "maintenance-temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°C"
    _attr_native_value = 25
    _attr_should_poll = False


class EntityManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        self.registry = er.async_get(self.hass)

    def obsolete(self, entry, suffix="retired"):
        return self.registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{suffix}",
            config_entry=entry, suggested_object_id=f"maintenance_{suffix}",
        )

    async def relay_sauna(self):
        await async_setup_component(self.hass, "switch", {})
        relay = MaintenanceRelay()
        await self.hass.data["switch"].async_add_entities([relay])
        entry = await create_sauna(
            self.hass, binding_overrides={"heater": relay.entity_id}
        )
        relay.calls.clear()
        return entry, relay

    async def shared_sensor_saunas(self):
        await async_setup_component(self.hass, "sensor", {})
        sensor = MaintenanceTemperature()
        await self.hass.data["sensor"].async_add_entities([sensor])
        first = await create_sauna(
            self.hass, binding_overrides={"upper_temperature": sensor.entity_id}
        )
        second = await create_sauna(self.hass, binding_overrides={
            "heater": "switch.second_sauna_heater", "upper_temperature": sensor.entity_id,
        })
        return sensor, first, second

    async def test_cleanup_preserves_disabled_current_and_external_entities(self):
        entry = await create_sauna(self.hass)
        current = er.async_entries_for_config_entry(self.registry, entry.entry_id)
        self.assertEqual(
            {(item.domain, item.unique_id) for item in current},
            expected_entities(entry),
        )
        self.registry.async_update_entity(
            current[0].entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        await self.hass.async_block_till_done()
        retired = self.obsolete(entry)
        foreign = self.registry.async_get_or_create(
            "sensor", "external_test", "unrelated-source",
            suggested_object_id="maintenance_external",
        )
        preview = cleanup_candidates(self.hass, entry)
        self.assertEqual([item.entity_id for item in preview], [retired.entity_id])

        remove_entities(self.hass, entry, preview)
        await self.hass.async_block_till_done()

        self.assertIsNone(self.registry.async_get(retired.entity_id))
        self.assertIsNotNone(self.registry.async_get(foreign.entity_id))
        self.assertTrue(all(self.registry.async_get(item.entity_id) for item in current))

    async def test_cleanup_changed_preview_removes_nothing(self):
        entry = await create_sauna(self.hass)
        first = self.obsolete(entry, "first")
        preview = cleanup_candidates(self.hass, entry)
        second = self.obsolete(entry, "second")

        with self.assertRaises(MaintenanceError):
            remove_entities(self.hass, entry, preview)

        self.assertIsNotNone(self.registry.async_get(first.entity_id))
        self.assertIsNotNone(self.registry.async_get(second.entity_id))

    async def test_cleanup_waits_for_queued_configuration_change(self):
        entry = await create_sauna(self.hass)
        retired = self.obsolete(entry)
        before = dict(entry.options)
        self.hass.config_entries.async_update_entry(entry, options={
            **before,
            "bindings": {
                key: value for key, value in before["bindings"].items()
                if not key.startswith("lower_")
            },
        })
        # No event-loop turn: the options listener has not set busy yet.
        with self.assertRaises(MaintenanceError) as error:
            cleanup_candidates(self.hass, entry)
        self.assertEqual(error.exception.code, "configuration_busy")
        self.assertIsNotNone(self.registry.async_get(retired.entity_id))
        await self.hass.async_block_till_done()

    async def test_cleanup_preserves_loaded_and_bound_obsolete_entities(self):
        entry = await create_sauna(self.hass)
        bound = self.obsolete(entry, "bound_source")
        self.hass.config_entries.async_update_entry(entry, options={
            **entry.options,
            "bindings": {**entry.options["bindings"], "environment_temperature": bound.entity_id},
        })
        await self.hass.async_block_till_done()
        # Mark a loaded entity obsolete only after the binding update settles.
        operation = next(
            item for item in er.async_entries_for_config_entry(self.registry, entry.entry_id)
            if item.unique_id.endswith("_operation")
        )
        self.registry.async_update_entity(
            operation.entity_id, new_unique_id=f"{entry.entry_id}_loaded_obsolete"
        )
        await self.hass.async_block_till_done()

        candidates = {item.entity_id for item in cleanup_candidates(self.hass, entry)}

        self.assertNotIn(operation.entity_id, candidates)
        self.assertNotIn(bound.entity_id, candidates)

    async def test_cleanup_reused_entity_id_is_not_previewed_identity(self):
        entry = await create_sauna(self.hass)
        retired = self.obsolete(entry)
        preview = cleanup_candidates(self.hass, entry)
        self.registry.async_remove(retired.entity_id)
        replacement = self.obsolete(entry, "replacement")
        replacement = self.registry.async_update_entity(
            replacement.entity_id, new_entity_id=retired.entity_id
        )

        with self.assertRaises(MaintenanceError):
            remove_entities(self.hass, entry, preview)

        self.assertEqual(self.registry.async_get(retired.entity_id).id, replacement.id)

    async def test_cleanup_cancel_keeps_previewed_entity(self):
        entry = await create_sauna(self.hass)
        retired = self.obsolete(entry)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "cleanup_entities"}
        )
        self.assertEqual(flow["step_id"], "cleanup_entities")
        self.assertIn(retired.entity_id, str(flow.get("description_placeholders", {})))
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"confirm": False}
        )
        self.assertEqual(result["step_id"], "init")
        self.assertIsNotNone(self.registry.async_get(retired.entity_id))

    async def test_cleanup_flow_requires_new_confirmation_after_preview_changes(self):
        entry = await create_sauna(self.hass)
        first = self.obsolete(entry, "first")
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "cleanup_entities"}
        )
        second = self.obsolete(entry, "second")

        revised = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"confirm": True}
        )

        self.assertEqual(revised["step_id"], "cleanup_entities")
        self.assertEqual(revised["errors"], {"base": "preview_changed"})
        for item in (first, second):
            self.assertIsNotNone(self.registry.async_get(item.entity_id))
            self.assertIn(item.entity_id, revised["description_placeholders"]["entities"])
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"confirm": True}
        )
        self.assertEqual(result["step_id"], "init")
        self.assertIsNone(self.registry.async_get(first.entity_id))
        self.assertIsNone(self.registry.async_get(second.entity_id))

    async def test_own_entity_rename_preserves_unique_identity_and_bindings(self):
        entry = await create_sauna(self.hass)
        selected = next(
            item for item in er.async_entries_for_config_entry(self.registry, entry.entry_id)
            if item.unique_id.endswith("_operation")
        )
        before = dict(entry.options)
        new_id = "switch.renamed_sauna_operation"

        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "rename_entity"}
        )
        group = next(key for key, value in rename_groups(self.hass, entry).items()
                     if selected in value["entities"])
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"group": group}
        )
        self.assertEqual(flow["step_id"], "rename_entity_select")
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"entity_id": selected.entity_id}
        )
        self.assertEqual(flow["step_id"], "rename_entity_edit")
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"new_entity_id": new_id, "name": "Saunasteuerung"}
        )
        self.assertEqual(result["step_id"], "init")
        await self.hass.async_block_till_done()

        renamed = self.registry.async_get(new_id)
        self.assertEqual((renamed.id, renamed.unique_id), (selected.id, selected.unique_id))
        self.assertEqual(renamed.name, "Saunasteuerung")
        self.assertIsNone(self.registry.async_get(selected.entity_id))
        self.assertIsNotNone(self.hass.states.get(new_id))
        self.assertEqual(dict(entry.options), before)

    async def test_grouped_rename_keeps_disabled_entities_and_back_navigation(self):
        entry = await create_sauna(self.hass)
        selected = next(
            item for item in er.async_entries_for_config_entry(self.registry, entry.entry_id)
            if item.unique_id.endswith("_target_temperature_c")
        )
        selected = self.registry.async_update_entity(
            selected.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        await self.hass.async_block_till_done()
        self.assertIsNone(self.hass.states.get(selected.entity_id))
        groups = rename_groups(self.hass, entry)
        group = next(key for key, value in groups.items() if selected in value["entities"])
        self.assertNotEqual(group, "own:controls")
        self.assertGreater(len(groups), 3)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "rename_entity"}
        )
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"group": group}
        )
        fields = {str(key): value for key, value in flow["data_schema"].schema.items()}
        self.assertIn(selected.entity_id, [
            option["value"] for option in fields["inactive_entity_id"].config["options"]
        ])
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"inactive_entity_id": selected.entity_id}
        )
        self.assertEqual(flow["step_id"], "rename_entity_edit")
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"back": True}
        )
        self.assertEqual(flow["step_id"], "rename_entity_select")
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"back": True}
        )
        self.assertEqual(flow["step_id"], "rename_entity")
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"group": "back"}
        )
        self.assertEqual(result["step_id"], "init")
        self.assertEqual(self.registry.async_get(selected.entity_id), selected)

    async def test_rename_groups_limit_native_picker_to_selected_group(self):
        entry, relay = await self.relay_sauna()
        foreign = self.registry.async_get_or_create(
            "sensor", "unrelated", "unrelated-picker-source",
            suggested_object_id="unrelated_picker_source",
        )
        groups = rename_groups(self.hass, entry)
        selected = self.registry.async_get(relay.entity_id)
        group = next(key for key, value in groups.items() if selected in value["entities"])
        self.assertTrue(group.startswith("entity:"))
        self.assertNotIn(foreign.entity_id, [
            entity.entity_id for value in groups.values() for entity in value["entities"]
        ])
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "rename_entity"}
        )
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"group": group}
        )
        fields = {str(key): value for key, value in flow["data_schema"].schema.items()}
        self.assertEqual(fields["entity_id"].selector_type, "entity")
        self.assertEqual(fields["entity_id"].config["include_entities"], [relay.entity_id])

    async def test_name_only_rename_does_not_reload(self):
        entry, relay = await self.relay_sauna()
        runtime = entry.runtime_data
        selected = self.registry.async_get(relay.entity_id)

        with patch.object(self.hass.config_entries, "async_unload") as unload:
            await async_rename_entity(
                self.hass, entry, selected, selected.entity_id, "Ofenrelais"
            )
            await self.hass.async_block_till_done()
            unload.assert_not_called()

        self.assertIs(entry.runtime_data, runtime)
        self.assertEqual(self.registry.async_get(relay.entity_id).name, "Ofenrelais")

    async def test_heater_rename_shuts_down_old_id_before_updating_binding(self):
        entry, relay = await self.relay_sauna()
        old_id = relay.entity_id
        selected = self.registry.async_get(old_id)
        new_id = "switch.renamed_maintenance_heater"

        await async_rename_entity(self.hass, entry, selected, new_id, None)
        await self.hass.async_block_till_done()

        self.assertIn((old_id, False), relay.calls)
        self.assertNotIn((old_id, True), relay.calls)
        self.assertEqual(entry.options["bindings"]["heater"], new_id)
        self.assertEqual(entry.runtime_data.device.bindings["heater"], new_id)
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(relay.entity_id, new_id)
        await entry.runtime_data.device.send(False, entry.runtime_data._clock(), force=True)
        self.assertIn((new_id, False), relay.calls)

    async def test_unload_failure_leaves_registry_and_bindings_unchanged(self):
        entry, relay = await self.relay_sauna()
        selected = self.registry.async_get(relay.entity_id)
        before = dict(entry.options)
        with patch.object(self.hass.config_entries, "async_unload", return_value=False):
            with self.assertRaises(MaintenanceError):
                await async_rename_entity(
                    self.hass, entry, selected, "switch.failed_rename", "Changed"
                )

        self.assertEqual(self.registry.async_get(selected.entity_id), selected)
        self.assertIsNone(self.registry.async_get("switch.failed_rename"))
        self.assertEqual(dict(entry.options), before)
        self.assertFalse(entry.runtime_data.reconfiguring)

    async def test_setup_failure_keeps_new_binding_for_retry(self):
        entry, relay = await self.relay_sauna()
        selected = self.registry.async_get(relay.entity_id)
        new_id = "switch.setup_failed_renamed_heater"
        with patch.object(self.hass.config_entries, "async_setup", return_value=False):
            with self.assertRaises(MaintenanceError):
                await async_rename_entity(self.hass, entry, selected, new_id, None)
            await self.hass.async_block_till_done()

        self.assertIsNone(self.registry.async_get(selected.entity_id))
        self.assertEqual(self.registry.async_get(new_id).id, selected.id)
        self.assertEqual(entry.options["bindings"]["heater"], new_id)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(entry.runtime_data.device.bindings["heater"], new_id)

    async def test_state_only_id_collision_changes_nothing(self):
        entry, relay = await self.relay_sauna()
        selected = self.registry.async_get(relay.entity_id)
        self.hass.states.async_set("switch.occupied_id", "off")
        before = dict(entry.options)
        runtime = entry.runtime_data

        with self.assertRaises(MaintenanceError):
            await async_rename_entity(self.hass, entry, selected, "switch.occupied_id", None)

        self.assertEqual(dict(entry.options), before)
        self.assertEqual(self.registry.async_get(selected.entity_id), selected)
        self.assertIs(entry.runtime_data, runtime)

    async def test_invalid_id_or_unrelated_selection_changes_nothing(self):
        entry, relay = await self.relay_sauna()
        selected = self.registry.async_get(relay.entity_id)
        original = dict(entry.options)
        foreign = self.registry.async_get_or_create(
            "sensor", "external_test", "not-a-sauna-binding",
            suggested_object_id="unrelated_maintenance_sensor",
        )
        for new_id in ("no_domain", "switch.has space", "sensor.wrong_domain"):
            with self.subTest(new_id=new_id):
                with self.assertRaises(MaintenanceError) as error:
                    await async_rename_entity(self.hass, entry, selected, new_id, None)
                self.assertEqual(error.exception.code, "invalid_entity_id")
        with self.assertRaises(MaintenanceError) as error:
            await async_rename_entity(self.hass, entry, foreign, "sensor.unrelated_renamed", None)
        self.assertEqual(error.exception.code, "entity_changed")
        self.assertEqual(self.registry.async_get(selected.entity_id), selected)
        self.assertEqual(self.registry.async_get(foreign.entity_id), foreign)
        self.assertEqual(dict(entry.options), original)

    async def test_rename_flow_rejects_selection_changed_since_edit_form(self):
        entry, relay = await self.relay_sauna()
        selected = self.registry.async_get(relay.entity_id)
        original = dict(entry.options)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "rename_entity"}
        )
        group = next(key for key, value in rename_groups(self.hass, entry).items()
                     if selected in value["entities"])
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"group": group}
        )
        self.assertEqual(flow["step_id"], "rename_entity_select")
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"entity_id": selected.entity_id}
        )
        self.registry.async_update_entity(selected.entity_id, name="Changed elsewhere")
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"new_entity_id": "switch.stale_selection", "name": "Edited"}
        )

        self.assertEqual(result["step_id"], "rename_entity_edit")
        self.assertEqual(result["errors"], {"base": "entity_changed"})
        self.assertEqual(self.registry.async_get(selected.entity_id).name, "Changed elsewhere")
        self.assertIsNone(self.registry.async_get("switch.stale_selection"))
        self.assertEqual(dict(entry.options), original)

    async def test_shared_sensor_rename_updates_all_sauna_bindings(self):
        sensor, first, second = await self.shared_sensor_saunas()
        selected = self.registry.async_get(sensor.entity_id)
        new_id = "sensor.shared_renamed_temperature"

        await async_rename_entity(self.hass, first, selected, new_id, None)
        await self.hass.async_block_till_done()

        for entry in (first, second):
            self.assertEqual(entry.options["bindings"]["upper_temperature"], new_id)
            self.assertEqual(entry.runtime_data.device.bindings["upper_temperature"], new_id)
        sensor._attr_native_value = 39
        sensor.async_write_ha_state()
        await self.hass.async_block_till_done()
        for entry in (first, second):
            self.assertEqual(entry.runtime_data.device.states["upper_temperature"].state, "39")

    async def test_shared_source_rename_rejects_busy_and_active_other_sauna(self):
        sensor, first, second = await self.shared_sensor_saunas()
        selected = self.registry.async_get(sensor.entity_id)
        original = {entry.entry_id: dict(entry.options) for entry in (first, second)}
        second.runtime_data.reconfiguring = True
        try:
            with self.assertRaises(MaintenanceError) as error:
                await async_rename_entity(
                    self.hass, first, selected, "sensor.busy_shared_source", None
                )
            self.assertEqual(error.exception.code, "configuration_busy")
        finally:
            second.runtime_data.reconfiguring = False
        await second.runtime_data.set_operation(True)
        with self.assertRaises(MaintenanceError) as error:
            await async_rename_entity(
                self.hass, first, selected, "sensor.active_shared_source", None
            )
        self.assertEqual(error.exception.code, "session_exists")
        self.assertEqual(self.registry.async_get(selected.entity_id), selected)
        for entry in (first, second):
            self.assertEqual(dict(entry.options), original[entry.entry_id])

    async def test_setup_exception_does_not_skip_other_affected_entry(self):
        sensor, first, second = await self.shared_sensor_saunas()
        selected = self.registry.async_get(sensor.entity_id)
        ordered = sorted((first, second), key=lambda entry: entry.entry_id)
        original_setup = self.hass.config_entries.async_setup
        attempted = []

        async def setup(entry_id, *args, **kwargs):
            attempted.append(entry_id)
            if entry_id == ordered[0].entry_id:
                raise RuntimeError("Synthetic setup failure")
            return await original_setup(entry_id, *args, **kwargs)

        new_id = "sensor.setup_exception_renamed_source"
        with patch.object(self.hass.config_entries, "async_setup", side_effect=setup):
            with self.assertRaises(MaintenanceError) as error:
                await async_rename_entity(self.hass, first, selected, new_id, None)
            self.assertEqual(error.exception.code, "reload_failed")
        await self.hass.async_block_till_done()

        self.assertEqual(attempted, [entry.entry_id for entry in ordered])
        self.assertEqual(ordered[1].state, ConfigEntryState.LOADED)
        for entry in ordered:
            self.assertEqual(entry.options["bindings"]["upper_temperature"], new_id)
        self.assertEqual(self.registry.async_get(new_id).id, selected.id)
        self.assertTrue(await original_setup(ordered[0].entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(ordered[0].runtime_data.device.bindings["upper_temperature"], new_id)

    async def test_second_unload_failure_restores_first_without_renaming(self):
        sensor, first, second = await self.shared_sensor_saunas()
        selected = self.registry.async_get(sensor.entity_id)
        ordered = sorted((first, second), key=lambda entry: entry.entry_id)
        original = {entry.entry_id: dict(entry.options) for entry in ordered}
        original_unload = self.hass.config_entries.async_unload

        async def unload(entry_id, *args, **kwargs):
            if entry_id == ordered[1].entry_id:
                return False
            return await original_unload(entry_id, *args, **kwargs)

        with patch.object(self.hass.config_entries, "async_unload", side_effect=unload):
            with self.assertRaises(MaintenanceError):
                await async_rename_entity(
                    self.hass, first, selected, "sensor.failed_second_unload", None
                )
        await self.hass.async_block_till_done()

        self.assertEqual(self.registry.async_get(selected.entity_id), selected)
        self.assertIsNone(self.registry.async_get("sensor.failed_second_unload"))
        for entry in ordered:
            self.assertEqual(entry.state, ConfigEntryState.LOADED)
            self.assertFalse(entry.runtime_data.reconfiguring)
            self.assertEqual(dict(entry.options), original[entry.entry_id])

    async def test_integration_name_updates_device_without_reload(self):
        entry = await create_sauna(self.hass)
        runtime = entry.runtime_data
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "name"}
        )
        await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"name": "Gartensauna"}
        )
        await self.hass.async_block_till_done()

        self.assertEqual(entry.title, "Gartensauna")
        self.assertIs(entry.runtime_data, runtime)
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        self.assertEqual(device.name_by_user or device.name, "Gartensauna")
        token = await credentials(self.hass)
        base = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna"
        async with ClientSession(headers={"Authorization": f"Bearer {token}"}) as client:
            async with client.get(base) as response:
                instances = await response.json()
            async with client.get(f"{base}/{entry.entry_id}/state") as response:
                self.assertEqual(response.status, 200)
                state = await response.json()
        self.assertEqual(state["title"], entry.title)
        self.assertEqual(state["instances"], instances)
        self.assertEqual(instances, [{"entry_id": entry.entry_id, "title": entry.title}])
