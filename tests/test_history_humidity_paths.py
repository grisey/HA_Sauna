"""LZ3: Die schnelle Feuchteprojektion gleicht dem vollen Messmodell auch in Sonderfällen.

Der Vergleich rechnet jeden Anzeigewert unabhängig mit Measurement.from_payload und
current_absolute_humidity: je Rolle die Messung mit der spätesten (Empfangszeit, ID)
unter denen, die nicht später empfangen und nicht später archiviert wurden.
"""

import json
import random
import unittest
from datetime import UTC, timedelta, timezone

from test_foundation import T0

from custom_components.ha_sauna import history_humidity as projection
from custom_components.ha_sauna.core.moisture import current_absolute_humidity

BINDINGS = {
    "upper_temperature": "sensor.ot", "upper_humidity": "sensor.oh",
    "lower_temperature": "sensor.ut", "lower_humidity": "sensor.uh",
}
ROLES = {"upper": ("upper_temperature", "upper_humidity"),
         "lower": ("lower_temperature", "lower_humidity")}


def expected(records, context, bindings, timeout):
    inputs = {}
    for record in [*context, *records]:
        identity = projection._record_id(record)
        if identity is not None:
            inputs[identity] = record
    models = {identity: projection._measurement(record, bindings)
              for identity, record in inputs.items()}
    result = []
    for record in records:
        if record.get("kind") not in projection.MEASUREMENT_KINDS:
            result.append(dict(record))
            continue
        model = projection._measurement(record, bindings)
        identity = projection._record_id(record)
        payload = record.get("payload")
        received = model.received_at if model is not None else (
            projection._received_at(payload) if isinstance(payload, dict) else None)
        shown = {"value": None, "received_at": received.isoformat() if received else None,
                 "valid_until": None}
        if model is not None and identity is not None:
            found = {}
            for role in ROLES[model.position.value]:
                candidates = [
                    (other.received_at, other_id, other)
                    for other_id, other in models.items()
                    if other is not None
                    and f"{other.position.value}_{other.quantity.value}" == role
                    and other.received_at <= model.received_at and other_id <= identity
                ]
                if candidates:
                    found[role] = max(candidates, key=lambda item: item[:2])[2]
            value = current_absolute_humidity(found, model.position, model.received_at, timeout)
            if value is not None:
                try:
                    until = min(m.freshness_at for m in found.values()) + timedelta(seconds=timeout)
                except OverflowError:
                    until = None
                if until is not None:
                    shown.update(value=value, valid_until=until.isoformat())
        previous = record.get("display")
        result.append({**record, "display": {
            **(previous if isinstance(previous, dict) else {}), "absolute_humidity": shown}})
    return result


def random_records(rng, count, *, first_id=1, monotone=True, start=0.0):
    records, second, position = [], start, "upper"
    for offset in range(count):
        # Meist Paare aus Temperatur und Feuchte derselben Probe, wie vom Sensor.
        second += rng.choice((0.0, 0.4, 1.0, 3.0, 70.0)) if offset % 2 == 0 else 0.0
        at = T0 + timedelta(seconds=second if monotone else rng.uniform(0, count * 2))
        if offset % 2 == 0:
            position = rng.choice(("upper", "lower"))
        quantity = ("temperature", "humidity")[offset % 2]
        if rng.random() < 0.1:
            quantity = rng.choice(("temperature", "humidity"))
        role = f"{position}_{quantity}"
        number = offset // 2 + 1
        payload = {
            "position": position, "quantity": quantity,
            "value": rng.choice((60.5, 21.25, 80, 15.0, 30, None)),
            "raw_value": "x", "source": BINDINGS[role],
            "received_at": at.isoformat(),
            "measured_at": (at - timedelta(seconds=rng.choice((0, 0.5, 2)))).isoformat(),
            "sample": {"sensor_id": f"s-{position}", "boot_id": "b",
                       "sample_id": number + (rng.random() < 0.1), "monotonic_ns": number},
            "original_id": None,
        }
        variant = rng.random()
        if variant < 0.04:
            payload["value"] = rng.choice((True, "73", float("nan"), 101.0, -5.0))
        elif variant < 0.07:
            payload["sample"] = rng.choice((None, {"sensor_id": "s"}, {
                "sensor_id": "", "boot_id": "b", "sample_id": 1, "monotonic_ns": 0}))
        elif variant < 0.10:
            payload["measured_at"] = rng.choice((None, "", "2026-01-01T00:00:00", "kaputt",
                                                 (at + timedelta(seconds=5)).isoformat()))
        elif variant < 0.12:
            payload["original_id"] = rng.choice(("", "o-1", 7))
        elif variant < 0.15:
            payload["received_at"] = rng.choice((
                "2026-01-01T00:00:01", None, at.astimezone(timezone(timedelta(hours=1))).isoformat()))
        elif variant < 0.17:
            payload["source"] = rng.choice(("sensor.fremd", "", None, 5))
        elif variant < 0.19:
            payload["position"] = rng.choice((["upper"], "mitte", None))
        elif variant < 0.20:
            payload = rng.choice((None, "kein Objekt"))
        kind = rng.choices(("measurement", "source_snapshot", "phase"), (88, 8, 4))[0]
        record = {"id": first_id + offset, "kind": kind, "received_at": at.isoformat(),
                  "payload": payload}
        if rng.random() < 0.02:
            record["display"] = {"weiteres": 1}
        records.append(record)
    return records


