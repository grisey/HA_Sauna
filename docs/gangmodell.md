# Beginn, Bestätigung und Ende eines Saunagangs

Die wirksame Präsenzquelle bestimmt, wie ein Saunagang erkannt wird: mit einem
direkten Präsenzsensor oder aus [Temperatur und Feuchte](erkennung.md)
(Proxyverfahren).

Die Erkennungsregeln gelten auch während der manuellen Aufzeichnung.
Heizanforderungen, Ofenkühlung und Temperaturstufen wirken nur in Automatik.

## Wirksame Präsenzquelle

Ein gewählter Präsenzsensor führt die Gangerkennung. Bei durchgehender
Nichtverfügbarkeit übernimmt nach der **Wartezeit bis zur Proxy-Erkennung** das
Proxyverfahren. Nach durchgehend gültigen Zuständen `on` oder `off` übernimmt
nach der **Wartezeit bis zum Präsenzsensor** wieder der Sensor. Beide Wartezeiten
sind unter **Sensoren und Erkennung** einstellbar; ihre Vorgaben stehen im
[Parameterkatalog](../custom_components/ha_sauna/defaults.json).
Ein gültiger, unveränderter Zustand verfällt nicht durch sein Alter. Ein Wechsel
zwischen `on` und `off` unterbricht die Verfügbarkeit nicht. Ist das Proxyverfahren
gewählt, bleibt es wirksam.

