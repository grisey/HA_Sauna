"""Grundgerüst mit synthetischen Daten: keine HA-Instanz und keine Geräte nötig."""
import asyncio
import ast
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from custom_components.ha_sauna.bindings import BindingError, Bindings, ROLES, validate_metadata
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.models import Deadline, HeatingTime, Measurement, Position, Quantity, Session
from custom_components.ha_sauna.core.parameters import DEFINITIONS, ParameterError, Parameters
from custom_components.ha_sauna.core.timeline import Confirmation, Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def parameters():
    # Rein synthetische Testeingabe; keine produktiven Ausgangswerte.
    return Parameters({**{definition.key: definition.default if definition.default is not None else 2.5 for definition in DEFINITIONS if definition.key not in {"final_temperature_c", "door_request_minutes"}},
                       "session_gap_minutes": 2.5})


def bindings():
    return Bindings({role.key: f"{role.domains[0]}.test_{role.key}" for role in ROLES if not role.optional})


def metadata(binding):
    result = {}
    for role in ROLES:
        if role.key not in binding.values:
            continue
        result[binding.values[role.key]] = {
            "device_class": role.device_class,
            "unit_of_measurement": role.unit,
            "supported_color_modes": ["brightness"],
        }
    return result


def event(key, kind, seconds, session="s"):
    at = T0 + timedelta(seconds=seconds)
    return Event(key, session, kind, at, at)


class ParameterTests(unittest.TestCase):
    def test_all_definitions_have_unique_keys_and_labels(self):
        self.assertEqual(len(DEFINITIONS), len({d.key for d in DEFINITIONS}))
        self.assertTrue(all(d.label and d.unit for d in DEFINITIONS))

    def test_required_values_have_defaults_and_saved_values_take_precedence(self):
        defaults = Parameters({})
        self.assertTrue(all(d.optional or d.key in defaults.values for d in DEFINITIONS))
        self.assertEqual(defaults.values["operation_brightness_percent"], 40)
        self.assertEqual(defaults.values["after_run_brightness_percent"], 15)
        self.assertEqual(defaults.values["cooling_brightness_percent"], 5)
        self.assertEqual(defaults.values["sensor_timeout_seconds"], 180)
        self.assertEqual(Parameters({"sensor_timeout_seconds": 7}).values["sensor_timeout_seconds"], 7)

    def test_oven_cooling_defaults_and_legacy_base_compatibility(self):
        defaults = Parameters({})
        self.assertEqual(defaults.values["after_run_minutes"], 5)
        self.assertEqual(defaults.values["oven_cooling_max_minutes"], 15)
        self.assertEqual(defaults.values["oven_cooling_half_life_minutes"], 15)
        self.assertEqual(defaults.values["oven_cooling_heat_idle_ratio"], 2)

        # Older saved options contain only the former fixed cooling duration.
        # Retain a value above the new default cap by deriving that cap once.
        legacy = Parameters({"after_run_minutes": 30})
        self.assertEqual(legacy.values["after_run_minutes"], 30)
        self.assertEqual(legacy.values["oven_cooling_max_minutes"], 30)
        self.assertEqual(legacy.minimum_for("oven_cooling_max_minutes"), 30)

        with self.assertRaises(ParameterError) as raised:
            Parameters({"after_run_minutes": 16, "oven_cooling_max_minutes": 15})
        self.assertEqual(raised.exception.key, "oven_cooling_max_minutes")

    def test_unknown_parameter_fails(self):
        with self.assertRaises(ParameterError):
            Parameters({**parameters().as_dict(), "unknown": 3})

    def test_invalid_numbers_fail_with_field_context(self):
        for value in (True, "2", None, float("nan"), float("inf"), -1, 10**400):
            with self.subTest(value=type(value)), self.assertRaises(ParameterError) as raised:
                Parameters({**parameters().as_dict(), "session_gap_minutes": value})
            self.assertEqual(raised.exception.key, "session_gap_minutes")

    def test_zero_policy_is_explicit(self):
        for d in DEFINITIONS:
            data = {**parameters().as_dict(), d.key: 0}
            with self.subTest(key=d.key):
                if d.allow_zero:
                    self.assertEqual(Parameters(data).values[d.key], 0)
                else:
                    with self.assertRaises(ParameterError):
                        Parameters(data)

    def test_seconds_are_derived_from_the_only_value(self):
        self.assertEqual(parameters().seconds("session_gap_minutes"), 150)
        with self.assertRaises(ParameterError):
            parameters().seconds("readiness_hysteresis_c")

    def test_caller_cannot_mutate_parameters(self):
        original = parameters().as_dict()
        result = Parameters(original)
        original["session_gap_minutes"] = 999
        self.assertEqual(result.values["session_gap_minutes"], 2.5)
        with self.assertRaises(TypeError):
            result.values["session_gap_minutes"] = 3

    def test_time_conversion_must_stay_finite(self):
        with self.assertRaises(ParameterError):
            Parameters({**parameters().as_dict(), "session_gap_minutes": 1e308})


