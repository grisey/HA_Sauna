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
ist zunächst ein Kandidat; der Modellscore ist keine kalibrierte
Ereigniswahrscheinlichkeit und wird nicht als Prozentwert gelesen.
Vergleichszeiten und technische Modellvorgaben stehen im
[`Parameterkatalog`](../custom_components/ha_sauna/defaults.json).
Die technischen Modellvorgaben sind keine Einstellungen der Oberfläche.

Die Auswertung verwendet Originalmesspunkte innerhalb zusammenhängender
Quellenabschnitte mit einheitlicher Zeitbasis. Vorhandene gültige Messzeiten
bleiben erhalten; ersatzweise verwendete Empfangszeiten bilden eine eigene
Zeitbasis. Raster-Haltewerte liefern keine zusätzliche Evidenz.

Für vollständige Aufgussspitzen und den anschließenden Übergang zum Hintergrund
werden dieselben Formfamilien gemeinsam verglichen: ein konstantes Niveau
(`L`), Gerade, Quadratik und Kubik (`P1` bis `P3`) sowie eine Quadratik mit
Steigungsknick (`M1`) oder mit stetiger Steigung und Krümmungsänderung (`M2`).
Die Anpassungen verwenden dieselben begrenzt ausgewählten Originalmessungen
und dieselbe robuste Rauschschätzung aus den kubischen Anpassungsresten.
Der Score berücksichtigt die Anpassungsfehler, die Anzahl der Modellparameter
und die Suche nach Knickzeiten.

Die konkrete zeitliche Form entscheidet über passende Form und Gegenform,
nicht der Modellname: Auch eine gekrümmte oder geknickte Spitze kann passen.
Eine eindeutige gesuchte Spitze oder Talsohle darf mit einer entgegengesetzten
Hintergrundbewegung zusammen auftreten. Mehrere gesuchte Umkehrpunkte bleiben
uneindeutig. Die passende Form muss die Gegenform unterscheidbar übertreffen;
zusätzlich müssen die tatsächlichen Originalflanken die erforderlichen
Richtungen belegen. Für eine Spitze sind das Anstieg und Abfall, für den
Hintergrundwechsel Abfall und danach ein Niveau oder Anstieg. Die Aussage
muss auch nach Weglassen eines besonders einflussreichen Originalpunkts bestehen.

Der erste unterstützte Formbefund bindet eine feste Referenz mit Modellzweig,
Umkehrpunkt und gegebenenfalls Knickzeit. Später hinzugekommene Originale müssen
diese Referenz erneut stützen. Ein deutlich besserer anderer Umkehrpunkt kann
die gebundene Referenz entwerten, obwohl weiterhin eine passende Form vorliegt.
Formvergleich und Identität der Referenz sind deshalb getrennte Nachweise.

Die Richtung nach einem Knick wird getrennt an den dort vorhandenen
Originalmessungen geprüft. Eine Gerade wird mit einem konstanten Niveau
verglichen; der Nachweis berücksichtigt das lokale Rauschen und muss auch
nach Weglassen eines besonders einflussreichen Messpunkts bestehen bleiben.
Die Ableitung der übergreifenden Kurvenanpassung genügt dafür nicht.
Eine zusätzliche Gegenprüfung vergleicht die Originalverläufe vor und nach dem
Knick. Nur wenn beide Seiten jeweils eine Gerade gegenüber gekrümmten Verläufen
bevorzugen, werden ihre Steigungen verglichen. Ein bevorzugter gemeinsamer
Verlauf oder eine klar entgegengesetzte Steigungsänderung widerspricht dann dem
Knick. Bei gekrümmten oder nicht eindeutig bewertbaren Seiten bleibt der
übergreifende Modellvergleich maßgeblich. Eine gleichmäßig fallende Temperatur
belegt keine Abbremsung.

Die Bestätigung eines Kurvenbeginns benötigt für jede erforderliche Messgröße
und Position einen erneut unterstützten Modellvergleich am selben Beginn mit
später hinzugekommenen Originalmessungen. Die einzelnen Anfänge müssen innerhalb der
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

