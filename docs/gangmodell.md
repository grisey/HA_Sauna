# Beginn, Bestätigung und Ende eines Saunagangs

Die gewählte Präsenzquelle bestimmt, wie ein Saunagang erkannt wird: mit einem
direkten Präsenzsensor oder aus [Temperatur und Feuchte](erkennung.md)
(Proxyverfahren).

Die Erkennungsregeln gelten auch während der manuellen Aufzeichnung.
Heizanforderungen, Ofenkühlung und Temperaturstufen wirken nur in Automatik.

## Eintritt, Aufenthalt und Verlassen

Türöffnung und Türschluss werden unabhängig von der Mindesttemperatur erkannt.
Der initiale Türzustand ist geschlossen; er ersetzt keine beobachtete Öffnung.

| Situation | Gangerkennung |
| --- | --- |
| Noch kein Gang | Eintritt und Anwesenheitsnachweis; Beginn frühestens bei gültiger Regeltemperatur ab der Mindesttemperatur |
| Gang läuft | Aufgüsse und Gangende bleiben auch unterhalb der Mindesttemperatur erkennbar |
| Ofenkühlung läuft | Ein erkannter neuer Gang beendet die Kühlung |
| Betrieb-AUS | Keine neuen Gänge |

Beim Eintritt folgt auf die Öffnung der Türschluss. Beim Verlassen wird die Tür
zum Durchlüften geöffnet und offengelassen. Die spätere Rückkehr durch diese
offene Tür kann mit dem anschließenden Türschluss einen neuen Eintritt belegen.
Das Ende des alten Gangs verbraucht diesen späteren Türschluss nicht.

## Gang mit direktem Präsenzsensor erkennen

Beim Schließen einer vollständigen Türfolge muss Anwesenheit anliegen.
Sie kann bereits vor der Öffnung bestanden haben. Eine spätere Präsenzmeldung
startet keinen Gang anhand einer früheren Türfolge. Bei verspäteter Türerkennung
zählt der bereits empfangene Präsenzzustand zur ursprünglichen Schließzeit.
Liegt die Temperatur beim Eintritt noch unter der Mindesttemperatur, bleibt der
belegte Eintritt bei anhaltender Präsenz erhalten. Der Gang beginnt beim Erreichen
der Grenze, ohne erneute Türfolge und ohne Rückdatierung auf den kälteren Eintritt.
Abwesenheit oder ein unbekannter Präsenzzustand unterbrechen diesen Nachweis.
Ein Gang endet erst bei Abwesenheit nach einer neuen Austrittsöffnung und
bestätigtem Durchlüften derselben Öffnung. Die beiden Nachweise können in
beliebiger Reihenfolge eintreffen; Anwesenheit, `unknown` oder `unavailable`
vor dem vollständigen Nachweis verhindern das Gangende.
Abwesenheit nach einer bereits geschlossenen Türfolge benötigt eine neue
Austrittsöffnung. Eine verspätet empfangene Abwesenheitsmeldung bleibt gültig,
wenn ihre Zustandszeit innerhalb der damaligen Öffnungsfolge liegt.

| Beleg | Wirkung |
| --- | --- |
| Eintritt mit Anwesenheit und erreichter Mindesttemperatur | Bestätigter Gang, ohne Wartefrist oder erforderlichen Aufguss |
| Öffnen zum Verlassen mit zugehöriger Abwesenheit und bestätigtem Durchlüften | Gangende beim vollständigen Nachweis während dieser Öffnungsphase |
| Einzelne Präsenzmeldung, Türschluss ohne Öffnung oder bloßer Zeitablauf | Kein Übergang |
| Durchlüften ohne zugehörige Abwesenheit | Kein Gangende |
| Betrieb-AUS | Ende des offenen Gangs |

Aufgüsse werden dem Gang zugeordnet, bestätigen ihn aber nicht erneut.
Gemeldete Zustandszeiten gelten ohne Abzug eines vermuteten Sensorverzugs.

`unknown` und `unavailable` belegen keine Abwesenheit. Der Gang bleibt offen,
seine zusätzliche Heizanforderung entfällt bis zur Rückkehr der Quelle.
Thermostat und Schutz bleiben wirksam. Es gibt keinen automatischen Wechsel zum
Proxyverfahren und keine Tür-Mindestheizhilfe.

## Gang aus Temperatur und Feuchte erkennen

| Ereignis | Wirkung |
| --- | --- |
| Zulässiges Personensignal nach vollständigem Eintritt | Vorläufiger Gang |
| Erster gültiger Aufguss | Bestätigt den vorläufigen Gang mit gleichem Beginn oder eröffnet nach vollständigem Eintritt einen bestätigten Gang |
| Weiterer Aufguss | Ergänzt den aktiven Gang |
| Bestätigungsfrist abgelaufen oder Durchlüften nach Austrittsöffnung vor Bestätigung | Vorläufiger Gang zurückgenommen |
| Bestätigtes Durchlüften nach Austrittsöffnung | Gangende zum Buchungszeitpunkt der Lüftungsbestätigung |
| Betrieb-AUS | Ende des offenen Gangs |

Als Beginn gilt die passende Türschließung, frühestens jedoch der Beginn des
anschließenden gültigen Temperaturbereichs ab Mindesttemperatur. Die
Personen- und Aufgussnachweise müssen zu diesem Eintritt gehören. Ohne gültigen,
ungenutzten Türbezug beginnt kein Gang.

Die Aufgussbestätigungsfrist läuft ab Gangbeginn einschließlich ihres
Endzeitpunkts. Auch ein Gangbeginn allein durch Aufguss muss innerhalb dieser
Frist ab möglichem Gangbeginn liegen. Nach Ablauf oder Rücknahme benötigt ein neuer Gang
einen neuen vollständigen Eintritt.

Bestätigtes Durchlüften während der Austrittsöffnung beendet den Gang;
Gangzählung und Ofenkühlung folgen dann. Eine kurze Türöffnung
ohne bestätigtes Durchlüften beendet keinen Gang.

## Gang zählen und nächste Temperaturstufe beginnen

Nur beendete, bestätigte Gänge zählen, jeweils einmal und unabhängig vom
Endgrund. Gleichzeitig folgt die nächste Stufe des
[Temperaturprogramms](bedienung.md#temperatur-während-der-sitzung-ändern).
Beginn und zugeordnete Aufgüsse bleiben erhalten.

[Heizpriorität](betrieb.md#heizpriorität) · [Ofenkühlung](ofenkuehlung.md)
