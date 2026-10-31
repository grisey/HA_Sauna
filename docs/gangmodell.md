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

Für Verlauf und Statistik reicht der Gang vom zugeordneten Eintritt bis zum
Abschluss des Austrittsvorgangs. Die Türöffnung allein beendet ihn nicht. Den
Abschluss belegt das bestätigte Durchlüften dieser Öffnung; das Gangende ist
dann seine Buchung. Im Sensorbetrieb belegt ihn außerdem die nach der Öffnung
leere Kabine; das Gangende ist dann die Austrittsöffnung selbst. Bis zur
Buchung des Abschlusses bleibt die Phase **Saunagang** bestehen; dazwischen
entsteht keine zusätzliche Aufheiz- oder Bereitschaftsphase.
Türöffnung und Türschluss bleiben als eigene Ereignisse sichtbar.
Fehlende Türquellen oder Betrieb-AUS ersetzen keinen Austrittsnachweis.
Bei unvollständigen Altarchiven bleibt der bisher aufgezeichnete Abschnitt
als unvollständige Zeitzuordnung erhalten.
Die angezeigte Dauer eines aktiven Gangs verwendet denselben belegten
Beginn bei erfüllter Mindesttemperatur. Fehlt dessen Türschluss, bleibt die Dauer unbekannt. API,
Vorschau und Zustandsentität verwenden dafür dieselbe Ableitung.

## Gang mit dem Präsenzsensor erkennen

Nur der Sensorbetrieb liefert einen Präsenzverlauf. Nach einem Türvorgang
stehen zwei Erklärungen gegeneinander: Jemand ist in der Kabine, oder sie ist
leer. Der Verlauf unterscheidet sie über vier gelernte Zeiten des Sensors:

| Zeit | Bedeutung |
| --- | --- |
| Haltezeit | Nach dem Verlassen meldet der Sensor noch so lange Anwesenheit, wie seine Abwesenheitsverzögerung und das Verlassen seines Bereichs dauern |
| Aufenthalt | Zeit vom Türschluss nach einem Eintritt bis zur ersten Abwesenheitsmeldung |
| Aussetzer | Abwesenheitsmeldung, obwohl jemand in der Kabine ist, bis zur nächsten Anwesenheit |
| Leerlauf | Abwesenheit bei leerer Kabine bis zur nächsten Anwesenheitsmeldung |

Entschieden wird, sobald eine Erklärung mindestens 150-mal wahrscheinlicher
ist als die andere, dem Beweisverhältnis der [Erkennung](erkennung.md). Es gibt
keine festgelegte Wartezeit; die Zeit bis zur Entscheidung folgt aus den
gelernten Verteilungen. Die Zeiten stammen aus Aufzeichnungen mit
Präsenzsensor und bewerteter Referenz
(`recognition_parameters.json`, Block `presence`, erzeugt mit
`tools/presence_learn.py`); das Nachlernen der Erkennung übernimmt sie
unverändert. Bei der Haltezeit trennt das Lernen Abschnitte ab, in denen der
Sensor nach dem Austritt weiter eine Person erfasst; diese gehören nicht zur
Haltezeit.

| Abschnitt des Verlaufs | Beleg |
| --- | --- |
| Anwesenheit ab dem Türschluss | Aufenthalt gegen Haltezeit: je länger sie über die Haltezeit hinaus andauert, desto stärker für einen Aufenthalt; endet sie innerhalb der Haltezeit, für eine leere Kabine |
| Abwesenheit, danach Anwesenheit bei geschlossener Tür | Aussetzer gegen Leerlauf |
| Andauernde Abwesenheit | Aussetzer gegen Leerlauf, nach ihrer bisherigen Dauer |
| Rückkehr der Anwesenheit bei offener Tür | Kein Beleg |
| Weitere Anwesenheitsabschnitte | Kein Beleg |
| `unknown`, `unavailable`, Quellenwechsel | Schließt den laufenden Abschnitt mit seiner beobachteten Dauer; mit dem nächsten bekannten Zustand beginnt ein neuer Abschnitt |

Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs. Eine
später empfangene Meldung mit früherer Zustandszeit geht in die Auswertung ein.
Eine Meldung zählt ab ihrer tatsächlichen Quellenfreigabe.

### Eintritt

Anwesenheit beim Türschluss ist die Voraussetzung eines Eintritts. Maßgeblich
ist der Beginn des Türschlusskandidaten aus dem erhaltenen Türschlussbeleg; ein
rückblickend zugeordneter Türzeitpunkt ersetzt ihn nicht. Ein Türschluss ohne
diesen Beleg begründet keinen Eintritt durch den Sensor. Belegt der
anschließende Verlauf den Aufenthalt, beginnt der Gang am Türschluss; erkannt
ist er zum Zeitpunkt dieser Entscheidung. Ein kurzer Überhang nach einem
Austritt erzeugt so keinen Gang. Ein Aussetzer nach dem Eintritt verhindert ihn
nicht, solange der Verlauf nicht die leere Kabine belegt.