| Prüfweg | Erwarteter zeitlicher Verlauf | Fehlender oder widersprechender Nachweis |
| --- | --- | --- |
| Öffnung | Unterstützte Änderung zu fallender Temperatur und Feuchte; beim Heizweg fallende Temperatur aus demselben bestätigten Heizabschnitt | Glatter Verlauf ohne belegte Änderung, fehlende Fallrichtung oder unterbrochener Heizbezug |
| Schließung am Knick | Vorheriger Temperaturabfall bremst ab; anschließend aktuelle Temperaturerholung oder passende Feuchteerholung | Gleichmäßig fortgesetzter Abfall oder fehlender aktueller Erholungsnachweis |
| Glatte Schließung | Vollständiger Temperaturabfall, Tal und Anstieg | Fehlende Originalflanke, unbestätigte Form oder nicht mehr aktuelle Erholung |

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
beteiligten Positionen auch bei gestörter Feuchtemessung belegen. Der frühere
Knick und die inzwischen eingetretene Erholung sind getrennte Nachweise:
Nach der erneuten Stützung durch neue Originalmessungen bleiben die Belege
des früheren Knicks gebunden. Spätere Messungen prüfen die aktuelle Erholung;
ein verschobenes Arbeitsfenster ersetzt die ursprünglichen Knickbelege nicht.
Quellenwechsel, Originalkorrekturen und der Kandidatenablauf gelten weiterhin.
Die Erholung wird an den jüngsten Originaltemperaturen innerhalb der
Türvergleichszeit geprüft.

Auch ein vollständiger glatter Temperaturverlauf aus Abfall, Tal und Anstieg
kann dieselbe Schließung ohne abrupten Knick bestätigen. Der Modellvergleich
unterscheidet diese Form von anderen glatten Verläufen; die Originalmessungen
vor und nach dem berechneten Tal müssen die fallende und steigende Richtung
getrennt belegen. Die vollständige Form und beide Richtungen müssen auch nach
Weglassen eines besonders einflussreichen Messpunkts bestehen bleiben.
Jede noch auswertbare beteiligte Temperaturposition benötigt nach ihrem ersten
Formbeleg eine erneute Stützung durch später hinzugekommene frische Originale.
Nach diesen beiden vollständigen Originalbefunden bleiben die Formstützen
gebunden, auch wenn ihre Fallflanke aus dem wandernden Analysefenster läuft.
Die weitere Bestätigung benötigt einen steigenden Trend aktueller
Originaltemperaturen innerhalb der Türvergleichszeit, der die zuletzt positiv
belegte Originalmessung noch enthält. Ein fallender Trend oder das Ablaufen
dieses Messpunkts verwirft den Formnachweis; ein gleichbleibender oder nicht
eindeutig bewertbarer Verlauf pausiert die Bestätigung.
Die Temperaturquellen und ihre ununterbrochenen Messabschnitte müssen zur
erkannten Öffnung gehören; Quellen- und Kontextbrüche verwerfen den Formnachweis.
Originale vor der Öffnung oder einem späteren bestätigten Lüftungsnachweis
werden nicht verwendet. Rasterprüfungen und Korrekturen derselben Originalprobe
liefern keine frische Bestätigung. Korrekturen verwendeter Originale entwerten
die betroffenen Form- oder aktuellen Erholungsbelege.

Der berechnete Talzeitpunkt (`valley_at`) beschreibt nur die Kurvengeometrie
und legt keinen Schließbeginn fest. Ein früherer Beginn benötigt einen noch
gültigen, durch neue Originalmessungen gebundenen Temperaturbremsknick. Der
zugehörige Originalmesspunkt muss an jeder beteiligten Position innerhalb der
Fallflanke der gebundenen Form liegen.
Andernfalls gilt der tatsächliche Bestätigungszeitpunkt.

