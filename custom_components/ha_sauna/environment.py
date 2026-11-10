"""Read optional weather sources: display data and the outside air of the sauna for the recognition."""

from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .bindings import ROLE_BY_KEY, metadata_error
from .core.moisture import absolute_humidity


# Weather attributes supplement the selected weather entity. Explicit sensor
# choices take precedence, including when that chosen sensor is unavailable.
METRICS = (
    ("temperature", "Temperatur", "temperature", "temperature_unit"),
    ("humidity", "Relative Feuchte", "humidity", "%"),
    ("absolute_humidity", "Absolute Feuchte", None, None),
    ("dew_point", "Taupunkt", "dew_point", "temperature_unit"),
    ("pressure", "Luftdruck", "pressure", "pressure_unit"),
    ("wind_speed", "Windgeschwindigkeit", "wind_speed", "wind_speed_unit"),
    ("wind_direction", "Windrichtung", "wind_bearing", "°"),
    ("precipitation", "Niederschlag", None, None),
)
UNAVAILABLE = {"unknown", "unavailable", "", "none"}
WIND_DIRECTIONS = frozenset(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
)


def _timestamp(value):
    """Keep actual source timestamps; never substitute the current time."""
    try:
        parsed = value if isinstance(value, datetime) else (
            dt_util.parse_datetime(value) if isinstance(value, str) else None
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _value(key, raw):
    if raw is None or isinstance(raw, bool) or str(raw).lower() in UNAVAILABLE:
        return None
    if key == "wind_direction" and isinstance(raw, str) and raw in WIND_DIRECTIONS:
        return raw
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(value):
        return None
    if key == "humidity" and not 0 <= value <= 100:
        return None
    if key == "wind_direction" and not 0 <= value <= 360:
        return None
    return value


def environment_snapshot(hass, bindings, *, sources=False):
    """Return display data without full HA attributes.

    Entity IDs only with ``sources`` (archive record): every value then names
    the entity that supplied it, ``entities`` the bound weather and time
    entities.
    """
    selected = {key: value for key, value in bindings.items()
                if key.startswith("environment_")}
    result = {
        "configured": bool(selected),
        "station": None,
        "condition": None,
        "values": [],
        "measurement_time": None,
        "forecast_time": None,
        "source": {"mode": None, "interpolated": None},
        "updated_at": None,
    }
    if not selected:
        return result

    states = {key: hass.states.get(entity_id) for key, entity_id in selected.items()}
    weather = states.get("environment_weather")
    weather_available = weather is not None and weather.state not in UNAVAILABLE
    if weather_available:
        result["condition"] = weather.state

    used_states = []
    for key, label, attribute, weather_unit in METRICS:
        role_key = "environment_" + key
        state = states.get(role_key)
        if role_key in selected:
            raw = state.state if state is not None else "unavailable"
            attributes = state.attributes if state is not None else None
            valid_metadata = metadata_error(
                ROLE_BY_KEY[role_key], selected[role_key], attributes
            ) is None
            value = _value(key, raw) if valid_metadata else None
            unit = attributes.get("unit_of_measurement") if attributes else None
        elif weather is not None and attribute is not None and attribute in weather.attributes:
            state = weather
            raw = weather.attributes[attribute]
            value = _value(key, raw) if weather_available else None
            unit = (weather.attributes.get(weather_unit)
                    if weather_unit.endswith("_unit") else weather_unit)
            role = ROLE_BY_KEY[role_key]
            if unit not in (role.accepted_units or (role.unit,)):
                value = None
        else:
            continue
        if state is not None:
            used_states.append(state)
        result["values"].append({
            "key": key,
            "label": label,
            "state": str(raw) if raw is not None else None,
            "value": value,
            "unit": unit,
            "available": value is not None,
            "last_updated": _timestamp(state.last_updated) if state else None,
            **({"entity_id": selected.get(role_key) or weather.entity_id} if sources else {}),
        })

    for name in ("measurement_time", "forecast_time"):
        role_key = "environment_" + name
        state = states.get(role_key)
        if state is not None and metadata_error(
            ROLE_BY_KEY[role_key], selected[role_key], state.attributes
        ) is None:
            result[name] = _timestamp(state.state)

    if weather is not None:
        used_states.append(weather)
    stations = set()
    for state in used_states:
        station_id = state.attributes.get("station_id")
        station_name = state.attributes.get("station_name")
        stations.add((
            station_id if isinstance(station_id, str) else None,
            station_name if isinstance(station_name, str) else None,
        ))
    if len(stations) == 1:
        station_id, name = next(iter(stations))
        if isinstance(name, str) and name:
            result["station"] = {
                "name": name,
                "id": station_id if isinstance(station_id, str) else None,
            }
    updates = [stamp for state in used_states if (stamp := _timestamp(
        state.attributes.get("latest_update") or state.last_updated
    ))]
    result["updated_at"] = max(updates, default=None)

    # DWD can expose measurements, forecasts, or interpolated mixed data.
    # Inspect only the source entries' relevant flags, without reading credentials
    # or importing the optional custom integration.
    registry = er.async_get(hass)
    entry_ids = set()
    for state in used_states:
        entry = registry.async_get(state.entity_id)
        entry_ids.add(entry.config_entry_id if entry else None)
    if len(entry_ids) == 1 and None not in entry_ids:
        entry = hass.config_entries.async_get_entry(next(iter(entry_ids)))
        if entry is not None and entry.domain == "dwd_weather":
            options = {**entry.data, **entry.options}
            mode = options.get("data_type")
            interpolation = options.get("interpolate")
            result["source"] = {
                "mode": mode if mode in {
                    "mixed_data", "forecast_data", "report_data"
                } else None,
                "interpolated": interpolation if isinstance(interpolation, bool) else None,
            }
    if sources:
        result["entities"] = {
            name: selected.get("environment_" + name)
            for name in ("weather", "measurement_time", "forecast_time")
        }
    return result


def track_environment(hass, runtime):
    """Archive the bound environment source with each session.

    Every state change of a bound entity is offered to the runtime; it
    archives the snapshot at the session start and then only for a new
    measurement time of the source (without one, for a changed value). There
    is no own interval. Returns the unsubscribe callback, or ``None`` without
    an environment binding.
    """
    bindings = runtime.configuration.bindings.values
    entities = sorted({entity_id for key, entity_id in bindings.items()
                       if key.startswith("environment_")})
    if not entities:
        return None
    runtime.environment = lambda: environment_snapshot(hass, bindings, sources=True)

    @callback
    def changed(_event):
        runtime.environment_changed()

    return async_track_state_change_event(hass, entities, changed)


def outside_air(hass, bindings) -> dict | None:
    """Outside air of the sauna from the configured weather source or sensors.

    ``{"temperature": °C, "absolute_humidity": g/m³}``; a value that is not
    available is omitted (the recognition then uses its typical value).  The
    absolute humidity comes from a direct value, else from temperature and
    relative humidity, else from the dew point.
    """
    snapshot = environment_snapshot(hass, bindings)
    if not snapshot["configured"]:
        return None
    values = {v["key"]: v["value"] for v in snapshot["values"] if v["available"]}
    air = {}
    temperature = values.get("temperature")
    if temperature is not None:
        air["temperature"] = float(temperature)
    try:
        if values.get("absolute_humidity") is not None:
            air["absolute_humidity"] = float(values["absolute_humidity"])
        elif temperature is not None and values.get("humidity") is not None:
            air["absolute_humidity"] = absolute_humidity(float(temperature), float(values["humidity"]))
        elif values.get("dew_point") is not None:
            air["absolute_humidity"] = absolute_humidity(float(values["dew_point"]), 100.0)
    except ValueError:
        pass
    return air or None
