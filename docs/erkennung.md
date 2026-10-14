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

Für Türöffnung, Türschluss und Aufguss kann ein mathematischer
Kurvenbeginn-Kandidat einen früheren Beginn vorschlagen. Der lokale
Kurvenvergleich berücksichtigt das gemessene Rauschen, glatte Trends und den
Einfluss einzelner Ausreißer. Sein Score ist keine Ereigniswahrscheinlichkeit
und löst allein kein Ereignis aus. Die Vorgaben stammen aus dem internen
Parameterkatalog und sind keine Einstellungen der Oberfläche.

Die Auswertung verwendet Originalmesspunkte innerhalb zusammenhängender
Quellenabschnitte mit einheitlicher Zeitbasis. Vorhandene gültige Messzeiten
bleiben erhalten; ersatzweise verwendete Empfangszeiten bilden eine eigene
Zeitbasis. Raster-Haltewerte liefern keine zusätzliche Evidenz. Ein Kandidat
behält seinen Beginn und wird mit neuen Originalmessungen erneut geprüft.
Gegenbelege verwerfen ihn; fehlende Evidenz ist kein Gegenbeleg und bestätigt
ihn nicht. Quellenwechsel, ungültige oder veraltete Messungen und ein Wechsel
des Ereigniskontexts beenden seine Gültigkeit.

Erst die folgenden fachlichen Tür- und Aufgussregeln bestätigen ein Ereignis;
Mindestverluste, Feuchtenachweise und Bestätigungsdauern bleiben unverändert.
Dabei kann ein passender, weiterhin belegter Kandidatenbeginn zugeordnet werden.
Fehlt er oder überschreitet er den zulässigen Kontext, bleibt die bisherige
Zeitzuordnung bestehen. Die frühere Zuordnung zieht weder die fachliche
Bestätigung noch die reale Erkennungszeit vor. Die Bedeutung der Zeitfelder
ist unter [Zeitangaben](speicherung.md#daten-und-zeitangaben) und
[nachträgliche Zuordnung im Verlauf](speicherung.md#nachträgliche-zuordnung-im-verlauf)
beschrieben.

## Öffnen und Schließen der Tür

Eine Öffnung benötigt entweder fallende Temperatur und Feuchte oder einen
Temperaturabfall bei durchgehend bestätigtem Heizen. Temperatur- und
Feuchteabfall dürfen zeitlich versetzt auftreten. Eine vollständige Messposition
genügt; bei zwei verfügbaren Positionen müssen beide den Abfall belegen.
Die Regeln gelten auch bei hoher Temperatur.

Zusätzlich muss die geglättete Temperatur jeder beteiligten Position den
eingestellten Mindesttemperaturverlust einer Türöffnung erreichen. Bezug ist
ihr höchster Wert im vorherigen Trendfenster und während des Öffnungshinweises.
Erst dann beginnt die Bestätigungsdauer.

Ein belegter Temperatur-/Feuchteabfall bleibt bei weiter fallender Temperatur
derselben Quellen gültig, auch wenn die Feuchte nicht weiter fällt. Endet der
Temperaturabfall oder wechselt eine Quelle, verfällt dieser Öffnungsnachweis.

Eine Schließung setzt eine erkannte Öffnung und Temperaturerholung voraus.
Gültige Temperaturwerte können sie auch bei gestörter Feuchtemessung belegen.

## Anwesenheit und Aufgüsse erkennen

Der Feuchtenachweis benötigt steigende relative Feuchte und zunehmenden absoluten
Wassergehalt. Ein Aufguss bleibt dadurch auch bei sinkender Temperatur erkennbar.

Das deutliche Personensignal benötigt ein vollständiges Messfenster und die
Bestätigungsdauer. Die empfindlichere Prüfung gilt nach erkannter Türöffnung
mit anschließender Schließung für die eingestellte Bestätigungsfrist. Sie benötigt
einen gültigen Temperaturbezug und einen neuen Feuchteanstieg nach der Öffnung;
bestätigtes Durchlüften ist nicht erforderlich. Eine weitere Öffnung beginnt
einen neuen Türbezug.

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

Zusammenhängende Bestätigungsnachweise enden beim Wechsel oder Ausfall der
beteiligten vollständigen Messpositionen oder am Ende der Türöffnung. Für die
Lüftungsreferenz gilt stattdessen die beschriebene Pausenregel. Ein fortlaufender
Öffnungsnachweis darf mehrere Merkmalsfenster überdauern.

### Diagnose im Verlauf

Die Erkennungskontrolle zeigt gespeicherte Merkmale und ihre Schwellen.
Ein Markerklick hebt den zugehörigen Diagrammabschnitt hervor und zeigt den
auslösenden Erkennungsnachweis. Der Wechsel zur Ereignisliste erfolgt über
**Ereignis im Verlauf**. Der ausgewählte Prüfzeitpunkt enthält auch unerfüllte
Bedingungen und Prüfpunkte mit dem Zählerstand 0; fehlende Angaben werden davon
unterschieden. Weitere gespeicherte Prüfzeitpunkte sind einzeln auswählbar.

Schwellen beziehen sich auf die gespeicherte Sitzungskonfiguration. Fehlt diese
in älteren Aufzeichnungen, ist der damalige Schwellenwert nicht belegt.
Haltezähler zählen Prüfpunkte, keine Sekunden; ihr Abstand folgt dem Prüfschritt.
Ein Ereignis verweist über `trace_at` auf seinen auslösenden Diagnosepunkt.
Ältere Archive verwenden dafür den fachlichen Ereignisbeginn.

[Zeitliche Zuordnung](speicherung.md#nachträgliche-zuordnung-im-verlauf) · [Originaldaten](speicherung.md)
