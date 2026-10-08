# Gangmodell

Der Ablaufkern ordnet Erkennungssignale einem Saunagang zu und führt dessen
Bestätigung, Ende und Zählung. Die [Erkennung](erkennung.md) liefert dafür
Ereignisse aus dem Messverlauf. Der Controller führt den verbindlichen Gangzustand.
Die konfigurierte Präsenzquelle führt die Gangzuordnung. Der Proxyweg bleibt
für Installationen ohne ausgewählten direkten Präsenzsensor erhalten.

## Direkter Präsenzsensor

Bei `ha_presence` benötigen Gangbeginn und Gangende jeweils einen vollständigen
Türvorgang: erkannte Öffnung mit anschließender Schließung. Eine einzelne
Präsenzmeldung, eine Türschließung ohne Öffnung und bloßer Zeitablauf genügen nicht.
Die Präsenzmeldung muss zur gewählten Entität gehören und darf nicht vor der
Öffnung liegen. Ein Türvorgang kann nur einen Gangübergang begründen.

Betrieb-EIN, gültige Mindesttemperatur und freigegebene Betriebsphase bleiben
Voraussetzungen des Beginns. Bei belegter Anwesenheit wird der Gang nach
vollständigem Türvorgang unmittelbar bestätigt, ohne zusätzliche Wartefrist und
ohne Aufgusszwang. Der Beginn wird dem Türschluss zugeordnet; der tatsächliche
Erkennungszeitpunkt bleibt getrennt erhalten. Eine kalte Türepisode wird nicht
durch späteres Aufheizen nachträglich zum Gangbeginn.

Ein nachfolgender vollständiger Türvorgang mit zugehöriger Abwesenheitsmeldung
beendet den Gang. Die Meldung darf auch erst nach dem Türschluss eintreffen.
Solange kein solcher Austritt belegt ist, bleibt der Gang offen. Alleinige
Abwesenheit oder Durchlüften beendet ihn nicht. Bei vollständigem Austritt
folgen Zählung, Temperaturstufe und Ofenkühlung dem gemeinsamen Ablaufkern.
Betrieb-AUS bleibt ein ausdrücklicher Bedienabschluss.

`unknown` und `unavailable` sind keine Abwesenheitsbelege. Der offene Gang bleibt
erhalten, seine zusätzliche Heizanforderung entfällt während des Quellausfalls.
Der Thermostat und die Schutzregeln gelten weiter. Es gibt keinen automatischen
Wechsel zur Proxyerkennung und keine Tür-Mindestheizhilfe.

Die Zuordnung verwendet die gemeldeten Zustandszeiten, ohne einen unbekannten
Abwesenheitsverzug des Sensors abzuziehen. Das reale Zusammenpassen von
Präsenzmeldung und thermischer Türerkennung bleibt an Messdaten zu prüfen.

## Proxyverfahren

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
Personen, während mindestens eine Person bleibt. Diese Lüftungsregel gehört zum
Proxyverfahren. Bei direkter Präsenzführung beendet stattdessen ein vollständiger
Türvorgang mit zugehöriger Abwesenheitsmeldung den Gang gemäß
[direkter Präsenzführung](#direkter-präsenzsensor).

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
