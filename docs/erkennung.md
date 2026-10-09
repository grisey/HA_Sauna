# Türöffnung, Aufguss und Durchlüften erkennen

Tür-, Personen-, Aufguss- und Lüftungssignale entstehen aus den zugeordneten
Temperatur-/Feuchtepaaren. Beobachtungsfenster, Schwellen und Bestätigungsdauern
stammen aus den [Parametern](parameter.md). Ihre Wirkung auf einen Gang beschreibt
die [Gangerkennung](gangmodell.md).

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

Personensignale eröffnen bei Erkennung aus Temperatur und Feuchte neue Gänge.
Während eines Gangs werden weitere Aufgüsse erkannt; bei direkter Präsenzführung
ersetzen Personensignale den gewählten Präsenzsensor nicht.

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

## Erkennungen bei Messlücken prüfen

Zusammenhängende Bestätigungsnachweise enden beim Wechsel oder Ausfall der
beteiligten vollständigen Messpositionen oder am Ende der Türöffnung. Für die
Lüftungsreferenz gilt stattdessen die beschriebene Pausenregel. Ein fortlaufender
Öffnungsnachweis darf mehrere Merkmalsfenster überdauern.

### Diagnose im Verlauf

Die Erkennungskontrolle zeigt gespeicherte Merkmale und erfüllte Prüfpunkte.
Haltezähler zählen Prüfpunkte, keine Sekunden; ihr Abstand folgt dem Prüfschritt.
Ein Ereignis verweist über `trace_at` auf seinen auslösenden Diagnosepunkt.
Ältere Archive verwenden dafür den fachlichen Ereignisbeginn.

[Zeitliche Zuordnung](speicherung.md#nachträgliche-zuordnung-im-verlauf) · [Originaldaten](speicherung.md)
