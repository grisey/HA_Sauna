"""Absolute Feuchte als reine Anzeigeprojektion unveränderter Archivmessungen."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from operator import itemgetter

from .core.models import Measurement, Position, Quantity
from .core.moisture import absolute_humidity
from .core.timeline import utc


MEASUREMENT_KINDS = {"measurement", "source_snapshot"}
# Role names in Quantity order, built once instead of for every original.
_ROLES = {
    position.value: tuple(f"{position.value}_{quantity.value}" for quantity in Quantity)
    for position in Position
}
_ROLE = {
    (position.value, quantity.value): f"{position.value}_{quantity.value}"
    for position in Position
    for quantity in Quantity
}
_SAMPLE_KEYS = frozenset({"sensor_id", "boot_id", "sample_id", "monotonic_ns"})
_ORDER = itemgetter(0, 1)
# A read original: (received_at, role, position, value, sample, freshness_at).
_RECEIVED, _ROLE_NAME, _POSITION, _VALUE, _SAMPLE, _FRESHNESS = range(6)


def _received_at(payload):
    try:
        return utc(datetime.fromisoformat(payload["received_at"]))
    except (KeyError, TypeError, ValueError):
        return None


def _measurement(record, bindings):
    payload = record.get("payload")
    if record.get("kind") not in MEASUREMENT_KINDS or not isinstance(payload, Mapping):
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
    try:
        return Measurement.from_payload({
            **payload, "value": value,
            "raw_value": raw_value if isinstance(raw_value, str) else "",
        })
    except (KeyError, TypeError, ValueError):
        return None


def _from_model(record, bindings):
    """Read an original through the full archive contract (Measurement.from_payload)."""
    measurement = _measurement(record, bindings)
    if measurement is None:
        return None
    sample = measurement.sample
    return (
        measurement.received_at,
        _ROLE[measurement.position.value, measurement.quantity.value],
        measurement.position.value,
        measurement.value,
        None if sample is None else (
            sample.sensor_id, sample.boot_id, sample.sample_id, sample.monotonic_ns
        ),
        measurement.freshness_at,
    )


def _aware(value):
    """A UTC time from an ISO text with zone, else ``None`` (then the full contract)."""
    if type(value) is not str:
        return None
    try:
        value = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value.astimezone(UTC) if value.utcoffset() is not None else None


def _original(record, bindings):
    """Read only the fields the display uses: time, role, value, sample, freshness.

    Regular archive originals are read directly. Every other form goes through
    ``_measurement`` (Measurement.from_payload), so validity and values are the
    same as with the full model: a read here implies that the model accepts the
    original with exactly these fields.
    """
    if record.get("kind") not in MEASUREMENT_KINDS:
        return None
    payload = record.get("payload")
    if type(payload) is not dict:
        return _from_model(record, bindings)
    try:
        role = _ROLE.get((payload.get("position"), payload.get("quantity")))
    except TypeError:
        role = None
    source = payload.get("source")
    if role is None or type(source) is not str:
        return _from_model(record, bindings)
    if not source or bindings.get(role) != source:
        return None
    received = _aware(payload.get("received_at"))
    value = payload.get("value")
    measured = payload.get("measured_at")
    if measured:
        measured = _aware(measured)
        if measured is None:
            return _from_model(record, bindings)
    else:
        measured = None
    sample = payload.get("sample")
    if sample is not None:
        if type(sample) is not dict or sample.keys() != _SAMPLE_KEYS:
            return _from_model(record, bindings)
        sample = (
            sample["sensor_id"], sample["boot_id"],
            sample["sample_id"], sample["monotonic_ns"],
        )
        if not (
            type(sample[0]) is str and sample[0]
            and type(sample[1]) is str and sample[1]
            and type(sample[2]) is int and sample[2] >= 1
            and type(sample[3]) is int and sample[3] >= 0
            and (value is None or measured is not None)
        ):
            return _from_model(record, bindings)
    original = payload.get("original_id")
    if (
        received is None
        or (value is not None and not (
            (type(value) is float or type(value) is int) and isfinite(value)
        ))
        or (original and type(original) is not str)
    ):
        return _from_model(record, bindings)
    return (
        received, role, payload["position"], value, sample,
        min(received, measured) if sample is not None and measured is not None
        else received,
    )


def _valid_timeout(value):
    """The freshness limit as current_absolute_humidity accepts it, else ``None``."""
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        return None
    value = float(value)
    return value if value >= 0 else None


def _pair_value(temperature, humidity, now, timeout):
    """Absolute humidity of one complete, fresh source pair.

    Same rule as ``current_absolute_humidity`` for originals already read for
    the matching role: both values present, ages between 0 and the limit, the
    same physical sample (or none), then the shared psychrometric formula.
    """
    if temperature is None or humidity is None:
        return None
    for original in (temperature, humidity):
        if original[_VALUE] is None:
            return None
        age = (now - original[_FRESHNESS]).total_seconds()
        if not 0 <= age <= timeout:
            return None
    if (
        (temperature[_SAMPLE] is not None or humidity[_SAMPLE] is not None)
        and temperature[_SAMPLE] != humidity[_SAMPLE]
    ):
        return None
    try:
        return absolute_humidity(temperature[_VALUE], humidity[_VALUE])
    except ValueError:
        return None


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

    def add(self, identity, original):
        observation = (original[_RECEIVED], identity, original)
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


def read_originals(records, bindings):
    """The read original of each page record (``None`` without a usable one).

    The archive uses these reads for the freshness windows of its context
    query and passes them back to ``project_absolute_humidity``, so every page
    original is read once.
    """
    return [_original(record, bindings) for record in records]


def project_absolute_humidity(
    records: Iterable[Mapping],
    *,
    context: Iterable[Mapping] = (),
    bindings: Mapping,
    timeout_seconds: float | None,
    reads: list | None = None,
    attach: bool = True,
) -> list:
    """Add display values without changing originals or returning page context.

    Context uses the same record shape as the page and must contain its needed
    earlier source observations. Pairing uses original payload reception times,
    saved source bindings and only IDs at or before the displayed original.
    Null observations replace earlier values. Source snapshots never refresh
    their copied input's reception time. The caller bounds context in SQLite;
    this projection sorts and indexes it once rather than replaying a session
    separately for every measurement.

    ``reads`` are the page reads from ``read_originals``. Without ``attach``
    the result is the display value of each record (``None`` for other kinds)
    instead of projected copies, so a caller can place them without copying.
    """
    records = list(records)
    page = read_originals(records, bindings) if reads is None else reads
    identities = [_record_id(record) for record in records]
    # Each original is read once: a page record is also the input with its ID,
    # and the last record with an ID is its input, as in archive order.
    inputs = {}
    for record in context:
        identity = _record_id(record)
        if identity is not None:
            inputs[identity] = record
    # A page record replaces a context record with its ID.
    on_page = set(identities)
    read = {
        identity: _original(record, bindings)
        for identity, record in inputs.items()
        if identity not in on_page
    }
    last = {}
    for index, identity in enumerate(identities):
        if identity is not None:
            read[identity] = page[index]
            last[identity] = index
    targets = {}
    display = []
    linear = True
    for index, record in enumerate(records):
        if record.get("kind") not in MEASUREMENT_KINDS:
            # Only measurement kinds receive a display value below.
            display.append(None)
            continue
        original = page[index]
        if original is not None:
            # The same original reception time, already read for the input.
            received_at = original[_RECEIVED]
        else:
            payload = record.get("payload")
            received_at = _received_at(payload) if isinstance(payload, Mapping) else None
        display.append(
            {
                "value": None,
                "received_at": received_at.isoformat()
                if received_at is not None
                else None,
                "valid_until": None,
            }
        )
        identity = identities[index]
        if original is not None and identity is not None:
            targets[index] = (identity, original)
            # A second original with this ID is paired by the general search.
            linear = linear and last[identity] == index
    timeout = _valid_timeout(timeout_seconds)
    if timeout is None or not targets:
        values = {}
    else:
        values = _pairs(read, targets, timeout, linear)
    if values:
        try:
            ttl = timedelta(seconds=timeout_seconds)
        except OverflowError:
            ttl = None
        for index, (value, temperature, humidity) in values.items():
            if ttl is None:
                continue
            try:
                valid_until = min(temperature[_FRESHNESS], humidity[_FRESHNESS]) + ttl
            except OverflowError:
                continue
            display[index].update(value=value, valid_until=valid_until.isoformat())
    if not attach:
        return display
    return [
        {**record, "display": attach_display(record, display[index])}
        if display[index] is not None else dict(record)
        for index, record in enumerate(records)
    ]


def attach_display(record, value):
    """The record's display with the absolute humidity, keeping other display values."""
    previous = record.get("display")
    return {
        **(previous if isinstance(previous, Mapping) else {}),
        "absolute_humidity": value,
    }


