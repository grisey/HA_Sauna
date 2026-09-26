# Ofenkühlung – geltender Stand vom 26.09.2026

Dieser Auftrag ersetzt alle älteren Regeln zur eigenständigen Zwangskühlung,
zu Heizbudgets, Wiederanlauf-Restbudgets, Temperatur-Zusatzkühlung und zur
Anrechnung von Nachläufen. Die älteren Beschreibungen sind dafür historisch.

Nach einem bisher nachlaufberechtigten Gangende läuft genau eine **Ofenkühlung**
mit dem gespeicherten Wert `after_run_minutes`. Ihre Dauer wird zentral durch
`Controller.oven_cooling_duration_seconds()` ermittelt; vorerst liefert diese
Stelle ausschließlich die konfigurierte Dauer. Während dieser Phase bleibt der
Ofen aus, danach übernimmt die bisherige Temperaturregelung. Betriebsunterbrechung,
manuelle Pause, Fortsetzung, Restzeit, manuelles Beenden, bestehende manuelle
Wiedereinstiegsausnahmen und Lichtwirkung bleiben erhalten. Keine Rest- oder
Zwangskühlung folgt. Technische Schlüssel `after_run` und `nachlauf` bleiben
kompatibel; der separate Lichtnachlauf nach Sitzungsende bleibt unverändert.

Veraltete Kühlparameter werden beim Einlesen bestehender Einstellungen ignoriert.
Historische Kühlzyklen bleiben im Archiv lesbar, lösen aber keine aktive Steuerung
aus. `cooling_brightness_percent` bleibt für die normale temperaturabhängige
Lichtkurve erhalten; `heat_reset_minutes` bleibt Teil der Heizzeitbuchhaltung.
Heizzeitbuchhaltung, Energiezählung, mechanischer Timer, Temperaturprogramm,
Hysterese, Mindestheizzeit, Thermostatpause und technische Schutzsperren bleiben
unverändert. Ebenso unverändert bleiben die Erkennungsalgorithmen und die
Gangzuordnung; nur die gegenstandslose eigenständige Kühlsperre entfällt.

## Spätere dynamische Dauer: noch keine Bemessung

Datengrundlage ist die Schnittmenge **Bereitschaftsphase ∩ Betrieb eingeschaltet ∩
tatsächlich rückgemeldeter Schütz AUS**. Bereitschaft umfasst Heiz- und
Idle-Intervalle. Unbekannte Schützstellung bestätigt keine Auszeit; Ofenkühlung
und Betrieb-Aus zählen nicht als Bereitschafts-Idle. Vorhandene Phasenzeitstempel
(`phase` im Archiv) und Aktorrückmeldungen (`source_state`, `heating_observation`
mit Schützstellung) sind die Quellen. Es gibt keinen zweiten unabhängigen
Heizzeit- oder Pausenzähler und keine vorweggenommene Formel.

Offen bleiben:

1. Welcher vorausgehende Zeitraum berücksichtigt wird.
2. Welche Dauer und Verteilung der Bereitschaftspausen ausreicht.
3. Wie daraus die Ofenkühldauer folgt und wann sie aktualisiert wird.

Alte Heizbudgets, Rücksetzfristen und Zwangskühlzeiten werden nicht als Kriterien
übernommen. Tür-Heizanforderung, Änderung der Gang-Heizwirkung, Präsenzsensoren,
allgemeine Phasenhistorienreparatur und Neukalibrierung sind nicht Teil der Änderung.
