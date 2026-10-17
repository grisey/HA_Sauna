# Beginn, Bestätigung und Ende eines Saunagangs

Präsenzbelege stammen von einem direkten Präsenzsensor oder aus
[Temperatur und Feuchte](erkennung.md) (Proxyverfahren). Nach ihrer Annahme
entscheiden Belegqualität, Verfügbarkeit, fachliche Zeiten sowie die vorhandene
Türfolge und der Gangbestand über Beginn, Bestätigung und Ende.

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
seinen Bestätigungsstand und seine Aufgüsse. Eine laufende Ofenkühlung und eine
bestehende Türwärmeanforderung bleiben erhalten. Der Auswahlwechsel selbst
beendet oder bestätigt keinen Gang und verwendet keinen zuvor abgeschlossenen
Eintritt erneut. Maßgeblich bleiben die tatsächlich angenommenen Belege und ihre
erste fachliche Nutzbarkeit. Im Verlauf gilt für jeden Zeitabschnitt die damals
wirksame Präsenzquelle.

## Eintritt, Aufenthalt und Verlassen

Türöffnung und Türschluss werden unabhängig von der Mindesttemperatur erkannt.
Der initiale Türzustand ist geschlossen; er ersetzt keine beobachtete Öffnung.

| Situation | Gangerkennung |
| --- | --- |
| Noch kein Gang | Eintritt und Anwesenheitsnachweis; Beginn frühestens bei gültiger Regeltemperatur ab der Mindesttemperatur |
| Gang läuft | Aufgüsse und Gangende bleiben auch unterhalb der Mindesttemperatur erkennbar |
| Ofenkühlung läuft | Gangerkennung und Aufzeichnung bleiben aktiv; die Wirkung auf die Kühlung richtet sich nach der [Belegqualität](ofenkuehlung.md#kühlung-abwarten-oder-vorzeitig-beenden) |
| Betrieb-AUS | Keine neuen Gänge |

Beim Eintritt folgt auf die Öffnung der Türschluss. Beim Verlassen wird die Tür
zum Lüften geöffnet und offengelassen. Die spätere Rückkehr durch diese
offene Tür kann mit dem anschließenden Türschluss einen neuen Eintritt belegen.
Das Ende des alten Gangs verbraucht diesen späteren Türschluss nicht.

Für Verlauf, Statistik und Betriebsphase reicht der Gang vom zugeordneten
Eintritt bis zum bestätigten Abschluss des Austrittsvorgangs. Die Türöffnung
allein beendet ihn nicht. Das Durchlüften gehört zu dieser vorher beobachteten
Öffnung; bei Führung durch einen Präsenzsensor gehört außerdem der
Abwesenheitsnachweis dazu. Die Gangdauer endet am gebuchten Abschluss dieses
zusammengehörigen Vorgangs. Bis dahin bleibt die Phase **Saunagang** bestehen;
dazwischen entsteht keine zusätzliche Aufheiz- oder Bereitschaftsphase.
Türöffnung und Türschluss bleiben als eigene Ereignisse sichtbar.
Fehlende Türquellen oder Betrieb-AUS ersetzen keinen Austrittsnachweis.
Bei unvollständigen Altarchiven bleibt der bisher aufgezeichnete Abschnitt
als unvollständige Zeitzuordnung erhalten.
Die angezeigte Dauer eines aktiven Gangs verwendet denselben belegten
Beginn bei erfüllter Mindesttemperatur. Fehlt dessen Türschluss, bleibt die Dauer unbekannt. API,
Vorschau und Zustandsentität verwenden dafür dieselbe Ableitung.

## Gang durch qualifizierte Belegungszustände bestätigen

Ein qualifizierter Belegungszustand beschreibt fortbestehende Anwesenheit oder
Abwesenheit. Beim Schließen einer vollständigen Türfolge muss verfügbare,
qualifizierte Anwesenheit anliegen.
Sie kann bereits vor der Öffnung bestanden haben. Entscheidend ist die gemeldete
Zustandszeit zusammen mit ihrer tatsächlichen fachlichen Nutzbarkeit. Erst nach dem
Türschluss beginnende Anwesenheit verwendet diese Türfolge nicht erneut. Ein
erst nach dem Türschluss fachlich nutzbarer Beleg kann den Eintritt ebenfalls
nicht rückwirkend bestätigen. Bei gleichem Zeitstempel zählt die tatsächliche
Verarbeitungsreihenfolge; ein bereits abgeschlossener Eintritt wird durch
spätere Auswertung nicht wieder gültig.
Liegt die Temperatur beim Eintritt noch unter der Mindesttemperatur, bleibt der
belegte Eintritt bis zu einer neuen Türöffnung erhalten. Der Gang beginnt beim Erreichen
der Grenze, ohne erneute Türfolge und ohne Rückdatierung auf den kälteren Eintritt.
Eine spätere Abwesenheitsmeldung ohne erneute Türöffnung hebt den beim
Türschluss belegten Aufenthalt nicht auf.
Ein Gang endet durch einen zusammengehörigen Austrittsvorgang: neue
Türöffnung, zugehörige Abwesenheit und bestätigtes Durchlüften dieser Öffnung.
Die Abwesenheit kann wegen unterschiedlicher Sensorlaufzeiten bereits vor dem
thermischen Öffnungshinweis gemeldet sein und muss dieser Öffnung zugehören.
Einzelne Anwesenheitsmeldungen während der offenen Tür verwerfen den
Abwesenheitsbeleg nicht. Ohne verfügbaren Präsenzzustand bleibt der Abschluss
offen. Ein anschließender Türschluss verwirft den belegten Austritt ebenfalls
nicht; dessen Abwesenheit kann verspätet eintreffen. Eine ältere Austrittsfolge
kann keinen später begonnenen Gang beenden.

Präsenz beim anschließenden Türschluss bestätigt einen neuen Aufenthalt.
Dafür ist keine zusätzliche Mindestdauer erforderlich. Derselbe Türvorgang
kann den Austritt des alten und den Eintritt des neuen Gangs tragen; beide
Gänge überlappen sich nicht. Eine Abwesenheitsmeldung bei weiter geschlossener
Tür beendet den bestätigten Aufenthalt nicht.

| Beleg | Wirkung |
| --- | --- |
| Eintritt mit Anwesenheit und erreichter Mindesttemperatur | Bestätigter Gang, ohne Wartefrist oder erforderlichen Aufguss |
| Öffnen zum Verlassen mit zugehöriger Abwesenheit und bestätigtem Lüften | Gangende beim vollständigen Nachweis, auch nach dem anschließenden Türschluss |
| Einzelne Präsenzmeldung, Türschluss ohne Öffnung oder bloßer Zeitablauf | Kein Übergang |
| Durchlüften der Austrittsöffnung ohne zugehörige Abwesenheit | Kein Gangende |
| Betrieb-AUS | Ende des offenen Gangs |

Aufgüsse werden dem Gang zugeordnet, bestätigen ihn aber nicht erneut.
Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs.

`unknown` und `unavailable` belegen keine Abwesenheit. Ein dadurch allein
unvollständiger Austrittsnachweis beendet den Gang nicht. Die Wirkung aktueller
Belegungszustände auf Gangwärme und die gemeinsame Türhilfe beschreibt die
[Heizpriorität](betrieb.md#heizpriorität).

## Gang durch einmalige Personenbelege erkennen

Einmalige Personenbelege aus Temperatur und Feuchte behaupten weder dauerhafte
Anwesenheit noch spätere Abwesenheit. Ein Aufguss bestätigt einen Gang, erzeugt
aber keinen fortbestehenden Belegungszustand.

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
ungenutzten Türbezug beginnt kein Gang. Eine vor ihrer tatsächlichen Zulassung
begonnene Aufgussepisode eröffnet keinen neuen Gang und verkürzt keine Kühlung;
sie kann einem bereits offenen Gang weiterhin zugeordnet werden. Ihr
ursprünglicher Beginn bleibt dabei erhalten.

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
