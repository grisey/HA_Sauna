"""Gemeinsame Fähigkeiten und Bedienrollen einer konfigurierten Anlage.

Die Projektion beschreibt vorhandene Zuordnungen, keine aktuelle Sensorqualität.
Sie enthält weder Schaltentscheidungen noch eigene Grenzwerte oder Zeitgeber.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .button import NATIVE_BUTTON_EVENTS
from .defaults import section
from .parameters import BY_KEY, EDITABLE_DEFINITIONS

_EDITABLE_KEYS = frozenset(definition.key for definition in EDITABLE_DEFINITIONS)
_SUBGROUP_SOURCES = {
    group["id"]: tuple(group["presence_sources"])
    for group in section("frontend")["settings_subgroups"]
    if "presence_sources" in group
}


def regulation_causes(control_mode, presence_source):
    """Automatische Regelursachen; Messung und Erkennung bleiben davon getrennt."""
    if control_mode != "automatic":
        return ()
    return (
        ("gang_heat", "cooling") if presence_source == "ha_presence"
        else ("gang_heat", "provisional_door_heat", "cooling")
    )


def cooling_presence_policy(control_mode, presence_source):
    """Gemeinsame Wirkung der führenden Personenquelle auf laufende Kühlung."""
    if control_mode != "automatic":
        return "none"
    return (
        "direct_gang_start" if presence_source == "ha_presence"
        else "proxy_person_minimum"
    )


def gang_start_interrupts_cooling(control_mode, presence_source):
    """Nur ein direkter Gangbeginn beendet die Kühlung ohne Mindestrestzeit."""
    return cooling_presence_policy(control_mode, presence_source) == "direct_gang_start"


@dataclass(frozen=True)
class ButtonCapabilities:
    """Getrennte Gerätegesten und Kanten eines Bedienungseingangs."""

    input_kind: str
    native_events_known: bool
    press: bool
    release: bool
    short: bool
    long: bool
    double: bool
    triple: bool
    local_hold_timing: bool
    additional_short_event_configurable: bool
    event_selection_configurable: bool
    selected_event: str
    native_gestures: tuple[str, ...]

    @classmethod
    def from_input(cls, entity_id, mode, selected_event="", event_types=None):
        """Also usable while an entity-assignment form is still incomplete."""
        selected_event = (selected_event or "").strip()
        event_input = str(entity_id or "").startswith("event.")
        binary_input = str(entity_id or "").startswith("binary_sensor.")
        is_button = mode == "button"
        reported = (
            {NATIVE_BUTTON_EVENTS.get(value) for value in event_types}
            if event_types is not None else set()
        )
        binary_button = binary_input and is_button
        native_button = event_input and is_button
        additional = bool(
            native_button
            and selected_event
            and selected_event not in NATIVE_BUTTON_EVENTS
        )
        return cls(
            input_kind=(
                "event" if native_button else "binary" if binary_button
                else "switch_event" if event_input
                else "switch_binary" if binary_input else "unassigned"
            ),
            native_events_known=event_input and event_types is not None,
            press=binary_button or (native_button and "press" in reported),
            release=binary_button or (native_button and "release" in reported),
            short=binary_button or (native_button and "short" in reported) or additional,
            long=binary_button or (native_button and "long" in reported),
            double=native_button and "double" in reported,
            triple=native_button and "triple" in reported,
            local_hold_timing=binary_button,
            additional_short_event_configurable=bool(
                native_button and (
                    "short" not in reported
                    or (selected_event and NATIVE_BUTTON_EVENTS.get(selected_event) != "short")
                )
            ),
            event_selection_configurable=event_input and not is_button,
            selected_event=selected_event or "",
            native_gestures=tuple(
                name for name in ("press", "release", "short", "long", "double", "triple")
                if native_button and name in reported
            ),
        )

    @property
    def session_gestures(self):
        return tuple(
            name for name in ("long", "double", "triple") if getattr(self, name)
        )

    @property
    def press_release_feedback(self):
        return self.press and self.release

    @property
    def event_name_valid(self):
        return (
            self.input_kind != "event"
            or not self.selected_event
            or self.selected_event not in NATIVE_BUTTON_EVENTS
            or NATIVE_BUTTON_EVENTS[self.selected_event] == "short"
        )

    def action_for_event(self, name):
        """A native classified gesture cannot be redefined as a short press."""
        if self.input_kind == "event":
            native = NATIVE_BUTTON_EVENTS.get(name)
            if native is not None:
                return native
            return "short" if self.selected_event and name == self.selected_event else None
        if self.input_kind == "switch_event":
            if self.selected_event:
                return "toggle" if name == self.selected_event else None
            return "toggle" if name in (None, "single_push", "single") else None
        return None

    def as_dict(self):
        return {
            key: value for key, value in asdict(self).items() if key != "selected_event"
        } | {
            "session_gestures": self.session_gestures,
            "press_release_feedback": self.press_release_feedback,
        }


@dataclass(frozen=True)
class ConfigurationModel:
    """Anwendbarkeit aus gespeichertem Anlagenaufbau und Gerätefähigkeiten."""

    measurement_positions: tuple[str, ...]
    presence_source: str
    presence_sensor_configured: bool
    illuminance_configured: bool
    power_sensor_configured: bool
    control_mode: str
    button: ButtonCapabilities

    @classmethod
    def from_configuration(cls, configuration, button_event_types=None):
        bindings = configuration.bindings.values
        return cls(
            measurement_positions=tuple(
                position for position in ("upper", "lower")
                if f"{position}_temperature" in bindings
                and f"{position}_humidity" in bindings
            ),
            presence_source=configuration.presence_source,
            presence_sensor_configured="presence" in bindings,
            illuminance_configured="presence_illuminance" in bindings,
            power_sensor_configured="heater_power" in bindings,
            control_mode=configuration.control_mode,
            button=ButtonCapabilities.from_input(
                bindings.get("control_input"), configuration.control_input_mode,
                configuration.button_event_type, button_event_types,
            ),
        )

    @property
    def direct_presence(self):
        return self.presence_source == "ha_presence"

    @property
    def regulation_causes(self):
        return regulation_causes(self.control_mode, self.presence_source)

    @property
    def gang_start_interrupts_cooling(self):
        return gang_start_interrupts_cooling(self.control_mode, self.presence_source)

    @property
    def cooling_presence_policy(self):
        return cooling_presence_policy(self.control_mode, self.presence_source)

    def parameter_applicable(self, definition):
        if isinstance(definition, str):
            definition = BY_KEY.get(definition)
        if definition is None or definition.key not in _EDITABLE_KEYS:
            return False
        if self.presence_source not in _SUBGROUP_SOURCES.get(
            definition.settings_subgroup, ("proxy", "ha_presence")
        ):
            return False
        for position in ("upper", "lower"):
            if definition.key.endswith(f"_{position}"):
                return position in self.measurement_positions
        if definition.key == "power_heating_threshold_w":
            return self.power_sensor_configured
        if definition.key == "button_hold_seconds":
            return self.button.local_hold_timing
        return True

    def as_dict(self):
        return {
            "measurement_positions": self.measurement_positions,
            "presence": {
                "source": self.presence_source,
                "direct": self.direct_presence,
                "observer": self.presence_sensor_configured and not self.direct_presence,
                "sensor_configured": self.presence_sensor_configured,
                "illuminance_configured": self.illuminance_configured,
            },
            "power_sensor_configured": self.power_sensor_configured,
            "button": self.button.as_dict(),
            "regulation_causes": self.regulation_causes,
            "gang_start_interrupts_cooling": self.gang_start_interrupts_cooling,
            "cooling_presence_policy": self.cooling_presence_policy,
        }


@dataclass(frozen=True)
class TemperatureControl:
    """Dasselbe Eingabeziel für Anzeige und Schreibweg aller Oberflächen."""

    scope: str
    value: float | None
    current_target: float | None
    next_target: float | None
    program_mode: str

    def as_dict(self):
        return asdict(self)


def temperature_control(controller):
    session = controller.session
    program_mode = (
        session.temperature_program_mode
        if session and session.temperature_program_mode else controller.program_mode
    )
    next_gang = bool(
        controller.control_mode == "automatic" and session
        and program_mode == "progressive"
    )
    current = controller.target_temperature
    upcoming = controller.next_gang_temperature
    return TemperatureControl(
        "next_gang" if next_gang else "current",
        upcoming if next_gang else current,
        current, upcoming, program_mode,
    )
