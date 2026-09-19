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
    Role("upper_temperature", "Temperatur oben", ("sensor",), "temperature", "°C"),
    Role("upper_humidity", "Luftfeuchte oben", ("sensor",), "humidity", "%"),
    Role("lower_temperature", "Temperatur unten", ("sensor",), "temperature", "°C"),
    Role("lower_humidity", "Luftfeuchte unten", ("sensor",), "humidity", "%"),
    Role("heater", "Schalter des Heizschützes", ("switch",)),
    Role("control_input", "Taster oder Betriebsschalter", ("event", "binary_sensor")),
    Role("light", "Dimmbares Saunalicht", ("light",)),
    Role("upper_status", "Sensorstatus oben", ("sensor", "binary_sensor"), optional=True),
    Role("lower_status", "Sensorstatus unten", ("sensor", "binary_sensor"), optional=True),
    Role("heater_feedback", "Unabhängiger binärer Heiznachweis (optional)", ("switch", "binary_sensor"), optional=True),
    Role("heater_power", "Leistungsmessung des Ofens (optional)", ("sensor",), "power", "W",
         optional=True, accepted_units=("W", "kW")),
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
        for metric in ("temperature", "humidity"):
            if values[f"upper_{metric}"] == values[f"lower_{metric}"]:
                raise BindingError(f"lower_{metric}", "duplicate_sensor")
        object.__setattr__(self, "values", MappingProxyType(values))

    def as_dict(self) -> dict[str, str]:
        return dict(self.values)


def validate_metadata(bindings: Bindings, attributes: Mapping[str, Mapping | None]) -> None:
    """Bei der Auswahl Typ, Einheit und Dimmbarkeit prüfen, nicht Messwert != unknown.

    Die Einrichtung braucht Metadaten der ausgewählten Entitäten. Eine temporäre
    Nichtverfügbarkeit einer schon gespeicherten Quelle wird NICHT durch eine neue
    Zuordnung ersetzt. Laufende Ausfalldiagnose übernimmt der Messadapter.
    """
    for key, entity_id in bindings.values.items():
        attrs = attributes.get(entity_id)
        if attrs is None:
            raise BindingError(key, "entity_not_ready")
        role = ROLE_BY_KEY[key]
        if role.device_class and attrs.get("device_class") != role.device_class:
            raise BindingError(key, "wrong_device_class")
        if role.unit and attrs.get("unit_of_measurement") not in (role.accepted_units or (role.unit,)):
            raise BindingError(key, "wrong_unit")
        if key == "light":
            modes = attrs.get("supported_color_modes") or ()
            if not isinstance(modes, (list, tuple, set, frozenset)) or not any(
                mode in {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"}
                for mode in modes
            ):
                raise BindingError(key, "light_not_dimmable")
