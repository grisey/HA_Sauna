"""Archive JSON keeps original values while already plain data is encoded once."""
from dataclasses import replace
from datetime import timedelta
import json
import math
import unittest
from unittest.mock import Mock, patch

from test_foundation import T0, bindings, event, parameters

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna import runtime as runtime_module
from custom_components.ha_sauna.archive import encoded, encoded_plain, plain
from custom_components.ha_sauna.core.energy import advance
from custom_components.ha_sauna.core.models import EnergyInterval
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


class ArchiveEncodingTests(unittest.TestCase):
    def test_plain_encoder_keeps_bytes_order_precision_and_rejects_nonfinite(self):
        value = {"text": "Kühlung", "time": T0, "value": 78.12345678901234,
                 "nested": [False, None, {"z": 2, "a": 1}]}
        expected = encoded(value)
        converted = plain(value)
        with patch.object(archive_module, "plain", side_effect=AssertionError("converted twice")):
            self.assertEqual(encoded_plain(converted), expected)
            with self.assertRaises(ValueError):
                encoded_plain({"value": math.nan})

    def test_session_energy_keeps_legacy_bytes_without_interval_traversal(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        measured_kw = nominal_kw / 2
        seconds = float(p.values["sensor_timeout_seconds"])
        interval = timedelta(seconds=seconds)
        runtime = SaunaRuntime(Configuration(bindings(), p), lambda: T0)
        session = runtime.controller.begin_session("energy-encoding", T0)
        energy = advance(
            session.energy, T0 + interval * 2,
            power_w=measured_kw * 1000, valid_until=T0 + interval,
            heating=True, nominal_kw=nominal_kw,
        )
        energy = advance(
            energy, T0 + interval * 3,
            power_w=None, valid_until=None, heating=None, nominal_kw=nominal_kw,
        )
        self.assertTrue(energy.intervals)
        expected_session = plain(session)
        # The original six fields are the compatibility oracle, in wire order.
        expected_session["energy"] = {
            # Historical watt arithmetic preserves the serialized float bytes.
            "measured_kwh": (measured_kw * 1000) * seconds / 3600000,
            "estimated_kwh": nominal_kw * seconds / 3600,
            "measured_seconds": seconds,
            "estimated_seconds": seconds,
            "unknown_seconds": seconds,
            "accounted_at": (T0 + interval * 3).isoformat(),
        }
        expected = json.dumps(
            {"session": expected_session, "text": "Kühlung"},
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        )
        with patch.object(archive_module, "plain", wraps=plain) as convert:
            actual = encoded({
                "session": replace(session, energy=energy), "text": "Kühlung",
            })
        self.assertEqual(actual, expected)
        self.assertFalse(any(
            isinstance(call.args[0], EnergyInterval)
            for call in convert.call_args_list
        ))

    def test_runtime_reuses_unchanged_signature_without_reconverting_plain_value(self):
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
        runtime.archive = Mock()
        runtime.controller.begin_session("signature", T0)
        signature = plain(runtime.session)
        signature["heating"].pop("accounted_at")
        signature["heating"].pop("elapsed_seconds")
        signature.pop("energy")
        expected = json.dumps(signature, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with patch.object(runtime_module, "encoded_plain", wraps=encoded_plain) as encode:
            runtime.persist()
            runtime.persist()
        self.assertEqual(encode.call_count, 2)
        self.assertEqual(runtime._archive_signature, expected)
        runtime.archive.save_session.assert_called_once()

    def test_oven_cooling_counters_do_not_create_session_revisions(self):
        def at(seconds):
            return T0 + timedelta(seconds=seconds)

        values = {**parameters().as_dict(), "target_temperature_c": 80}
        runtime = SaunaRuntime(Configuration(bindings(), Parameters(values)), lambda: T0)
        runtime.archive = Mock()
        c = runtime.controller
        c.set_temperature(70, T0)
        c.set_operation(True, T0, session_id="s")
        c.report_contactor(True, at(0))
        c.report_heating(True, at(0))
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("infusion", Kind.INFUSION, 3))
        runtime.persist()
        saves = runtime.archive.save_session

        def revisions():
            count = saves.call_count
            runtime.persist()
            return saves.call_count - count

        c.advance(at(60))
        self.assertEqual(revisions(), 0)  # Heizzähler wie bisher ohne Stand.
        c.process(event("open", Kind.DOOR_OPEN, 70))
        c.process(event("vent", Kind.VENTILATION, 71))
        c.report_contactor(False, at(71))
        c.report_heating(False, at(71))
        started = c.session.after_run
        self.assertIsNotNone(started)
        self.assertFalse(started.pending_start)
        self.assertEqual(revisions(), 1)  # Beginn der Ofenkühlung.
        for seconds in (72, 73.5, 90):
            c.advance(at(seconds))
            self.assertEqual(revisions(), 0)
        counted = c.session.after_run
        self.assertEqual(counted.phase_id, started.phase_id)
        self.assertGreater(counted.elapsed_seconds, started.elapsed_seconds)
        self.assertLess(counted.remaining_seconds, started.remaining_seconds)
        self.assertNotEqual(counted.accounted_at, started.accounted_at)
        c.report_contactor(None, at(91))  # Andere Änderung: Ablauf ausgesetzt.
        self.assertIsNone(c.session.after_run.ends_at)
        self.assertEqual(revisions(), 1)
        c.report_contactor(False, at(92))
        self.assertIsNotNone(c.session.after_run.ends_at)
        self.assertEqual(revisions(), 1)
        c.advance(at(100))
        self.assertEqual(revisions(), 0)
        c.finish_phase("after_run", started.phase_id, at(110))  # Ende der Ofenkühlung.
        self.assertIsNone(c.session.after_run)
        self.assertEqual(revisions(), 1)
        saved = saves.call_args.args[0]
        self.assertEqual(saved.after_run_history[-1].phase_id, started.phase_id)
        self.assertEqual(saved.after_run_history[-1].ends_at, at(110))
        c.advance(at(120))
        self.assertEqual(revisions(), 0)
