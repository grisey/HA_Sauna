"""Archive JSON keeps original values while already plain data is encoded once."""
from dataclasses import replace
from datetime import timedelta
import json
import math
import unittest
from unittest.mock import Mock, patch

from test_foundation import T0, bindings, parameters

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna import runtime as runtime_module
from custom_components.ha_sauna.archive import encoded, encoded_plain, plain
from custom_components.ha_sauna.core.energy import advance
from custom_components.ha_sauna.core.models import EnergyInterval
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
