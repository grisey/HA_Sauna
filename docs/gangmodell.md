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
Abschluss belegt in beiden Verfahren das bestätigte Durchlüften dieser Öffnung;
das Gangende ist seine Buchung. Bis dahin bleibt die Phase **Saunagang**
bestehen; dazwischen entsteht keine zusätzliche Aufheiz- oder Bereitschaftsphase.
Türöffnung und Türschluss bleiben als eigene Ereignisse sichtbar.
Fehlende Türquellen oder Betrieb-AUS ersetzen keinen Austrittsnachweis.
Bei unvollständigen Altarchiven bleibt der bisher aufgezeichnete Abschnitt
als unvollständige Zeitzuordnung erhalten.
Die angezeigte Dauer eines aktiven Gangs verwendet denselben belegten
Beginn bei erfüllter Mindesttemperatur. Fehlt dessen Türschluss, bleibt die Dauer unbekannt. API,
Vorschau und Zustandsentität verwenden dafür dieselbe Ableitung.

## Gang mit dem Präsenzsensor erkennen

Im Sensorbetrieb zählt der Belegungszustand beim Türschluss. Zeitverläufe des
Sensors nach dem Türschluss, etwa seine Abwesenheitsverzögerung, gehen nicht in
die Erkennung ein.

| Beleg | Wirkung |
| --- | --- |
| Anwesenheit beim Türschluss nach vollständigem Eintritt | Eröffnet mit der Bestätigung des Türschlusses einen vorläufigen Gang; Beginn ist der Türschluss |
| Weiterer Türvorgang mit Anwesenheit beim Türschluss vor der Bestätigung | Ersetzt den vorläufigen Gang; Beginn und Frist gehen vom neuen Türschluss aus |
| Erster Aufguss im vorläufigen Gang | Bestätigt ihn mit gleichem Beginn |
| Ablauf der [Frist für Personenhinweis und Aufguss](#gang-aus-temperatur-und-feuchte-erkennen) ohne Aufguss | Nimmt den vorläufigen Gang zurück |
| Bestätigtes Durchlüften einer neuen Öffnung | Beendet den bestätigten Gang zum Buchungszeitpunkt; nimmt einen vorläufigen zurück |
| Abwesenheit, `unknown`, `unavailable` | Kein Übergang |
| Betrieb-AUS | Ende des offenen Gangs |

Maßgeblich ist der Zustand am Beginn des Türschlusskandidaten aus dem erhaltenen
Türschlussbeleg; ein rückblickend zugeordneter Türzeitpunkt ersetzt ihn nicht.
Erkannt und wirksam wird der Gang frühestens mit der Bestätigung des
Türschlusses, bei später empfangener Meldung mit deren Empfang.
Ein Türschluss ohne diesen Beleg eröffnet keinen Gang durch den Sensor. Ein
unveränderter Zustand `on` gilt ohne weitere Meldung fort; erst nach dem
Kandidatenbeginn einsetzende Anwesenheit zählt nicht. `unknown`, `unavailable`
oder ein Quellenwechsel vor dem Kandidatenbeginn unterbrechen den Nachweis.
Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs. Eine
später empfangene Meldung mit früherer Zustandszeit geht in die Auswertung ein;
eine Meldung zählt ab ihrer tatsächlichen Quellenfreigabe.

Liegt die Temperatur beim Eintritt noch unter der Mindesttemperatur, bleibt der
Eintritt bis zu einer neuen Türöffnung erhalten. Der vorläufige Gang beginnt
beim Erreichen der Grenze, ohne erneute Türfolge und ohne Rückdatierung auf den
kälteren Eintritt.

Wie im Proxybetrieb beginnt ein bestätigter Gang damit am letzten Eintritt vor
dem ersten Aufguss, und allein das bestätigte Durchlüften beendet ihn.
Abwesenheit beendet keinen Gang, auch nicht nach einer Türöffnung. Nach einer
Türöffnung im Gang beendet gemeldete Abwesenheit nur die Ganganforderung
([Heizpriorität](betrieb.md#heizpriorität)). Eine ältere
Austrittsfolge kann keinen später begonnenen Gang beenden. Bei einem
Quellenwechsel bleibt die ursprüngliche Bestätigungsart des Gangs erhalten.

Nach einem durch Lüften beendeten Gang eröffnet der Türschluss desselben
Türvorgangs nur dann einen vorläufigen Gang, wenn der Sensor nach der
Austrittsöffnung Abwesenheit gemeldet hat. Hält die Anwesenheit über den ganzen
Austrittsvorgang an, gehört sie zur austretenden Person. Ein anschließender
Aufguss eröffnet in diesem Fall einen bestätigten Gang mit Beginn am Türschluss.
Ohne Anwesenheit beim Türschluss eröffnet ein Aufguss im Sensorbetrieb keinen
Gang.

Weitere Aufgüsse werden dem Gang zugeordnet. Die Wirkung vorläufiger und
bestätigter Gänge auf die Gangwärme beschreibt die
[Heizpriorität](betrieb.md#heizpriorität), die auf eine laufende Kühlung die
[Ofenkühlung](ofenkuehlung.md#kühlung-abwarten-oder-vorzeitig-beenden).

Erfasst der Sensor auch den Bereich vor der Tür, kann eine Person dort beim
Türschluss einen vorläufigen Gang eröffnen; ohne Aufguss wird er zurückgenommen.

## Gang aus Temperatur und Feuchte erkennen

Die Erkennung aus Temperatur und Feuchte liefert Türvorgänge, Lüften und
Aufgüsse, aber keine eigenen Personensignale. Ein Aufguss belegt eine Person in
der Kabine; er behauptet weder dauerhafte Anwesenheit noch spätere Abwesenheit.
Ein Gang ohne Aufguss wird im Proxyverfahren deshalb nicht erkannt; im
Sensorbetrieb bleibt er vorläufig.

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