class BindingTests(unittest.TestCase):
    def test_roles_have_no_installation_specific_names(self):
        self.assertEqual(bindings().values["upper_temperature"], "sensor.test_upper_temperature")
        self.assertFalse(any("kanal" in role.key or "shelly" in role.key for role in ROLES))

    def test_required_roles_are_required(self):
        with self.assertRaises(BindingError):
            Bindings({})

    def test_domains_and_ids_are_checked(self):
        for value in ("light.wrong", "switch.bad/name", "on", "", None):
            with self.subTest(value=value), self.assertRaises(BindingError):
                Bindings({**bindings().as_dict(), "heater": value})

    def test_unknown_binding_rejected(self):
        with self.assertRaises(BindingError):
            Bindings({**bindings().as_dict(), "missing_role": "sensor.any"})

    def test_duplicate_positions_rejected(self):
        for metric in ("temperature", "humidity"):
            data = bindings().as_dict()
            data[f"lower_{metric}"] = data[f"upper_{metric}"]
            with self.subTest(metric=metric), self.assertRaises(BindingError):
                Bindings(data)

    def test_optional_statuses_do_not_become_required(self):
        self.assertNotIn("upper_status", bindings().values)
        data = {**bindings().as_dict(), "upper_status": "sensor.test_status"}
        self.assertEqual(Bindings(data).values["upper_status"], "sensor.test_status")

    def test_metadata_passes(self):
        validate_metadata(bindings(), metadata(bindings()))

    def test_missing_metadata_fails(self):
        with self.assertRaises(BindingError):
            validate_metadata(bindings(), {})

    def test_wrong_unit_and_device_class_fail(self):
        b = bindings()
        for key, wrong in (("unit_of_measurement", "°F"), ("device_class", "power")):
            attrs = metadata(b)
            attrs[b.values["upper_temperature"]][key] = wrong
            with self.subTest(key=key), self.assertRaises(BindingError):
                validate_metadata(b, attrs)

    def test_on_off_light_is_not_dimmable(self):
        b = bindings()
        for modes in (["onoff"], [], "brightness"):
            attrs = metadata(b)
            attrs[b.values["light"]]["supported_color_modes"] = modes
            with self.subTest(modes=modes), self.assertRaises(BindingError):
                validate_metadata(b, attrs)

    def test_unavailable_measurement_does_not_erase_binding(self):
        b = bindings()
        attrs = metadata(b)
        attrs[b.values["upper_temperature"]]["state"] = "unavailable"
        validate_metadata(b, attrs)
        self.assertEqual(b.values["upper_temperature"], "sensor.test_upper_temperature")

    def test_bindings_are_immutable(self):
        with self.assertRaises(TypeError):
            bindings().values["heater"] = "switch.other"


