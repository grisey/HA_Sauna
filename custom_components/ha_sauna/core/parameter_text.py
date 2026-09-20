"""Gemeinsame deutsche Begriffe und Feldhilfen für Einrichtung und Oberfläche."""

PARAMETER_TEXT = {
    "session_light_minutes": (
        "Lichtnachlauf nach Sitzungsende",
        "Dauer ab dem endgültigen Sitzungsende. Danach wird das Licht ausgeschaltet. Eine neue Sitzung beendet den Lichtnachlauf. Null schaltet das Licht direkt am Sitzungsende aus.",
        "light",
    ),
    "session_light_brightness_percent": (
        "Helligkeit für „Hell“ und Lichtnachlauf",
        "Helligkeit der Lichtstufe „Hell“ und des Lichtnachlaufs nach Sitzungsende. Standard: 50 %.",
        "light",
    ),
    "temperature_increase_c": (
        "Frühere feste Temperatursteigerung",
        "Gespeicherter Wert aus älteren Versionen. Die Steigerung wird jetzt aus Starttemperatur, Endtemperatur und Verteilung berechnet.",
        "temperature",
    ),
    "final_temperature_c": (
        "Endtemperatur der Steigerung",
        "Ziel der gleichmäßigen Temperatursteigerung. Danach gilt diese Temperatur für alle weiteren Saunagänge. Bei einer Änderung bleibt das nächste Ziel erhalten; die weitere Verteilung wird angepasst.",
        "temperature_programs",
    ),
    "temperature_gangs": (
        "Verteilung der Steigerung",
        "Über wie viele Saunagänge die Temperatur vom Start- zum Endwert steigt. Danach bleibt die Endtemperatur für beliebig viele weitere Gänge erhalten. Während der Sitzung änderbar.",
        "temperature_programs",
    ),
    "program_1_start_c": (
        "Programm 1: Starttemperatur",
        "Solltemperatur vor dem ersten gezählten Saunagang.",
        "temperature_programs",
    ),
    "program_1_end_c": (
        "Programm 1: Endtemperatur",
        "Temperatur am Ende der Steigerung und für alle weiteren Saunagänge.",
        "temperature_programs",
    ),
    "program_1_gangs": (
        "Programm 1: Verteilung der Steigerung",
        "Über wie viele Saunagänge die Steigerung verteilt wird. Keine Begrenzung der tatsächlichen Gänge.",
        "temperature_programs",
    ),
    "program_2_start_c": (
        "Programm 2: Starttemperatur",
        "Solltemperatur vor dem ersten gezählten Saunagang.",
        "temperature_programs",
    ),
    "program_2_end_c": (
        "Programm 2: Endtemperatur",
        "Temperatur am Ende der Steigerung und für alle weiteren Saunagänge.",
        "temperature_programs",
    ),
    "program_2_gangs": (
        "Programm 2: Verteilung der Steigerung",
        "Über wie viele Saunagänge die Steigerung verteilt wird. Keine Begrenzung der tatsächlichen Gänge.",
        "temperature_programs",
    ),
    "target_temperature_c": (
        "Solltemperatur",
        "Gewünschte Temperatur während eines Saunagangs. Die direkte Auswahl einer Solltemperatur gilt sofort und bleibt anschließend konstant. Eine Steigerung wird separat als Programm gewählt.",
        "temperature",
    ),
    "sauna_min_temperature_c": (
        "Mindesttemperatur der Sauna",
        "Untergrenze für Soll- und Endtemperatur sowie die Temperaturschnellauswahl. Bereits gespeicherte Werte werden nicht verändert; Werte darunter werden bei einer Änderung abgewiesen.",
        "temperature",
    ),
    "readiness_offset_c": (
        "Temperaturaufschlag für Bereitschaft",
        "Für die Bereitschaft wird die Solltemperatur um diesen Wert angehoben.",
        "temperature",
    ),
    "readiness_hysteresis_c": (
        "Schaltabstand unter der Bereitschaftstemperatur",
        "Die Heizung darf wieder einschalten, wenn die Temperatur um diesen Wert unter die Bereitschaftstemperatur fällt.",
        "temperature",
    ),
    "warmup_estimation_minutes": (
        "Zeitfenster der Aufheizschätzung",
        "Die Anzeige schätzt die Restzeit nur aus einem stabilen positiven Temperaturtrend in diesem Zeitraum. Die Schätzung steuert den Ofen nicht.",
        "display",
    ),
    "thermostat_cooldown_minutes": (
        "Heizpause nach Erreichen der Bereitschaft",
        "Wartezeit bis zum erneuten Heizen nach Erreichen der Bereitschaftstemperatur.",
        "temperature",
    ),
    "minimum_heating_minutes": (
        "Mindestheizzeit nach dem Einschalten",
        "Verhindert kurze Heizintervalle. Nachlauf, Zwangskühlung, Ausschalten und Schutzabschaltung haben Vorrang.",
        "temperature",
    ),
    "session_gap_minutes": (
        "Pause bis zum Sitzungsende",
        "Nach dieser Zeit mit ausgeschaltetem Saunabetrieb endet die Saunasitzung. Vorheriges Einschalten setzt sie fort.",
        "operation",
    ),
    "confirmation_minutes": (
        "Zeit für die Aufgussbestätigung",
        "Zeit ab dem zugeordneten Gangbeginn. Ohne Aufguss wird der vorläufige Saunagang aufgehoben.",
        "operation",
    ),
    "heating_minutes": (
        "Heizzeit bis zur ersten Zwangskühlung",
        "Gezählte Heizzeit, nach der die erste Zwangskühlung fällig wird.",
        "operation",
    ),
    "heating_reduction_minutes": (
        "Verkürzung der Heizzeit nach erster Kühlung",
        "Wird nach der ersten abgeschlossenen Kühlung einmal von der Heizzeitgrenze abgezogen. Danach bleibt die Grenze gleich.",
        "operation",
    ),
    "heat_reset_minutes": (
        "Ofen-Auszeit zum Zurücksetzen der Heizzeit",
        "Nach dieser zusammenhängenden Auszeit beginnt der Heizzeitzähler wieder bei null. Die Saunasitzung und ihr Energieverbrauch bleiben erhalten.",
        "operation",
    ),
    "forced_cooling_minutes": (
        "Dauer der Zwangskühlung",
        "Zeit mit ausgeschaltetem Ofen. Ein vorausgehender Nachlauf wird vollständig angerechnet.",
        "operation",
    ),
    "after_run_minutes": (
        "Nachlauf nach einem Saunagang",
        "Ofen-Auszeit nach einem beendeten, durch Aufguss bestätigten Saunagang. Manuelles Heizen pausiert sie und ermöglicht einen neuen Gang; dieser storniert den alten Nachlauf.",
        "operation",
    ),
    "person_wait_minutes": (
        "Wartezeit nach dem Schließen der Tür",
        "Verschiebt eine fällige Zwangskühlung, damit eine eintretende Person erkannt werden kann.",
        "operation",
    ),
    "open_door_wait_minutes": (
        "Wartezeit bei offen bleibender Tür",
        "Längster Kühlaufschub ab der Türöffnung während Aufheizen oder Bereitschaft.",
        "operation",
    ),
    "light_reference_temperature_c": (
        "Referenztemperatur für das Licht",
        "Am Kaltpunkt dieser Temperatur beginnt die lineare Temperaturkurve für die Lichthelligkeit.",
        "light",
    ),
    "light_transition_seconds": (
        "Dauer des Lichtübergangs",
        "Zeit für einen sanften Wechsel der automatisch vorgegebenen Lichthelligkeit. Null schaltet ohne Übergang.",
        "light",
    ),
    "night_brightness_percent": (
        "Lichthelligkeit nachts",
        "Automatische Lichthelligkeit während der Nacht.",
        "light",
    ),
    "cooling_brightness_percent": (
        "Grundhelligkeit",
        "Helligkeit am Kaltpunkt der Temperaturkurve. Zu Beginn einer Zwangskühlung wird ebenfalls auf diesen Wert gedimmt; anschließend steigt das Licht bis zum Kühlende wieder temperaturabhängig an.",
        "light",
    ),
    "operation_brightness_percent": (
        "Lichthelligkeit tagsüber",
        "Automatische Lichthelligkeit tagsüber. Manuelle Lichtänderungen bleiben bis zum nächsten Phasenwechsel erhalten.",
        "light",
    ),
    "after_run_brightness_percent": (
        "Lichthelligkeit zu Nachlaufbeginn",
        "Auf diesen Wert wird zu Beginn des Nachlaufs gedimmt. Anschließend steigt die Helligkeit bis zum Nachlaufende wieder auf das temperaturabhängige Niveau.",
        "light",
    ),
    "safety_temperature_c": (
        "Temperaturgrenze für zusätzliche Kühlung",
        "Wird diese Temperatur anhaltend überschritten, ist eine verlängerte Zwangskühlung fällig; die Sitzung wird nicht abgebrochen.",
        "monitoring",
    ),
    "overtemperature_minutes": (
        "Dauer der Temperaturüberschreitung",
        "Die Temperaturgrenze muss länger als diese Zeit ununterbrochen überschritten sein.",
        "monitoring",
    ),
    "overtemperature_cooling_factor": (
        "Verlängerung der Kühlzeit bei Übertemperatur",
        "Faktor für die eingestellte Kühlzeit. Der Wert 2 bedeutet doppelte Kühlzeit.",
        "monitoring",
    ),
    "sensor_timeout_seconds": (
        "Höchstalter eines Messwerts",
        "Nach dieser Zeit ohne neue Meldung gilt ein Sensorwert als veraltet. Der Standard beträgt 180 Sekunden. Die Frist muss die normalen Meldeabstände der Sensoren abdecken; ein zu kurzer Wert verursacht unnötige Heizunterbrechungen. Fehlt ein gültiger oberer Temperaturwert, pausiert die Heizung sofort.",
        "monitoring",
    ),
    "feedback_timeout_seconds": (
        "Wartezeit auf die Schützrückmeldung",
        "Zeit, in der der Schütz einen Ein- oder Ausschaltbefehl bestätigen muss.",
        "monitoring",
    ),
    "fault_confirmation_seconds": (
        "Dauer bis zur bestätigten Störung",
        "Ein zentraler technischer Fehler muss so lange durchgehend bestehen, bevor die Schutzabschaltung verriegelt. Ein fehlender gültiger oberer Temperaturwert pausiert die Heizung bereits vorher. Solange keine Verriegelung vorliegt, kann die Heizung mit Rückkehr eines gültigen Werts normal weiterarbeiten.",
        "monitoring",
    ),
    "mechanical_timer_minutes": (
        "Laufzeit des mechanischen Ofentimers",
        "Geschätzte Laufzeit bei eingeschaltetem Saunabetrieb und eingeschaltetem Schütz. Bei ausgeschaltetem oder nicht verfügbarem Schütz hält die Anzeige an, auch während Heizpause, Nachlauf und Kühlung. Nach einer beendeten Sitzung mit gezählten Saunagängen beginnt sie beim nächsten Start neu. Die Anzeige schaltet nichts.",
        "timer",
    ),
    "mechanical_timer_warning_minutes": (
        "Erinnerung vor Ablauf des Ofentimers",
        "So lange vor dem geschätzten Ablauf erscheint eine Erinnerung zum Einstellen des Drehschalters. Leer lassen, wenn keine Vorwarnung gewünscht ist.",
        "timer",
    ),
    "nominal_power_kw": (
        "Ofenleistung für die Verbrauchsschätzung",
        "Nennleistung des Ofens in kW. Ohne Leistungsmessung wird daraus und aus der Heizzeit der Verbrauch in kWh berechnet.",
        "timer",
    ),
    "power_heating_threshold_w": (
        "Leistungsschwelle für das Heizen",
        "Nur bei zugeordnetem Leistungssensor: Oberhalb dieser Leistung zählt die Zeit als Heizzeit. Standby bleibt beim Energieverbrauch berücksichtigt.",
        "timer",
    ),
    "manual_override_minutes": (
        "Höchstdauer manueller Übersteuerungen",
        "Spätestens nach dieser Zeit folgen Ofen und Licht wieder der Automatik. Der vereinbarte Phasen- oder Schaltwechsel kann die Übersteuerung früher beenden. Im Betriebsmodus Manuell gilt keine Frist.",
        "operation",
    ),
    "button_hold_seconds": (
        "Langdruckdauer des Saunatasters",
        "So lange muss ein binärer Saunataster gedrückt bleiben, um die Sitzung zu beenden.",
        "operation",
    ),
    "button_hold_brightness_percent": (
        "Lichthelligkeit bei Langdruck",
        "Helligkeit zur Bestätigung des Sitzungsendes, solange der Saunataster noch gedrückt ist. Beim Loslassen beginnt der normale Lichtnachlauf.",
        "light",
    ),
    "preset_start_c": (
        "Niedrigste Temperatur der Schnellauswahl",
        "Solltemperatur der ersten Taste in der Übersicht.",
        "display",
    ),
    "preset_step_c": (
        "Temperaturabstand der Schnellauswahl",
        "Abstand zwischen zwei benachbarten Temperaturtasten.",
        "display",
    ),
    "preset_count": (
        "Anzahl der Temperaturtasten",
        "Anzahl der sichtbaren Tasten für die Solltemperatur.",
        "display",
    ),
    "grid_seconds": (
        "Zeitabstand der Auswertung",
        "Das feste Ein-Sekunden-Raster gehört zum geprüften Erkennungsverfahren.",
        "detection",
    ),
    "median_seconds": (
        "Glättungszeit der Messwerte",
        "Zeitfenster für den Median. Es glättet einzelne Messausreißer vor der Erkennung.",
        "detection",
    ),
    "door_window_seconds": (
        "Zeitfenster für den Temperaturtrend an der Tür",
        "Zeitraum für den Temperaturtrend zur Erkennung einer Türöffnung oder Türschließung.",
        "detection",
    ),
    "door_humidity_seconds": (
        "Zeitfenster für die Luftfeuchteänderung an der Tür",
        "Zeitraum für den ergänzenden Luftfeuchteabfall bei einer Türöffnung.",
        "detection",
    ),
    "door_open_slope": (
        "Temperaturtrend bei Türöffnung",
        "Die Temperatur muss mindestens so stark fallen. Negative Werte bezeichnen eine Abkühlung.",
        "detection",
    ),
    "door_open_humidity_upper": (
        "Luftfeuchteabfall bei Türöffnung oben",
        "Mindestabfall am oberen Sensor in Prozentpunkten; wird zusammen mit dem Temperaturtrend bewertet.",
        "detection",
    ),
    "door_open_humidity_lower": (
        "Luftfeuchteabfall bei Türöffnung unten",
        "Mindestabfall am unteren Sensor in Prozentpunkten; wird zusammen mit dem Temperaturtrend bewertet.",
        "detection",
    ),
    "door_open_hold_seconds": (
        "Bestätigungsdauer der Türöffnung",
        "So lange müssen die Bedingungen für eine offene Tür bestehen.",
        "detection",
    ),
    "door_heating_slope": (
        "Türöffnung: Temperaturabfall trotz eingeschalteter Heizung",
        "Zusätzliche Erkennung ohne Feuchteabfall: Beide Temperaturtrends müssen unter diesem Wert liegen, während die Heizung durchgehend eingeschaltet ist. Bei nur einer verfügbaren Messposition bleibt die bisherige Regel mit Luftfeuchte maßgeblich.",
        "detection",
    ),
    "door_heating_hold_seconds": (
        "Bestätigungsdauer des Temperaturabfalls beim Heizen",
        "So lange muss der zusätzliche Temperaturabfall an beiden Messpositionen gleichzeitig bestehen. Eine Heizabschaltung verwirft diesen Nachweis.",
        "detection",
    ),
    "door_heating_max_temperature_c": (
        "Temperaturgrenze der ergänzenden Türerkennung",
        "Nur solange beide geglätteten Temperaturen unter diesem Wert liegen, darf die zusätzliche Regel ohne Feuchteabfall auslösen. Im heißen Bereich bleibt die Regel mit Temperatur- und Feuchteabfall maßgeblich. Null schaltet die Ergänzung aus.",
        "detection",
    ),
    "door_close_slope": (
        "Temperaturtrend bei Türschließung",
        "Mindestanstieg der Temperatur für die Erkennung einer geschlossenen Tür.",
        "detection",
    ),
    "door_close_hold_seconds": (
        "Bestätigungsdauer der Türschließung",
        "So lange muss die Bedingung für eine geschlossene Tür bestehen.",
        "detection",
    ),
    "vent_baseline_seconds": (
        "Vergleichszeit vor dem Lüften",
        "Rückblick für die Temperatur vor der Türöffnung.",
        "detection",
    ),
    "vent_hold_seconds": (
        "Mindestdauer des Durchlüftens",
        "So lange muss die Tür offen sein, bevor Durchlüften bestätigt werden kann.",
        "detection",
    ),
    "vent_drop_upper": (
        "Temperaturabfall beim Durchlüften oben",
        "Mindestabfall am oberen Sensor gegenüber der Temperatur vor der Öffnung.",
        "detection",
    ),
    "vent_drop_lower": (
        "Temperaturabfall beim Durchlüften unten",
        "Mindestabfall am unteren Sensor gegenüber der Temperatur vor der Öffnung.",
        "detection",
    ),
    "vent_absolute_humidity_loss_percent": (
        "Absoluter Wasserverlust beim Durchlüften",
        "Bei zwei gültigen Messpositionen muss der Wassergehalt der Luft an beiden Positionen gegenüber dem gemeinsamen Referenzpaar vor der Türöffnung mindestens um diesen Anteil sinken. Im Ein-Sensor-Betrieb bleibt zusätzlich die Mindestdauer maßgeblich.",
        "detection",
    ),
    "person_step_seconds": (
        "Zeitabstand der Personenprüfung",
        "Abstand zwischen zwei Auswertungen der Personenbedingungen.",
        "detection",
    ),
    "strong_window_seconds": (
        "Zeitfenster für deutliche Personensignale",
        "Zeitraum für Temperatur- und Luftfeuchtetrends bei deutlichen Hinweisen auf Personen.",
        "detection",
    ),
    "strong_humidity_upper": (
        "Luftfeuchtetrend bei deutlichem Personensignal oben",
        "Mindestanstieg am oberen Sensor in Prozentpunkten pro Minute.",
        "detection",
    ),
    "strong_humidity_lower": (
        "Luftfeuchtetrend bei deutlichem Personensignal unten",
        "Mindestanstieg am unteren Sensor in Prozentpunkten pro Minute.",
        "detection",
    ),
    "strong_temperature_upper": (
        "Temperaturtrend bei deutlichem Personensignal oben",
        "Untergrenze des Temperaturtrends am oberen Sensor für diesen Erkennungspfad.",
        "detection",
    ),
    "strong_temperature_lower": (
        "Temperaturtrend bei deutlichem Personensignal unten",
        "Untergrenze des Temperaturtrends am unteren Sensor für diesen Erkennungspfad.",
        "detection",
    ),
    "strong_hold_seconds": (
        "Bestätigungsdauer deutlicher Personensignale",
        "So lange müssen die Bedingungen für ein deutliches Personensignal bestehen.",
        "detection",
    ),
    "weak_window_seconds": (
        "Zeitfenster für schwache Personensignale",
        "Längerer Vergleichszeitraum für schwache Personensignale nach bestätigtem Durchlüften.",
        "detection",
    ),
    "weak_humidity_upper": (
        "Luftfeuchtetrend bei schwachem Personensignal oben",
        "Mindestanstieg am oberen Sensor in Prozentpunkten pro Minute.",
        "detection",
    ),
    "weak_humidity_lower": (
        "Luftfeuchtetrend bei schwachem Personensignal unten",
        "Mindestanstieg am unteren Sensor in Prozentpunkten pro Minute.",
        "detection",
    ),
    "weak_temperature_upper": (
        "Temperaturtrend bei schwachem Personensignal oben",
        "Untergrenze des Temperaturtrends am oberen Sensor nach dem Durchlüften.",
        "detection",
    ),
    "weak_temperature_lower": (
        "Temperaturtrend bei schwachem Personensignal unten",
        "Untergrenze des Temperaturtrends am unteren Sensor nach dem Durchlüften.",
        "detection",
    ),
    "weak_hold_seconds": (
        "Bestätigungsdauer schwacher Personensignale",
        "So lange müssen die Bedingungen für ein schwaches Personensignal bestehen.",
        "detection",
    ),
    "infusion_window_seconds": (
        "Zeitfenster für die Aufgusserkennung",
        "Zeitraum, in dem Luftfeuchteanstieg und Temperaturänderung eines Aufgusses bewertet werden.",
        "detection",
    ),
    "infusion_humidity": (
        "Luftfeuchteanstieg bei einem Aufguss",
        "Mindestanstieg in Prozentpunkten innerhalb des Aufgussfensters.",
        "detection",
    ),
    "infusion_temperature": (
        "Temperaturänderung bei einem Aufguss",
        "Untergrenze der Temperaturänderung innerhalb des Aufgussfensters.",
        "detection",
    ),
    "infusion_hold_seconds": (
        "Bestätigungsdauer des Aufgusssignals",
        "So lange müssen die Aufgussbedingungen bestehen. Dies ist nicht die Wartefrist eines vorläufigen Saunagangs.",
        "detection",
    ),
}
