"""Explicit adapter to the frozen reference candidate, not a default source."""


def candidate_values(values):
    """Reiner Referenzadapter für Tests; keine zweite Konfigurationsquelle."""
    return {
        "raster_s": values["grid_seconds"],
        "median_s": values["median_seconds"],
        "tuer": {
            "trendfenster_s": values["door_window_seconds"],
            "feuchtefenster_s": values["door_humidity_seconds"],
            "oeffnung_trend_max_c_min": values["door_open_slope"],
            "oeffnung_feuchteabfall_pp": {
                c: values[f"door_open_humidity_{p}"]
                for c, p in (("3", "upper"), ("6", "lower"))
            },
            "oeffnung_bestaetigung_s": values["door_open_hold_seconds"],
            "schliessung_trend_min_c_min": values["door_close_slope"],
            "schliessung_bestaetigung_s": values["door_close_hold_seconds"],
        },
        "lueftung": {
            "basis_rueckblick_s": values["vent_baseline_seconds"],
            "mindestens_s": values["vent_hold_seconds"],
            "mindestabfall_c": {
                c: values[f"vent_drop_{p}"] for c, p in (("3", "upper"), ("6", "lower"))
            },
        },
        "person": {
            "pruefraster_s": values["person_step_seconds"],
            "schwach_nur_nach_bestaetigter_lueftung": True,
            **{
                de: {
                    "fenster_s": values[f"{en}_window_seconds"],
                    "feuchtetrend_pp_min": {
                        c: values[f"{en}_humidity_{p}"]
                        for c, p in (("3", "upper"), ("6", "lower"))
                    },
                    "temperaturtrend_c_min": {
                        c: values[f"{en}_temperature_{p}"]
                        for c, p in (("3", "upper"), ("6", "lower"))
                    },
                    "bestaetigung_s": values[f"{en}_hold_seconds"],
                }
                for de, en in (("stark", "strong"), ("schwach", "weak"))
            },
        },
        "aufguss": {
            "fenster_s": values["infusion_window_seconds"],
            "feuchteanstieg_pp": values["infusion_humidity"],
            "temperaturaenderung_min_c": values["infusion_temperature"],
            "bestaetigung_s": values["infusion_hold_seconds"],
        },
    }
