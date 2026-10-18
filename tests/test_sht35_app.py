"""Exercise the actual 1.2 publisher; replace only hardware and transport."""

from contextlib import ExitStack
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

from test_detector import trace
from test_foundation import T0


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "tools" / "sht35_pca_mqtt"


class StopAfterRounds(Exception):
    pass


class Client:
    def __init__(self, *args, **kwargs):
        self.published = []

    def publish(self, topic, payload, **kwargs):
        self.published.append((topic, payload, kwargs))

    def connect(self, *args, **kwargs):
        pass

    def loop_start(self):
        pass

    def will_set(self, *args, **kwargs):
        pass

    def subscribe(self, *args, **kwargs):
        pass


class Bus:
    def __init__(self, *args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def load_app():
    modules = {name: ModuleType(name) for name in (
        "paho", "paho.mqtt", "paho.mqtt.client", "smbus2",
    )}
    modules["paho"].mqtt = modules["paho.mqtt"]
    modules["paho.mqtt"].client = modules["paho.mqtt.client"]
    modules["paho.mqtt.client"].Client = Client
    modules["paho.mqtt.client"].CallbackAPIVersion = SimpleNamespace(VERSION2=2)
    modules["smbus2"].SMBus = Bus
    modules["smbus2"].i2c_msg = object()
    spec = importlib.util.spec_from_file_location("sht35_publisher_under_test", APP / "app.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def run_rounds(readings, *, boot="first-start", publish_nan=True):
    module = load_app()
    client = Client()
    options = {"interval_seconds": 1, "publish_nan_on_error": publish_nan}
    rounds = len(readings) // 2
    sleep_calls = []

    def sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == rounds:
            raise StopAfterRounds

    with ExitStack() as stack:
        stack.enter_context(patch("builtins.open", mock_open(read_data=json.dumps(options))))
        stack.enter_context(patch.object(module, "configure_logging"))
        stack.enter_context(patch.object(module, "logger", Mock()))
        stack.enter_context(patch.object(module, "uuid4", return_value=SimpleNamespace(hex=boot)))
        stack.enter_context(patch.object(module.mqtt, "Client", return_value=client))
        stack.enter_context(patch.object(module, "pca_select_channel"))
        stack.enter_context(patch.object(module, "sht35_read", side_effect=readings))
        stack.enter_context(patch.object(module.time, "sleep", side_effect=sleep))
        with unittest.TestCase().assertRaises(StopAfterRounds):
            module.main()
    packets = [(topic, json.loads(payload), options) for topic, payload, options in client.published
               if topic.endswith("/sample")]
    return client, packets, sleep_calls


class Sht35AppTests(unittest.TestCase):
    def test_actual_loop_publishes_one_shared_identity_for_every_physical_read(self):
        client, packets, sleeps = run_rounds([trace(0), trace(0), trace(1), trace(1)])
        self.assertEqual(sleeps, [1, 1])
        self.assertEqual(len(packets), 4)
        self.assertEqual([p[1]["sample_id"] for p in packets], [1, 2, 3, 4])
        self.assertEqual({p[1]["boot_id"] for p in packets}, {"first-start"})
        for index, (topic, packet, options) in enumerate(packets):
            channel = (3, 6)[index % 2]
            self.assertEqual(topic, f"sensors/sht35_pca/ch{channel}/sample")
            self.assertEqual(packet["sensor_id"], topic.removesuffix("/sample"))
            self.assertEqual(packet["schema_version"], 1)
            self.assertEqual(packet["status"], "ok")
            self.assertEqual(packet["temperature"], trace(0)[0])
            self.assertEqual(packet["humidity"], trace(0)[1])
            self.assertEqual(datetime.fromisoformat(packet["measured_at"]).utcoffset().total_seconds(), 0)
            self.assertGreater(packet["monotonic_ns"], 0)
            self.assertEqual(options, {"retain": False})
        scalar = [(topic, payload) for topic, payload, _ in client.published
                  if topic.endswith(("/temperature", "/humidity"))]
        self.assertEqual(len(scalar), 8)
        self.assertEqual(scalar[:2], [
            ("sensors/sht35_pca/ch3/temperature", "70.00"),
            ("sensors/sht35_pca/ch3/humidity", "40.00"),
        ])

    def test_failed_read_gets_an_identity_but_no_fabricated_measurement(self):
        for publish_nan in (True, False):
            client, packets, _ = run_rounds(
                [trace(0), RuntimeError("fixture read error"), trace(1), trace(1)],
                publish_nan=publish_nan,
            )
            failed = packets[1][1]
            self.assertEqual(failed["sample_id"], 2)
            self.assertEqual(failed["status"], "read_error")
            self.assertIsNone(failed["measured_at"])
            self.assertIsNone(failed["temperature"])
            self.assertIsNone(failed["humidity"])
            self.assertEqual(packets[2][1]["status"], "ok")
            self.assertEqual(packets[2][1]["sample_id"], 3)
            nan = [(topic, payload) for topic, payload, _ in client.published if payload == "nan"]
            self.assertEqual(len(nan), 2 if publish_nan else 0)

    def test_app_restart_changes_identity_namespace_without_changing_scalar_discovery(self):
        first, a, _ = run_rounds([trace(0), trace(0)], boot="first-start")
        second, b, _ = run_rounds([trace(0), trace(0)], boot="second-start")
        self.assertEqual(a[0][1]["sample_id"], b[0][1]["sample_id"])
        self.assertNotEqual((a[0][1]["boot_id"], a[0][1]["sample_id"]),
                            (b[0][1]["boot_id"], b[0][1]["sample_id"]))
        for client in (first, second):
            configs = [json.loads(payload) for topic, payload, _ in client.published
                       if topic.endswith("/config") and "/sensor/" in topic]
            self.assertEqual(len(configs), 6)
            for config in configs:
                self.assertNotIn("json_attributes_topic", config)
                self.assertEqual(config["device"]["sw_version"], "1.2")
        self.assertIn('version: "1.2"', (APP / "config.yaml").read_text())

    def test_packet_encoding_uses_original_sample_metadata_not_transport_time(self):
        module = load_app()
        stamp = T0.isoformat(timespec="microseconds")
        temperature, humidity = trace(151)
        packet = module.sample_payload("existing_source", 3, "boot", 17, stamp,
                                       123456789, temperature, humidity)
        # Re-encoding/retransmitting a stored packet changes neither time nor ID.
        restored = json.loads(json.dumps(packet, allow_nan=False))
        self.assertEqual(restored, packet)
        self.assertEqual(restored["measured_at"], stamp)
        self.assertEqual(restored["monotonic_ns"], 123456789)
        self.assertEqual(restored["sample_id"], 17)
        self.assertEqual(restored["temperature"], float(f"{temperature:.2f}"))
        self.assertEqual(restored["humidity"], float(f"{humidity:.2f}"))