Ein laufender Gang behält beim Quellenwechsel seine Kennung, seinen Beginn,
seinen Bestätigungsstand und seine Aufgüsse. Eine laufende Ofenkühlung bleibt
erhalten. Liegen bereits eine passende Austrittsöffnung und bestätigtes
[Lüften](erkennung.md#durchlüften) vor, kann die Proxyübernahme den Gang zum tatsächlichen
Quellenwechsel beenden. Die Rückkehr zum Sensor erzeugt allein keinen neuen Gang
und verwendet keinen zuvor abgeschlossenen Eintritt erneut.
Im Verlauf gilt für jeden Zeitabschnitt die damals wirksame Präsenzquelle.

## Eintritt, Aufenthalt und Verlassen

Türöffnung und Türschluss werden unabhängig von der Mindesttemperatur erkannt.
Der initiale Türzustand ist geschlossen; er ersetzt keine beobachtete Öffnung.

| Situation | Gangerkennung |
| --- | --- |
| Noch kein Gang | Eintritt und Anwesenheitsnachweis; Beginn frühestens bei gültiger Regeltemperatur ab der Mindesttemperatur |
| Gang läuft | Aufgüsse und Gangende bleiben auch unterhalb der Mindesttemperatur erkennbar |
| Ofenkühlung läuft | Gangerkennung und Aufzeichnung bleiben aktiv; die Wirkung auf die Kühlung richtet sich nach der [Präsenzquelle](ofenkuehlung.md#kühlung-abwarten-oder-vorzeitig-beenden) |
| Betrieb-AUS | Keine neuen Gänge |

Beim Eintritt folgt auf die Öffnung der Türschluss. Beim Verlassen wird die Tür
zum Lüften geöffnet und offengelassen. Die spätere Rückkehr durch diese
offene Tür kann mit dem anschließenden Türschluss einen neuen Eintritt belegen.
Das Ende des alten Gangs verbraucht diesen späteren Türschluss nicht.

## Gang mit direktem Präsenzsensor erkennen

Beim Schließen einer vollständigen Türfolge muss Anwesenheit anliegen.
Sie kann bereits vor der Öffnung bestanden haben. Entscheidend ist die gemeldete
Zustandszeit, unabhängig von der Verarbeitungsreihenfolge. Erst nach dem
Türschluss beginnende Anwesenheit verwendet diese Türfolge nicht erneut.
Liegt die Temperatur beim Eintritt noch unter der Mindesttemperatur, bleibt der
belegte Eintritt bei anhaltender Präsenz erhalten. Der Gang beginnt beim Erreichen
der Grenze, ohne erneute Türfolge und ohne Rückdatierung auf den kälteren Eintritt.
Abwesenheit oder ein unbekannter Präsenzzustand unterbrechen diesen Nachweis.
Ein Gang endet erst bei einer neuen Austrittsöffnung, bestätigtem Lüften
derselben Öffnung und anliegender Abwesenheit. Diese kann bereits vor dem
thermischen Öffnungshinweis gemeldet sein. Die Nachweise können in beliebiger
Reihenfolge eintreffen; Anwesenheit, `unknown` oder `unavailable`
vor dem vollständigen Nachweis verhindern das Gangende.
Ein anschließender Türschluss verwirft den belegten Austritt nicht. Die
Abwesenheit kann auch erst danach gemeldet werden oder beginnen; maßgeblich
bleiben dieselbe Austrittsöffnung, ihr bestätigtes Lüften und die weiterhin
gültige Abwesenheit. Erneute Anwesenheit überholt einen früheren
Abwesenheitsnachweis. Eine ältere Austrittsfolge kann keinen später begonnenen
Gang beenden.

| Beleg | Wirkung |
| --- | --- |
| Eintritt mit Anwesenheit und erreichter Mindesttemperatur | Bestätigter Gang, ohne Wartefrist oder erforderlichen Aufguss |
| Öffnen zum Verlassen mit zugehöriger Abwesenheit und bestätigtem Lüften | Gangende beim vollständigen Nachweis, auch nach dem anschließenden Türschluss |
| Einzelne Präsenzmeldung, Türschluss ohne Öffnung oder bloßer Zeitablauf | Kein Übergang |
| Lüften ohne zugehörige Abwesenheit | Kein Gangende |
| Betrieb-AUS | Ende des offenen Gangs |

Aufgüsse werden dem Gang zugeordnet, bestätigen ihn aber nicht erneut.
Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs.

`unknown` und `unavailable` belegen keine Abwesenheit. Solange der Präsenzsensor
führt, bleibt der Gang offen und seine zusätzliche Heizanforderung entfällt.
Thermostat und Schutz bleiben wirksam; eine Tür-Mindestheizhilfe gibt es in
diesem Verfahren nicht.

## Gang aus Temperatur und Feuchte erkennen

| Ereignis | Wirkung |
| --- | --- |
| Zulässiges Personensignal nach vollständigem Eintritt | Vorläufiger Gang |
| Erster gültiger Aufguss | Bestätigt den vorläufigen Gang mit gleichem Beginn oder eröffnet nach vollständigem Eintritt einen bestätigten Gang |
| Weiterer Aufguss | Ergänzt den aktiven Gang |
| Bestätigungsfrist abgelaufen oder Lüften nach Austrittsöffnung vor Bestätigung | Vorläufiger Gang zurückgenommen |
| Bestätigtes Lüften nach Austrittsöffnung | Gangende zum Buchungszeitpunkt der Lüftungsbestätigung |
| Betrieb-AUS | Ende des offenen Gangs |

Als Beginn gilt die passende Türschließung, frühestens jedoch der Beginn des
anschließenden gültigen Temperaturbereichs ab Mindesttemperatur. Die
Personen- und Aufgussnachweise müssen zu diesem Eintritt gehören. Ohne gültigen,
ungenutzten Türbezug beginnt kein Gang.

Die Aufgussbestätigungsfrist läuft ab Gangbeginn einschließlich ihres
Endzeitpunkts. Auch ein Gangbeginn allein durch Aufguss muss innerhalb dieser
Frist ab möglichem Gangbeginn liegen. Nach Ablauf oder Rücknahme benötigt ein neuer Gang
einen neuen vollständigen Eintritt.

Bestätigtes Lüften während der Austrittsöffnung beendet den Gang;
er wird gezählt und löst in Automatik die Ofenkühlung aus, sofern diese nicht
bereits läuft. Erkannte Personenbelege begrenzen eine laufende Kühlung auf ihre
[Mindestgesamtdauer](ofenkuehlung.md#kühlung-abwarten-oder-vorzeitig-beenden), auch
ohne bestätigten Gang. Eine kurze Türöffnung ohne bestätigtes Lüften beendet
keinen Gang.

## Gang zählen und nächste Temperaturstufe beginnen

Nur beendete, bestätigte Gänge zählen, jeweils einmal und unabhängig vom
Endgrund. In Automatik folgt gleichzeitig die nächste Stufe des
[Temperaturprogramms](bedienung.md#temperatur-während-der-sitzung-ändern).
Beginn und zugeordnete Aufgüsse bleiben erhalten.

[Heizpriorität](betrieb.md#heizpriorität) · [Ofenkühlung](ofenkuehlung.md)
