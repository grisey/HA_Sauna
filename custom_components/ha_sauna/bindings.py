"""HA-Quellen nach Rollen zuordnen; keine Zuordnung durch Gerätenamen erraten."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType


class BindingError(ValueError):
    def __init__(self, key: str, code: str) -> None:
        self.key, self.code = key, code
        super().__init__(f"{key}: {code}")


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    domains: tuple[str, ...]
    device_class: str | None = None
    unit: str | None = None
    optional: bool = False
    accepted_units: tuple[str, ...] = ()


ROLES = (
    Role(
        "upper_temperature", "Temperatursensor oben", ("sensor",), "temperature", "°C",
        optional=True,
    ),
    Role(
        "upper_humidity", "Luftfeuchtesensor oben", ("sensor",), "humidity", "%",
        optional=True,
    ),
    Role(
        "upper_status", "Zusätzlicher Sensorstatus oben", ("sensor", "binary_sensor"), optional=True
    ),
    Role(
        "lower_temperature", "Temperatursensor unten", ("sensor",), "temperature", "°C",
        optional=True,
    ),
    Role(
        "lower_humidity", "Luftfeuchtesensor unten", ("sensor",), "humidity", "%",
        optional=True,
    ),
    Role(
        "lower_status", "Zusätzlicher Sensorstatus unten", ("sensor", "binary_sensor"), optional=True
    ),
    Role("heater", "Schalter des Heizschützes", ("switch",)),
    Role(
        "heater_feedback",
        "Zusätzliche Heizrückmeldung",
        ("switch", "binary_sensor"),
        optional=True,
    ),
    Role(
        "heater_power",
        "Leistungsmessung des Ofens",
        ("sensor",),
        "power",
        "W",
        optional=True,
        accepted_units=("W", "kW"),
    ),
    Role("light", "Dimmbares Saunalicht", ("light",)),
    Role("control_input", "Taster oder Betriebsschalter", ("event", "binary_sensor")),
    Role("presence", "Präsenzmeldung", ("binary_sensor",), optional=True),
    Role(
        "presence_illuminance", "Lichtstärke am Präsenzsensor", ("sensor",),
        "illuminance", "lx", optional=True,
    ),
    Role("audio_output", "Audioziel (vorbereitet)", ("media_player",), optional=True),
    Role(
        "environment_weather", "Wetterquelle", ("weather",),
        None, None, optional=True,
    ),
    Role(
        "environment_temperature", "Außentemperatur", ("sensor",),
        "temperature", "°C", optional=True,
    ),
    Role(
        "environment_humidity", "Relative Außenfeuchte", ("sensor",),
        "humidity", "%", optional=True,
    ),
    Role(
        "environment_absolute_humidity", "Absolute Außenfeuchte", ("sensor",),
        "absolute_humidity", "g/m³", optional=True,
    ),
    Role(
        "environment_dew_point", "Taupunkt außen", ("sensor",),
        "temperature", "°C", optional=True,
    ),
    Role(
        "environment_pressure", "Luftdruck", ("sensor",),
        "pressure", "hPa", optional=True,
    ),
    Role(
        "environment_wind_speed", "Windgeschwindigkeit", ("sensor",),
        "wind_speed", "km/h", optional=True,
    ),
    Role(
        "environment_wind_direction", "Windrichtung", ("sensor",),
        "wind_direction", "°", optional=True,
    ),
    Role(
        "environment_precipitation", "Niederschlagsintensität", ("sensor",),
        "precipitation_intensity", "mm/h", optional=True,
    ),
    Role(
        "environment_measurement_time", "Messzeitpunkt", ("sensor",),
        "timestamp", None, optional=True,
    ),
    Role(
        "environment_forecast_time", "Vorhersagezeitpunkt", ("sensor",),
        "timestamp", None, optional=True,
    ),
)
ROLE_BY_KEY = MappingProxyType({role.key: role for role in ROLES})
ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


@dataclass(frozen=True)
class Bindings:
    values: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise BindingError("base", "invalid_bindings")
        if set(self.values) - ROLE_BY_KEY.keys():
            raise BindingError("base", "unknown_binding")
        values = dict(self.values)
        for role in ROLES:
            value = values.get(role.key)
            if value is None and role.optional:
                values.pop(role.key, None)
                continue
            if not isinstance(value, str) or not ENTITY_ID.fullmatch(value):
                raise BindingError(role.key, "entity_required")
            if value.split(".", 1)[0] not in role.domains:
                raise BindingError(role.key, "wrong_domain")
        for position in ("upper", "lower"):
            temperature = f"{position}_temperature"
            humidity = f"{position}_humidity"
            if (temperature in values) != (humidity in values):
                missing = humidity if temperature in values else temperature
                raise BindingError(missing, "entity_required")
        if not ("upper_temperature" in values or "lower_temperature" in values):
            raise BindingError("base", "sensor_pair_required")
        for metric in ("temperature", "humidity"):
            upper = values.get(f"upper_{metric}")
            if upper is not None and upper == values.get(f"lower_{metric}"):
                raise BindingError(f"lower_{metric}", "duplicate_sensor")
        object.__setattr__(self, "values", MappingProxyType(values))

    def as_dict(self) -> dict[str, str]:
        return dict(self.values)


def metadata_error(role: Role, entity_id: str, attributes: Mapping | None) -> str | None:
    """One suitability contract for form candidates and submitted bindings."""
    domain = entity_id.partition(".")[0]
    if domain not in role.domains:
        return "wrong_domain"
    if attributes is None:
        return "entity_not_ready"
    device_class = attributes.get("device_class")
    if role.device_class and device_class != role.device_class:
        return "wrong_device_class"
    accepted_classes = None
    if role.key == "presence":
        accepted_classes = {"occupancy", "presence", "motion"}
    elif role.key == "control_input":
        accepted_classes = {"button"} if domain == "event" else None
    elif role.key in {"upper_status", "lower_status"}:
        accepted_classes = {None} if domain == "sensor" else None
    if accepted_classes is not None and device_class not in accepted_classes:
        return "wrong_device_class"
    if role.key in {"upper_status", "lower_status"} and attributes.get("unit_of_measurement"):
        return "wrong_unit"
    if role.unit and attributes.get("unit_of_measurement") not in (role.accepted_units or (role.unit,)):
        return "wrong_unit"
    if role.key == "control_input" and domain == "event":
        event_types = attributes.get("event_types")
        if not isinstance(event_types, (list, tuple)) or not event_types or not all(
            isinstance(value, str) and value for value in event_types
        ):
            return "invalid_event_types"
    if role.key == "light":
        modes = attributes.get("supported_color_modes") or ()
        if not isinstance(modes, (list, tuple, set, frozenset)) or not any(
            mode in {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"}
            for mode in modes
        ):
            return "light_not_dimmable"
    return None


def validate_metadata(
    bindings: Bindings, attributes: Mapping[str, Mapping | None], *, preserved=()
) -> None:
    """Validate metadata; unchanged missing assignments may be explicitly preserved."""
    for key, entity_id in bindings.values.items():
        if key in preserved:
            continue
        error = metadata_error(ROLE_BY_KEY[key], entity_id, attributes.get(entity_id))
        if error:
            raise BindingError(key, error)
