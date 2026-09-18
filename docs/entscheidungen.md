# Entscheidungen und offene Punkte

Grundlage: Nutzerfestlegungen der Besprechung, Stand 18.09.2026. Der
[Messkandidat](kandidat.md) dokumentiert die eingefrorene Kalibrierung; das
[Gangmodell](gangmodell.md) präzisiert ihre fachliche Verarbeitung.

## Vereinbart

**Aufbau:** eigene Home-Assistant-Integration mit eigenem Thermostat und einem
zentralen Python-Ablaufkern. Messung, Erkennung, Gang/Session, Temperaturregelung,
Bedienung und Speicherung haben getrennte Zuständigkeiten.

**Sensoren:** Kanal 3 auf Kopfhöhe der obersten Bank, Kanal 6 etwa 20–30 cm
darunter. Beide sind im Normalbetrieb fest eingebunden. Bei Ausfall eines
Sensors erfolgt Weiterbetrieb mit dem verbleibenden Kanal und Fehlermeldung.
Die Temperaturen werden weder gleichgesetzt noch mit einem erfundenen festen
Höhenoffset umgerechnet. IBS wird nicht verwendet.

**Vorbereitung und Gang:** Durchlüften und anschließende Schließung können die
Personenfrüherkennung stützen. Erst das Personenmuster legt einen **vorläufigen
Gang** an, der bereits als Saunagang angezeigt und behandelt wird. Ausschließlich
ein Aufguss bestätigt den Gang. Er kann einen verpassten Start auch unmittelbar
bestätigt nachholen. Die starke/schwache Erkennung bleibt wie im Kandidaten
parametriert; nur der schwache Startpfad benötigt vorbereitendes Durchlüften.

**Türereignisse:** schnelle Türmeldungen, Durchlüftungseinordnung und Gangablauf
bleiben getrennt. Kurze Türbetätigung erhält den laufenden Gang, seine Zeitbasis
und vorhandene Aufgüsse. Erst bestätigtes Durchlüften nach Aufguss ermöglicht
den regulären Abschluss. Ein Türereignis allein belegt keinen Öffnungszweck
und keine Personenzahl.

**Zeitbezug:** Beginn ab zugeordneter Türschließung, erste Erkennung und
Aufgussbestätigung werden getrennt geführt. Der Aufguss bestätigt denselben
Gang, ohne einen neuen Beginn zu setzen. Reale Heizentscheidungen wirken ab
Erkennung; die rückwirkende Zuordnung dient Dauer und eigener Historie.

**Heizung:** engere Hysterese im eigenen Thermostat. Bereits im vorläufigen
Gang werden reguläre Hysterese- und betriebliche Ablaufabschaltungen unterdrückt.
Sicherheitsabschaltung und ausdrückliches Ausschalten bleiben übergeordnet.
Dynamische Solltemperaturnachführung innerhalb eines Gangs ist verworfen.

**Anheizen und Heizlaufzeit:** Der Sessionmerker `aufgeheizt` wird beim
temperaturbedingten Übergang zu `idle` gesetzt und innerhalb derselben Session
beibehalten. Die Heizdauer wird aus Anheizdauer (Ausgangspunkt 100 Minuten)
und späterer kürzerer Dauer gewählt. Ausreichend lange Auszeit setzt den
Heizzeitabschnitt zurück, eine kurze Unterbrechung nicht. Zwangskühlung hängt
an der Heizlaufzeit statt an jedem normalen Thermostatstopp.

**Session:** Kurzes Aus-/Wiedereinschalten erhält dieselbe Session. Ausschalten
wirkt sofort auf den Betrieb; der endgültige Sessionabschluss ist eine eigene
Entscheidung. Normale Hysteresepausen unterbrechen keine Session.

**Parameter und Anzeige:** eigene einheitliche Parameterentitäten, eine
konsumierte Quelle je Einstellungswert. Abgeleitete Werte, Dauer und Restzeiten
sind lesbare Ergebnisse. Phase und Bestätigungsstand stammen aus dem Ablaufkern.

**Daten:** gesicherter Betriebszustand und Sessionarchiv unabhängig vom Recorder.
Speicherverfahren und Datenumfang werden gesondert besprochen.

## Korrekturen früherer Annahmen

Die Gefäßentnahme um 22:39:40 ist das zusätzlich bestätigte 15. Türereignis der
Referenzsession. Nicht jede Öffnung nach Aufguss beendet einen Gang. Manuelle
Eingriffe waren einmalige Reparaturversuche, keine konkurrierenden Sollvorgaben
für die Automatik. Die frühere Zulassung normaler Hystereseabschaltungen im
Gang wurde durch die eigene zustandsabhängige Thermostatregelung ersetzt.

## Noch zu klären, in Besprechungsreihenfolge

1. **Gang und Session:** Aufhebung eines vorläufigen Gangs ohne Aufguss bei
   erneutem Durchlüften oder Fristablauf; Bestand und Zeitbasis der alten
   Zwölf-Minuten-Frist; möglicher rückwirkender Gangabschluss; Dauer einer
   zulässigen Betriebsunterbrechung und Wiederaufnahme von Gang/Nachlauf.
2. **Heizregelung:** Hysterese, kürzere Heizdauer, Rücksetz-Auszeit und Kühlpause;
   Zeitrechnung kurzer Auszeiten, Umschaltpunkt des Dauerlimits, Gangbeginn bei
   bereits laufender Kühlsperre, Nachlauf, Schutzgrenzen und Ersatztemperatur
   bei Ausfall von Kanal 3.
3. **Bedienung:** Bedeutung von `bereit`, Parametergrenzen und Einheiten,
   Änderungen bei laufender Frist, Start-/Endstufen sowie Taster, Licht, Musik
   und Benachrichtigungen.
4. **Speicherung:** Messauflösung, Speicherverfahren, Wiederanlauf nach HA-Neustart,
   Aufbewahrung, Export und Sicherung; siehe [Speicherblock](speicherung.md).

Bestandswerte der alten Automationen werden nicht stillschweigend als neue
Produktivregeln übernommen. Ein-Sensor-Tests des Kandidaten belegen keine
bereits getestete automatische Hardwarediagnose oder dynamische Umschaltung.
