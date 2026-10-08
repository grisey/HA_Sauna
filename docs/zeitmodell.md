# Zeitmodell

Zeitstempel tragen eine Zeitzone und werden intern in UTC geführt. Die
Oberfläche verwendet die lokale Zeitzone des Browsers.

## Laufzeiten und Fristen

Dauern stammen aus den [Parametern](parameter.md). Maßgeblich ist jeweils
der folgende Beginn; spätere Verarbeitung verschiebt ihn nicht.

| Vorgang | Beginn und Zeitbezug |
| --- | --- |
| Gangbeginn | Passende Türschließung derselben Sitzung und Türöffnung; ersatzweise Buchungszeit des zulässigen Personensignals oder Aufgusses. |
| Aufgussbestätigung im Proxyverfahren | Gangbeginn + eingestellte Bestätigungsdauer. Der erste zugeordnete Aufguss bestätigt den bestehenden Gang. |
| Wiederaufnahme und Lichtnachlauf | Betrieb-AUS + eingestellte Wiederaufnahmezeit, mit gemeinsamem Endzeitpunkt. Wiedereinschalten beendet den Lichtnachlauf. Nach einem langen Enddruck beginnt der Lichtnachlauf mit dem bestätigten Loslassen; die Sitzung ist bereits abgeschlossen. Die [Gestenzuordnung](betrieb.md#bedienhandlungen-und-betriebsart) unterscheidet Start- und Enddruck. |
| Ofenkühlung | Bestätigtes Schütz-AUS startet Berechnung und Laufzeit. Ausschließlich bestätigte AUS-Zeit zählt zur gespeicherten Dauer gemäß [Ofenkühlung](ofenkuehlung.md). |
| Mindestheizzeit | Tatsächlich bestätigter Beginn eines Heizintervalls + eingestellte Mindestheizzeit. Ein laufendes Intervall behält seinen Beginn. |
| Thermostatpause | Reguläre Temperaturabschaltung + eingestellte Heizpause. |
| Lichtübergang | Eingestellte Übergangsdauer; bei Kühlbeginn höchstens die Hälfte der verbleibenden Kühlzeit. Der Lichtnachlauf endet durch unmittelbares Ausschalten. |
| Vorübergehende Ofen- oder Lichtwahl in Automatik | Wahl + eingestellte Übersteuerungsdauer. Danach gilt die aktuelle Automatik. Frühere Rückkehrpunkte richten sich nach der jeweiligen [Bedienhandlung](betrieb.md#bedienhandlungen-und-betriebsart). |
| Mechanischer Timer | Verbleibende konfigurierte Laufzeit zählt bei Betrieb-EIN und bestätigtem Schütz-EIN herunter. |

Jede Frist gehört zu einer Sitzung und einem bestimmten Vorgang. Ausschließlich
die aktuelle Frist dieses Vorgangs ist wirksam. Wiederholte Ereignisse behalten
dieselbe einmalige Wirkung.

Ofen- und Lichtwahl in der eigenständigen Betriebsart Manuell haben keine
Übersteuerungsfrist. Die Aufgussbestätigungsfrist gehört ausschließlich zum
Proxyverfahren; bei direkter Präsenzführung gilt sie nicht.

[Betriebsarten](betrieb.md#bedienhandlungen-und-betriebsart) ·
[Präsenzquellen](praesenz-ofen-phasen.md#präsenzquellen-im-aktuellen-programm)

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
Quellenangabe übernommen.

[Datenverträge](schnittstellen.md)

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

Für die Gangerkennung bilden alle bereits gemeinsam aufgenommenen Messungen
mit gleichem Empfangszeitpunkt ein Raster. Dessen Gangfreigabe verwendet die
abschließende führende Temperatur samt Gültigkeitsfrist aus derselben
Geräteauswahl wie die Heizregelung. Eine dazwischen zugestellte Betriebskante
ändert dieses Raster nicht. Betriebskanten, Thermostat und Bereitschaft werden
weiter in Eingangsreihenfolge gebucht: Ein erst später eingegangener
Temperaturwert verändert nicht rückwirkend den Zustand beim Einschalten.
Die gemeinsame Temperaturfreigabe gilt ausschließlich während der Buchung
dieser Empfangsgruppe; zeitlich frühere und folgende Gruppen bleiben getrennt.

Heizentscheidungen führen ihre logische Buchungszeit in `Decision.at` und
ihre tatsächliche Erzeugungszeit im Controller in `Decision.created_at`.
Der Archivrecord übernimmt `created_at` als `received_at`. Diese Herkunft
bleibt beim Puffern und späteren Schreiben erhalten. Historische Entscheidungen
werden weiterhin mit ihren ursprünglich gespeicherten Feldern gelesen.

Die historische Phasenansicht ordnet den Verlauf rückblickend zu. Zeitlich spät
erkannte Ereignisse können diese Zuordnung ergänzen. Die bestätigten
Schalterrückmeldungen bewahren dabei den tatsächlich gemeldeten Verlauf.

[Phasen und Heizanforderung](praesenz-ofen-phasen.md)
