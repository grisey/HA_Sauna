# Beginn, Bestätigung und Ende eines Saunagangs

Die gewählte Präsenzquelle bestimmt, wie ein Saunagang erkannt wird: mit einem
direkten Präsenzsensor oder aus [Temperatur und Feuchte](erkennung.md)
(Proxyverfahren).

## Wann ein Gang beginnen kann

Bei beiden Erkennungsverfahren benötigt ein neuer Gang Betrieb-EIN, keine angeforderte
oder laufende Ofenkühlung und eine frische, gültige
[Regeltemperatur](betrieb.md#aufheizen-und-bereitschaft) ab der eingestellten
Mindesttemperatur.
Ein vollständiger Türvorgang besteht aus erkannter Öffnung und anschließender
Schließung. Beide Verfahren benötigen ihn für den Eintritt. Für den erkannten
Austritt genügen eine neue Türöffnung und der passende Erkennungsnachweis;
ein anschließender Türschluss ist nicht erforderlich.
Der initiale Türzustand ist geschlossen; er ersetzt keinen Türvorgang.

Ein späterer Temperaturabfall beendet einen aktiven Gang nicht; Aufgüsse und
Enderkennung bleiben möglich. Nach einer Freigabe verwendet die Erkennung neue
Messfenster aus dem freigegebenen Zeitraum.

## Gang mit direktem Präsenzsensor erkennen

Beim Schließen einer vollständigen Türfolge muss Anwesenheit anliegen.
Sie kann bereits vor der Öffnung bestanden haben. Eine spätere Präsenzmeldung
startet keinen Gang anhand einer früheren Türfolge. Bei verspäteter Türerkennung
zählt der bereits empfangene Präsenzzustand zur ursprünglichen Schließzeit.
Jeder Türvorgang begründet höchstens einen Übergang.
Ein Gang endet erst bei Abwesenheit nach einer neuen Austrittsöffnung und
bestätigtem Durchlüften derselben Öffnung. Die beiden Nachweise können in
beliebiger Reihenfolge eintreffen; Anwesenheit, `unknown` oder `unavailable`
vor dem vollständigen Nachweis verhindern das Gangende.
Abwesenheit nach einer bereits geschlossenen Türfolge benötigt eine neue
Austrittsöffnung. Eine verspätet empfangene Abwesenheitsmeldung bleibt gültig,
wenn ihre Zustandszeit innerhalb der damaligen Öffnungsfolge liegt.

| Beleg | Wirkung |
| --- | --- |
| Vollständiger Türvorgang mit Anwesenheit und erfüllten Startbedingungen | Bestätigter Gang ab Türschluss, ohne Wartefrist oder erforderlichen Aufguss |
| Neue Türöffnung mit zugehöriger Abwesenheit und bestätigtem Durchlüften | Gangende beim vollständigen Nachweis, ohne erforderlichen Türschluss |
| Einzelne Präsenzmeldung, Türschluss ohne Öffnung oder bloßer Zeitablauf | Kein Übergang |
| Durchlüften ohne zugehörige Abwesenheit | Kein Gangende |
| Betrieb-AUS | Ende des offenen Gangs |

Ein Türvorgang unterhalb der Mindesttemperatur wird durch späteres Aufheizen
nicht nachträglich zum Gangbeginn.
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

Als Beginn gilt die passende Türschließung derselben Sitzung und Türöffnung,
wenn zu diesem Zeitpunkt die Temperaturfreigabe bestand. Ein ungenutzter
Türbezug verfällt bei unterbrochener Temperaturfreigabe oder veraltetem Messwert.
Ohne gültigen, ungenutzten Türbezug beginnt kein Gang. Das gilt auch für ein
deutliches Personensignal oder einen Aufguss.

Die Aufgussbestätigungsfrist läuft ab Gangbeginn einschließlich ihres
Endzeitpunkts. Auch ein Gangbeginn allein durch Aufguss muss innerhalb dieser
Frist ab Türschluss liegen. Nach Ablauf oder Rücknahme benötigt ein neuer Gang
einen neuen vollständigen Eintritt.

Bestätigtes Durchlüften nach der Austrittsöffnung beendet den Gang auch bei
offener Tür; Gangzählung und Ofenkühlung folgen dann. Eine kurze Türöffnung
ohne bestätigtes Durchlüften beendet keinen Gang.

## Gang zählen und nächste Temperaturstufe beginnen

Nur beendete, bestätigte Gänge zählen, jeweils einmal und unabhängig vom
Endgrund. Gleichzeitig folgt die nächste Stufe des
[Temperaturprogramms](bedienung.md#temperatur-während-der-sitzung-ändern).
Beginn und zugeordnete Aufgüsse bleiben erhalten.

[Heizpriorität](betrieb.md#heizpriorität) · [Ofenkühlung](ofenkuehlung.md)
