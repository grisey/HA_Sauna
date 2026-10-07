"""Archive JSON keeps original values while already plain data is encoded once."""
import json
import math
import unittest
from unittest.mock import Mock, patch

from test_foundation import T0, bindings, parameters

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna import runtime as runtime_module
from custom_components.ha_sauna.archive import encoded, encoded_plain, plain
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
