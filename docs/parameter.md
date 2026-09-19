# Parameter und Entitätsrollen

Der einzige produktive Parametersatz liegt in `ConfigEntry.options`.
Config Flow, Optionsdialog, Number-Entitäten, Climate-Ziel und Panel schreiben
denselben Stand. Die `Parameters`-Validierung weist unbekannte Felder,
nicht endliche Zahlen, ungültige Vorzeichen und inkonsistente Beziehungen ab.
Heizzeitverkürzung muss kleiner als Anfangsbudget sein. Personenfenster müssen
zum Abtastraster passen. Während einer Sitzung sind nur Solltemperatur, Erhöhung je Gang und Endtemperatur änderbar. Die Änderung läuft durch denselben Ablaufkern, ohne Reload, Timerneustart oder Aufhebung einer Heizsperre. Andere Parameter und Gerätezuordnungen bleiben gesperrt, auch bei kurzem Betrieb-Aus.

## Ablauf und Darstellung

Alle notwendigen Parameter haben auf Nutzeranweisung einstellbare Standardwerte. Fehlende Werte älterer Konfigurationen werden daraus ergänzt; ausdrücklich gespeicherte Werte bleiben erhalten. Vorwarnzeit und Endtemperatur sind optional. Die Startprüfung auf gültige Temperatur und Schützrückmeldung bleibt bestehen.

| Parameter | Einheit | Ausgangswert |
|---|---|---|
| Session-Unterbrechungsfrist (`session_gap_minutes`) | min | 15 |
| Aufgussbestätigungsfrist (`confirmation_minutes`) | min | 12 |
| Heizzeit vor erster Kühlung (`heating_minutes`) | min | 90 |
| Einmalige Heizzeitverkürzung (`heating_reduction_minutes`) | min | 30 |
| Heizzeit-Rücksetz-Auszeit (`heat_reset_minutes`) | min | 10 |
| Thermostat-Cooldown (`thermostat_cooldown_minutes`) | min | 5 |
| Mindestheizzeit nach Einschalten (`minimum_heating_minutes`) | min | 10 |
| Mechanischer Ofentimer (`mechanical_timer_minutes`) | min | 240 |
| Vorwarnung Ofentimer (`mechanical_timer_warning_minutes`) | min | Optional leer |
| Zwangskühlungsdauer (`forced_cooling_minutes`) | min | 15 |
| Personenerkennung nach Türschließung abwarten (`person_wait_minutes`) | min | 4 |
| Kühlaufschub bei offener Tür (`open_door_wait_minutes`) | min | 10 |
| Nachlaufdauer (`after_run_minutes`) | min | 8 |
| Bereitschaftsaufschlag (`readiness_offset_c`) | °C | 5 |
| Bereitschaftshysterese (`readiness_hysteresis_c`) | °C | 3 |
| Erste Temperaturkachel (`preset_start_c`) | °C | 70 |
| Abstand der Temperaturkacheln (`preset_step_c`) | °C | 5 |
| Anzahl der Temperaturkacheln (`preset_count`) | Anzahl | 6 |
| Solltemperatur oben (`target_temperature_c`) | °C | 80 |
| Temperaturgrenze für Zusatzkühlung (`safety_temperature_c`) | °C | 105 |
| Auslösezeit für Zusatzkühlung (`overtemperature_minutes`) | min | 10 |
| Faktor für Zusatzkühlung (`overtemperature_cooling_factor`) | × | 2 |
| Bestätigungsfrist zentraler Ausfälle (`fault_confirmation_seconds`) | s | 60 |
| Messwert-Gültigkeitsdauer (`sensor_timeout_seconds`) | s | 180 |
| Rückmeldungsfrist (`feedback_timeout_seconds`) | s | 10 |
| Heizen oberhalb dieser Ofenleistung (`power_heating_threshold_w`) | W | 50 |
| Ofenleistung für Energieschätzung (`nominal_power_kw`) | kW | 4,5 |
| Licht bei Zwangskühlung (`cooling_brightness_percent`) | % | 5 |
| Licht beim Einschalten (`operation_brightness_percent`) | % | 35 |
| Licht im Nachlauf (`after_run_brightness_percent`) | % | 15 |
| Erhöhung je gezähltem Gang (`temperature_increase_c`) | °C | 5 |
| Endtemperatur (`final_temperature_c`) | °C | Optional leer, dann konstante Temperatur |

## Erkennungsexpertenwerte

Alle Prüfgrenzen, Mess-/Medianfenster, Haltezeiten und Positionsschwellen des
Detektors stehen im selben Katalog. Die ursprünglichen Defaults entsprechen weiterhin dem unveränderten Kandidaten; `test_detector.py` prüft diese Gleichheit. Zusätzlich wird unter standardmäßig 70 °C eine Türöffnung erkannt, wenn beide Temperaturtrends trotz durchgehend eingeschalteter Heizung mindestens 5 Sekunden unter −0,8 °C/min bleiben. Diese drei Werte sind einstellbar. Bei höherer Temperatur oder nur einer verfügbaren Messposition bleibt die ursprüngliche Regel mit Feuchteabfall maßgeblich. Die neue Regel ist anhand der aktuellen Testöffnungen kalibriert, keine unabhängige Validierung.
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
Die Watt-Schwelle beträgt standardmäßig 50 W und kann an einen optionalen Leistungsmesser angepasst werden. Messausfälle bleiben sichtbar.
Die untere Höhe wird nicht als feste Ersatztemperatur interpretiert. Ein-Sensor-
Erkennung ist davon getrennt. Einheiten und Rollen: `bindings.py`.
