"""Deutsche Anzeigetexte; technische Zustandskennungen bleiben in der API erhalten."""

from .core.parameters import BY_KEY

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
    "ventilation_confirmed": "Durchlüften bestätigt",
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
    "regulation_temperature_unavailable": "Für die Heizregelung fehlt ein gültiger oberer Temperaturwert.",
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
    "upper_temperature_unavailable": "Ein gültiger oberer Temperaturwert fehlt.",
    "gang_veto": "Der Saunagang verhindert eine reguläre Abschaltung; er schaltet einen ausgeschalteten Ofen nicht selbst ein.",
    "door_request": "Die einmalige Heizanforderung aus dem Türereignis wurde angenommen.",
    "door_request_at_limit": "Die Türanforderung wurde verbraucht; die obere Temperaturgrenze verhindert das Einschalten.",
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
        "below_start_temperature": "Die Endtemperatur darf nicht unter der Starttemperatur liegen.",
        "window_not_divisible": "Das Zeitfenster muss durch den Zeitabstand der Personenprüfung teilbar sein.",
        "program_catalog_invalid": "Die Mindesttemperatur passt nicht zu den gespeicherten Temperaturprogrammen.",
        "button_temperature_invalid": "Die gespeicherte Tastertemperatur liegt außerhalb des neuen Regelbereichs.",
    }.get(error.code, "Bitte die eingegebenen Werte prüfen.")
    return label + ": " + detail
