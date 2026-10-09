# Saunabetrieb

## Sitzung und Bedienung

### Sitzung

Betrieb-EIN beginnt eine Sitzung. Betrieb-AUS beendet den offenen Gang und
startet die gemeinsame Wiederaufnahme- und Lichtnachlauffrist. Einschalten
innerhalb dieser Frist setzt die Sitzung fort und beendet den Lichtnachlauf;
danach beginnt eine neue Sitzung. Ein langer Enddruck schließt die Sitzung sofort.

Nach einem Home-Assistant-Neustart bleibt das [Archiv](speicherung.md) erhalten.
Der Ofen startet erst durch ausdrückliches Einschalten.

### Bedienhandlungen und Betriebsart

| Tastergeste | Wirkung |
| --- | --- |
| Kurz bei Betrieb-AUS | Startet Automatik mit der gespeicherten Tastervorgabe. |
| Kurz in Automatik | Wechselt die vorübergehende Ofenwahl oder gibt an die Automatik zurück. |
| Kurz in Manuell | Wechselt die Ofenvorgabe zwischen EIN und AUS. |
| Lang bei laufendem Betrieb | Beendet Betrieb und Sitzung. Licht bleibt beim Halten AUS; Loslassen startet den Lichtnachlauf. |
| Lang bei Betrieb-AUS | Kein Saunastart, auch nicht durch zugehöriges Loslassen. |

**Übersteuerung in Automatik:** Die Ofenwahl endet bei Rückgabe, Phasenwechsel,
Änderung der automatischen EIN-/AUS-Anforderung oder Fristablauf. Ofen-AUS
unterbricht auch Gangheizung und Mindestheizzeit. Die manuelle Lichtwahl endet
beim passenden Phasenwechsel oder spätestens mit ihrer Übersteuerungsfrist.

**Betriebsart Manuell:** Nach Sitzungsende wählbar; bleibt bis zur erneuten
Betriebsartwahl erhalten. Ofen und Licht starten beim Wechsel und nach Neustart
mit AUS. Die Bedienwahlen gelten ohne Übersteuerungsfrist. Zurückschalten auf
Automatik gibt beide frei. Auch manuelles EIN benötigt gültige Regeltemperatur
und Heizfreigabe; Schutz und Betrieb-AUS haben weiter Vorrang.

### Temperaturprogramm

Direkte Sollwahl hält eine konstante Temperatur. Programme folgen gespeicherten
oder frei gewählten Stufen, auf Wunsch gleichmäßig aus Start, Ende und Anzahl
verteilt. Steigende und fallende Folgen sind möglich. Jeder beendete bestätigte
Gang schaltet einmal weiter; nach der letzten Stufe bleibt deren Temperatur.

Eine alleinige Änderung der Endtemperatur erhält das aktuelle Ziel und verteilt
die verbleibenden Schritte neu. Ein neuer Startwert beginnt eine neue Verteilung.
Gangzählung und laufende Fristen bleiben erhalten.

## Regelung und Schutz

### Temperatur und Bereitschaft

Für die Regelung führt die gültige obere Temperatur, ersatzweise die untere.
Sobald oben wieder gültige Werte vorliegen, übernimmt diese Quelle erneut.
Eine vollständige Temperatur-/Feuchteposition genügt für den Betrieb.

Die Thermostatgrenzen liegen bei:

- **AUS:** Solltemperatur plus Abschaltaufschlag.
- **EIN:** Solltemperatur minus Wiedereinschaltabstand.

An den Grenzen wird geschaltet; dazwischen bleibt der bisherige Zustand erhalten.
Nach einer Temperaturabschaltung muss zusätzlich die Heizpause ablaufen.
Die Mindestheizzeit beginnt mit dem tatsächlich bestätigten Heizbeginn und
verzögert eine reguläre Temperaturabschaltung. Ihr Beginn bleibt auch bei
Solländerungen oder Rückgabe einer manuellen EIN-Wahl erhalten.

**Bereit** gilt im freigegebenen Automatikbetrieb ab dem ersten Erreichen des
Sollwerts, unabhängig von der höheren Abschaltschwelle. Temperaturabfall und
Solländerung heben diese Bereitschaft nicht auf. Eine vorläufige Gangerkennung
bewahrt sie; ein bestätigter Gang beendet sie. Nach Gang und Kühlung kann sie
neu entstehen ([Gangmodell](gangmodell.md)).

### Heizpriorität

Im Automatikbetrieb gilt, von oben nach unten:

1. Schutz, fehlende Heizfreigabe oder Betrieb-AUS: Ofen AUS.
2. Angeforderte oder laufende [Ofenkühlung](ofenkuehlung.md): Ofen AUS.
3. Ausdrückliche vorübergehende Ofenwahl, solange sie gilt.
4. Aktiver Gang: durchgehende Heizanforderung, auch oberhalb der Thermostatgrenze.
5. Geeigneter Türschluss im Proxybetrieb: Mindestheizhilfe.
6. Thermostat einschließlich Mindestheizzeit und Heizpause.

