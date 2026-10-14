# Türöffnung, Aufguss und Durchlüften erkennen

Die [wirksame Präsenzquelle](gangmodell.md#wirksame-präsenzquelle) bestimmt den
Anwesenheitsnachweis. Türereignisse und Aufgüsse stammen in beiden Verfahren
aus Temperatur und Feuchte.

| Erkennung | Aus Temperatur und Feuchte | Mit Präsenzsensor |
| --- | --- | --- |
| Türöffnung und Türschluss | Gemeinsame Erkennung aus den Messpaaren | Dieselbe Erkennung aus den Messpaaren |
| Anwesenheit | Personensignale; zunächst vorläufiger Gang | Belegungszustand der zugeordneten Entität |
| Aufguss | Bestätigt den Gang; weitere Aufgüsse werden erfasst | Erfassung im bereits bestätigten Gang |
| Durchlüften | Nach Austrittsöffnung Nachweis für das Gangende | Gemeinsam mit zugehöriger Abwesenheit Nachweis für das Gangende |

**Sensoren und Erkennung** in der Integrationskonfiguration trennt
Messwertaufbereitung, Türerkennung, Personenerkennung, Aufgüsse und Lüftung. Personenschwellen und
Aufgussbestätigungsfrist erscheinen nur beim indirekten Verfahren.
Ihre gespeicherten Werte bleiben beim Verfahrenswechsel erhalten.
Die Übergangsregeln beschreibt die [Gangerkennung](gangmodell.md).

## Kurvenbeginn und Bestätigung

Für Türöffnung, Türschluss und Aufguss vergleicht die Erkennung eine lokale
Kurvenänderung mit glatten Trends. Dabei berücksichtigt sie das gemessene
Rauschen und den Einfluss einzelner Ausreißer. Ein unterstützter Kurvenbeginn
ist zunächst ein Kandidat; der Modellscore ist keine Ereigniswahrscheinlichkeit.
Vergleichszeiten und technische Modellvorgaben stehen im
[`Parameterkatalog`](../custom_components/ha_sauna/defaults.json).
Die technischen Modellvorgaben sind keine Einstellungen der Oberfläche.

Die Auswertung verwendet Originalmesspunkte innerhalb zusammenhängender
Quellenabschnitte mit einheitlicher Zeitbasis. Vorhandene gültige Messzeiten
bleiben erhalten; ersatzweise verwendete Empfangszeiten bilden eine eigene
Zeitbasis. Raster-Haltewerte liefern keine zusätzliche Evidenz.

Die Bestätigung benötigt für jede erforderliche Messgröße und Position einen
erneut unterstützten Modellvergleich am selben Beginn mit später
hinzugekommenen Originalmessungen. Die einzelnen Anfänge müssen innerhalb der
zugehörigen Vergleichszeit liegen. Zeitversetzte Meldungen behalten jeweils
ihren ersten Befund; der zuletzt vollständig gewordene Satz startet keine
zusätzliche Wartefrist. Wiederholte Rasterprüfungen und Korrekturen derselben
Originalprobe ersetzen diesen neuen Nachweis nicht.

Quellen- oder Kontextbrüche, widerlegte Kurvenformen und das Ablaufen eines
Kandidaten beenden seinen Nachweis. Korrekturen seiner Originalmessungen
erfordern eine erneute Prüfung an denselben Messpunkten und am selben Beginn;
bleibt er danach unbelegt, wird er verworfen. Fehlende Evidenz bestätigt keinen
Kandidaten.

Erst der vollständige neue Nachweis unter den folgenden fachlichen Bedingungen
bestätigt ein Ereignis. Sein belegter Kandidatenbeginn kann innerhalb des
zulässigen Tür- und Betriebskontexts als früherer Beginn zugeordnet werden.
Wird diese Zuordnung vom laufenden Kontext abgewiesen, gilt der
Bestätigungszeitpunkt. Die frühere Zuordnung zieht weder die fachliche
Bestätigung noch die reale Erkennungszeit vor.
Die Bedeutung der Zeitfelder
ist unter [Zeitangaben](speicherung.md#daten-und-zeitangaben) und
[nachträgliche Zuordnung im Verlauf](speicherung.md#nachträgliche-zuordnung-im-verlauf)
beschrieben.

## Öffnen und Schließen der Tür

Eine Öffnung benötigt eine unterstützte Änderung zu fallender Temperatur und
relativer Feuchte. Beide Änderungen dürfen zeitlich versetzt auftreten.
Eine vollständige Messposition genügt; bei zwei beteiligten Positionen müssen
beide den gemeinsamen Verlauf belegen. Die Regeln gelten auch bei hoher
Temperatur.

Ein zusätzlicher Öffnungsweg verwendet allein die Temperaturform bei
durchgehend bestätigtem Heizen. Sämtliche Originalmessungen seines
Kurvenvergleichs müssen aus diesem Heizabschnitt stammen. Eine
Heizunterbrechung verwirft diesen Nachweis.

Eine Schließung setzt eine erkannte Öffnung voraus. Eine Änderung vom
Temperaturabfall zu steigender Temperatur kann sie an allen noch auswertbaren
beteiligten Positionen auch bei gestörter Feuchtemessung belegen. Alternativ
kann ein Abbremsen des Temperaturabfalls zusammen mit einer passenden Erholung
der Feuchte einen früheren Schluss vor dem Temperaturminimum belegen. Dabei
darf die relative Feuchte bereits wieder steigen. Dieser gemischte Nachweis
muss aus derselben gemeinsam belegten Öffnung stammen und bleibt an die
ursprünglichen Feuchtequellen und deren ununterbrochene Messabschnitte gebunden.

## Anwesenheit und Aufgüsse erkennen

Der Feuchtenachweis benötigt steigende relative Feuchte und zunehmenden absoluten
Wassergehalt. Ein Aufguss bleibt dadurch auch bei sinkender Temperatur erkennbar.

Das deutliche Personensignal benötigt ein vollständiges Messfenster und die
Bestätigungsdauer. Die empfindlichere Prüfung gilt nach erkannter Türöffnung
mit anschließender Schließung für die eingestellte Bestätigungsfrist. Sie benötigt
einen gültigen Temperaturbezug und einen neuen Feuchteanstieg nach der Öffnung;
bestätigtes Durchlüften ist nicht erforderlich. Eine weitere Öffnung beginnt
einen neuen Türbezug.

Ein Aufguss wird bereits beim erneut durch neue Originalmessungen bestätigten
Feuchteanstieg erfasst, sofern der absolute Wassergehalt an allen beteiligten
Positionen zunimmt. Weitere Spitzen gehören bis zum belegten Abschluss zu
diesem einen Aufguss.

Bei ununterbrochenem Verlauf müssen zum Abschluss nach der Feuchtespitze ein
Abfall und anschließend an allen beteiligten Positionen ein neues Niveau belegt
sein. Der Niveauvergleich verwendet den jüngsten zusammenhängenden
Originalverlauf über die
Aufgussvergleichszeit und muss durch weitere neue Originalmessungen bestätigt
werden. Das Niveau wird gegenüber glatten Trends frei bestimmt; eine Rückkehr
zum Wert vor dem Aufguss ist nicht erforderlich. Ein später erkannter Anstieg,
der schon vor diesem Abschluss begann, setzt denselben Aufguss fort. Erst ein
nach dem belegten Abschluss begonnener Anstieg kann einen weiteren Aufguss
ergeben.

Personen- und Aufgusssignale eröffnen bei Erkennung aus Temperatur und Feuchte
nur nach erkannter Türöffnung und anschließender Schließung einen Gang;
es gelten die [Startbedingungen](gangmodell.md#gang-aus-temperatur-und-feuchte-erkennen).
Während eines Gangs werden weitere Aufgüsse erkannt; solange der Präsenzsensor
führt, ersetzen Personensignale ihn nicht.

## Durchlüften

Die absolute Feuchte wird aus frischen, gültigen Temperatur-/Feuchtepaaren
berechnet und als Diagnosewert je Messposition bereitgestellt.

Beim ersten Öffnungshinweis wird eine vollständige Messung aus der Zeit davor
als Referenz gespeichert. Durchlüften erfordert die eingestellten Verluste an
Temperatur und absolutem Wassergehalt; der Wasserverlust ist ein relativer Anteil.

| Zu Beginn gültige Positionen | Bestätigung |
| --- | --- |
| Zwei | Beide ursprünglichen Positionen erreichen die Verlustschwellen |
| Eine | Verlustschwellen erreicht und Mindestöffnungsdauer verstrichen |

Eine Messlücke pausiert den Vergleich mit unveränderter Referenz. Er setzt sich
mit denselben gültigen Quellen fort; ein Quellenwechsel verwirft die Referenz.
Temperaturerholung beginnt die Schließprüfung und beendet einen noch offenen
Lüftungsnachweis.

Ein bestätigter Lüftungsnachweis bleibt seiner Austrittsöffnung auch nach
Türschluss zugeordnet. Ob damit ein Gang endet, bestimmt die
[Gangerkennung](gangmodell.md); bei direkter Präsenzführung gehört dazu außerdem
eine gültige Abwesenheitsmeldung.

## Erkennungen bei Messlücken prüfen

Personenhaltezähler beginnen nach einem Wechsel oder Ausfall der beteiligten
vollständigen Messpositionen neu. Unbestätigte Kurvennachweise bleiben an ihre
jeweiligen Quellenabschnitte gebunden. Für die Lüftungsreferenz gilt die
beschriebene Pausenregel.

Bei einem bereits gezählten Aufguss pausieren Messlücken, Quellenwechsel und
vorübergehend fehlende Auswertbarkeit den Abschluss. Die Zuordnung bleibt
bestehen; ein unbekannter Verlauf belegt weder Abklingen noch ein neues Niveau.
Nach Wiedererwerb muss ein neues Niveau auf den wieder zusammenhängenden
Messreihen aller ursprünglich beteiligten Positionen erneut belegt werden.
Die Zuordnung endet bei einer bestätigten Türöffnung oder Betriebsunterbrechung.
Ein Aus- und Einschalten bleibt auch zwischen Rasterpunkten wirksam.

### Diagnose im Verlauf

Die Erkennungskontrolle zeigt gespeicherte Merkmale und Erkennungsnachweise.
Ein Markerklick hebt den zugehörigen Diagrammabschnitt hervor und zeigt den
auslösenden Erkennungsnachweis. Der Wechsel zur Ereignisliste erfolgt über
**Ereignis im Verlauf**. Der ausgewählte Prüfzeitpunkt enthält auch unerfüllte
Bedingungen und Prüfpunkte mit dem Zählerstand 0; fehlende Angaben werden davon
unterschieden. Weitere gespeicherte Prüfzeitpunkte sind einzeln auswählbar.

Schwellen beziehen sich auf die gespeicherte Sitzungskonfiguration. Fehlt diese
in älteren Aufzeichnungen, ist der damalige Schwellenwert nicht belegt.
Historische Tür- und Aufgussschwellen beschreiben die damalige Erkennung;
neue Kurvennachweise verwenden die beschriebene Originalbestätigung.
Haltezähler zählen Prüfpunkte, keine Sekunden; ihr Abstand folgt dem Prüfschritt.
Ein Ereignis verweist über `trace_at` auf seinen auslösenden Diagnosepunkt.
Ältere Archive verwenden dafür den fachlichen Ereignisbeginn.

[Zeitliche Zuordnung](speicherung.md#nachträgliche-zuordnung-im-verlauf) · [Originaldaten](speicherung.md)
