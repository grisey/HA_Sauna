"""Werkseinstellungen aus dem akzeptierten Kandidaten, mit funktionalen Rollen.

Die Laufzeit konsumiert ausschließlich Parameters.values. Dieses Mapping wird
nur zum Anlegen zentraler Parameterdefinitionen und für den Referenztest benutzt.
"""
# key, label, unit, default, minimum, maximum, integer
SPECS = (
    ("grid_seconds", "Experte: Grundraster", "s", 1, 1, 1, True),
    ("median_seconds", "Experte: Medianfenster", "s", 5, 1, 600, True),
    ("door_window_seconds", "Experte: Türtrendfenster", "s", 8, 1, 600, True),
    ("door_humidity_seconds", "Experte: Türfeuchtefenster", "s", 10, 1, 600, True),
    ("door_open_slope", "Experte: Öffnung Temperaturtrend", "°C/min", -1.8, -100, 0, False),
    ("door_open_humidity_upper", "Experte: Öffnung Feuchteabfall oben", "%", .45, 0, 100, False),
    ("door_open_humidity_lower", "Experte: Öffnung Feuchteabfall unten", "%", .3, 0, 100, False),
    ("door_open_hold_seconds", "Experte: Öffnungsbestätigung", "s", 2, 1, 600, True),
    ("door_heating_slope", "Experte: Temperaturabfall trotz Heizen", "°C/min", -.8, -100, 0, False),
    ("door_heating_hold_seconds", "Experte: Temperaturöffnung bestätigen", "s", 5, 1, 600, True),
    ("door_heating_max_temperature_c", "Experte: Ergänzende Türerkennung unter", "°C", 70, 0, 150, False),
    ("door_close_slope", "Experte: Schließung Temperaturtrend", "°C/min", .15, 0, 100, False),
    ("door_close_hold_seconds", "Experte: Schließungsbestätigung", "s", 3, 1, 600, True),
    ("vent_baseline_seconds", "Experte: Lüftungsrückblick", "s", 60, 1, 600, True),
    ("vent_hold_seconds", "Experte: Lüftungsmindestdauer", "s", 60, 1, 3600, True),
    ("vent_drop_upper", "Experte: Lüftungsabfall oben", "°C", 3, 0, 100, False),
    ("vent_drop_lower", "Experte: Lüftungsabfall unten", "°C", 3, 0, 100, False),
    ("vent_absolute_humidity_loss_percent", "Experte: Absoluter Wasserverlust beim Durchlüften", "%", 30, 0, 100, False),
    ("person_step_seconds", "Experte: Personenprüfraster", "s", 5, 1, 60, True),
    ("strong_window_seconds", "Experte: Starkes Personenfenster", "s", 60, 1, 600, True),
    ("strong_humidity_upper", "Experte: Starker Feuchtetrend oben", "%/min", .5, 0, 100, False),
    ("strong_humidity_lower", "Experte: Starker Feuchtetrend unten", "%/min", .5, 0, 100, False),
    ("strong_temperature_upper", "Experte: Starker Temperaturtrend oben", "°C/min", 0, -100, 100, False),
    ("strong_temperature_lower", "Experte: Starker Temperaturtrend unten", "°C/min", 0, -100, 100, False),
    ("strong_hold_seconds", "Experte: Starke Personenbestätigung", "s", 10, 1, 600, True),
    ("weak_window_seconds", "Experte: Schwaches Personenfenster", "s", 120, 1, 600, True),
    ("weak_humidity_upper", "Experte: Schwacher Feuchtetrend oben", "%/min", .14, 0, 100, False),
    ("weak_humidity_lower", "Experte: Schwacher Feuchtetrend unten", "%/min", .14, 0, 100, False),
    ("weak_temperature_upper", "Experte: Schwacher Temperaturtrend oben", "°C/min", 1, -100, 100, False),
    ("weak_temperature_lower", "Experte: Schwacher Temperaturtrend unten", "°C/min", .8, -100, 100, False),
    ("weak_hold_seconds", "Experte: Schwache Personenbestätigung", "s", 30, 1, 600, True),
    ("infusion_window_seconds", "Experte: Aufgussfenster", "s", 10, 1, 600, True),
    ("infusion_humidity", "Experte: Aufgussfeuchteanstieg", "%", 2, 0, 100, False),
    ("infusion_temperature", "Experte: Aufgusstemperaturänderung", "°C", -.3, -100, 100, False),
    ("infusion_hold_seconds", "Experte: Aufgussbestätigung", "s", 3, 1, 600, True),
)


def candidate_values(values):
    """Reiner Referenzadapter für Tests; keine zweite Konfigurationsquelle."""
    return {
        "raster_s": values["grid_seconds"], "median_s": values["median_seconds"],
        "tuer": {
            "trendfenster_s": values["door_window_seconds"],
            "feuchtefenster_s": values["door_humidity_seconds"],
            "oeffnung_trend_max_c_min": values["door_open_slope"],
            "oeffnung_feuchteabfall_pp": {c: values[f"door_open_humidity_{p}"] for c, p in (("3", "upper"), ("6", "lower"))},
            "oeffnung_bestaetigung_s": values["door_open_hold_seconds"],
            "schliessung_trend_min_c_min": values["door_close_slope"],
            "schliessung_bestaetigung_s": values["door_close_hold_seconds"],
        },
        "lueftung": {"basis_rueckblick_s": values["vent_baseline_seconds"],
            "mindestens_s": values["vent_hold_seconds"],
            "mindestabfall_c": {c: values[f"vent_drop_{p}"] for c, p in (("3", "upper"), ("6", "lower"))}},
        "person": {"pruefraster_s": values["person_step_seconds"],
            "schwach_nur_nach_bestaetigter_lueftung": True,
            **{de: {"fenster_s": values[f"{en}_window_seconds"],
                "feuchtetrend_pp_min": {c: values[f"{en}_humidity_{p}"] for c, p in (("3", "upper"), ("6", "lower"))},
                "temperaturtrend_c_min": {c: values[f"{en}_temperature_{p}"] for c, p in (("3", "upper"), ("6", "lower"))},
                "bestaetigung_s": values[f"{en}_hold_seconds"]} for de, en in (("stark", "strong"), ("schwach", "weak"))}},
        "aufguss": {"fenster_s": values["infusion_window_seconds"],
            "feuchteanstieg_pp": values["infusion_humidity"],
            "temperaturaenderung_min_c": values["infusion_temperature"],
            "bestaetigung_s": values["infusion_hold_seconds"]},
    }
