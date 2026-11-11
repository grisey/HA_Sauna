"""Deutsche Anzeigetexte; technische Zustandskennungen bleiben in der API erhalten."""

from hashlib import sha256
from collections.abc import Mapping
from math import isfinite

from .bindings import Bindings
from .core import calibration
from .core.parameters import BY_KEY, LIVE_TEMPERATURE_KEYS
from .core.power import watts
from .history_humidity import attach_display

PHASES = {
    "aus": "Aus",
    "aufheizen": "Aufheizen",
    "bereit": "Bereit",
    "saunagang": "Saunagang",
    "nachlauf": "Ofenkühlung",
    # Alte Archivphasen bleiben verständlich, werden aber nicht mehr erzeugt.
    "zwangskühlung": "Zwangskühlung (historisch)",
    "manuell": "Manueller Betrieb",
}
EVENTS = {
    "door_open": "Tür geöffnet",
    "door_close": "Tür geschlossen",
    "person_strong": "Person erkannt",
    "person_weak": "Schwaches Personensignal",
    "infusion": "Aufguss",
    "ventilation_confirmed": "Lüften bestätigt",
    "ventilation_complete": "Vollständiges Lüften bestätigt",
}

ROLES = {
    "upper_temperature": "Temperatur oben",
    "lower_temperature": "Temperatur unten",
    "upper_humidity": "Luftfeuchte oben",
    "lower_humidity": "Luftfeuchte unten",
    "heater_power": "Leistungsmessung",
    "heater_feedback": "Unabhängige Heizrückmeldung",
}
FAULTS = {
    "regulation_temperature_unavailable": "Für die Heizregelung fehlt ein gültiger Temperaturwert.",
    "heater_feedback_unavailable": "Die Stellung des Heizschützes ist nicht verfügbar.",
    "heater_feedback_mismatch": "Der Heizschütz hat den Schaltbefehl nicht bestätigt.",
    "heater_still_heating": "Trotz Ausschaltbefehl wird weiterhin Heizleistung gemessen.",
    "heater_service_unavailable": "Der Heizschütz konnte nicht geschaltet werden.",
    "heater_no_power": "Der Heizschütz ist eingeschaltet, es wird jedoch keine Heizleistung gemessen.",
    "archive": "Das Sitzungsarchiv konnte nicht geschrieben werden.",
    "operation_light": "Das Saunalicht konnte beim Start des Saunabetriebs nicht eingeschaltet werden.",
    "after_run_light": "Das Saunalicht konnte für die Ofenkühlung nicht gedimmt werden.",
    "session_light": "Das Saunalicht konnte nach dem Sitzungsende nicht eingestellt werden.",
    "start_rejected": "Der Start wurde verhindert. Bitte die fehlenden Einstellungen und Messwerte prüfen.",
}
REASONS = {
    "operation_off": "Der Saunabetrieb ist ausgeschaltet.",
    "temperature_configuration_required": "Die Temperatureinstellungen sind noch unvollständig.",
    "upper_temperature_unavailable": "Ein gültiger Temperaturwert für die Heizregelung fehlt.",
    "gang_veto": "Historisches Gang-Abschaltveto für eine bereits laufende Heizung.",
    "gang_heat_demand": "Der Saunagang fordert durchgehend Heizen an.",
    "temporary_door_heat": "Die Türschließung fordert vorübergehend Heizen mit Mindestdauer an.",
    "door_request": "Historische einmalige Heizanforderung aus dem Türereignis.",
    "door_request_at_limit": "Historische Türanforderung ohne Einschalten an der oberen Temperaturgrenze.",
    "gang": "Die Heizung bleibt während des Saunagangs eingeschaltet.",
    "forced_cooling": "Historische Zwangskühlung; der Ofen blieb aus.",
    "after_run": "Die Ofenkühlung läuft; der Ofen bleibt aus.",
    "minimum_heating": "Die Mindestheizzeit ist noch nicht abgelaufen.",
    "temperature_reached": "Die obere Regeltemperatur ist erreicht.",
    "thermostat_cooldown": "Die Heizpause nach der Temperaturabschaltung läuft.",
    "below_target": "Die Temperatur liegt unter der Einschaltgrenze.",
    "hysteresis_band": "Die Temperatur liegt im eingestellten Schaltbereich.",
    "manual_mode": "Der Ofen wird von Hand bedient und ist ausgeschaltet.",
}


