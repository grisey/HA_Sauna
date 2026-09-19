# Parameter und Entitätsrollen

Der einzige produktive Parametersatz liegt in `ConfigEntry.options`.
Config Flow, Optionsdialog, Number-Entitäten, Climate-Ziel und Panel schreiben
denselben Stand. Die `Parameters`-Validierung weist unbekannte Felder,
nicht endliche Zahlen, ungültige Vorzeichen und inkonsistente Beziehungen ab.
Heizzeitverkürzung muss kleiner als Anfangsbudget sein. Personenfenster müssen
zum Abtastraster passen. Alle Änderungen sind während einer Session gesperrt.

## Ablauf und Darstellung

Die folgenden Zahlen sind veränderbare Defaults. „Einzugeben“ bedeutet, dass
kein Ausgangswert vereinbart wurde. Ein optional leerer Schutz-/Regelungswert
stellt keine Heizfreigabe dar: benötigte fehlende Werte werden als Sperre gemeldet.

| Parameter | Einheit | Ausgangswert |
|---|---|---|
| Session-Unterbrechungsfrist (`session_gap_minutes`) | min | Einzugeben |
| Aufgussbestätigungsfrist (`confirmation_minutes`) | min | Einzugeben |
| Heizzeit vor erster Kühlung (`heating_minutes`) | min | 90 |
| Einmalige Heizzeitverkürzung (`heating_reduction_minutes`) | min | 30 |
| Heizzeit-Rücksetz-Auszeit (`heat_reset_minutes`) | min | Einzugeben |
| Thermostat-Cooldown (`thermostat_cooldown_minutes`) | min | 5 |
| Mindestheizzeit nach Einschalten (`minimum_heating_minutes`) | min | 10 |
| Mechanischer Ofentimer (`mechanical_timer_minutes`) | min | 240 |
| Vorwarnung Ofentimer (`mechanical_timer_warning_minutes`) | min | Optional leer |
| Zwangskühlungsdauer (`forced_cooling_minutes`) | min | 15 |
| Personenerkennung nach Türschließung abwarten (`person_wait_minutes`) | min | 4 |
| Kühlaufschub bei offener Tür (`open_door_wait_minutes`) | min | 10 |
| Nachlaufdauer (`after_run_minutes`) | min | Einzugeben |
| Bereitschaftsaufschlag (`readiness_offset_c`) | °C | 5 |
| Bereitschaftshysterese (`readiness_hysteresis_c`) | °C | 3 |
| Erste Temperaturkachel (`preset_start_c`) | °C | 70 |
| Abstand der Temperaturkacheln (`preset_step_c`) | °C | 5 |
| Anzahl der Temperaturkacheln (`preset_count`) | Anzahl | 6 |
| Solltemperatur oben (`target_temperature_c`) | °C | Optional leer |
| Temperaturgrenze für Zusatzkühlung (`safety_temperature_c`) | °C | 105 |
| Auslösezeit für Zusatzkühlung (`overtemperature_minutes`) | min | 10 |
| Faktor für Zusatzkühlung (`overtemperature_cooling_factor`) | × | 2 |
| Bestätigungsfrist zentraler Ausfälle (`fault_confirmation_seconds`) | s | Optional leer |
| Messwert-Gültigkeitsdauer (`sensor_timeout_seconds`) | s | Optional leer |
| Rückmeldungsfrist (`feedback_timeout_seconds`) | s | Optional leer |
| Heizen oberhalb dieser Ofenleistung (`power_heating_threshold_w`) | W | Nur bei Leistungsmesser einzugeben |
| Ofenleistung für Energieschätzung (`nominal_power_kw`) | kW | 4,5 |
| Licht bei Zwangskühlung (`cooling_brightness_percent`) | % | Optional leer |

## Erkennungsexpertenwerte

Alle Prüfgrenzen, Mess-/Medianfenster, Haltezeiten und Positionsschwellen des
Detektors stehen im selben Katalog. Die Defaults entsprechen exakt dem
unveränderten Kandidaten; `test_detector.py` prüft die Gleichheit.
`core/detection_parameters.py` beschreibt Einheiten, technische Grenzen und
Ganzzahlvorgaben. Das Ein-Sekunden-Grundraster ist eine feste Voraussetzung des
übernommenen Algorithmus, keine zweite frei kombinierbare Abtastregel.

Eine adaptive relative Erkennung ist lediglich ein früher diskutierter Vorschlag
und wird nicht eingeführt. Experteneinstellungen ändern den bestehenden kausalen
Detektor; die Kontrollansicht zeichnet seine tatsächlichen Merkmale auf.

## Externe Entitäten

Temperatur und Luftfeuchte oben/unten, Heizaktor (Switch), Bedienquelle
(Event oder Binary Sensor) und dimmbares Licht werden über HA-Selektoren gewählt.
Leistungssensor (Geräteklasse power, W oder kW), unabhängiger binärer Heiznachweis
und Statusquellen sind optional. Ohne Leistungsmesser zählt die Schützstellung.
Eine mit dem Heizaktor identische binäre Rückmeldequelle gilt ebenfalls als Schätzung.
Metadatenprüfung kontrolliert Domain, Geräteklasse, Einheit und Dimmbarkeit.
Obere und untere Quellen derselben Messgröße dürfen nicht identisch sein.
Konkrete Gerätebezeichnungen/Entity-IDs stehen nicht im Produktivcode.

Vor Start braucht die Regelung insbesondere gültige obere Temperatur,
Solltemperatur, Messgültigkeit, Schütz-Rückmeldungsfrist und Fehler-Bestätigungsfrist.
Bei ausgewähltem Leistungsmesser ist zusätzlich die Watt-Schwelle einzustellen;
es gibt keinen erfundenen Standby-Grenzwert. Fehlende Werte sind sichtbar.
Die untere Höhe wird nicht als feste Ersatztemperatur interpretiert. Ein-Sensor-
Erkennung ist davon getrennt. Einheiten und Rollen: `bindings.py`.
