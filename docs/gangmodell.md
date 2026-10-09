# Gangmodell

Die gewählte Präsenzquelle bestimmt Beginn und Ende eines Saunagangs:
`ha_presence` verwendet einen direkten Präsenzsensor, `proxy` die
[Temperatur- und Feuchtesignale](erkennung.md).

## Startbedingungen

In beiden Betriebsarten benötigt ein neuer Gang Betrieb-EIN, keine angeforderte
oder laufende Ofenkühlung und eine frische, gültige
[Regeltemperatur](betrieb.md) ab der eingestellten Mindesttemperatur.
Ein späterer Temperaturabfall beendet einen aktiven Gang nicht; Aufgüsse und
Enderkennung bleiben möglich. Nach einer Freigabe verwendet die Erkennung neue
Messfenster aus dem freigegebenen Zeitraum.

## Direkter Präsenzsensor

Ein vollständiger Türvorgang besteht aus erkannter Öffnung und anschließender
Schließung. Die zugehörige Präsenzmeldung muss von der gewählten Entität stammen
und darf nicht vor der Öffnung liegen. Sie kann auch nach dem Türschluss
eintreffen. Jeder Türvorgang begründet höchstens einen Übergang.

| Beleg | Wirkung |
| --- | --- |
| Vollständiger Türvorgang mit Anwesenheit und erfüllten Startbedingungen | Bestätigter Gang ab Türschluss, ohne Wartefrist oder erforderlichen Aufguss |
| Nachfolgender vollständiger Türvorgang mit Abwesenheit | Gangende |
| Einzelne Präsenzmeldung, Türschluss ohne Öffnung oder bloßer Zeitablauf | Kein Übergang |
| Durchlüften | Kein Gangende |
| Betrieb-AUS | Ende des offenen Gangs |

Eine kalte Türepisode wird durch späteres Aufheizen nicht nachträglich zum Start.
Aufgüsse werden dem Gang zugeordnet, bestätigen ihn aber nicht erneut.
Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs.

`unknown` und `unavailable` belegen keine Abwesenheit. Der Gang bleibt offen,
seine zusätzliche Heizanforderung entfällt bis zur Rückkehr der Quelle.
Thermostat und Schutz bleiben wirksam. Es gibt keinen automatischen Wechsel zum
Proxyverfahren und keine Tür-Mindestheizhilfe.

## Proxyverfahren

### Beginn und Bestätigung

| Ereignis | Wirkung |
| --- | --- |
| Zulässiges Personensignal | Vorläufiger Gang |
| Erster gültiger Aufguss | Bestätigt den vorläufigen Gang mit gleichem Beginn oder eröffnet einen bestätigten Gang |
| Weiterer Aufguss | Ergänzt den aktiven Gang |
| Bestätigungsfrist abgelaufen oder Durchlüften vor Bestätigung | Vorläufiger Gang zurückgenommen |
| Durchlüften nach Bestätigung | Gangende zum Buchungszeitpunkt der Lüftungsbestätigung |
| Betrieb-AUS | Ende des offenen Gangs |

Als Beginn gilt die passende Türschließung derselben Sitzung und Türöffnung,
wenn zu diesem Zeitpunkt die Temperaturfreigabe bestand. Ein ungenutzter
Türbezug verfällt bei unterbrochener Temperaturfreigabe oder veraltetem Messwert.
Ohne gültigen Türbezug gilt der Buchungszeitpunkt des Personen- oder Aufgusssignals.

Die Aufgussbestätigungsfrist läuft ab Gangbeginn einschließlich ihres
Endzeitpunkts. Nach einer Rücknahme kann ein neuer gültiger Aufguss einen eigenen
Gang beginnen. Eine kurze Türöffnung mit anschließendem Schließen beendet
keinen aktiven Gang; dafür ist bestätigtes Durchlüften erforderlich.

## Zählung und Folge

Nur beendete, bestätigte Gänge zählen, jeweils einmal und unabhängig vom
Endgrund. Gleichzeitig folgt die nächste Temperaturstufe. Beginn und zugeordnete
Aufgüsse bleiben erhalten.

[Heizpriorität](betrieb.md#heizpriorität) · [Ofenkühlung](ofenkuehlung.md)