Alternativ kann ein Abbremsen des Temperaturabfalls zusammen mit einer passenden
Erholung der Feuchte einen früheren Schluss vor dem Temperaturminimum belegen. Dabei
darf die relative Feuchte bereits wieder steigen. Dieser gemischte Nachweis
muss aus derselben gemeinsam belegten Öffnung stammen und bleibt an die
ursprünglichen Feuchtequellen und deren ununterbrochene Messabschnitte gebunden.
Steigende Feuchte ohne neuen Feuchteknick ergänzt nur einen weiterhin aktuell
gestützten Temperaturknick. Dazu müssen die jüngsten Originaltemperaturen in
der Türvergleichszeit und die Originalfeuchtewerte in der Feuchtevergleichszeit
liegen. Die historische Bindung des Temperaturknicks allein genügt für diesen
Weg nicht.

Bei einer aus Temperatur und Feuchte belegten Öffnung kann die Schließprüfung
auch eine stetige Abbremsung (`M2`) statt eines Steigungsknicks verwenden.
Hier bleiben die Steigungen am Beginn gleich; eine positive Änderung der
Krümmung stützt die Abbremsung des zuvor fallenden Verlaufs. Die nachfolgenden
Originalmessungen müssen eine auswertbare Richtung liefern, dürfen zu diesem
Zeitpunkt aber noch fallen. Dieser Formbefund allein schließt die Tür nicht;
die beschriebenen Erholungs- und Originalbestätigungen bleiben erforderlich.

## Anwesenheit und Aufgüsse erkennen

Der Feuchtenachweis benötigt steigende relative Feuchte und zunehmenden absoluten
Wassergehalt. Ein Aufguss bleibt dadurch auch bei sinkender Temperatur erkennbar.

Das deutliche Personensignal benötigt ein vollständiges Messfenster und die
Bestätigungsdauer. Beide Personenprüfungen verwenden weiterhin die
positionsbezogenen Feuchtetrendschwellen aus dem Parameterkatalog und verlangen
einen zunehmenden absoluten Wassergehalt. Die empfindlichere Prüfung gilt nach
erkannter Türöffnung mit anschließender Schließung für die eingestellte
Bestätigungsfrist. Sie benötigt einen gültigen Temperaturbezug und einen neuen
Feuchteanstieg nach der Öffnung;
bestätigtes Durchlüften ist nicht erforderlich. Eine weitere Öffnung beginnt
einen neuen Türbezug.

Ein durch neue Originalmessungen belegter Feuchteanstieg wird zunächst als
Kandidat gebunden, sofern der absolute Wassergehalt an allen beteiligten
Positionen zunimmt. Erst die folgende Feuchtespitze mit belegtem beginnendem
Abfall bestätigt den Aufguss. Der Beginn bleibt dem ersten Anstieg zugeordnet;
die Steuerung reagiert bei der Bestätigung. Ein fortgesetzter Anstieg allein
bestätigt keinen Aufguss. Weitere Spitzen gehören bis zum belegten Abschluss
zu diesem einen Aufguss.

Die Spitze kann einen Steigungsknick oder einen glatten Übergang von Anstieg
zu Abfall haben. Für die glatte Form gelten der gemeinsame Formvergleich, die
getrennt belegten Originalflanken und die erneute Stützung ihrer festen Referenz
durch spätere Originalmessungen. Ein berechnetes Maximum ohne belegten Abfall
oder ein unentschiedener Vergleich mit der Gegenform bestätigt keinen Aufguss.

Weitere Spitzen werden je Messposition verfolgt. Eine zusätzliche Spitze an
einer Position entwertet nur dort den bisherigen Abklingnachweis; die gültigen
Nachweise der anderen Position bleiben erhalten.

Zum Abschluss können nach der Feuchtespitze ein Abfall und anschließend an
allen beteiligten Positionen ein neues Niveau belegt werden.
Der Niveauvergleich verwendet den jüngsten zusammenhängenden Originalverlauf
über die Aufgussvergleichszeit und muss durch weitere neue Originalmessungen
bestätigt werden. Das Niveau wird gegenüber glatten Trends frei bestimmt; eine Rückkehr
zum Wert vor dem Aufguss ist nicht erforderlich.

