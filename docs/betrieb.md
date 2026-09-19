# Betrieb, Session und Heizregelung

Stand 19.09.2026. Die folgenden Regeln sind implementiert; ausgeführte Prüfungen
und Hardwaregrenzen stehen getrennt im [Abnahmebericht](abnahme.md).

## Bedienung und Session

Physischer Eingang, Betriebsschalter, Climate-Entität und Panel bedienen denselben
Controller. Eine Binary-Sensor-Bedienquelle folgt An/Aus; neue Event-Impulse
schalten den logischen Betrieb um. Rückkehr eines ausgefallenen Eingangs startet
keinen Betrieb. Eine normale Heizpause ändert den logischen Betrieb nicht.

Ausdrückliches Aus beendet einen Gang sofort. Bei rechtzeitigem Wiedereinschalten
bleibt die Session erhalten, der beendete Gang bleibt beendet. Nach der
konfigurierten Session-Unterbrechungsfrist wird beim nächsten Einschalten eine
neue Session samt Gang-, Heizzeit-, Nachlauf-, Kühlungs- und Fristobjekten angelegt.
Übergeordnete Schutzfunktionen, Konfiguration und Archiv bleiben erhalten.
Grundparameter und Entitätszuordnung sind während der gesamten Session gesperrt.

## Temperatur und Heizzeit

Solltemperatur meint die gewünschte ungefähre Temperatur des oberen Sensors im
Gang. Bereitschaftsziel = Solltemperatur + einstellbarer Aufschlag (Standard 5 °C).
Beim erstmaligen Erreichen wird Bereitschaft gemeldet. Heizung aus am Ziel,
wieder an um die eingestellte Hysterese darunter (Standard 3 °C). Der separate
Cooldown beträgt standardmäßig 5 Minuten. Nach tatsächlichem Einschalten läuft
reguläres Heizen mindestens 10 Minuten. Diese Mindestzeit verzögert niemals
Betrieb-Aus, Nachlauf, Zwangskühlung oder technische Schutzabschaltung.

Schon ein vorläufiger Gang unterdrückt reguläre Hysterese-/Ablaufabschaltungen.
Temperaturzieländerung während einer Session ist nicht vorgesehen. Ein unterer
Messwert wird niemals durch Mittelwertbildung oder Höhenoffset zur oberen
Regeltemperatur erklärt. Kurz fehlende Pakete überbrückt nur ein noch gültiger
oberer Messwert; nach dessen Gültigkeitsende pausiert die Heizung.

Ohne unabhängige Messung zählt Heizzeit bei rückgemeldetem Schütz EIN. Eine
gültige optionale Leistungsmessung (W/kW, einstellbare Watt-Schwelle) hat Vorrang
vor optionalem binärem Heiznachweis und Schützstellung. Die Quelle und der
Schätzcharakter sind sichtbar und archiviert. Bei fehlender optionaler Messung
wird mit Fehleranzeige auf die nächste verfügbare Quelle zurückgefallen.
Unbekannte Schützstellung ohne andere Messung beweist weder Heizen noch Auszeit.
Eine genügend lange zusammenhängende rückgemeldete Auszeit setzt die lokale
Heizsumme zurück; sie ist kein Sessionwechsel und kein abgeschlossener Kühlvorgang.
Keine zusätzliche Idle-Gutschrift. Die vorgeschlagene feste Anstiegsgrenze von
0,5 °C/5 Minuten ist verworfen; die spätere Krümmungserkennung ist zurückgestellt.

Das Heizbudget beträgt zunächst standardmäßig 90 Minuten. Nach der
ersten abgeschlossenen Kühlung derselben Session wird es einmalig um 30 Minuten
verringert, danach bleibt es konstant. Mit abgeschlossenem Kühlvorgang beginnt
der nächste Heizabschnitt bei verbrauchter Zeit null. Eine neue Session erhält
das ursprüngliche Budget. Beide Vorgabewerte sind einstellbar.

## Sessionenergie

Ohne Leistungsmesser gilt gezählte Heizzeit × einstellbare Ofenleistung
(Standard 4,5 kW). Die Summe bleibt bei Kühlung und lokaler Heizzeitrücksetzung
erhalten und beginnt erst mit einer neuen Session neu. Mit gültigem Leistungsmesser
ersetzt dessen zeitliches Integral die Schätzung; auch gemessener Standbyverbrauch
gehört dazu. Der letzte Messwert gilt maximal bis zur Messgültigkeitsgrenze.
Ausfälle werden mit erkennbar geschätzten Anteilen überbrückt; ohne bekannte
Heizaktivität bleibt der betroffene Abschnitt ausdrücklich unvollständig.
Oberfläche, eigener Energiesensor und Sessionarchiv erhalten dieselben Werte.

