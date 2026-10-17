"""Options forms reach the existing standards owner and the real HA listener."""

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from custom_components.ha_sauna.core.parameters import BY_KEY
from custom_components.ha_sauna.runtime import Configuration

from harness import create_sauna, start_hass
from test_settings import async_timeout


class OptionsFlowStandardsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.flow_inputs = []
        self.last_form = None
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def _seed(self, values=None, standards=None, **options):
        candidate = self.entry.runtime_data.configuration.with_field_values(
            values or {}
        )
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **candidate.as_options(),
                **options,
                "field_standards": standards or {},
            },
        )
        await self.hass.async_block_till_done()

    async def _submit(self, form, values):
        self.flow_inputs.append(
            {"step": form["step_id"], "submitted": deepcopy(values)}
        )
        result = await self.hass.config_entries.options.async_configure(
            form["flow_id"], values
        )
        self.last_form = result
        return result

    async def _open(self, subgroup=None):
        form = await self.hass.config_entries.options.async_init(self.entry.entry_id)
        self.assertEqual((form["type"], form["step_id"]), ("menu", "init"))
        if subgroup is None:
            return await self._submit(form, {"next_step_id": "field_standards"})
        area = next(
            definition.settings_group
            for definition in BY_KEY.values()
            if definition.settings_subgroup == subgroup
        )
        form = await self._submit(form, {"next_step_id": area})
        form = await self._submit(form, {"next_step_id": f"parameters_{subgroup}"})
        self.assertEqual(form["step_id"], f"parameters_{subgroup}")
        return form

    @staticmethod
    def _suggested(form):
        return {
            str(marker): marker.description["suggested_value"]
            for marker in form["data_schema"].schema
            if marker.description and "suggested_value" in marker.description
        }

    @staticmethod
    def _choices(form, key):
        selector = next(
            value
            for marker, value in form["data_schema"].schema.items()
            if str(marker) == key
        )
        return {
            item["value"] if isinstance(item, dict) else item
            for item in selector.config["options"]
        }

    def _snapshot(self):
        runtime = self.entry.runtime_data
        return {
            "options": deepcopy(dict(self.entry.options)),
            "active": runtime.configuration.as_options(),
            "archive": deepcopy(runtime.archive.read()),
            "runtime": runtime,
            "configuration": runtime.configuration,
        }

    def _assert_unchanged(self, before):
        self.assertEqual(dict(self.entry.options), before["options"])
        self.assertIs(self.entry.runtime_data, before["runtime"])
        self.assertIs(self.entry.runtime_data.configuration, before["configuration"])
        self.assertEqual(
            self.entry.runtime_data.configuration.as_options(), before["active"]
        )
        self.assertEqual(self.entry.runtime_data.archive.read(), before["archive"])

    def _targets(self, action, fields, value=None):
        configuration = self.entry.runtime_data.configuration
        metadata = configuration.standard_field_metadata()
        return [
            {
                "id": field,
                **metadata[field],
                "factory": configuration.field_value(field, source="factory"),
                "active": configuration.field_value(field),
                "standard": configuration.field_value(field, source="standard"),
                "has_standard": field in configuration.field_standards,
                "target": configuration.field_value(
                    field,
                    source=(
                        "active"
                        if action == "overwrite_standard"
                        else ("factory" if action == "restore_factory" else "standard")
                    ),
                ),
                "target_standard": value
                if action == "overwrite_standard"
                else (
                    configuration.field_value(
                        field,
                        source="factory" if action == "restore_factory" else "standard",
                    )
                ),
                "target_has_standard": action == "overwrite_standard"
                or (
                    action != "restore_factory"
                    and field in configuration.field_standards
                ),
            }
            for field in fields
        ]

    async def _preview(self, form, action, scope, draft=None):
        self.assertEqual(form["step_id"], "field_standards")
        form = await self._submit(form, {"action": action})
        self.assertEqual(form["step_id"], "field_standard_scope")
        self.assertIn(scope, self._choices(form, "scope"))
        if draft is not None:
            self.assertEqual(
                self._choices(form, "scope"),
                {"cancel"}
                | {f"field:parameters.{key}" for key in draft if key in BY_KEY},
            )
        form = await self._submit(form, {"scope": scope})
        self.assertEqual(form["step_id"], "field_standard_confirm")
        self.assertFalse(form["errors"])
        self.assertEqual(self._choices(form, "decision"), {"confirm", "cancel"})
        marker = next(iter(form["data_schema"].schema))
        self.assertEqual(marker.default(), "cancel")
        return form

    def _assert_preview(self, form, targets):
        text = form["description_placeholders"]["preview"]
        rows = [
            [cell.strip() for cell in line.split("|")[1:-1]]
            for line in text.splitlines()
            if line.startswith("| ")
        ]
        self.assertEqual(len(rows), len(targets))
        for target in targets:

            def rendered(value):
                if value is None:
                    return "—"
                if isinstance(value, bool):
                    return "✓" if value else "—"
                return str(value) + (f" {target['unit']}" if target["unit"] else "")

            expected = [
                rendered(target[key])
                for key in (
                    "factory",
                    "standard",
                    "target_standard",
                    "active",
                    "target",
                )
            ] + [
                ("✓" if target["has_standard"] else "—")
                + " → "
                + ("✓" if target["target_has_standard"] else "—")
            ]
            matched = next(
                (
                    row
                    for row in rows
                    if row[0].startswith(target["label"]) and row[1:] == expected
                ),
                None,
            )
            self.assertIsNotNone(matched, {"target": target, "rows": rows})
            rows.remove(matched)
        return text

    def _assert_applied(self, before, targets):
        current = self.entry.runtime_data.configuration
        stored = Configuration.from_options(self.entry.options)
        by_id = {row["id"]: row for row in targets}
        previous = before["configuration"]
        expected_options = deepcopy(before["active"])
        for row in targets:
            parts = row["id"].split(".")
            owner = expected_options
            for part in parts[:-1]:
                owner = owner[part]
            owner[parts[-1]] = row["target"]
            if row["target_has_standard"]:
                expected_options["field_standards"][row["id"]] = row["target_standard"]
            else:
                expected_options["field_standards"].pop(row["id"], None)
        self.assertEqual(stored.as_options(), expected_options)
        self.assertEqual(current.as_options(), expected_options)
        for field in previous.standard_fields():
            row = by_id.get(field)
            active = row["target"] if row else previous.field_value(field)
            standard = (
                row["target_standard"]
                if row
                else previous.field_value(field, source="standard")
            )
            has_standard = (
                row["target_has_standard"]
                if row
                else (field in previous.field_standards)
            )
            for configuration in (stored, current):
                self.assertEqual(configuration.field_value(field), active, field)
                self.assertEqual(
                    configuration.field_value(field, source="standard"), standard, field
                )
                self.assertEqual(
                    field in configuration.field_standards, has_standard, field
                )
        excluded_before = {
            key: value
            for key, value in before["active"].items()
            if key not in {"parameters", "appearance", "field_standards"}
        }
        self.assertEqual(
            {
                key: value
                for key, value in current.as_options().items()
                if key not in {"parameters", "appearance", "field_standards"}
            },
            excluded_before,
        )
        self.assertEqual(current.temperature_programs, previous.temperature_programs)
        self.assertEqual(self.entry.runtime_data.archive.read(), before["archive"])

    def _evidence(self, case, inputs, preview=None, targets=(), guards=None):
        stored = Configuration.from_options(self.entry.options)
        runtime = self.entry.runtime_data.configuration
        print(
            "OPTIONSFLOW_EVIDENCE "
            + json.dumps(
                {
                    "case": case,
                    "fixture": "harness.start_hass/create_sauna; synthetic HA sources",
                    "input": inputs,
                    "actual_flow_inputs": self.flow_inputs,
                    "observed_form": {
                        "type": self.last_form["type"],
                        "step": self.last_form.get("step_id"),
                        "errors": self.last_form.get("errors"),
                        "reason": self.last_form.get("reason"),
                    },
                    "visible_D": self._suggested(self.last_form)
                    if (self.last_form.get("step_id", "").startswith("parameters_"))
                    else None,
                    "current_preview": preview,
                    "confirmed_target": targets,
                    "persisted_A": {
                        field: stored.field_value(field)
                        for field in stored.standard_fields()
                    },
                    "persisted_S": dict(stored.field_standards),
                    "runtime_A": {
                        field: runtime.field_value(field)
                        for field in runtime.standard_fields()
                    },
                    "runtime_S": dict(runtime.field_standards),
                    "unchanged_guards": guards or {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    async def test_visible_draft_overwrite_cancel_and_save_keep_a_s_separate(self):
        key = "nominal_power_kw"
        field = f"parameters.{key}"
        definition = BY_KEY[key]
        factory = definition.default
        active, saved, draft = (
            round(factory + count * definition.number_step, 6) for count in (1, 2, 3)
        )
        await self._seed({field: active}, {field: saved})
        before = self._snapshot()
        form = await self._open("heater_accounting")
        self.assertEqual(self._suggested(form)[key], active)
        fields = {str(marker) for marker in form["data_schema"].schema}
        self.assertEqual(
            fields - {"settings_action"},
            {
                definition.key
                for definition in BY_KEY.values()
                if definition.settings_subgroup == "heater_accounting"
                and definition.configuration_surface == "integration"
                and before["configuration"].standard_field_metadata()[
                    f"parameters.{definition.key}"
                ]["applicable"]
            },
        )
        values = {**self._suggested(form), key: draft}
        sibling = "heat_reset_minutes"
        values[sibling] += BY_KEY[sibling].number_step
        navigation = {**values, "settings_action": "field_standards"}
        form = await self._submit(form, navigation)
        targets = self._targets("overwrite_standard", [field], draft)
        form = await self._preview(form, "overwrite_standard", f"field:{field}", values)
        preview = self._assert_preview(form, targets)
        self._assert_unchanged(before)
        form = await self._submit(form, {"decision": "cancel"})
        self.assertEqual(form["step_id"], "parameters_heater_accounting")
        self.assertEqual(self._suggested(form)[key], draft)
        self.assertEqual(self._suggested(form)[sibling], values[sibling])
        self._assert_unchanged(before)
        self._evidence(
            "cancel_visible_draft",
            [
                navigation,
                {"action": "overwrite_standard"},
                {"scope": f"field:{field}"},
                {"decision": "cancel"},
            ],
            preview,
            guards={"options_runtime_archive_unchanged": True, "all_D_retained": True},
        )

        form = await self._submit(form, navigation)
        form = await self._preview(form, "overwrite_standard", f"field:{field}", values)
        preview = self._assert_preview(form, targets)
        form = await self._submit(form, {"decision": "confirm"})
        await self.hass.async_block_till_done()
        self._assert_applied(before, targets)
        self.assertIs(self.entry.runtime_data, before["runtime"])
        self.assertEqual(self._suggested(form)[key], draft)
        self.assertEqual(self._suggested(form)[sibling], values[sibling])
        self._evidence(
            "visible_D_to_S",
            [
                navigation,
                {"action": "overwrite_standard"},
                {"scope": f"field:{field}"},
                {"decision": "confirm"},
            ],
            preview,
            targets,
            {"A_unchanged": True, "other_D_retained": True},
        )

        later_active = round(factory + 4 * definition.number_step, 6)
        save_input = {
            **self._suggested(form),
            key: later_active,
            "settings_action": "save",
        }
        result = await self._submit(form, save_input)
        self.assertEqual((result["type"], result["step_id"]), ("menu", "operation"))
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["parameters"][key], later_active)
        self.assertEqual(self.entry.options["parameters"][sibling], values[sibling])
        self.assertEqual(self.entry.options["field_standards"], {field: draft})
        self.assertEqual(
            self.entry.runtime_data.configuration.parameters.values[key], later_active
        )
        self.assertEqual(
            self.entry.runtime_data.configuration.parameters.values[sibling],
            values[sibling],
        )
        self.assertEqual(
            dict(self.entry.runtime_data.configuration.field_standards), {field: draft}
        )
        reopened = await self._open("heater_accounting")
        self.assertEqual(self._suggested(reopened)[key], later_active)
        self._evidence("save_D_to_A", [save_input], guards={"S_retained": True})
        before_discard = self._snapshot()
        discard_input = {
            **self._suggested(reopened),
            key: draft,
            "settings_action": "discard",
        }
        discarded = await self._submit(reopened, discard_input)
        self.assertEqual(self._suggested(discarded)[key], later_active)
        self._assert_unchanged(before_discard)
        self._evidence(
            "discard_D_to_A",
            [discard_input],
            guards={"options_runtime_archive_unchanged": True},
        )

    async def test_optional_price_none_and_zero_use_the_real_draft_and_save_path(self):
        key = "electricity_price_eur_per_kwh"
        field = f"parameters.{key}"
        await self._seed({field: BY_KEY[key].number_step})
        for value in (None, 0):
            with self.subTest(value=value):
                before = self._snapshot()
                form = await self._open("heater_accounting")
                values = self._suggested(form)
                if value is None:
                    # The native optional number input omits an empty price.
                    values.pop(key, None)
                else:
                    values[key] = value
                navigation = {**values, "settings_action": "field_standards"}
                form = await self._submit(form, navigation)
                targets = self._targets("overwrite_standard", [field], value)
                form = await self._preview(form, "overwrite_standard", f"field:{field}")
                preview = self._assert_preview(form, targets)
                self._assert_unchanged(before)
                form = await self._submit(form, {"decision": "confirm"})
                await self.hass.async_block_till_done()
                self._assert_applied(before, targets)
                self.assertIn(field, self.entry.options["field_standards"])
                self.assertEqual(self.entry.options["field_standards"][field], value)
                self.assertEqual(self._suggested(form)[key], value)
                self._evidence(
                    "optional_price_D_to_S",
                    [
                        navigation,
                        {"action": "overwrite_standard"},
                        {"scope": f"field:{field}"},
                        {"decision": "confirm"},
                    ],
                    preview,
                    targets,
                    {"A_unchanged": True, "None_distinct_from_zero": True},
                )
                save_input = {**self._suggested(form), "settings_action": "save"}
                if value is None:
                    save_input.pop(key, None)
                result = await self._submit(form, save_input)
                self.assertEqual(
                    (result["type"], result["step_id"]), ("menu", "operation")
                )
                await self.hass.async_block_till_done()
                for configuration in (
                    Configuration.from_options(self.entry.options),
                    self.entry.runtime_data.configuration,
                ):
                    self.assertEqual(configuration.field_value(field), value)
                    self.assertEqual(
                        configuration.field_value(field, source="standard"), value
                    )
                    self.assertIn(field, configuration.field_standards)
                self._evidence(
                    "optional_price_save_D_to_A",
                    [save_input],
                    guards={"S_retained": True, "None_distinct_from_zero": True},
                )

    async def test_field_restores_update_only_affected_draft_and_use_factory_fallback(
        self,
    ):
        key, sibling = "nominal_power_kw", "heat_reset_minutes"
        field = f"parameters.{key}"
        definition = BY_KEY[key]
        active = definition.default + definition.number_step
        saved = definition.default + 2 * definition.number_step
        for action, has_standard in (
            ("restore_standard", True),
            ("restore_standard", False),
            ("restore_factory", True),
        ):
            with self.subTest(action=action, has_standard=has_standard):
                await self._seed(
                    {field: active}, {field: saved} if has_standard else {}
                )
                before = self._snapshot()
                form = await self._open("heater_accounting")
                values = self._suggested(form)
                values[key] = round(active + 3 * definition.number_step, 6)
                values[sibling] += BY_KEY[sibling].number_step
                navigation = {**values, "settings_action": "field_standards"}
                form = await self._submit(form, navigation)
                targets = self._targets(action, [field])
                form = await self._preview(form, action, f"field:{field}")
                preview = self._assert_preview(form, targets)
                self._assert_unchanged(before)
                form = await self._submit(form, {"decision": "confirm"})
                await self.hass.async_block_till_done()
                self._assert_applied(before, targets)
                expected_draft = (
                    saved
                    if has_standard and action == "restore_standard"
                    else definition.default
                )
                self.assertEqual(self._suggested(form)[key], expected_draft)
                self.assertEqual(self._suggested(form)[sibling], values[sibling])
                self._evidence(
                    "field_" + action,
                    [
                        navigation,
                        {"action": action},
                        {"scope": f"field:{field}"},
                        {"decision": "confirm"},
                    ],
                    preview,
                    targets,
                    {
                        "out_of_scope_A_S_D_retained": True,
                        "W_fallback": not has_standard,
                    },
                )

    async def test_group_restores_preview_hidden_fields_and_preserve_outside_a_s_d(
        self,
    ):
        hidden = "parameters.strong_humidity_upper"
        outside = "parameters.nominal_power_kw"
        color = "appearance.colors.event_door"
        for action in ("restore_standard", "restore_factory"):
            with self.subTest(action=action):
                await self._seed(
                    {
                        hidden: BY_KEY["strong_humidity_upper"].default + 0.1,
                        outside: BY_KEY["nominal_power_kw"].default + 0.1,
                        color: "#102030",
                    },
                    {
                        hidden: BY_KEY["strong_humidity_upper"].default + 0.2,
                        outside: BY_KEY["nominal_power_kw"].default + 0.2,
                        color: "#405060",
                    },
                    presence_source="ha_presence",
                )
                before = self._snapshot()
                self.assertFalse(
                    before["configuration"].standard_field_metadata()[hidden][
                        "applicable"
                    ]
                )
                form = await self._open("heater_accounting")
                values = self._suggested(form)
                values["nominal_power_kw"] += BY_KEY["nominal_power_kw"].number_step
                navigation = {**values, "settings_action": "field_standards"}
                form = await self._submit(form, navigation)
                fields = before["configuration"].standard_fields(group="sensors")
                self.assertIn(hidden, fields)
                targets = self._targets(action, fields)
                form = await self._preview(form, action, "group:sensors")
                preview = self._assert_preview(form, targets)
                self._assert_unchanged(before)
                form = await self._submit(form, {"decision": "confirm"})
                await self.hass.async_block_till_done()
                self._assert_applied(before, targets)
                self.assertEqual(
                    self._suggested(form)["nominal_power_kw"],
                    values["nominal_power_kw"],
                )
                self._evidence(
                    "hidden_group_" + action,
                    [
                        navigation,
                        {"action": action},
                        {"scope": "group:sensors"},
                        {"decision": "confirm"},
                    ],
                    preview,
                    targets,
                    {
                        "hidden_field_in_preview": hidden,
                        "outside_A_S_D_retained": True,
                        "bindings_programs_archive_retained": True,
                    },
                )

    async def test_global_restores_include_appearance_and_preserve_excluded_owners(
        self,
    ):
        hidden = "parameters.strong_humidity_upper"
        visible = "parameters.nominal_power_kw"
        color = "appearance.colors.event_door"
        for action in ("restore_standard", "restore_factory"):
            with self.subTest(action=action):
                await self._seed(
                    {
                        hidden: BY_KEY["strong_humidity_upper"].default + 0.1,
                        visible: BY_KEY["nominal_power_kw"].default + 0.1,
                        color: "#102030",
                    },
                    {
                        hidden: BY_KEY["strong_humidity_upper"].default + 0.2,
                        visible: BY_KEY["nominal_power_kw"].default + 0.2,
                        color: "#405060",
                    },
                    presence_source="ha_presence",
                )
                before = self._snapshot()
                form = await self._open()
                self.assertNotIn("overwrite_standard", self._choices(form, "action"))
                targets = self._targets(
                    action, before["configuration"].standard_fields()
                )
                self.assertIn(color, {row["id"] for row in targets})
                self.assertIn(hidden, {row["id"] for row in targets})
                form = await self._preview(form, action, "all")
                preview = self._assert_preview(form, targets)
                self._assert_unchanged(before)
                result = await self._submit(form, {"decision": "confirm"})
                self.assertEqual((result["type"], result["step_id"]), ("menu", "init"))
                await self.hass.async_block_till_done()
                self._assert_applied(before, targets)
                self._evidence(
                    "global_" + action,
                    [{"action": action}, {"scope": "all"}, {"decision": "confirm"}],
                    preview,
                    targets,
                    {
                        "full_hidden_appearance_scope": True,
                        "bindings_programs_archive_retained": True,
                    },
                )

    async def test_stale_preview_requires_a_second_explicit_confirmation(self):
        field = "parameters.nominal_power_kw"
        factory = BY_KEY["nominal_power_kw"].default
        await self._seed({field: factory + 0.1}, {field: factory + 0.2})
        form = await self._open()
        old_targets = self._targets("restore_standard", [field])
        form = await self._preview(form, "restore_standard", f"field:{field}")
        old_preview = self._assert_preview(form, old_targets)
        await self._seed({field: factory + 0.1}, {field: factory + 0.3})
        before = self._snapshot()
        targets = self._targets("restore_standard", [field])
        renewed = await self._submit(form, {"decision": "confirm"})
        self.assertEqual(renewed["step_id"], "field_standard_confirm")
        self.assertEqual(renewed["errors"], {"base": "preview_changed"})
        preview = self._assert_preview(renewed, targets)
        self.assertNotEqual(preview, old_preview)
        self._assert_unchanged(before)
        self._evidence(
            "stale_no_write",
            [{"decision": "confirm"}],
            preview,
            guards={
                "old_preview": old_preview,
                "options_runtime_archive_unchanged": True,
            },
        )
        await self._submit(renewed, {"decision": "confirm"})
        await self.hass.async_block_till_done()
        self._assert_applied(before, targets)
        self._evidence(
            "renewed_confirmation",
            [{"decision": "confirm"}, {"decision": "confirm"}],
            preview,
            targets,
            {"renewed_target_applied": True},
        )

    async def test_invalid_restore_dependency_never_writes_options_or_runtime(self):
        field = "parameters.sauna_min_temperature_c"
        # S is valid as a scalar; restoring it would exceed the retained target.
        dependent_keys = (
            "preset_start_c",
            "target_temperature_c",
            "standard_temperature_c",
            "final_temperature_c",
        )
        invalid_minimum = (
            min(
                self.entry.runtime_data.configuration.parameters.values[key]
                for key in dependent_keys
            )
            + BY_KEY["sauna_min_temperature_c"].number_step
        )
        dependent_key = next(
            key
            for key in dependent_keys
            if self.entry.runtime_data.configuration.parameters.values[key]
            < invalid_minimum
        )
        await self._seed(standards={field: invalid_minimum})
        before = self._snapshot()
        form = await self._open()
        form = await self._submit(form, {"action": "restore_standard"})
        result = await self._submit(form, {"scope": f"field:{field}"})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "field_standard_scope")
        self.assertEqual(result["errors"], {"base": "parameter_dependency"})
        self.assertEqual(
            result["description_placeholders"]["parameter"], BY_KEY[dependent_key].label
        )
        self.assertNotIn(
            "decision", {str(marker) for marker in result["data_schema"].schema}
        )
        self._assert_unchanged(before)
        self._evidence(
            "invalid_dependency",
            [{"action": "restore_standard"}, {"scope": f"field:{field}"}],
            result.get("description_placeholders"),
            guards={
                "errors": result["errors"],
                "options_runtime_archive_unchanged": True,
            },
        )

    async def test_existing_reconfiguration_drift_and_session_guards_reject_confirmation(
        self,
    ):
        field = "parameters.nominal_power_kw"
        factory = BY_KEY["nominal_power_kw"].default
        await self._seed({field: factory + 0.1}, {field: factory + 0.2})
        for guard in ("reconfiguring", "options_drift", "session"):
            with self.subTest(guard=guard):
                form = await self._open()
                targets = self._targets("restore_standard", [field])
                form = await self._preview(form, "restore_standard", f"field:{field}")
                preview = self._assert_preview(form, targets)
                runtime = self.entry.runtime_data
                release = asyncio.Event()
                terminal_gate = None
                try:
                    if guard == "reconfiguring":
                        runtime.reconfiguring = True
                    elif guard == "options_drift":
                        started = asyncio.Event()
                        original_handoff = runtime.device.prepare_heater_handoff

                        async def delayed_handoff(**kwargs):
                            started.set()
                            await release.wait()
                            return await original_handoff(**kwargs)

                        # Only the terminal device waits. HA's listener and
                        # settings owner retain their production implementations.
                        terminal_gate = patch.object(
                            runtime.device,
                            "prepare_heater_handoff",
                            side_effect=delayed_handoff,
                        )
                        terminal_gate.start()
                        self.hass.config_entries.async_update_entry(
                            self.entry,
                            options={
                                **self.entry.options,
                                "parameters": {
                                    **self.entry.options["parameters"],
                                    "nominal_power_kw": factory + 0.3,
                                },
                            },
                        )
                        await asyncio.wait_for(
                            started.wait(), timeout=async_timeout("reload")
                        )
                        self.assertNotEqual(
                            Configuration.from_options(self.entry.options).as_options(),
                            runtime.configuration.as_options(),
                        )
                    else:
                        await runtime.set_operation(True)
                        await self.hass.async_block_till_done()
                    before = self._snapshot()
                    result = await self._submit(form, {"decision": "confirm"})
                    if guard == "session":
                        self.assertEqual(
                            (result["type"], result["reason"]),
                            ("abort", "session_exists"),
                        )
                    else:
                        self.assertEqual(result["step_id"], "field_standard_confirm")
                        self.assertEqual(
                            result["errors"], {"base": "configuration_busy"}
                        )
                    self._assert_unchanged(before)
                    self._evidence(
                        "locked_" + guard,
                        [{"decision": "confirm"}],
                        preview,
                        guards={
                            "guard": guard,
                            "options_runtime_archive_unchanged": True,
                            "result": result.get("errors", result.get("reason")),
                        },
                    )
                finally:
                    if guard == "reconfiguring":
                        runtime.reconfiguring = False
                    release.set()
                    await self.hass.async_block_till_done()
                    if terminal_gate is not None:
                        terminal_gate.stop()
