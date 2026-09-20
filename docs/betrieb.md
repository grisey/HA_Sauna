# Betrieb, Session und Heizregelung

Stand 20.09.2026. Ausgeführte Prüfungen
und Hardwaregrenzen stehen getrennt im [Abnahmebericht](abnahme.md).

## Bedienung und Session

Physischer Eingang, Betriebsschalter, Climate-Entität und Panel bedienen denselben
Controller. Ein als dauerhafter Schalter konfigurierter Eingang folgt seiner
Ein-/Ausstellung. Ein Taster startet bei ausgeschaltetem Betrieb mit dem
hinterlegten Temperaturprogramm im Automatikbetrieb. Im laufenden Betrieb wechselt ein kurzer Druck
zwischen direkter Ofenübersteuerung und Rückkehr zur Automatik. Langes Drücken
beendet die Sitzung und schaltet den Ofen aus; das Licht bestätigt dies bis zum
Loslassen mit standardmäßig 1 %. Anschließend beginnt der Lichtnachlauf.
Eine zum Start verwendete Betätigung kann dieselbe Sitzung nicht wieder beenden.
Rückkehr eines ausgefallenen Eingangs startet keinen Betrieb. Eine normale
Heizpause ändert den logischen Betrieb nicht.

Ausdrückliches Aus beendet einen Gang sofort. Bei rechtzeitigem Wiedereinschalten
bleibt die Session erhalten, der beendete Gang bleibt beendet. Nach der
konfigurierten Session-Unterbrechungsfrist wird beim nächsten Einschalten eine
neue Session samt Gang-, Heizzeit-, Nachlauf-, Kühlungs- und Fristobjekten angelegt.
Übergeordnete Schutzfunktionen, Konfiguration und Archiv bleiben erhalten.
Während einer Sitzung sind Solltemperatur, Endtemperatur und Temperaturverteilung
änderbar. Weitere Grundparameter und Entitätszuordnung bleiben gesperrt.

Die Betriebsart **Manuell** wird nur zwischen abgeschlossenen Sitzungen gewählt.
Ofen und Licht folgen dort der manuellen Bedienung. Messung, Archivierung,
technische Schutzabschaltung und bestätigte Übertemperatur bleiben wirksam;
Thermostat, reguläre Heizzeit-Kühlpausen und automatische Lichtwechsel entfallen.
Eine bestätigte Übertemperatur beendet die manuelle Heizanforderung und löst
die zusätzliche Kühlung aus. Danach ist eine neue manuelle Heizwahl erforderlich.
Die vorübergehende Übersteuerung im Automatikbetrieb bleibt davon getrennt.
Sie endet spätestens nach der eingestellten Übersteuerungsdauer, standardmäßig
zehn Minuten. Phasenwechsel, ein neuer automatischer Heiz-Schaltbefehl oder die
ausdrückliche Rückgabe an die Automatik können die Ofenübersteuerung früher
beenden. Für Licht gilt als früherer Rückkehrpunkt der Phasenwechsel. Messwerte
und Statusabfragen verlängern keine Übersteuerung. Die eigenständige Betriebsart
Manuell hat keine solche Zeitbegrenzung.

## Temperatur und Heizzeit

Solltemperatur meint die gewünschte ungefähre Temperatur des oberen Sensors im
Gang. Bereitschaftsziel = Solltemperatur + einstellbarer Aufschlag (Standard 5 °C).
Beim erstmaligen Erreichen wird Bereitschaft gemeldet. Heizung aus am Ziel,
wieder an um die eingestellte Hysterese darunter (Standard 3 °C). Der separate
Cooldown beträgt standardmäßig 5 Minuten. Nach tatsächlichem Einschalten läuft
reguläres Heizen mindestens 10 Minuten. Diese Mindestzeit verzögert niemals
Betrieb-Aus, Nachlauf, Zwangskühlung oder technische Schutzabschaltung.