Ein Aufguss bestätigt einen Eintritt mit Anwesenheit beim Türschluss auch dann,
wenn der Präsenzverlauf noch nicht entschieden hat; als Beginn gilt ebenfalls
der Türschluss. Hat der Verlauf die leere Kabine belegt oder lag beim
Türschluss keine Anwesenheit vor, eröffnet ein Aufguss im Sensorbetrieb keinen
Gang. Für ihn gilt die [Frist für Personenhinweis und Aufguss](#gang-aus-temperatur-und-feuchte-erkennen).

Liegt die Temperatur beim belegten Eintritt noch unter der Mindesttemperatur,
bleibt der Eintritt bis zu einer neuen Türöffnung erhalten. Der Gang beginnt
beim Erreichen der Grenze, ohne erneute Türfolge und ohne Rückdatierung auf den
kälteren Eintritt.

### Aufenthalt und Austritt

Bei geschlossener Tür bleibt die Person im Raum: Abwesenheit ohne neue
Türöffnung beendet keinen Gang, auch nicht ein später verdeckter Sensor. Nach
einer neuen Öffnung bewertet der Verlauf ab dieser Öffnung, ob noch jemand in
der Kabine ist. Eine schon vor der Öffnung bestehende Abwesenheit gilt bis zur
Öffnung als Aussetzer; die leere Kabine kann erst mit der Öffnung beginnen.
Nach dem anschließenden Türschluss gilt dessen Anwesenheitsabschnitt wie beim
Eintritt.

| Beleg nach der Austrittsöffnung | Wirkung |
| --- | --- |
| Bestätigtes Durchlüften der Öffnung | Gangende zum Buchungszeitpunkt der Lüftungsbestätigung, unabhängig vom Sensorzustand |
| Leere Kabine | Gangende an der Austrittsöffnung; ein bereits beobachteter Türschluss dieses Vorgangs gehört zum Austritt und belegt keinen neuen Eintritt |
| Aufenthalt | Der Gang läuft weiter; spätere Abwesenheit ohne neue Öffnung ändert das nicht |
| Unentschieden | Der Gang läuft weiter |
| Betrieb-AUS | Ende des offenen Gangs |

Nach einem durch Lüften beendeten Gang kann der anschließende Türschluss mit
Anwesenheit einen neuen Eintritt belegen, wenn der Sensor nach der
Austrittsöffnung Abwesenheit gemeldet hat. Hält die Anwesenheit über den ganzen
Austrittsvorgang an, gehört sie zur austretenden Person, deren
Abwesenheitsmeldung später eintrifft; dieser Türschluss belegt dann keinen
neuen Eintritt. Ein Aufguss bestätigt den Aufenthalt auch in diesem Fall. Eine ältere Austrittsfolge kann
keinen später begonnenen Gang beenden. Die aktuell ausgewählte Quelle bestimmt
die Abschlussregel, auch nach einem Quellenwechsel; die ursprüngliche
Bestätigungsart des Gangs bleibt erhalten. Im Proxybetrieb beendet allein das
bestätigte Durchlüften den Gang.

Aufgüsse werden dem Gang zugeordnet, bestätigen ihn aber nicht erneut.
Einzelne Belege des Proxybetriebs (Aufgüsse) bilden keinen Präsenzverlauf; für
sie gelten die nachfolgenden Regeln des indirekten Verfahrens. Die Wirkung
aktueller Belegungszustände auf Gangwärme und die gemeinsame Türhilfe
beschreibt die [Heizpriorität](betrieb.md#heizpriorität).

Der Sensor muss die Kabine erfassen und nicht den Bereich vor der Tür. Bleibt
eine Person nach dem Austritt vor der Tür im Erfassungsbereich, ist sie nach
einem weiteren Türvorgang vom Aufenthalt in der Kabine nicht zu unterscheiden
und erzeugt einen Gang.

## Gang aus Temperatur und Feuchte erkennen

Die Erkennung aus Temperatur und Feuchte liefert Türvorgänge, Lüften und
Aufgüsse, aber keine eigenen Personensignale. Ein Aufguss belegt eine Person in
der Kabine; er behauptet weder dauerhafte Anwesenheit noch spätere Abwesenheit.
Ein Gang ohne Aufguss wird im Proxyverfahren deshalb nicht erkannt; dafür ist
ein Präsenzsensor vorgesehen.

| Ereignis | Wirkung |
| --- | --- |
| Erster gültiger Aufguss nach vollständigem Eintritt | Eröffnet einen bestätigten Gang |
| Weiterer Aufguss | Ergänzt den aktiven Gang |
| Bestätigtes Lüften nach Austrittsöffnung | Gangende zum Buchungszeitpunkt der Lüftungsbestätigung |
| Betrieb-AUS | Ende des offenen Gangs |

Einmalige Personensignale anderer Herkunft (etwa aus älteren Aufzeichnungen)
behandelt das Gangmodell weiterhin: Ein zulässiges Personensignal nach
vollständigem Eintritt eröffnet einen vorläufigen Gang, den der erste gültige
Aufguss mit gleichem Beginn bestätigt. Läuft die Bestätigungsfrist ab oder wird
vor der Bestätigung nach einer Austrittsöffnung gelüftet, wird der vorläufige
Gang zurückgenommen.

Als Beginn gilt die passende Türschließung, frühestens jedoch der Beginn des
anschließenden gültigen Temperaturbereichs ab Mindesttemperatur. Die
Aufguss- und Personennachweise müssen zu diesem Eintritt gehören. Ohne gültigen,
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