Die Ganganforderung gilt im Proxybetrieb schon ab der vorläufigen Erkennung.
Bei direkter Präsenz setzt sie einen bestätigten Gang voraus; Quellausfall nimmt
nur diese zusätzliche Heizanforderung zurück, ohne den Gang zu beenden.

Für die Türhilfe müssen Öffnung und anschließender Schluss zu demselben Vorgang
bei Betrieb-EIN in Aufheizen oder Bereit gehören. Gangbeginn oder Kühlung
verwerfen diesen Vorgang. Bis zur tatsächlichen Heizbestätigung bleibt die
Anforderung widerrufbar, danach gilt die Mindestheizzeit. Betrieb-AUS und
manuelles Ofen-AUS verwerfen die Türanforderung. Direkte Präsenzführung verwendet
keine Türhilfe.

### Rückmeldungen und Schutz

Ohne gültige Regeltemperatur bleibt die Heizung aus. Gültige Ersatzquellen
übernehmen ihre Aufgaben; ein einzelner Quellenausfall beendet die Sitzung nicht.

Fehlende Schützrückmeldung, ausgebliebener Schaltvollzug, Befehlsfehler oder
Weiterheizen trotz AUS sind abschaltrelevant. Ebenso fehlende Regeltemperatur:
Bleiben solche Fehler in der Betriebs- oder Heizüberwachung über die
Bestätigungsdauer bestehen, wird die Heizfreigabe verriegelt. Quittieren setzt
Betrieb-AUS und bestätigtes Ofen-AUS voraus.

### Licht

| Betriebsabschnitt | Automatisches Lichtziel |
| --- | --- |
| Aufheizen und Bereit | Linear von Grundhelligkeit an der Referenztemperatur zur Normalhelligkeit am Sollwert. |
| Saunagang | Normalhelligkeit. |
| Ofenkühlung | Zunächst Kühlhelligkeit, danach über die Restzeit linear zum temperaturabhängigen Ziel. |
| Betrieb-AUS | Nachlaufhelligkeit bis zum Fristende, dann AUS. |

Die Normalhelligkeit geht während der bürgerlichen Dämmerung zwischen Tag- und
Nachtwert über. Thermostatgrenzen beeinflussen die Lichtkurve nicht.
Automatische Stellwerte werden auf ganze Prozent gerundet und durch die
Ausgabehysterese beruhigt; manuelle Wahlen und fälliges AUS werden dadurch nicht
verzögert.

Übergänge beginnen an der beobachteten Helligkeit. Bei Kühlbeginn ist der erste
Übergang auf die halbe Restzeit begrenzt. Rückkehr aus manueller Lichtwahl
verlängert weder Kühlung noch Nachlauf; fälliges AUS beendet auch einen Übergang.
Eine spätere Raumlichtwahl startet keinen neuen automatischen Nachlauf.

## Zeit und Verbrauch

### Heizzeit, Timer und Energie

Heizzeit verwendet die erste gültige Quelle dieser Reihenfolge: Leistungsmessung,
Heizrückmeldung, native Ofenschalterrückmeldung. Die letzte belegt nur die
Heizfreigabe, nicht die tatsächliche Leistung hinter einem internen Thermostat.
Nach der eingestellten durchgehend bestätigten Auszeit wird die lokale Heizsumme
zurückgesetzt.

Der mechanische Timer zählt nur bei Betrieb-EIN und bestätigtem Schütz-EIN.
Bei AUS oder unbekannter Rückmeldung pausiert die Schätzung. Wiederaufnahme
erhält den Rest; eine neue Sitzung beginnt mit voller Laufzeit. Die Anzeige
verstellt den Timer am Ofen nicht.

Energie stammt aus gültigen Leistungsmessungen, ersatzweise aus Heizzeit mal
konfigurierter Ofenleistung. Gemessene, geschätzte und unbekannte Anteile werden
unterschieden. Kühlung und Heizzeitrücksetzung löschen keinen Sitzungsverbrauch.

### Zeitliche Zuordnung

Empfangene Messungen werden vor späteren Bedienhandlungen mit ihren damaligen
Freigaben ausgewertet. Auch ein nur zwischenzeitlich erreichter Sollwert zählt
für die Bereitschaft. Am Ende einer Bestätigungsfrist werden bereits empfangene
Messungen dieses Zeitpunkts noch berücksichtigt.

Ereignisbeginn, Buchung und Erkennung können auseinanderliegen. Späte Erkennung
kann Gang- und Phasenzuordnung im Verlauf berichtigen; Gerätebefehle gelten erst
zur tatsächlichen Ausgabezeit. Messzeiten und bestätigte Schalterrückmeldungen
bleiben erhalten. Die Anzeige verwendet die lokale Browserzeit.