def configuration_message(keys):
    return (
        "Vor dem Start bitte einstellen: "
        + "; ".join(BY_KEY[k].label for k in keys)
        + "."
    )


def fault_resolved(key):
    subject = ROLES.get(key) or {
        "configuration": "Einrichtung",
        "regulation_temperature_unavailable": "Regeltemperatur",
        "heater_feedback_unavailable": "Schützrückmeldung",
        "heater_feedback_mismatch": "Schaltbestätigung",
        "heater_service_unavailable": "Ansteuerung des Heizschützes",
        "heater_still_heating": "Heizabschaltung",
        "heater_no_power": "Heizleistung",
        "archive": "Sitzungsarchiv",
        "operation_light": "Saunalicht",
        "after_run_light": "Saunalicht",
        "session_light": "Lichtnachlauf",
        "start_rejected": "Startvoraussetzungen",
    }.get(key, "Gerätediagnose")
    return subject + ": Der vorherige Fehlerhinweis ist nicht mehr aktiv."


# Felder eines Sitzungsstands in der Verlaufsprojektion (``projection: session_state``).
_SESSION_STATE_KEYS = frozenset({
    "operation_enabled", "operation_off_at", "ended_at", "thermostat", "energy",
    "ready_at", "active_gang_temperature_c", "next_gang_temperature_c",
    "next_gang_temperature_blocked_by", "configuration",
})


def archive_record_display(record, *, history):
    """Anzeige eines Archivdatensatzes für Administratoren, ohne Bezug zur Anfrage.

    Diagnosen erhalten ihre Meldungstexte. In der Verlaufsprojektion (``history``)
    zusätzlich: gemessene Leistung, Grund einer Kalibrierunterbrechung und
    Sitzungsstände ohne die wachsende Zeitleiste; die Abfrage ohne Projektion und
    der Export behalten die Originale. Ändert den Datensatz an Ort und Stelle.
    """
    kind = record["kind"]
    if kind == "diagnostic":
        record["payload"]["messages"] = [
            fault_message(key, value)
            for key, value in record["payload"].get("faults", {}).items()
        ]
    elif not history:
        return
    elif kind == "source_state":
        payload = record["payload"]
        if payload.get("role") == "heater_power":
            attributes = payload.get("attributes", {})
            record["display"] = {
                "power_w": watts(payload.get("state"), attributes.get("unit_of_measurement"))
                if attributes.get("device_class") == "power" else None
            }
    elif kind == "calibration":
        record["display"] = {
            "reason_text": calibration.INTERRUPTIONS.get(record["payload"].get("reason"))
        }
    elif kind == "session":
        record["projection"] = "session_state"
        record["payload"] = {
            key: value for key, value in record["payload"].items()
            if key.startswith("temperature_") or key in _SESSION_STATE_KEYS
        }
        energy = record["payload"].get("energy")
        if isinstance(energy, dict):
            record["payload"]["energy"] = {
                key: value for key, value in energy.items() if key != "interval_delta"
            }


def fault_message(key, value):
    if key == "session_light" and value == "turn_off_failed":
        return "Das Saunalicht konnte nach Ablauf des Lichtnachlaufs nicht ausgeschaltet werden. Bitte das Licht prüfen."
    if key == "configuration":
        return configuration_message(
            [k.strip() for k in value.split(",") if k.strip() in BY_KEY]
        )
    if key in ROLES:
        detail = {
            "measurement_stale": "Der letzte Messwert ist veraltet.",
            "validity_unconfigured": "Das Höchstalter eines Messwerts ist noch nicht eingestellt.",
        }.get(value, "Zurzeit ist kein gültiger Messwert verfügbar.")
        return ROLES[key] + ": " + detail
    text = FAULTS.get(
        key, "Eine Störung wurde erkannt. Bitte die Gerätediagnose prüfen."
    )
    if value == "pending":
        text += " Der Ausfall wird noch geprüft."
    elif value == "confirmed":
        text += " Die Störung ist bestätigt; die Schutzabschaltung ist verriegelt."
    return text


