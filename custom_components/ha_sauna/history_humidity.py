"""Absolute Feuchte als reine Anzeigeprojektion unveränderter Archivmessungen."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from itertools import chain
from math import isfinite

from .core.models import Measurement, Position, Quantity
from .core.moisture import current_absolute_humidity
from .core.timeline import utc


MEASUREMENT_KINDS = {"measurement", "source_snapshot"}


def _received_at(payload):
    try:
        return utc(datetime.fromisoformat(payload["received_at"]))
    except (KeyError, TypeError, ValueError):
        return None


def _measurement(record, bindings):
    payload = record.get("payload")
    if (
        record.get("kind") not in MEASUREMENT_KINDS
        or not isinstance(payload, Mapping)
    ):
        return None
    try:
        position = Position(payload["position"])
        quantity = Quantity(payload["quantity"])
    except (KeyError, TypeError, ValueError):
        return None
    source = payload.get("source")
    if not isinstance(source, str) or not source:
        return None
    if bindings.get(f"{position.value}_{quantity.value}") != source:
        return None
    received_at = _received_at(payload)
    if received_at is None:
        return None
    value = payload.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        value = None
    raw_value = payload.get("raw_value")
    return Measurement(
        position, quantity, value,
        raw_value if isinstance(raw_value, str) else "",
        source, received_at,
    )


def _record_id(record):
    identity = record.get("id")
    if isinstance(identity, int) and not isinstance(identity, bool):
        return identity
    return None


class _SourceIndex:
    """Find the latest received source within an already archived ID prefix.

    A Fenwick prefix maximum avoids rescanning earlier inputs for each point,
    including sources whose reception times arrive out of order in the page.
    """

    def __init__(self, identities):
        self.identities = sorted(identities)
        self.tree = [None] * (len(self.identities) + 1)

    def add(self, identity, measurement):
        observation = (measurement.received_at, identity, measurement)
        index = bisect_left(self.identities, identity) + 1
        while index < len(self.tree):
            previous = self.tree[index]
            if previous is None or observation[:2] > previous[:2]:
                self.tree[index] = observation
            index += index & -index

    def latest(self, identity):
        index = bisect_right(self.identities, identity)
        latest = None
        while index:
            observation = self.tree[index]
            if observation is not None and (
                latest is None or observation[:2] > latest[:2]
            ):
                latest = observation
            index -= index & -index
        return latest[2] if latest is not None else None


def project_absolute_humidity(
    records: Iterable[Mapping], *, context: Iterable[Mapping] = (),
    bindings: Mapping, timeout_seconds: float | None,
) -> list[dict]:
    """Add display values without changing originals or returning page context.

    Context uses the same record shape as the page and must contain its needed
    earlier source observations. Pairing uses original payload reception times,
    saved source bindings and only IDs at or before the displayed original.
    Null observations replace earlier values. Source snapshots never refresh
    their copied input's reception time. The caller bounds context in SQLite;
    this projection sorts and indexes it once rather than replaying a session
    separately for every measurement.
    """
    records = list(records)
    inputs = {}
    for record in chain(context, records):
        identity = _record_id(record)
        if identity is not None:
            inputs[identity] = record
    observations = []
    source_ids = {}
    for identity, record in inputs.items():
        measurement = _measurement(record, bindings)
        if measurement is None:
            continue
        role = f"{measurement.position.value}_{measurement.quantity.value}"
        observations.append((measurement.received_at, identity, role, measurement))
        source_ids.setdefault(role, []).append(identity)
    observations.sort(key=lambda item: item[:2])
    sources = {
        role: _SourceIndex(identities) for role, identities in source_ids.items()
    }
    targets = []
    display = []
    for index, record in enumerate(records):
        payload = record.get("payload")
        received_at = (
            _received_at(payload) if isinstance(payload, Mapping) else None
        )
        display.append({
            "value": None,
            "received_at": received_at.isoformat() if received_at is not None else None,
            "valid_until": None,
        })
        measurement = _measurement(record, bindings)
        identity = _record_id(record)
        if measurement is not None and identity is not None:
            targets.append((
                measurement.received_at, identity, index, measurement.position,
            ))
    targets.sort(key=lambda item: item[:2])
    cursor = 0
    for received_at, identity, index, position in targets:
        while cursor < len(observations) and observations[cursor][0] <= received_at:
            _, source_id, role, measurement = observations[cursor]
            sources[role].add(source_id, measurement)
            cursor += 1
        measurements = {}
        for quantity in Quantity:
            role = f"{position.value}_{quantity.value}"
            source = sources.get(role)
            if source is not None:
                measurement = source.latest(identity)
                if measurement is not None:
                    measurements[role] = measurement
        value = current_absolute_humidity(
            measurements, position, received_at, timeout_seconds,
        )
        if value is not None:
            try:
                valid_until = min(
                    measurement.received_at for measurement in measurements.values()
                ) + timedelta(seconds=timeout_seconds)
            except OverflowError:
                continue
            display[index].update(value=value, valid_until=valid_until.isoformat())
    result = []
    for index, record in enumerate(records):
        projected = dict(record)
        if record.get("kind") in MEASUREMENT_KINDS:
            previous = record.get("display")
            projected["display"] = {
                **(previous if isinstance(previous, Mapping) else {}),
                "absolute_humidity": display[index],
            }
        result.append(projected)
    return result