Schon ein vorläufiger Gang unterdrückt reguläre Hysterese-/Ablaufabschaltungen.
Eine direkte Solltemperaturwahl gilt sofort und bedeutet konstantes Heizen.
Die Temperaturautomatik verteilt Start- und Endwert gleichmäßig über die
eingestellte Anzahl von Temperaturstufen. Nach Erreichen des Endwerts bleibt
dieser für weitere Gänge erhalten. Wird nur der Endwert geändert, bleibt das
nächste Ziel erhalten; die verbleibenden Steigerungen werden neu verteilt.
Bereits gezählte Gänge werden nicht erneut addiert. Änderungen
erhalten laufende Gänge, Fristen, Mindestheizzeit, Kühlung und Schutz. Ein unterer
Messwert wird niemals durch Mittelwertbildung oder Höhenoffset zur oberen
Regeltemperatur erklärt. Kurz fehlende Pakete überbrückt nur ein noch gültiger
oberer Messwert; nach dessen Gültigkeitsende pausiert die Heizung. Die konfigurierte
Gültigkeitsdauer muss die normalen Meldeabstände abdecken. Sonst erzeugt eine
gewöhnliche Meldepause einen tatsächlichen Aus-/Ein-Zyklus und damit eine neue
Mindestheizzeit. Die separate Fehler-Bestätigungsfrist betrifft die spätere
Verriegelung; sie verlängert die Messwertgültigkeit nicht. Der Standard bleibt
180 Sekunden; gespeicherte abweichende Einstellungen bleiben erhalten.

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
→ nur verbleibende Kühlung. Nachlauf hält den Ofen im automatischen Ablauf aus
und sperrt neue Gänge. Ein manueller Heizstart per Taster oder Detailbedienung
pausiert den Nachlauf und gibt den Beginn eines neuen Gangs frei.
Eine Betriebsunterbrechung verwirft den Nachlauf nicht. Bei manuellem Heizen
pausiert seine Uhr. Beginnt dabei kein Gang, läuft die Restzeit nach Rückkehr
zur Automatik weiter. Bei einem neuen Gang wird der bisherige Nachlauf storniert;
nach diesem Gang beginnt ein neuer vollständiger Nachlauf.
Seine tatsächlich gezählte Dauer wird genau einmal auf die
nächste Kühlung angerechnet, auch wenn diese erst später fällig wird:

`Restkühlzeit = max(0, Kühlvorgabe − angerechneter Nachlauf)`

Standard-Kühlvorgabe: einstellbare 15 Minuten. Bei Restzeit null entfällt ein
weiterer Kühlabschnitt. Laufende Zwangskühlung sperrt reguläre Gangstarts.
Manuelle Ofenübersteuerung im Automatikbetrieb und ein dadurch möglicher Gang
pausieren die Kühlung. Nach dessen Nachlauf setzt die verbleibende Kühlzeit fort.
Reicht das Heizbudget beim Gangende nicht mehr für die Mindestheizdauer, folgt
die Kühlung unmittelbar auf den Nachlauf. Ein unnötiges Zwischenheizen entfällt.

Die Lichthelligkeit steigt linear mit der Temperatur: standardmäßig von 5 %
bei 30 °C bis zur Bereitschaftshelligkeit. Diese beträgt tagsüber 40 % und
nachts 25 %; während der bürgerlichen Dämmerung erfolgt der gleitende Wechsel.
Ein laufender Gang hält die Bereitschaftshelligkeit auch bei Temperaturabfall.
Nachlauf und Kühlung dimmen innerhalb von standardmäßig 30 Sekunden auf 15 %
beziehungsweise den Grundlichtwert 5 %. Bis zum jeweiligen Timerende steigt
die Helligkeit wieder zum temperaturabhängigen Ziel. Alle Ausgangswerte und
die Übergangsdauer sind einstellbar. Eine manuelle Lichtwahl bleibt bis zum
nächsten Phasenwechsel erhalten, höchstens bis zum Ende der eingestellten
Übersteuerungsdauer. Reine Temperaturänderungen beenden sie nicht.
Dies gilt auch für direkt mit dem Licht verbundene Taster: Ausschalten und
Dimmen werden als manuelle Lichtwahl übernommen. Unveränderte Rückmeldungen
verlängern die Übersteuerung nicht; eigene Lichtbefehle der Integration lösen
keine manuelle Wahl aus.
Ein reiner Lichtfehler wird gemeldet und beendet nicht den Heizbetrieb.

Eine Türöffnung beim Aufheizen/in Bereitschaft hält eine fällige Kühlung zurück:
bei offener Tür maximal standardmäßig 10 Minuten ab Öffnung; bei rechtzeitiger
Schließung anschließend standardmäßig 4 Minuten ab Schließung. Ein Personensignal
am Fristende wird noch berücksichtigt. Ohne Signal beginnt die fällige Kühlung.
Ein erkannter Gang verwendet die bestehenden Gangregeln. Aufhebung des vorläufigen
Gangs gibt die ausstehende Kühlung frei. Eine bereits laufende Kühlung wird durch
Türöffnung nicht zurückgenommen. Beide Wartewerte sind einstellbar.