def _pairs(read, targets, timeout, linear):
    """Value and source pair for each target index with a complete fresh pair.

    For every target, each role uses the original with the latest (reception
    time, ID) among those received no later and archived no later than the
    target. When reception times never decrease along the archive IDs, that is
    simply the last original of the role in ID order, found in one pass;
    otherwise the prefix search over reception order applies.
    """
    order = sorted(identity for identity, original in read.items() if original is not None)
    if linear:
        previous = None
        for identity in order:
            received = read[identity][_RECEIVED]
            if previous is not None and received < previous:
                linear = False
                break
            previous = received
    values = {}
    if linear:
        at = {identity: index for index, (identity, _) in targets.items()}
        latest = {}
        for identity in order:
            original = read[identity]
            latest[original[_ROLE_NAME]] = original
            index = at.get(identity)
            if index is None:
                continue
            temperature_role, humidity_role = _ROLES[original[_POSITION]]
            temperature, humidity = latest.get(temperature_role), latest.get(humidity_role)
            value = _pair_value(temperature, humidity, original[_RECEIVED], timeout)
            if value is not None:
                values[index] = (value, temperature, humidity)
        return values
    observations = sorted(
        ((read[identity][_RECEIVED], identity, read[identity]) for identity in order),
        key=_ORDER,
    )
    source_ids = {}
    for identity in order:
        source_ids.setdefault(read[identity][_ROLE_NAME], []).append(identity)
    sources = {role: _SourceIndex(ids) for role, ids in source_ids.items()}
    cursor = 0
    for received_at, identity, index, original in sorted(
        (original[_RECEIVED], identity, index, original)
        for index, (identity, original) in targets.items()
    ):
        while cursor < len(observations) and observations[cursor][0] <= received_at:
            _, source_id, observed = observations[cursor]
            sources[observed[_ROLE_NAME]].add(source_id, observed)
            cursor += 1
        found = {}
        for role in _ROLES[original[_POSITION]]:
            source = sources.get(role)
            if source is not None:
                found[role] = source.latest(identity)
        temperature_role, humidity_role = _ROLES[original[_POSITION]]
        temperature, humidity = found.get(temperature_role), found.get(humidity_role)
        value = _pair_value(temperature, humidity, received_at, timeout)
        if value is not None:
            values[index] = (value, temperature, humidity)
    return values