def issues(runtime):
    device = runtime.device
    result = []
    for key, value in device.faults.items() if device else ():
        # Fehlende Einrichtung einmal erklären, nicht vier vorhandene Sensoren
        # fälschlich als ausgefallen melden.
        if value == "validity_unconfigured" or (
            key == "regulation_temperature_unavailable"
            and "sensor_timeout_seconds" in device.missing_configuration
        ):
            continue
        result.append(
            {
                "key": key,
                "message": fault_message(key, value),
                "action": "settings" if key == "configuration" else None,
            }
        )
    return result


def decision_message(decision):
    if decision is None:
        return "Noch keine Heizentscheidung."
    if decision.reason == "manual_override":
        return (
            "Der Ofen ist manuell eingeschaltet."
            if decision.heat
            else "Der Ofen ist manuell ausgeschaltet."
        )
    if decision.reason.startswith("protection:"):
        return "Eine bestätigte technische Störung verhindert das Heizen."
    if decision.reason.startswith("inhibit:"):
        return "Die Einrichtung muss vor dem Heizen vervollständigt werden."
    return REASONS.get(decision.reason, "Die Regelung prüft den Heizbedarf.")


def parameter_error(error):
    label = BY_KEY[error.key].label if error.key in BY_KEY else "Einstellungen"
    detail = {
        "required": "Bitte einen Wert eingeben.",
        "invalid_number": "Bitte eine gültige Zahl eingeben.",
        "positive": "Der Wert muss größer als null sein.",
        "non_negative": "Der Wert darf nicht negativ sein.",
        "too_small": "Der Wert liegt unter der zulässigen Untergrenze.",
        "too_large": "Der Wert liegt über der zulässigen Obergrenze.",
        "integer_required": "Bitte eine ganze Zahl eingeben.",
        "reduction_too_large": "Die Verkürzung muss kleiner als die erste Heizzeit sein.",
        "window_not_divisible": "Das Zeitfenster muss durch sein Erkennungsraster und den jeweiligen Prüfabstand teilbar sein.",
        "catalog_only": "Dieser Wert ist fest vorgegeben und nicht einstellbar.",
        "program_catalog_invalid": "Die Mindesttemperatur passt nicht zu den gespeicherten Temperaturprogrammen.",
        "not_applicable": "Diese Einstellung wird mit den zugeordneten Geräten und dem gewählten Erkennungsverfahren nicht verwendet.",
    }.get(error.code, "Bitte die eingegebenen Werte prüfen.")
    return label + ": " + detail


def public_configuration(options):
    """Only the choices and bounds used by normal control remain visible."""
    parameters = options.get("parameters", {})
    keys = (
        "target_temperature_c",
        "final_temperature_c",
        "temperature_gangs",
        "sauna_min_temperature_c",
        "preset_count",
        "preset_start_c",
        "standard_temperature_c",
        "preset_step_c",
        "session_light_brightness_percent",
    )
    return {
        key: value
        for key, value in options.items()
        if key in {
            "program_mode", "temperature_programs", "selected_program_id",
            "control_mode", "temperature_steps", "appearance",
            "button_program", "button_session_gesture",
        }
    } | {"parameters": {key: parameters[key] for key in keys if key in parameters}}


HISTORY_MEASUREMENT_ROLES = (
    "upper_temperature", "lower_temperature", "upper_humidity", "lower_humidity",
)


