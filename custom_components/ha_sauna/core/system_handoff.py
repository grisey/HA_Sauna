"""Closed values for already obtained environment and archive results.

Factories copy supplied results only. They never read sources, query an archive,
calculate a measurement window, or project a historical session.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields

from .model_handoff import FrozenMap, freeze_data


class WireValue:
    """Serialize an explicit result using the existing archive conventions."""

    def to_wire(self):
        return to_wire(self)

    def _wire_value(self):
        return {
            definition.name: getattr(self, definition.name)
            for definition in fields(self)
        }


def to_wire(value):
    """Thaw result containers and retain existing model serialization.

    ``plain`` already owns datetime/enum and TimedPhase serialization. Its
    dataclass-first dispatch needs an explicit FrozenMap boundary here.
    The lazy import keeps archive infrastructure out of core module imports.
    """
    from ..archive import plain

    def convert(item):
        if isinstance(item, WireValue):
            return convert(item._wire_value())
        if isinstance(item, Mapping):
            return {key: convert(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [convert(child) for child in item]
        return plain(item)

    return convert(value)


def frozen_map(value: Mapping) -> FrozenMap:
    """Copy one intentionally open result map, retaining every supplied key."""
    if isinstance(value, FrozenMap):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("An existing result map is required")
    return freeze_data(value)


@dataclass(frozen=True)
class EnvironmentStation(WireValue):
    name: str
    id: str | None


@dataclass(frozen=True)
class EnvironmentSource(WireValue):
    mode: str | None
    interpolated: bool | None


@dataclass(frozen=True)
class EnvironmentValue(WireValue):
    key: str
    label: str
    state: str | None
    value: float | str | None
    unit: str | None
    available: bool
    last_updated: str | None


@dataclass(frozen=True)
class EnvironmentSnapshot(WireValue):
    """Complete environment_snapshot result; original ISO strings survive."""

    configured: bool
    station: EnvironmentStation | None
    condition: str | None
    values: tuple[EnvironmentValue, ...]
    measurement_time: str | None
    forecast_time: str | None
    source: EnvironmentSource
    updated_at: str | None

    def __post_init__(self):
        object.__setattr__(self, "values", tuple(self.values))

    @classmethod
    def from_result(cls, result: Mapping):
        values = dict(result)
        station = values["station"]
        values["station"] = None if station is None else EnvironmentStation(**station)
        values["source"] = EnvironmentSource(**values["source"])
        values["values"] = tuple(EnvironmentValue(**item) for item in values["values"])
        return cls(**values)


@dataclass(frozen=True)
class MeasurementWindow(WireValue):
    """The existing history result, without repeating its time calculation."""

    started_at: str
    ended_at: str
    complete: bool

    @classmethod
    def from_result(cls, result: Mapping):
        return cls(**result)


@dataclass(frozen=True)
class ArchiveRecord(WireValue):
    """An API record retaining payload and optional header key presence."""

    id: int
    entry_id: str
    session_id: str | None
    kind: str
    received_at: str
    payload: object
    optional_headers: FrozenMap

    def __post_init__(self):
        object.__setattr__(self, "payload", freeze_data(self.payload))
        headers = frozen_map(self.optional_headers)
        if any(key not in ("display", "projection") for key in headers):
            raise TypeError("Unknown archive record header")
        object.__setattr__(self, "optional_headers", headers)

    @classmethod
    def from_result(cls, result: Mapping):
        values = dict(result)
        headers = {
            key: values.pop(key) for key in ("display", "projection") if key in values
        }
        return cls(**values, optional_headers=frozen_map(headers))

    def _wire_value(self):
        values = super()._wire_value()
        values.pop("optional_headers")
        values.update(self.optional_headers)
        return values


@dataclass(frozen=True)
class ArchiveSessionSummary(WireValue):
    session_id: str
    started_at: str
    updated_at: str
    ended_at: str | None

    @classmethod
    def from_result(cls, result: Mapping):
        return cls(**result)


@dataclass(frozen=True)
class ArchiveSessionList(WireValue):
    """A successful list read, including an empty list."""

    sessions: tuple[ArchiveSessionSummary, ...]

    def __post_init__(self):
        object.__setattr__(self, "sessions", tuple(self.sessions))

    @classmethod
    def from_result(cls, result):
        return cls(tuple(ArchiveSessionSummary.from_result(item) for item in result))

    def _wire_value(self):
        return self.sessions


@dataclass(frozen=True)
class ArchiveSessionNotFound(WireValue):
    """An explicit missing read, retaining the supplied request identity."""

    session_id: str

    def _wire_value(self):
        return None


@dataclass(frozen=True)
class ArchiveSessionOutput(WireValue):
    """Complete Archive.read session page, with open stored JSON preserved."""

    session: FrozenMap
    measurement_window: MeasurementWindow
    phase_projection: FrozenMap
    records: tuple[ArchiveRecord, ...]
    next_after: int | None

    def __post_init__(self):
        object.__setattr__(self, "session", frozen_map(self.session))
        object.__setattr__(self, "phase_projection", frozen_map(self.phase_projection))
        object.__setattr__(self, "records", tuple(self.records))

    @classmethod
    def from_result(cls, result: Mapping):
        values = dict(result)
        values["measurement_window"] = MeasurementWindow.from_result(
            values["measurement_window"]
        )
        values["records"] = tuple(
            ArchiveRecord.from_result(item) for item in values["records"]
        )
        return cls(**values)


def archive_read_output(
    session_id: str | None,
    result,
) -> ArchiveSessionList | ArchiveSessionNotFound | ArchiveSessionOutput:
    """Close one supplied read result without looking up missing information."""
    if session_id is None:
        return ArchiveSessionList.from_result(result)
    if result is None:
        return ArchiveSessionNotFound(session_id)
    return ArchiveSessionOutput.from_result(result)