Nach dem endgültigen Sitzungsende folgt ein eigener Lichtnachlauf: standardmäßig
10 Minuten bei 50 %, anschließend Licht aus. Er beginnt nach Ablauf der Pause
bis zum Sitzungsende, nicht bei einer kurzen Betriebsunterbrechung. Dauer und
Helligkeit sind einstellbar; Dauer null bedeutet direktes Ausschalten am Sitzungsende.
Eine neue Sitzung beendet den Lichtnachlauf und verwendet wieder das Betriebslicht.
Die Lichtfrist verändert keine Ofenbefehle. Ihr Endzeitpunkt bleibt beim Übernehmen
von Grundeinstellungen erhalten; eine neue Lichtzuordnung beendet sie am bisherigen
Gerät. Fehler beim Einschalten oder Ausschalten erscheinen in Anzeige und Protokoll.

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
4 Stunden bei eingeschaltetem Saunabetrieb und bestätigtem Schütz-Ein. Bei
Schütz-Aus oder unbekannter Schützstellung hält sie an; dies gilt auch für
Thermostatpause, Nachlauf und Zwangskühlung. Betriebsunterbrechungen halten
die Anzeige ebenfalls an. Einstellbare Vorwarnung und Ablaufhinweis erscheinen
als HA-Benachrichtigung. Fehlende gemessene Heizleistung bei weiterhin
angezogenem Schütz pausiert den Heizzähler ohne technischen Abbruch. Die
mechanische Timeranzeige folgt weiterhin der Schützstellung, unabhängig von
der optionalen Heizleistungsmessung. Schützstellung
bestätigt den Schaltvollzug; eine ausbleibende Schaltbestätigung oder trotz
Ausschaltbefehl weiter gemessene Heizleistung unterliegt der technischen Fehlerfrist.
Die Schätzung ist ausschließlich Anzeige und Erinnerung zum erneuten Einstellen
des Drehschalters. Ihr Ablauf verändert weder Heizbefehle noch Kühlung oder
technische Schutzregeln. Diese richten sich ausschließlich nach tatsächlichen
Eingängen und bestätigten Fehlern, unabhängig von der Timeranzeige.

Setup/HA-Neustart aktiviert keinen Betrieb. Setup und Unload senden Ofen-Aus.
Keine automatische Wiederaufnahme; historische Daten bleiben verfügbar.


## Ergänzungen zur Bedienung vom 19.09.2026

Die mechanische Timeranzeige hält beim Ausschalten des Saunabetriebs an. Ein
kurzer Test oder eine beendete Sitzung ohne gezählte Saunagänge behält die Restzeit.
Nach einer beendeten Sitzung mit gezählten Gängen beginnt die Anzeige erst beim
nächsten Einschalten wieder mit der eingestellten Gesamtdauer. Bei ausgeschaltetem
Schütz bleiben Restzeit und Timeridentität erhalten; erst seine bestätigte
Einschaltung setzt die Anzeige fort. Optionsänderungen erhalten die angehaltene Anzeige; ein vollständiger
HA-Neustart stellt keinen Laufzeitzustand wieder her. Der Timer bleibt rein informativ.

In der Detailansicht können laufender Nachlauf und laufende Zwangskühlung einzeln
manuell beendet werden. Es gilt derselbe Folgeablauf wie beim jeweiligen Fristende,
mit dem tatsächlichen Bedienzeitpunkt als Ende. Beim Nachlauf wird nur die bis
dahin verstrichene Dauer angerechnet; eine folgende Restkühlung bleibt bestehen.
Beendete Zwangskühlung schließt den Kühlzyklus ab und beginnt den nächsten
Heizabschnitt mit dem regulären Folgebudget. Betrieb-Aus, Thermostat und technische
Schutzsperren bleiben wirksam. Die Bedienung steht im INFO-Protokoll und im Archiv.
Ein alter oder wiederholter Klick darf keine spätere Phase beenden.

Nur bestätigte, beendete Gänge erhöhen die Temperatur. Vorläufige oder aufgehobene
Gänge verändern die Stufe nicht. Neue Sitzungen beginnen wieder beim Startwert;
kurzes Aus-/Einschalten erhält die erreichte Stufe. Bereitschaftsaufschlag und
Hysterese beziehen sich auf die aktuell berechnete Solltemperatur. Nachlauf und
Kühlung behalten Vorrang. Erhöhte Bereitschaft muss zunächst erreicht werden.

Ein Start mit fehlenden notwendigen Einstellungen, ohne gültige Regeltemperatur
oder ohne Schützrückmeldung erzeugt keine Sitzung. Die Einrichtung bleibt damit
zugänglich. Sensor-, Geräte- und Ablaufänderungen sind nach Sitzungsende möglich.