def historical_measurement_context(session):
    """Project only roles and validity recorded in this session's configuration."""
    configuration = session.get("configuration")
    if not isinstance(configuration, Mapping):
        return None
    bindings = configuration.get("bindings")
    parameters = configuration.get("parameters")
    if not isinstance(bindings, Mapping) or not isinstance(parameters, Mapping):
        return None
    try:
        # Optional sensor pairs are absent in the original serialized bindings.
        # Reuse their owner to distinguish a valid single pair from partial data.
        bindings = Bindings(bindings).values
    except ValueError:
        return None
    ttl = parameters.get("sensor_timeout_seconds")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not isfinite(ttl) or ttl < 0:
        return None
    roles = [role for role in HISTORY_MEASUREMENT_ROLES if bindings.get(role)]
    regulation = next((position for position in ("upper", "lower")
                       if f"{position}_temperature" in roles), None)
    return {"roles": roles, "regulation_position": regulation,
            "sensor_timeout_seconds": ttl}


def public_measurement(measurement, *, configuration=None):
    """Keep chart values and timestamps without exposing entity bindings."""
    result = {key: value for key, value in measurement.items()
              if key not in {"source", "source_matches_binding", "sample", "original_id"}}
    bindings = configuration.get("bindings") if isinstance(configuration, Mapping) else None
    if isinstance(bindings, Mapping):
        role = f"{measurement.get('position')}_{measurement.get('quantity')}"
        source = measurement.get("source")
        result["source_matches_binding"] = bool(
            role in HISTORY_MEASUREMENT_ROLES and isinstance(source, str)
            and source and source == bindings.get(role)
        )
    return result


_COLUMN_RECORD_KEYS = frozenset({"id", "entry_id", "session_id", "kind", "received_at",
                                 "payload", "display"})
_COLUMN_DISPLAY_KEYS = frozenset({"value", "received_at", "valid_until"})
_COLUMN_KINDS = frozenset({"measurement", "source_snapshot"})
_NUMBERS = (int, float)
_MISSING = object()
_HUMIDITY_KEY = frozenset({"absolute_humidity"})


def _finite_or_none(value):
    return value is None or (
        type(value) in (int, float) and isfinite(value)
    )


def history_measurement_columns(records, *, configuration=None, public=False,
                                displays=None):
    """Verlaufsprojektion: Messungen spaltenweise statt als einzelne Datensätze.

    Übertragen wird nur, was der Verlauf aus einer Messung liest: Art, Position,
    Größe, Quelle (Administratoren) oder deren Übereinstimmung mit der Zuordnung
    (sonst), Wert, Empfangszeit und die absolute Feuchte mit ihrer Gültigkeit.
    Messungen in anderer Form bleiben unverändert unter ``records``; die
    Originalfelder liefern die Abfrage ohne Projektion und der Export.
    Gibt die übrigen Datensätze und die Spalten zurück; die Reihenfolge der
    Spalten ist die der Datensätze.

    ``displays`` sind die Feuchtewerte je Datensatz (``Archive.read`` mit
    ``humidity_values``). Dann entstehen Spalten und übrige Datensätze mit
    ihrer Anzeige in einem Durchgang, ohne vorher jede Messung zu kopieren.
    """
    columns = {
        "roles": [], "role": [], "id": [], "received_at": [], "value": [],
        "absolute_humidity": [], "absolute_humidity_valid_until": [],
        "record_received_at": [],
    }
    known_roles = columns["roles"]
    add_role, add_id = columns["role"].append, columns["id"].append
    add_received, add_value = columns["received_at"].append, columns["value"].append
    add_humidity = columns["absolute_humidity"].append
    add_valid_until = columns["absolute_humidity_valid_until"].append
    add_record_received = columns["record_received_at"].append
    roles = {}
    remaining = []
    append = remaining.append
    for index, record in enumerate(records):
        kind = record.get("kind")
        if kind not in _COLUMN_KINDS:
            append(record)
            continue
        if displays is None:
            display = record.get("display")
            humidity = display.get("absolute_humidity") if type(display) is dict else None
            single = humidity is not None and len(display) == 1
        else:
            humidity = displays[index]
            previous = record.get("display")
            # The attached display would hold other values beside the humidity.
            single = not isinstance(previous, Mapping) or previous.keys() <= _HUMIDITY_KEY
        payload = record.get("payload")
        if type(payload) is dict:
            position, quantity = payload.get("position"), payload.get("quantity")
            received, value = payload.get("received_at"), payload.get("value", _MISSING)
            source = payload.get("source")
        else:
            position = quantity = received = value = source = None
        identity = record.get("id")
        if (
            not single
            or type(identity) is not int
            or type(position) is not str
            or type(quantity) is not str
            or type(received) is not str
            or not (value is None or (type(value) in _NUMBERS and isfinite(value)))
            or not (type(source) is str or (public and source is None))
            or type(record.get("received_at")) is not str
            or not _COLUMN_RECORD_KEYS.issuperset(record)
            or type(humidity) is not dict
            or humidity.keys() != _COLUMN_DISPLAY_KEYS
            or type(humidity["received_at"]) is not str
            or not _finite_or_none(humidity["value"])
            or not (humidity["valid_until"] is None
                    or type(humidity["valid_until"]) is str)
        ):
            append(record if displays is None else {
                **record, "display": attach_display(record, humidity),
            })
            continue
        key = (kind, position, quantity, source)
        number = roles.get(key)
        if number is None:
            role = {"kind": kind, "position": position, "quantity": quantity}
            if public:
                role.update({
                    name: shown for name, shown in public_measurement(
                        {"position": position, "quantity": quantity, "source": source},
                        configuration=configuration,
                    ).items() if name == "source_matches_binding"
                })
            else:
                role["source"] = source
            # Sources that project identically share one public role.
            number = roles[key] = next(
                (known for known, other in enumerate(known_roles) if other == role),
                len(known_roles),
            )
            if number == len(known_roles):
                known_roles.append(role)
        add_role(number)
        add_id(identity)
        add_received(received)
        add_value(value)
        add_humidity(humidity["value"])
        add_valid_until(humidity["valid_until"])
        if record["received_at"] != received:
            add_record_received([identity, record["received_at"]])
    return remaining, columns