class ModelTests(unittest.TestCase):
    def test_session_owns_fresh_children(self):
        a, b = Session.create("a", T0), Session.create("b", T0)
        self.assertIsNot(a.timeline, b.timeline)
        self.assertIsNot(a.heating, b.heating)
        self.assertEqual(a.deadlines, ())
        self.assertEqual(a.session_id, a.timeline.session_id)
        self.assertEqual(a.started_at, a.timeline.session_started_at)

    def test_wrong_session_deadline_rejected(self):
        with self.assertRaises(ValueError):
            replace(Session.create("s", T0), deadlines=(Deadline("other", "test", "token", T0),))

    def test_duplicate_deadline_purpose_rejected(self):
        with self.assertRaises(ValueError):
            replace(Session.create("s", T0), deadlines=(
                Deadline("s", "test", "1", T0), Deadline("s", "test", "2", T0),
            ))

    def test_invalid_time_or_id_rejected(self):
        with self.assertRaises(ValueError):
            Deadline("s", "test", "token", datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            Deadline("s", "test", "", T0)

    def test_original_measurement_and_missing_source_time_preserved(self):
        value = Measurement(Position.UPPER, Quantity.TEMPERATURE, 30.125, "30.125", "source", T0)
        self.assertEqual(value.raw_value, "30.125")
        self.assertIsNone(value.measured_at)
        missing = replace(value, value=None, raw_value="unavailable")
        self.assertIsNone(missing.value)

    def test_invalid_measurement_rejected(self):
        for number in (float("nan"), True, float("inf")):
            with self.subTest(number=number), self.assertRaises(ValueError):
                Measurement(Position.UPPER, Quantity.TEMPERATURE, number, "invalid", "source", T0)

    def test_invalid_heating_time_rejected(self):
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HeatingTime(value)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(parameters())
        self.controller.begin_session("s", T0)

    def test_session_cannot_be_replaced_accidentally(self):
        with self.assertRaises(ValueError):
            self.controller.begin_session("other", T0)

    def test_existing_gang_core_is_used(self):
        self.controller.process(event("close", Kind.DOOR_CLOSE, 10))
        result = self.controller.process(event("person", Kind.PERSON_STRONG, 20))
        self.assertEqual(result.session.timeline.active.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(result.session.timeline.active.started_at, T0 + timedelta(seconds=10))
        confirmed = self.controller.process(event("water", Kind.INFUSION, 30))
        self.assertEqual(confirmed.session.timeline.active.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(result.session.heating.elapsed_seconds, 0)

    def test_duplicate_input_does_not_change_state(self):
        e = event("close", Kind.DOOR_CLOSE, 10)
        self.controller.process(e)
        before = self.controller.session
        result = self.controller.process(e)
        self.assertFalse(result.changed)
        self.assertIs(before, self.controller.session)

    def test_wrong_session_event_does_not_mutate_state(self):
        before = self.controller.session
        with self.assertRaises(ValueError):
            self.controller.process(event("wrong", Kind.DOOR_CLOSE, 10, "other"))
        self.assertIs(before, self.controller.session)

    def test_event_requires_session(self):
        with self.assertRaises(ValueError):
            Controller(parameters()).process(event("close", Kind.DOOR_CLOSE, 10))

    def test_registered_deadline_fires_once(self):
        deadline = Deadline("s", "test", "one", T0 + timedelta(seconds=1))
        self.controller.register_deadline(deadline)
        self.assertTrue(self.controller.consume_deadline(deadline, T0 + timedelta(seconds=1)))
        self.assertFalse(self.controller.consume_deadline(deadline, T0 + timedelta(seconds=2)))

    def test_old_deadline_cannot_affect_new_session(self):
        self.assertFalse(self.controller.consume_deadline(Deadline("old", "test", "one", T0), T0))

    def test_replaced_deadline_cannot_fire(self):
        old = Deadline("s", "test", "one", T0)
        new = Deadline("s", "test", "two", T0 + timedelta(seconds=10))
        self.controller.register_deadline(old)
        self.controller.register_deadline(new)
        self.assertFalse(self.controller.consume_deadline(old, T0))
        self.assertEqual(self.controller.session.deadlines, (new,))

    def test_changed_deadline_requires_new_token(self):
        old = Deadline("s", "test", "one", T0)
        self.controller.register_deadline(old)
        with self.assertRaises(ValueError):
            self.controller.register_deadline(replace(old, due_at=T0 + timedelta(seconds=10)))

    def test_early_deadline_rejected_without_mutation(self):
        deadline = Deadline("s", "test", "one", T0 + timedelta(seconds=10))
        self.controller.register_deadline(deadline)
        before = self.controller.session
        with self.assertRaises(ValueError):
            self.controller.consume_deadline(deadline, T0)
        self.assertIs(before, self.controller.session)

    def test_configuration_survives_session_creation(self):
        source = parameters()
        controller = Controller(source)
        controller.begin_session("other", T0)
        self.assertIs(controller.parameters, source)
        self.assertEqual(controller.parameters.seconds("session_gap_minutes"), 150)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = T0
        self.runtime = SaunaRuntime(Configuration(bindings(), parameters()), clock=lambda: self.now)

    async def test_configuration_roundtrip_has_single_storage(self):
        options = self.runtime.configuration.as_options()
        rebuilt = Configuration.from_options(options)
        self.assertEqual(rebuilt, self.runtime.configuration)
        options["parameters"]["session_gap_minutes"] = 999
        self.assertEqual(rebuilt.parameters.values["session_gap_minutes"], 2.5)

    async def test_configuration_cannot_silently_fill_missing_values(self):
        with self.assertRaises(ValueError):
            Configuration.from_options({})

    async def test_injected_clock_and_duplicate_serialization(self):
        session = await self.runtime.begin_session("s")
        self.assertEqual(session.started_at, T0)
        self.now = T0 + timedelta(seconds=10)
        e = event("close", Kind.DOOR_CLOSE, 10)
        results = await asyncio.gather(self.runtime.receive(e), self.runtime.receive(e))
        self.assertEqual([r.changed for r in results], [True, False])

    async def test_future_input_rejected(self):
        await self.runtime.begin_session("s")
        with self.assertRaises(ValueError):
            await self.runtime.receive(event("close", Kind.DOOR_CLOSE, 10))

    async def test_unload_unregisters_callbacks_once(self):
        called = []
        self.runtime.on_close(lambda: called.append("removed"))
        await self.runtime.close()
        await self.runtime.close()
        self.assertEqual(called, ["removed"])
        with self.assertRaises(RuntimeError):
            await self.runtime.begin_session("s")
        with self.assertRaises(RuntimeError):
            self.runtime.on_close(lambda: None)

    async def test_all_callbacks_attempted_on_cleanup_failure(self):
        called = []
        self.runtime.on_close(lambda: called.append("still_removed"))
        def fails():
            raise RuntimeError("synthetic")
        self.runtime.on_close(fails)
        with self.assertRaises(ExceptionGroup):
            await self.runtime.close()
        self.assertEqual(called, ["still_removed"])
        self.assertTrue(self.runtime.closed)

    async def test_deadline_uses_injected_clock(self):
        await self.runtime.begin_session("s")
        d = Deadline("s", "test", "one", T0 + timedelta(seconds=5))
        self.runtime.controller.register_deadline(d)
        self.now += timedelta(seconds=5)
        self.assertTrue(await self.runtime.deadline_due(d))


class PackagingTests(unittest.TestCase):
    def test_manifest_and_translation_fields(self):
        folder = ROOT / "custom_components/ha_sauna"
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "ha_sauna")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["requirements"], [])
        strings = json.loads((folder / "strings.json").read_text())
        translated = json.loads((folder / "translations/de.json").read_text())
        self.assertEqual(strings, translated)
        self.assertEqual(set(strings["config"]["step"]["parameters"]["data"]),
                         {d.key for d in DEFINITIONS} | {"program_mode", "button_program", "button_temperature_c"})
        self.assertEqual(set(strings["options"]["step"]["bindings"]["data"]), {r.key for r in ROLES} | {"control_input_mode", "button_event_type", "presence_source"})

    def test_ha_transport_is_confined_to_device_adapter(self):
        folder = ROOT / "custom_components/ha_sauna"
        for file in folder.rglob("*.py"):
            text = file.read_text()
            ast.parse(text)
            with self.subTest(file=file.name):
                if file.name != "device.py":
                    self.assertNotIn("hass.services", text)
                if file.name != "device.py":
                    self.assertNotIn("async_call(", text)
                self.assertNotIn("SUPERVISOR_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