Alternativ kann nach dem bereits gezählten Aufguss ein Übergang aus dem Abfall
in einen neuen Hintergrund den Abschluss belegen (`infusion_background`).
An allen beteiligten Positionen müssen die Originale zunächst fallen und danach
ein Niveau oder einen Anstieg stützen. Eine bloße Abbremsung bei weiterhin
fallendem Verlauf genügt nicht. Die vollständige Form muss gegen ihre Gegenformen
bestehen; anschließend müssen spätere Originale dieselbe feste Referenz erneut
stützen. Quellen, Kontext und tragende Originale müssen weiterhin zusammenpassen.
Dieser Abschluss zählt selbst keinen Aufguss.

Ein später erkannter Anstieg, der schon vor diesem Abschluss begann, setzt
denselben Aufguss fort. Erst ein nach dem belegten Abschluss begonnener Anstieg
kann einen weiteren Aufguss
ergeben; auch er benötigt wieder die Bestätigung durch seine Feuchtespitze.

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
Diese Verlustschwellen und die Mindestöffnungsdauer bei einer Position bleiben
Katalogbedingungen. Ein fallender Kurvenverlauf allein ersetzt sie nicht.

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

Ein noch unbestätigter Aufgusskandidat wird bei einem Bruch seiner Quellen
oder seines Tür- und Betriebskontexts verworfen. Auch sein Beginn muss noch
im Analysefenster vorhanden sein; ein abgelaufener Anstieg wird keinem späteren
Aufguss zugeordnet.

Bei einem bereits gezählten Aufguss pausieren Messlücken, Quellenwechsel und
vorübergehend fehlende Auswertbarkeit den Abschluss. Die Zuordnung bleibt
bestehen; ein unbekannter Verlauf belegt weder Abklingen noch ein neues Niveau.
Nach Wiedererwerb muss der Abschluss auf den wieder zusammenhängenden
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

Die gespeicherten Entscheidungsbedingungen (`gates`) unterscheiden erfüllt,
verfehlt und nicht ausgewertet oder nicht auswertbar. Sie halten, soweit
vorhanden, Messwert, Vergleichsoperator, Grenze, Einheit und den Grund eines
fehlenden Nachweises fest. Bei Zahlenvergleichen liegt ein positiver Abstand
zur Grenze auf der erfüllenden Seite. Jeder Abstand gehört zu genau dieser
Bedingung; die Abstände werden nicht zu einer Gesamtwahrscheinlichkeit addiert.

Beim gemeinsamen Formvergleich werden passende Form und tatsächliche
Gegenform getrennt ausgewiesen. Numerische Scorevergleiche enthalten den
Scoregewinn und seine Vergleichsgrenze. Die zusammengefasste Formbewertung
berücksichtigt zusätzlich die Originalflanken und die feste Referenz. Weitere
Bedingungen betreffen die Gegenprüfung ohne einen einflussreichen Punkt und
eine mögliche Verdrängung der Referenz durch eine andere passende Geometrie.
Ein positiver Scoreabstand allein erfüllt daher nicht alle Formbedingungen.
Der Aufgussabschluss trennt Niveaunachweis und Hintergrundwechsel; eine noch
nicht frisch bestätigte Referenz kennzeichnet einen ausstehenden Originalnachweis.

Schwellen beziehen sich auf die gespeicherte Sitzungskonfiguration. Fehlt diese
in älteren Aufzeichnungen, ist der damalige Schwellenwert nicht belegt.
Historische Tür- und Aufgussschwellen beschreiben die damalige Erkennung;
neue Kurvennachweise verwenden die beschriebene Originalbestätigung.
Haltezähler zählen Prüfpunkte, keine Sekunden; ihr Abstand folgt dem Prüfschritt.
Ein Ereignis verweist über `trace_at` auf seinen auslösenden Diagnosepunkt.
Ältere Archive verwenden dafür den fachlichen Ereignisbeginn.
Bei einer durch glatte Erholung bestätigten Schließung trennt der gespeicherte
Nachweis die historischen Ankerbelege, die gebundene vollständige Form mit ihrem
Bindungszeitpunkt (`bound_at`) und die aktuelle Originalerholung
(`current_recovery`).

[Zeitliche Zuordnung](speicherung.md#nachträgliche-zuordnung-im-verlauf) · [Originaldaten](speicherung.md)