def _public_references(value):
    """Keep direct-presence references stable without their embedded source."""
    if isinstance(value, dict):
        return {
            key: item if key == "session_id" else _public_references(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_references(item) for item in value]
    if isinstance(value, str) and "presence:direct:" in value:
        # Gang, cooling and projection references reuse these identities.
        # Hash the entire identity consistently across live and archive reads.
        return "public:" + sha256(value.encode("utf-8")).hexdigest()
    return value


def resolve_cooling_token(session, token):
    """Resolve only the public identity of the currently active cooling phase."""
    phase = session.after_run if session else None
    if phase is not None and token == _public_references(phase.phase_id):
        return phase.phase_id
    return token


def public_session(session):
    """Project a serialized session without configuration or entity-bearing IDs."""
    if session is None:
        return None
    result = {
        key: value for key, value in session.items() if key != "configuration"
    }
    if "configuration" in session:
        result["measurement_context"] = historical_measurement_context(session)
    return _public_references(result)


def public_phase_projection(projection):
    """Use the same public identities as the session's gang and cooling data."""
    return _public_references(projection)


def public_state(result):
    """Public panel contract, shared by HA and the local role preview."""
    result = dict(result)
    configuration = result["configuration"]
    result["configuration"] = public_configuration(result["configuration"])
    result["parameters"] = [item for item in result["parameters"]
                            if item["key"] in LIVE_TEMPERATURE_KEYS]
    result["measurements"] = [public_measurement(item, configuration=configuration)
                              for item in result["measurements"]]
    for key in ("session", "last_session"):
        if key in result:
            result[key] = public_session(result[key])
    if "phase_projection" in result:
        result["phase_projection"] = public_phase_projection(result["phase_projection"])
    if "calibration" in result:
        # The control hint is public; results stay with the protocol (administrators).
        result["calibration"] = {"active": result["calibration"]["active"]}
    for key in ("rule_inputs", "heating_observation", "decision", "faults", "protection",
                "inhibits", "archive_error", "detection_channels", "detector_trace", "presence",
                "field_states"):
        result.pop(key, None)
    return result
