# Gangmodell

Der Ablaufkern ordnet Erkennungssignale einem Saunagang zu und führt dessen
Bestätigung, Ende und Zählung. Die [Erkennung](erkennung.md) liefert dafür
Ereignisse aus dem Messverlauf. Der Controller führt den verbindlichen Gangzustand.
Das aktuelle Programm führt die Gangzuordnung aus der Proxyerkennung.
Direkte Präsenzmeldungen bleiben beobachtend.

## Beginn und Bestätigung

| Ereignis | Wirkung auf den Gang |
| --- | --- |
| Zulässiges Personensignal | Eröffnet einen vorläufigen Gang. |
| Erster gültiger Aufguss | Bestätigt den vorläufigen Gang mit derselben Identität und demselben Beginn oder eröffnet unmittelbar einen bestätigten Gang. |
| Weiterer Aufguss | Ergänzt den aktiven Gang unter derselben Identität. |
| Ablauf der Bestätigungsfrist | Nimmt den noch vorläufigen Gang zurück. |
| Bestätigtes Durchlüften während eines vorläufigen Gangs | Nimmt den vorläufigen Gang zurück. |
| Bestätigtes Durchlüften nach einem Aufguss | Beendet den bestätigten Gang zum Buchungszeitpunkt der Lüftungsbestätigung. |
| Betrieb-AUS | Beendet den offenen Gang. |

Ein neuer Gang setzt Betrieb-EIN, fehlende Ofenkühlung und eine aktuelle gültige
führende Regeltemperatur mindestens in Höhe von `sauna_min_temperature_c` voraus.
Die bestehende Quellenauswahl bevorzugt die obere gültige Temperatur und weicht
bei Bedarf auf die untere aus. Diese gemeinsame Freigabe gilt für starke und
schwache Proxy-Personensignale sowie unmittelbar startende Aufgüsse in beiden
Betriebsarten. Fehlende oder veraltete Regeltemperaturen erlauben keinen Start.
Ein späterer Temperaturabfall beendet einen aktiven Gang nicht; Bestätigung,
weitere Aufgüsse und Lüftungsende bleiben möglich.

Als Beginn dient die passende Türschließung derselben Sitzung und Türöffnung,
wenn ihre effektive Zeit bereits eine gültige Temperaturfreigabe hatte.
Eine kalte oder ungültige Türschließung bleibt beobachtet, liefert aber keinen
Ganganker und keine empfindlichere Personenprüfung.
Ein ungenutzter Anker verfällt auch bei einer Unterbrechung dieser
Temperaturfreigabe oder beim Ablauf der führenden Messung.
Ersatzweise gilt der Buchungszeitpunkt des zulässigen Personensignals oder
Aufgusses. Die tatsächliche Erkennungszeit wird gesondert festgehalten.

Eine erkannte Türöffnung mit anschließender Schließung eröffnet die empfindlichere
Personenprüfung für die eingestellte Bestätigungsfrist. Ein neuer Feuchteanstieg
mit zunehmendem absolutem Wassergehalt kann in dieser Zeit einen vorläufigen
Gang auslösen. Eine weitere Türöffnung beginnt einen neuen Türbezug. Diese
Zuordnung setzt eine neue Feuchteentwicklung nach der Öffnung voraus.

Die Aufgussbestätigungsfrist des Proxyverfahrens endet standardmäßig 12 Minuten
nach dem Gangbeginn.
Ein rechtzeitig zugeordneter Aufguss bestätigt den Gang bis einschließlich dieses
Endzeitpunkts. Nach einer Rücknahme kann ein neuer gültiger Aufguss einen eigenen
bestätigten Gang beginnen. Die Anzeige nennt die seit Gangbeginn verstrichene
Zeit.

Eine kurze Türöffnung mit anschließender Schließung erhält den aktiven Gang.
Die Lüftungsbestätigung schützt den laufenden Gang bei kurzem Austritt einzelner
Personen, während mindestens eine Person bleibt. Die spätere Kopplung dieser
Bestätigung an direkte Präsenz bleibt bis zur Prüfung von Sensorposition und
realem Verhalten unkonkretisiert.

[Erkennung](erkennung.md) · [Zeitmodell](zeitmodell.md) ·
[Erweiterungsvertrag für direkte Präsenzführung](praesenz-ofen-phasen.md#erweiterungsvertrag-für-direkte-präsenzführung)

## Zählung und Folge

Die Gangzahl steigt genau einmal beim Ende eines bestätigten Gangs, unabhängig
vom Endgrund. Zugleich wechselt die Temperaturautomatik zur nächsten Stufe.
Der abgeschlossene Gang bewahrt seinen Beginn und seine zugeordneten Aufgüsse.

Während angeforderter oder laufender Kühlung bleibt die Gangzuordnung für neue
Personen- und Aufgusssignale gesperrt. Nach ihrer Freigabe
beginnt die Erkennung mit Messfenstern aus dem freigegebenen Abschnitt.

Ein aktuell aktiver Gang fordert im Automatikbetrieb Heizen an. Das gilt für
vorläufige und bestätigte Gänge gleichermaßen.

[Heizpriorität](praesenz-ofen-phasen.md#heizpriorität) ·
[Gangende und Kühlung](ofenkuehlung.md#auslösung)