## Nachlauf und Zwangskühlung

Wird Kühlung im Gang fällig, bleibt dieser erhalten. Danach: Gangende → Nachlauf
→ nur verbleibende Kühlung. Nachlauf hält den Ofen aus und sperrt neue Gänge.
Sein ursprüngliches Ende bleibt auch bei Aus/Ein bestehen. Die tatsächlich
verstrichene Nachlaufdauer wird genau einmal vollständig angerechnet:

`Restkühlzeit = max(0, Kühlvorgabe − angerechneter Nachlauf)`

Standard-Kühlvorgabe: einstellbare 15 Minuten. Bei Restzeit null entfällt ein
weiterer Kühlabschnitt. Laufende Zwangskühlung sperrt neue Gänge und dimmt das
zugeordnete Licht auf den eingestellten Wert; anschließend wird dessen vorheriger
Zustand wiederhergestellt. Alte Startanker aus gesperrten Phasen werden verworfen.

Eine Türöffnung beim Aufheizen/in Bereitschaft hält eine fällige Kühlung zurück:
bei offener Tür maximal standardmäßig 10 Minuten ab Öffnung; bei rechtzeitiger
Schließung anschließend standardmäßig 4 Minuten ab Schließung. Ein Personensignal
am Fristende wird noch berücksichtigt. Ohne Signal beginnt die fällige Kühlung.
Ein erkannter Gang verwendet die bestehenden Gangregeln. Aufhebung des vorläufigen
Gangs gibt die ausstehende Kühlung frei. Eine bereits laufende Kühlung wird durch
Türöffnung nicht zurückgenommen. Beide Wartewerte sind einstellbar.

## Temperatur-Zusatzkühlung und technische Fehler

Wenn die gültige obere Temperatur **länger als** standardmäßig 10 Minuten
**über** standardmäßig 105 °C liegt, wird eine doppelte konfigurierte Kühlung
fällig (Faktor standardmäßig 2). Kein Sessionabbruch. Ein laufender Gang bleibt
nach der vereinbarten Kühlreihenfolge erhalten. Nachlauf wird auf diese erhöhte
Gesamtdauer angerechnet. Vorhandene Kühlung wird auf die erhöhte Gesamtdauer
angehoben, nicht bei jedem Tick erneut verdoppelt. Unterbrechung der gültigen
Temperaturbedingung setzt deren Nachweis zurück.

Technische Schutzabschaltung erfolgt erst nach bestätigtem dauerhaftem zentralem
Ausfall. Messgültigkeit, Rückmeldungsfrist und Ausfall-Bestätigungsfrist sind
separat zu konfigurieren. Ein einzelner Sensorausfall wird sichtbar; die
Erkennung arbeitet mit der verbliebenen Höhe weiter. Bestätigte Schutzgründe
verriegeln die Heizfreigabe. Quittierung benötigt Betrieb-Aus und bestätigten
Ofen-Aus; eine bloße Session-Neuanlage löscht keine Verriegelung.

Der mechanische Ofentimer unterbricht nach Ablauf physisch die Stromversorgung.
Seine Stellung ist nicht aus HA bekannt. Die Schätzung läuft standardmäßig
4 Stunden ab Sessionbeginn, unabhängig von Thermostatpausen und kurzen
Betriebsunterbrechungen. Einstellbare Vorwarnung und Ablaufhinweis erscheinen
als HA-Benachrichtigung. Fehlende gemessene Heizleistung bei weiterhin
angezogenem Schütz pausiert den Zähler ohne technischen Abbruch. Schützstellung
bestätigt den Schaltvollzug; eine ausbleibende Schaltbestätigung oder trotz
Ausschaltbefehl weiter gemessene Heizleistung unterliegt der technischen Fehlerfrist.
Die Schätzung ist ausschließlich Anzeige und Erinnerung zum erneuten Einstellen
des Drehschalters. Ihr Ablauf verändert weder Heizbefehle noch Kühlung oder
technische Schutzregeln. Diese richten sich ausschließlich nach tatsächlichen
Eingängen und bestätigten Fehlern, unabhängig von der Timeranzeige.

Setup/HA-Neustart aktiviert keinen Betrieb. Setup und Unload senden Ofen-Aus.
Keine automatische Wiederaufnahme; historische Daten bleiben verfügbar.
