# Zeitmodell

Diese Seite beschreibt, ab welchem Ereignis Fristen und Laufzeiten des
[Saunabetriebs](betrieb.md) zählen. Zeitstempel tragen eine Zeitzone und werden
intern in UTC geführt. Die Oberfläche verwendet die aktuelle lokale Zeitzone des Browsers.

## Laufzeiten und Fristen

Die angegebenen Dauern sind einstellbare Standardwerte.

| Vorgang | Beginn und Zeitbezug |
| --- | --- |
| Gangbeginn | Passende Türschließung derselben Sitzung und Türöffnung; ersatzweise Buchungszeit des zulässigen Personensignals oder Aufgusses. |
| Aufgussbestätigung | Frist bis Gangbeginn + 12 Minuten. Der erste zugeordnete Aufguss bestätigt den bestehenden Gang. |
| Wiederaufnahme und Lichtnachlauf | Betrieb-AUS + 15 Minuten, mit gemeinsamem Endzeitpunkt. Wiedereinschalten beendet den Lichtnachlauf. Nach einem langen Enddruck beginnt der Lichtnachlauf mit dem bestätigten Loslassen; die Sitzung ist bereits abgeschlossen. Die [Gestenzuordnung](betrieb.md#bedienhandlungen-und-betriebsart) unterscheidet Start- und Enddruck. |
| Ofenkühlung | Bestätigtes Schütz-AUS startet Berechnung und Laufzeit. Ausschließlich bestätigte AUS-Zeit zählt zur gespeicherten Dauer gemäß [Ofenkühlung](ofenkuehlung.md). |
| Mindestheizzeit | Tatsächlich bestätigter Beginn eines Heizintervalls + 10 Minuten. Ein laufendes Intervall behält seinen Beginn. |
| Thermostatpause | Reguläre Temperaturabschaltung + 5 Minuten. |
| Lichtübergang | Standardmäßig 30 Sekunden; bei Kühlbeginn höchstens die Hälfte der verbleibenden Kühlzeit. Der Lichtnachlauf endet durch unmittelbares Ausschalten. |
| Vorübergehende Ofen- oder Lichtwahl | Wahl + höchstens 10 Minuten. Frühere Rückkehrpunkte richten sich nach der jeweiligen [Bedienhandlung](betrieb.md). |
| Mechanischer Timer | Zählt maximal 240 Minuten bei Betrieb-EIN und bestätigtem Schütz-EIN. |

Jede Frist gehört zu einer Sitzung und einem bestimmten Vorgang. Ausschließlich
die aktuelle Frist dieses Vorgangs ist wirksam. Wiederholte Ereignisse behalten
dieselbe einmalige Wirkung; die Laufzeituhr schreitet zeitlich voran.

## Fachlicher Zeitpunkt und Verarbeitung

Ein Erkennungsereignis führt drei Zeitbezüge:

| Feld | Bedeutung |
| --- | --- |
| `effective_at` | Fachlich zugeordneter Ereigniszeitpunkt, beispielsweise der Beginn einer nachträglich bestätigten Türbewegung. |
| `booking_at` | Zeitpunkt der chronologischen Buchung im führenden Controller. |
| `detected_at` | Tatsächlicher Zeitpunkt, an dem die Erkennung das Ereignis feststellt. |

Es gilt `effective_at <= booking_at <= detected_at`. Bei unmittelbarer
Verarbeitung stimmen Buchungs- und Erkennungszeit überein. Originalmessungen
bewahren ihren Empfangszeitpunkt; ein Gerätezeitpunkt wird bei vorhandener
Quellenangabe übernommen. Die technischen Datenverträge stehen unter
[Schnittstellen](schnittstellen.md).

Beim Aufholen bereits empfangener Messungen verarbeitet der Controller deren
Erkennungsschritte vor zeitlich späteren Bedienhandlungen. Für jeden Schritt
gelten die damaligen Betriebs- und Kühlfreigaben. Ein damals zulässiger Aufguss
kann so einen Gang bestätigen, der durch eine folgende Bedienhandlung bereits
wieder beendet wird. Dieser Gang zählt zur damaligen Sitzung und zur
Temperaturfolge. Die aktuelle Heizanforderung ergibt sich anschließend aus dem
bis zur Gegenwart fortgeschriebenen Zustand. Gerätebefehle werden ausschließlich
zur tatsächlichen Verarbeitungszeit ausgegeben.

Am Ende einer Bestätigungsfrist werden die bereits empfangenen Messungen dieses
Zeitpunkts zuerst ausgewertet. Anschließend schließt die Steuerung die Frist
einschließlich ihres Endzeitpunkts ab. Gemeinsam bereitstehende
Geräterückmeldungen werden vor Beginn dieses Verarbeitungsschritts zugestellt.
Seine Eingangsmenge besteht aus den bis dahin empfangenen Messungen.

Jede empfangene, zeitlich wirksame Regeltemperatur erreicht den bestehenden
Controller in Eingangsreihenfolge. Das gilt auch für mehrere Meldungen, die
während eines wartenden Dienstaufrufs eingegangen sind. Die Auswahl verwendet
den gültigen oberen Wert und bei dessen Ausfall den gültigen unteren Wert.
Ein zwischenzeitliches Erreichen der Solltemperatur setzt die Bereitschaft
im selben Controllerzustand wie bei fortlaufender Verarbeitung.

Heizentscheidungen führen ihre logische Buchungszeit in `Decision.at` und
ihre tatsächliche Erzeugungszeit im Controller in `Decision.created_at`.
Der Archivrecord übernimmt `created_at` als `received_at`. Diese Herkunft
bleibt beim Puffern und späteren Schreiben erhalten. Historische Entscheidungen
werden weiterhin mit ihren ursprünglich gespeicherten Feldern gelesen.

Die historische Phasenansicht ordnet den Verlauf rückblickend zu. Zeitlich spät
erkannte Ereignisse können diese Zuordnung ergänzen. Die bestätigten
Schalterrückmeldungen bewahren dabei den tatsächlich gemeldeten Verlauf; ihre
Rolle für die [Phasen und Heizanforderung](praesenz-ofen-phasen.md) bleibt eindeutig.
