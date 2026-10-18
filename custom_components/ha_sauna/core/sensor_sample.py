"""SHT 1.2 packet contract; no time window, curve gate or HA dependency."""

from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite

from .models import Measurement, Position, Quantity, SampleIdentity
from .moisture import KELVIN_OFFSET_C
from .timeline import utc


@dataclass(frozen=True)
class ClimatePacket:
    measurements: tuple[Measurement, ...]

    def __post_init__(self):
        if len(self.measurements) != 2:
            raise ValueError("A physical climate packet contains temperature and humidity")
        first, second = self.measurements
        if (first.quantity != Quantity.TEMPERATURE or second.quantity != Quantity.HUMIDITY
                or first.sample is None or first.sample != second.sample
                or first.position != second.position or first.measured_at != second.measured_at
                or first.received_at != second.received_at):
            raise ValueError("Climate quantities must belong to the same physical sample")

    @property
    def received_at(self):
        return self.measurements[0].received_at


def decode_sample(topic, payload, position, sources, received_at):
    """Validate the complete packet before handing either quantity to consumers."""
    data = json.loads(payload)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported physical sample schema")
    stamp = SampleIdentity(*(data[key] for key in ("sensor_id", "boot_id", "sample_id", "monotonic_ns")))
    if topic != stamp.sensor_id + "/sample":
        raise ValueError("Physical sensor does not match its bound MQTT topic")
    received_at = utc(received_at)
    status = data["status"]
    if status == "ok":
        measured_at = utc(datetime.fromisoformat(data["measured_at"]))
        values = tuple(data[q.value] for q in Quantity)
        if (any(type(value) not in (int, float) or not isfinite(value) for value in values)
                or values[0] <= -KELVIN_OFFSET_C or not 0 <= values[1] <= 100):
            raise ValueError("Invalid physical temperature or humidity")
    elif status == "read_error":
        if any(data[key] is not None for key in ("temperature", "humidity", "measured_at")):
            raise ValueError("A failed read cannot contain measured values or a measurement time")
        measured_at, values = None, (None, None)
    else:
        raise ValueError("Unknown physical sample status")
    return ClimatePacket(tuple(
        Measurement(Position(position), quantity, value,
                    str(value) if value is not None else status,
                    sources[quantity.value], received_at, measured_at, stamp)
        for quantity, value in zip(Quantity, values, strict=True)
    ))


class SampleStream:
    """Immutable publisher packets: one cursor per sensor, no receipt-age limit.

    MQTT 1.2 uses one ordered publisher connection. Lower IDs from a completed
    boot are re-deliveries, never a new measurement or a renewed freshness lease.
    Numeric gaps are normal (the other sensor and failed reads also use IDs).
    """

    def __init__(self):
        self.last = {}
        self.retired = set()

    def accept(self, packet):
        stamp = packet.measurements[0].sample
        old = self.last.get(stamp.sensor_id)
        if (stamp.sensor_id, stamp.boot_id) in self.retired:
            return False
        if old is not None:
            previous = old.measurements[0].sample
            if stamp.boot_id == previous.boot_id:
                if stamp.sample_id <= previous.sample_id:
                    if stamp.sample_id == previous.sample_id and any(
                        (a.sample, a.measured_at, a.value) != (b.sample, b.measured_at, b.value)
                        for a, b in zip(packet.measurements, old.measurements, strict=True)
                    ):
                        raise ValueError("Conflicting immutable physical sample identity")
                    return False
                if stamp.monotonic_ns <= previous.monotonic_ns:
                    raise ValueError("Physical sample clock must advance within one boot")
            else:
                self.retired.add((previous.sensor_id, previous.boot_id))
        self.last[stamp.sensor_id] = packet
        return True


def archive_measurements(records, *, archive_id):
    """Yield originals in archive order, preserving both old and new formats.

    Old rows remain individual observations, identified by archive/record ID.
    A source snapshot is a view of an existing original, never another sample.
    New pairs are kept together; a truncated physical pair is reported instead
    of silently manufacturing its missing quantity.
    """
    pending = []
    for record in records:
        if record["kind"] != "measurement":
            continue
        measurement = Measurement.from_payload(
            record["payload"], original_id=f"{archive_id}:{record['id']}",
        )
        if measurement.sample is None:
            if pending:
                raise ValueError("Incomplete physical sample in archive")
            yield (measurement,)
            continue
        pending.append(measurement)
        if len(pending) == 2:
            packet = ClimatePacket(tuple(pending))
            yield packet.measurements
            pending = []
    if pending:
        raise ValueError("Incomplete physical sample in archive")
