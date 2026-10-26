# Türöffnung, Aufguss und Durchlüften erkennen

Die [wirksame Präsenzquelle](gangmodell.md#wirksame-präsenzquelle) bestimmt den
Anwesenheitsnachweis. Türvorgänge, Lüften und Aufgüsse stammen in beiden
Verfahren aus Temperatur und Feuchte.

| Erkennung | Aus Temperatur und Feuchte | Mit Präsenzsensor |
| --- | --- | --- |
| Türöffnung und Türschluss | Zustandsmodell der Kabine | Dasselbe Zustandsmodell |
| Anwesenheit | Keine eigenen Personensignale; der erste Aufguss nach einem Eintritt eröffnet den Gang | Belegungszustand der zugeordneten Entität |
| Aufguss | Eröffnet oder ergänzt den Gang | Erfassung im bereits bestätigten Gang |
| Lüften und Durchgelüftet | Kurvenform der Feuchte während der offenen Tür; Nachweis für das Gangende | Bestandteil derselben Austrittsöffnung; zusammen mit deren Abwesenheitsnachweis Beleg für das Gangende |

Die Übergangsregeln beschreibt die [Gangerkennung](gangmodell.md). Die Erkennung
kennt weder Sitzung noch Gang oder Heizung; sie erhält ausschließlich
Messwerte und liefert Ereignisse mit physikalischem Zeitpunkt und
Bestätigungszeitpunkt.

## Messkanäle

Je Messposition (oben, unten) gehen Temperatur und die daraus und aus der
relativen Feuchte berechnete absolute Feuchte ein, zusammen bis zu vier
Kanäle. Takt der Auswertung ist die Temperaturmeldung der oberen Position,
ersatzweise der unteren; die übrigen Kanäle gelten mit ihrem letzten Wert.
Ein Messschritt wird ausgewertet, wenn alle Werte seines Zeitpunkts vorliegen
(Temperatur und Feuchte eines Sensorpakets gehören zusammen). Werte von vor
dem Sitzungsbeginn gelten ab dem Beginn. Mit nur einer Position arbeitet das
Modell mit deren beiden Kanälen.

Zeitachse ist die Messzeit: bei Sensorpaketen mit eigener Uhr deren
monotone Zeit ab dem ersten Wert (eine Korrektur der Sensoruhrzeit verschiebt
die Kurve nicht), sonst die Messzeit, wenn sie nicht nach dem Empfang liegt,
sonst der Empfang. Ein erneut geliefertes Original mit demselben Wert zählt
nicht noch einmal; ein verspätetes älteres Original ersetzt keinen neueren
Wert.

Die Luft der Umgebung (wohin die Kabinenluft bei offener Tür entweicht und
woher Frischluft kommt) ist der Mittelwert der ersten 30 Messschritte einer
Sitzung. Beginnt eine Sitzung wärmer als die Umgebungsluft einer früheren
Sitzung, ist die Kabine noch vom vorherigen Betrieb warm; dann gilt die
früher bestimmte Umgebungsluft.

## Zustandsmodell der Kabine

Die Kabine befindet sich zu jedem Zeitpunkt in genau einem Zustand. Jeder
Zustand beschreibt, wie sich Temperatur und Feuchte physikalisch ändern:

| Zustand | Verlauf |
| --- | --- |
| Zu | Grunddrift `r` (Heizen oder Abkühlen) |
| Offen | Temperatur: Luftaustausch mit der Umgebung `λ·(L − außen)` und Nachschub aus Wand und Steinen `β·(L − W)`; `W` ist der Pegel beim Öffnen. Feuchte: Luftaustausch `λ·(L − außen)` und Feuchtequelle `s` (Holz, Steine, Personen) |
| Aufguss | Feuchte steigt mit der Rate `exp(z)` |
| Abklingen | Feuchte fällt mit der Rate `exp(z)` |
| Erholung | Nach dem Schließen steigt die Temperatur mit eigener Rate `e ≥ 0` |
| Heizwechsel | Wie zu; die Grunddrift der Temperatur springt (Heizschütz) |

Je Kanal führt das Modell Pegel `L`, Grunddrift `r`, Ereignisgröße `z` und
bei der Temperatur den Wandpegel `W`, bei der Feuchte die Feuchtequelle `s`.
Die Feuchtequelle einer Öffnung beginnt mit ihrer gelernten Verteilung (oben
und unten gemeinsam, eine Ursache) und wandert während der Öffnung je Sensor
mit der gelernten Stärke `qs`; so bleibt ein kurzer Feuchteanstieg bei offener
Tür, etwa durch eine Person in der Tür, Teil der Öffnung. Physikalisch mögliche Übergänge sind: zu → offen, zu →
Aufguss, zu ↔ Heizwechsel, offen → Erholung, Aufguss → Abklingen,
Abklingen → zu/offen/Aufguss und Erholung → zu/offen/Aufguss. Alle anderen
Übergänge haben die Wahrscheinlichkeit null; die Übergangswahrscheinlichkeit
für einen Messabstand folgt aus den gelernten Raten über das
Matrixexponential.

Ein Ereignis hat eine Ursache für alle Kanäle: eine Tür, eine Dampfquelle.
Deshalb sind die Ereignisgrößen der Kanäle beim Beginn und in ihrer zeitlichen
Wanderung korreliert, und der Zustand aller Kanäle wird gemeinsam geschätzt.
Steigt die Feuchte nur an einem Sensor, ohne dass der andere dem Verhältnis
eines Aufgusses folgt, ist das kein Aufguss.

Während Tür, Aufguss und Abklingen bleibt die Grunddrift auf ihrem Wert vor
dem Ereignis; sie wird nicht aus dem Ereignisverlauf geschätzt. Bei
geschlossener Tür kehrt sie zu ihrer gelernten Verteilung zurück; zu Beginn
gilt die Steigung der ersten 30 Messschritte. Das
Messrauschen ist Student-t-verteilt; die Feuchtesensoren messen relative
Feuchte, darum wächst das Rauschen der absoluten Feuchte mit dem
Sättigungswert am selben Sensor. Bei offener Tür und während des Dampfes
schwankt der Pegel zusätzlich mit der Stärke des Ereignisses (Turbulenz).

Ein Filter für schaltende Zustandsraummodelle (IMM) berechnet nach jedem
Messschritt die Wahrscheinlichkeit jedes Zustands.

## Entscheidung

Die Entscheidung über einen Zeitpunkt bezieht die folgenden Messschritte ein
(Glättung über einen festen Nachlauf): 30 Schritte für Türvorgänge, 10 für
Aufgüsse. Ein Kandidat beginnt, sobald die Wahrscheinlichkeit des Zustands
`1 − q` überschreitet; bestätigt ist er, wenn sie `q` erreicht (`q = 0,9`).
Das Verlassen ist symmetrisch. Der physikalische Zeitpunkt eines Ereignisses
ist der Kandidatenbeginn, der Bestätigungszeitpunkt liegt um den Nachlauf
später.

| Ereignis | Zustand |
| --- | --- |
| Tür auf | Beginn des Zustands offen |
| Tür zu | Ende des Zustands offen |
| Aufguss | Beginn des Zustands Aufguss |
| Aufgussende | Ende von Aufguss und Abklingen zusammen; öffnet sich vorher die Tür, endet der Aufguss mit der Öffnung |

Ein Aufguss, der bestätigt wird, solange eine Türöffnung noch nicht
entschieden ist, folgt dem bestätigten Türschluss; er liegt dann nicht vor
diesem. Wird die Öffnung nicht bestätigt, gilt der Aufguss mit seinem
eigenen Zeitpunkt.

## Lüften und Durchgelüftet

Während der offenen Tür prüft die Erkennung die Kurvenform der Feuchte am
führenden Sensor:

| Stufe | Kurve | Bedeutung |
| --- | --- | --- |
| Lüften | ln relative Feuchte flacht ab | Die feuchte Kabinenluft ist ausgetauscht |
| Durchgelüftet | ln absolute Feuchte flacht ab (nach dem Lüften) | Die Feuchte hat ihr neues Gleichgewicht erreicht |

Modell ist eine stetige Gerade mit einem Knick:
`y = b0 + b1·(x − τ) + b2·max(x − τ, 0)` bei unbekanntem Rauschen und
flacher A-priori-Verteilung; über den Knickzeitpunkt `τ` wird mit seiner
Evidenz gemittelt. Abgeflacht heißt: vorher fallend (`b1 < 0`) und danach
nicht mehr steil fallend und nicht wieder steigend
(`b1/e < b1 + b2 < 0,05·|b1|`). Steigt die Feuchte nach dem Knick wieder an
(kurze Öffnung als V-Form), ist das kein Abflachen. Die Wahrscheinlichkeit
muss `q` erreichen, die Tür muss nach dem Knick offen bleiben (mittlere
Wahrscheinlichkeit des Zustands offen mindestens `q`; ein Schließen beendet den
Feuchteabfall ebenfalls), und die Temperatur muss nach dem Knick weiter fallen
(Wahrscheinlichkeit `q`). Jedes Segment umfasst mindestens 20 Messschritte.
Der Zeitpunkt der Stufe ist der Knick.

Lüften und Durchgelüftet sind Eigenschaften der Türöffnung. Ob sie einen Gang
beenden, entscheidet das [Gangmodell](gangmodell.md).

## Gelernte Größen und Nachlernen

Rauschen, Austauschkoeffizienten, Aufguss-, Abkling- und Erholungsraten,
deren Korrelation über die Kanäle, Turbulenz, Übergangsraten und die
Student-t-Formen stammen aus bewerteten Aufzeichnungen
(`recognition_parameters.json`, erzeugt mit `tools/recognition_learn.py`).
Verwendet werden Sitzungen ohne Türöffnung während eines Gangs.

| Größe | Schätzung |
| --- | --- |
| Messrauschen | Negative Autokovarianz der Messdifferenzen in ruhigen Abschnitten |
| Austausch `λ` und Nachschub `β` | Temperatur: Zweiknotenverlauf je Öffnung; `β` aus langen Öffnungen, dann `λ` je Öffnung mit seinem Standardfehler. Feuchte: `λ` und Quellstärke `s` je Öffnung mit ihren Standardfehlern |
| Verteilung der Ereignisgrößen | Zufallseffektmodell über alle Ereignisse (unsicher bestimmte Ereignisse zählen entsprechend wenig) |
| Korrelation zwischen Kanälen | Robuste Paarkorrelation der Ereignisgrößen und der Quellstärken |
| Wanderung der Feuchtequelle | Maximum der Likelihood von Fenstern um die bezeichneten Öffnungen (nur Lernaufzeichnungen) |
| Turbulenz bei Aufguss und Abklingen | Maximum der Likelihood von Fenstern um die bezeichneten Aufgüsse (nur Lernaufzeichnungen) |
| Heizwechsel | Zweikomponenten-Mischung der Steigungsänderungen ruhiger Minuten fern von Ereignissen |
| Übergangsraten | Zählungen und Verweildauern der bezeichneten Zustände |
| Student-t-Formen | Innovationen je bezeichnetem Zustand |

Nach jeder abgeschlossenen Sitzung mit beiden Messpositionen zieht die
Integration aus deren Messwerten und eigenen bestätigten Erkennungen neue
Belege und schätzt die Größen neu; die Rechnung läuft im Hintergrund. Die Belege der Lernaufzeichnungen bleiben
als Startwissen erhalten; die Belege der letzten 40 Sitzungen ergänzen sie.
Die neuen Größen gelten ab der nächsten Sitzung. Der Lernstand liegt im
Home-Assistant-Speicher der Instanz.

Die Aktion **Rückmeldung zur Erkennung** (`ha_sauna.recognition_feedback`)
lehnt ein erkanntes Ereignis ab (`reject`) oder hebt die Ablehnung auf
(`confirm`). Angegeben wird die Ereigniskennung oder Ereignisart und Zeitpunkt;
gewählt wird dann das nächstgelegene Ereignis dieser Art. Abgelehnte
Ereignisse fließen nicht mehr in das Lernen ein. Was als Tür, Lüften oder
Aufguss gilt, lernt die Integration nicht selbständig; dafür ist diese
Rückmeldung vorgesehen.

## Diagnose

Die Zustandswahrscheinlichkeiten des letzten Messschritts stehen als
Erkennungsdiagnose zur Verfügung; im Archivprofil für Analysen wird jeder
Schritt gespeichert. Jedes gebuchte Ereignis wird mit seiner
Wahrscheinlichkeit archiviert, ein vom Gangmodell nicht übernommenes
Ereignis mit dem Grund.
