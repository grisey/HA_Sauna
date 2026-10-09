# Automatischer Saunabetrieb

## Aufheizen und Bereitschaft

Für die Regelung führt die gültige obere Temperatur, ersatzweise die untere.
Sobald oben wieder gültige Werte vorliegen, übernimmt diese Quelle erneut.

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
neu entstehen ([Gangerkennung](gangmodell.md)).

## Heizpriorität

Im Automatikbetrieb gilt, von oben nach unten:

1. Schutz, fehlende Heizfreigabe oder Betrieb-AUS: Ofen AUS.
2. Angeforderte oder laufende [Ofenkühlung](ofenkuehlung.md): Ofen AUS.
3. Ausdrückliche vorübergehende Ofenwahl, solange sie gilt.
4. Aktiver Gang: durchgehende Heizanforderung, auch oberhalb der Thermostatgrenze.
5. Geeigneter Türschluss bei Gangerkennung aus Temperatur und Feuchte: Mindestheizhilfe.
6. Thermostat einschließlich Mindestheizzeit und Heizpause.

Bei Gangerkennung aus Temperatur und Feuchte gilt die Ganganforderung schon ab
der vorläufigen Erkennung.
Bei direkter Präsenz setzt sie einen bestätigten Gang voraus; Quellausfall nimmt
nur diese zusätzliche Heizanforderung zurück, ohne den Gang zu beenden.

Die Türhilfe fordert nach einem geeigneten Türschluss zusätzliches Heizen an.
Dafür müssen Öffnung und anschließender Schluss zu demselben Vorgang
bei Betrieb-EIN in Aufheizen oder Bereit gehören. Gangbeginn oder Kühlung
verwerfen diesen Vorgang. Bis zur tatsächlichen Heizbestätigung bleibt die
Anforderung widerrufbar, danach gilt die Mindestheizzeit. Betrieb-AUS und
manuelles Ofen-AUS verwerfen die Türanforderung. Direkte Präsenzführung verwendet
keine Türhilfe.

## Licht während der Sitzung und beim Ausschalten

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
Übergang auf die halbe Restzeit begrenzt. Fälliges AUS beendet auch einen Übergang.

## Heizzeit und Energie

Heizzeit verwendet die erste gültige Quelle dieser Reihenfolge: Leistungsmessung,
Heizrückmeldung, native Ofenschalterrückmeldung. Die letzte belegt nur die
Heizfreigabe, nicht die tatsächliche Leistung hinter einem internen Thermostat.
Nach der eingestellten durchgehend bestätigten Auszeit wird die lokale Heizsumme
zurückgesetzt.

Energie stammt aus gültigen Leistungsmessungen, ersatzweise aus Heizzeit mal
konfigurierter Ofenleistung. Gemessene, geschätzte und unbekannte Anteile werden
unterschieden. Kühlung und Heizzeitrücksetzung löschen keinen Sitzungsverbrauch.

## Fehlender Temperaturanstieg

Bei bestätigtem Schütz-EIN unterhalb des Sollwerts wird jeder Heizabschnitt
beobachtet. Die Anlaufverzögerung stammt aus ungestörten archivierten
Heizabschnitten derselben Messquelle und Ofenzuordnung; maßgeblich ist die längste
belegte Verzögerung. Danach wird ein weiteres Temperaturauswertungsfenster geprüft.
Ohne geeignete Historie bleibt die Anlaufverzögerung unbekannt. Ein bereits
beobachteter Anstieg ermöglicht dann die Erkennung späterer Stagnation.

Türereignisse, Quellenwechsel und Messausfälle verwerfen den laufenden Vergleich.
Der Hinweis verändert die Heizungssteuerung nicht und verschwindet bei erneutem
Anstieg oder Ende des Vergleichs.

## Bei Sensorausfall oder Schutzabschaltung

Ohne gültige Regeltemperatur bleibt die Heizung aus. Gültige Ersatzquellen
übernehmen ihre Aufgaben; ein einzelner Quellenausfall beendet die Sitzung nicht.

Fehlende Schützrückmeldung, ausgebliebener Schaltvollzug, Befehlsfehler oder
Weiterheizen trotz AUS sind abschaltrelevant. Ebenso fehlende Regeltemperatur:
Bleiben solche Fehler in der Betriebs- oder Heizüberwachung über die
Bestätigungsdauer bestehen, wird die Heizfreigabe verriegelt. Quittieren setzt
Betrieb-AUS und bestätigtes Ofen-AUS voraus.