class FastHumidityPathTests(unittest.TestCase):
    def check(self, records, context, timeout, bindings=BINDINGS):
        shown = projection.project_absolute_humidity(
            records, context=context, bindings=bindings, timeout_seconds=timeout)
        reference = expected(records, context, bindings, timeout)
        self.assertEqual(json.dumps(shown), json.dumps(reference))
        self.assertEqual(shown, reference)
        return shown

    def test_random_pages_match_the_full_measurement_model(self):
        rng = random.Random(20261111)
        values = 0
        for case in range(160):
            monotone = case % 4 != 3
            context = random_records(rng, rng.randint(0, 12), first_id=1, monotone=monotone)
            # Die Seite folgt meist zeitlich auf den Kontext (lineare Paarung).
            start = rng.choice((0.0, 2000.0, 2000.0))
            records = random_records(rng, rng.randint(1, 40), first_id=100,
                                     monotone=monotone, start=start)
            if case % 9 == 0 and context:
                # Ein Datensatz der Seite steht mit derselben ID auch im Kontext.
                payload = context[-1]["payload"]
                records.insert(0, {**context[-1], "payload": dict(payload)
                                   if isinstance(payload, dict) else payload})
            if case % 11 == 0:
                records.append(dict(records[0]))  # dieselbe ID zweimal auf der Seite
            timeout = rng.choice((60, 60.0, 1.5, 0, 3600))
            shown = self.check(records, context, timeout)
            values += sum(1 for record in shown
                          if (record.get("display") or {}).get("absolute_humidity", {}).get("value"))
        self.assertGreater(values, 200)

    def test_invalid_and_overflowing_limits_give_no_value(self):
        rng = random.Random(7)
        records = random_records(rng, 30)
        for timeout in (None, -1, True, float("inf"), float("nan"), "60", 1e20):
            with self.subTest(timeout=timeout):
                shown = self.check(records, [], timeout)
                self.assertTrue(all(
                    record["display"]["absolute_humidity"]["value"] is None
                    for record in shown if record["kind"] != "phase"))

    def test_snapshot_before_its_id_uses_the_general_pairing(self):
        # Anfangsmessung: frühere Empfangszeit, spätere ID. Die Paarung darf nur
        # Originale bis zur eigenen ID verwenden, auch wenn sie früher empfangen wurden.
        at = T0.astimezone(UTC)
        def measured(identity, second, quantity, value, kind="measurement", sample=1):
            when = (at + timedelta(seconds=second)).isoformat()
            return {"id": identity, "kind": kind, "received_at": when, "payload": {
                "position": "upper", "quantity": quantity, "value": value, "raw_value": "x",
                "source": BINDINGS[f"upper_{quantity}"], "received_at": when, "measured_at": when,
                "sample": {"sensor_id": "s", "boot_id": "b", "sample_id": sample,
                           "monotonic_ns": sample}}}
        records = [
            measured(10, 5, "temperature", 70.0, sample=2),
            measured(11, 5, "humidity", 20.0, sample=2),
            measured(12, 1, "humidity", 25.0, "source_snapshot", sample=1),
            measured(13, 6, "temperature", 71.0, sample=3),
        ]
        shown = self.check(records, [], 60)
        self.assertIsNotNone(shown[1]["display"]["absolute_humidity"]["value"])
        self.assertIsNone(shown[2]["display"]["absolute_humidity"]["value"])


if __name__ == "__main__":
    unittest.main()
