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

Die gemeinsame Türerkennung und die Aufgusserkennung verwenden je Messkanal
die letzten `2K` Originalmessungen: ein aktuelles Fenster und sein unmittelbar
vorhergehendes, disjunktes Fenster mit jeweils `K` Punkten. In jedem Fenster
wird eine Gerade durch gewöhnliche kleinste Quadrate (OLS) bestimmt. Für
Originale `(t_i, y_i)` mit Mittelwerten `t̄`, `ȳ` gilt:

```text
S_tt = Σ(t_i − t̄)²
v    = Σ((t_i − t̄)(y_i − ȳ)) / S_tt
E    = ε · Σ|t_i − t̄| / S_tt
r    = max|y_i − ȳ − v(t_i − t̄)| / ε
```

Das Vorgängerfenster liefert die Steigung `b`, die Steigungsgrenze `E⁻` und
den Restfehler `r⁻`. `v`, `b`, `E` und `E⁻` haben die Einheit der Messgröße pro
Sekunde; `r` und `r⁻` sind dimensionslos. `v − b` ist eine Steigungsdifferenz,
keine zweite Zeitableitung. Die positive Wertskala `ε` ist ein empirischer
Katalogparameter. `E` begrenzt die Steigungsänderung unter der zusätzlichen
Annahme von Wertstörungen bis `ε`; daraus folgt keine zugesicherte
Sensorgenauigkeit oder Ereigniswahrscheinlichkeit.

Fenstergröße, positionsbezogene Skalen und zulässige Originalabstände sowie
die Auflösungsschwelle `s`, Öffnungsschwelle `γO`, Anstiegsschwellen `γN` und
`γC` und Hintergrundgrenzen `β` und `η` stehen als `curve_original_*` im
[`Parameterkatalog`](../custom_components/ha_sauna/defaults.json).
Der Katalog legt auch ihre Zuordnung zur Konfigurationsoberfläche fest.

Die Fenster benötigen gültige Originale mit strikt steigenden Zeiten in
zusammenhängenden Quellenabschnitten. Die zulässigen Abstände gelten auch
zwischen beiden Fenstern und vom jüngsten Original bis zur Auswertung.
Vorhandene gültige Messzeiten bleiben erhalten; ersatzweise verwendete
Empfangszeiten bilden eine eigene Zeitbasis. Fehlende oder nicht auswertbare
Stützen sind unbekannt. Sie bestätigen weder ein Ereignis noch dessen Ende.
Raster-Haltewerte liefern keine zusätzliche Evidenz; Korrekturen derselben
Originalprobe sind keine neuen Originale. Korrekturen tragender Originale
erfordern eine erneute Prüfung der gebundenen Belege; entwertete Stützen
liefern keine Bestätigung.

Die bisherigen Kurvenkandidaten können weiterhin einen früheren Beginn
belegen. Ihr Modellvergleich mit glatten Trends ist kein zusätzlicher
Zulassungsfilter für die folgenden Originalformen. Der reine Heizweg besitzt
seine eigene, unten beschriebene Bestätigung. Fensteranfang, Basisoriginal und
beobachtete Spitze sind mathematische Stützen, keine gemessenen Zeitpunkte
einer mechanischen Türbewegung oder Wasserzugabe.

Erst der vollständige Nachweis unter den folgenden fachlichen Bedingungen
bestätigt ein Ereignis. Ein gesondert belegter früherer Kandidatenbeginn kann
innerhalb des zulässigen Tür- und Betriebskontexts zugeordnet werden.
Ohne passenden früheren Beleg oder bei abgewiesener Zuordnung gilt der
Bestätigungszeitpunkt. Die frühere Zuordnung zieht weder die fachliche
Bestätigung noch die Erkennungszeit vor.
Die Bedeutung der Zeitfelder
ist unter [Zeitangaben](speicherung.md#daten-und-zeitangaben) und
[nachträgliche Zuordnung im Verlauf](speicherung.md#nachträgliche-zuordnung-im-verlauf)
beschrieben.

## Öffnen und Schließen der Tür

Für die beteiligten Positionen `p` fasst der Öffnungskontrast die fallende
Temperatur, die fallende relative Feuchte und den negativen Wechsel der
Temperatursteigung zusammen:

```text
O = min_p {−v_Tp/E_Tp, −v_Hp/E_Hp, −(v_Tp − b_Tp)/(E_Tp + E⁻_Tp)}
```

Ein neues Original mit `O > 0` bindet einen gemeinsamen Öffnungskandidaten.
Die Bestätigung benötigt `O > γO` und seit der Bindung an jeder beteiligten
Position mindestens ein späteres Temperatur- und Feuchteoriginal. Bei
`O ≤ 0` wird dieser gemeinsame Kandidat verworfen. Eine vollständige
Messposition genügt; bei zwei beteiligten Positionen gilt der Kontrast für
beide. Das überträgt keinen empirischen Nachweis von vier Kanälen auf eine
andere Sensoranordnung. Die Regeln gelten auch bei hoher Temperatur.

Ein zusätzlicher Öffnungsweg verwendet allein die Temperaturform bei
durchgehend bestätigtem Heizen. Er behält den bisherigen BIC-Modellvergleich
und die erneute Bestätigung seiner festen Originalbelege durch spätere
Originalmessungen. Sämtliche Originalmessungen des Vergleichs und sein
belegter Beginn müssen aus diesem Heizabschnitt stammen; die
Temperaturquellen müssen zur Öffnungsgruppe passen. Eine Heizunterbrechung
verwirft den Nachweis. Dieser bedingte Heizweg verwendet nicht den
gemeinsamen OLS-Öffnungskontrast.

Eine Schließung setzt eine erkannte Öffnung voraus. Die Temperaturbremse `B`
und die schwächste beziehungsweise stärkste aktuelle Erwärmung lauten:

```text
B     = min_p (v_Tp − b_Tp)/(E_Tp + E⁻_Tp)
W_min = min_p v_Tp/E_Tp
W_max = max_p v_Tp/E_Tp
```

Für die Feuchteerholung ist `M_p` das kleinste bisher belegte Mittel des
aktuellen Feuchtefensters in derselben geöffneten Gruppe. Mit dem aktuellen
Mittel `H̄_p` gilt:

```text
U = min_p (H̄_p − M_p)/(2ε_Hp)
C = max {min(B, U), min(B, W_max), W_min}
```

Ein früher Schließkandidat benötigt `B > s` oder `W_min > s`. Die Bestätigung
benötigt spätere Originale aller dafür verwendeten Temperaturpositionen und
`C > s`. Die drei hinreichenden Formzweige sind:

| Zweig | Erforderlicher Verlauf |
| --- | --- |
| `min(B, U)` | Temperaturabfall bremst an allen beteiligten Positionen ab; die Feuchte hat sich an allen bereits vom Gruppenminimum erholt |
| `min(B, W_max)` | Temperaturabfall bremst an allen beteiligten Positionen ab; mindestens eine Position erwärmt sich bereits |
| `W_min` | Alle beteiligten Temperaturpositionen erwärmen sich |

Der Feuchtezweig benötigt sämtliche ursprünglichen Positionen mit denselben
Quellenabschnitten wie bei der Öffnung. Bei gestörter Feuchtemessung bleiben
die beiden thermischen Zweige für die noch auswertbaren Temperaturpositionen
der Öffnungsgruppe verfügbar. Ein Wechsel dieser Positionsmenge erfordert
einen neuen Schließkandidaten. Im zweiten Zweig dürfen die andere Temperatur
und die Feuchte noch fallen. Bloßes Abflachen der Feuchte bei weiter fallender
Temperatur und ohne bereits belegte Feuchteerholung genügt nicht.

Diese Formkriterien bestimmen den erkannten Türzustand. Sie sichern keine
exakte mechanische Kontaktzeit; eine veränderte Durchmischung bei noch offener
Tür ist durch die Formen allein nicht allgemein ausgeschlossen.

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

Für Anstieg und Abfall der relativen Feuchte gelten über alle beteiligten
Positionen:

```text
R = min_p v_Hp/E_Hp
F = min_p −v_Hp/E_Hp
```

Ein neues Original mit `R > s` bindet eine Anstiegskomponente. Ihre Basis ist
je Position das früheste Minimum des aktuellen `K`-Originalfensters bei der
Bindung. Als Spitze wird das höchste danach beobachtete Feuchteoriginal
geführt; bei gleichen Werten bleibt das frühere maßgeblich. Diese
Originalbezüge legen keine physische Beginnzeit fest.

Ein starker Anstieg benötigt seit der Bindung neue Temperatur- und
Feuchteoriginale aller beteiligten Positionen und `R > γN` für eine neue
Episode beziehungsweise `R > γC` für eine Fortsetzung. Dieser starke Beleg
wird innerhalb derselben gültigen Komponente gespeichert. Er bleibt erhalten,
wenn der aktuelle Anstieg bereits zurückgeht und die Temperaturpaarung noch
aussteht. Eine nur schwache Komponente wird bei `R ≤ s` verworfen.

Die Wasserqualifikation verwendet zu jedem Feuchteoriginal die unmittelbar
benachbarten Temperaturoriginale. Bei gleicher Zeit genügt das eine
Temperaturoriginal; sonst wird die Temperatur dazwischen linear zugeordnet.
Das Paar ist erst verfügbar, wenn auch das rechte Temperaturoriginal vorliegt
und der zulässige Stützabstand eingehalten ist. Aus den Wertskalen `ε_Tp` und
`ε_Hp` werden mit dem vorhandenen Modell für absolute Feuchte eine untere
Dichte `ρ⁻` und eine obere Dichte `ρ⁺` berechnet. Je Position gilt:

```text
Q_p = max(verfügbare ρ⁻ seit dem Basisoriginal) − ρ⁺ des Basisoriginals
```

Erst der gespeicherte starke Beleg und `Q_p > 0` an allen beteiligten
Positionen qualifizieren die Komponente. Fehlende Paarungen bleiben unbekannt;
die Paarung der momentan höchsten Feuchtespitze muss dabei noch nicht
vorliegen. Ein vollständig gepaarter, nichtpositiver Wasserbeleg nach Spitze
und Abfall kann eine noch unqualifizierte Komponente verwerfen. Die
Dichtegrenzen beruhen auf den angenommenen Wertstörungen; eine zusätzliche
physische Grenze des Temperaturverlaufs zwischen den Originalen ist damit
nicht belegt. Der berechnete Wassergehalt ist kein unabhängiger Sensor und
beweist für sich allein keine Wasserzugabe.

Die Aufgussbestätigung benötigt außerdem den beobachteten Abfall `F > s`.
An jeder beteiligten Position müssen sämtliche Feuchteoriginale des aktuellen
Fensters nach ihrer geführten Spitze liegen. Der Fallbeleg kann gespeichert
werden, während die Wasserpaarung noch eintrifft. Ein höherer Peak vor der
abschließenden Beurteilung benötigt einen neuen nachfolgenden Fallbeleg.
Ein fortgesetzter Anstieg allein bestätigt keinen Aufguss. Die Steuerung
reagiert bei der Bestätigung; ein gesondert belegter früherer Beginn bleibt
der historischen Zuordnung vorbehalten.

Weitere qualifizierte Komponenten vor dem bestätigten Abschluss gehören zu
derselben Episode und erzeugen keine weitere Zählung. Der Abschluss benötigt
nach dem belegten Abfall einen neuen aktuellen Hintergrund. Dafür gelten:

```text
Z   = max_p |v_Hp/E_Hp|
Z_r = max_p {r_Hp, r⁻_Hp}
G   = (Z ≤ β) und (Z_r ≤ η) und (R ≤ s)
```

Im ununterbrochenen Nachlauf muss die letzte qualifizierte Komponente
abgefallen sein; ein offener Anstiegskandidat sperrt den Abschluss.
Der erste Befund `G` bindet nur einen
Hintergrundkandidaten. `G` muss an jedem folgenden auswertbaren Eingangspräfix
bestehen bleiben, bis an allen beteiligten Positionen spätere Temperatur- und
Feuchteoriginale vorliegen. Ein widersprechender oder nicht auswertbarer
Zwischenstand verwirft diesen Hintergrundkandidaten. Nach einer Unterbrechung
gelten die unten beschriebenen Regeln zum Wiedererwerb.

Der Hintergrund wird über die aktuelle Feuchtesteigung und die Restfehler
beider Fenster belegt. Die vorherige Steigung muss dafür nicht bereits flach
sein. Eine Rückkehr zum Feuchteniveau vor dem Aufguss oder eine zusätzliche
Wartezeit ist nicht erforderlich. Der Abschluss (`infusion_background`)
zählt selbst keinen Aufguss.

Ein später erkannter Anstieg setzt denselben Aufguss fort, sobald an einer
Position ein gültiger Beginnbeleg spätestens beim Abschluss liegt. Eine neue
Episode benötigt Beginnbelege aller beteiligten Positionen nach dem Abschluss.
Fehlt diese eindeutige Zuordnung, bleibt sie offen. Jeder weitere Aufguss
benötigt erneut starken Anstieg, Wasserqualifikation und den belegten Abfall
nach seiner Spitze.

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
oder seines Tür- und Betriebskontexts verworfen. Seine zeitliche Gültigkeit ist
durch das Analysefenster begrenzt; ein abgelaufener Kandidat wird keinem späteren
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

Die Originalformen weisen Öffnungs- und Schließkontrast sowie die
Anstiegs-, Fall- und Hintergrundbedingungen aus. Beim Aufguss werden der
gespeicherte starke Anstiegsbeleg, die aktuelle Steigung, die verfügbare
Wasserqualifikation und der nach der Spitze belegte Abfall getrennt geführt.
Ein passender Kontrast ohne die erforderlichen späteren Originale genügt nicht
zur Bestätigung. Entsprechend trennt der Hintergrundnachweis den ersten
Kandidaten vom durch neue Originale bestätigten Abschluss.

Die weiterhin gespeicherten Modellnachweise für frühere Beginne und den
Heizsonderweg können zusätzlich Scoregewinn, Vergleichsgrenze und
Originalbedingungen enthalten. Solche Scores sind keine kalibrierte
Ereigniswahrscheinlichkeit.

Schwellen beziehen sich auf die gespeicherte Sitzungskonfiguration. Fehlt diese
in älteren Aufzeichnungen, ist der damalige Schwellenwert nicht belegt.
Historische Tür- und Aufgussschwellen und Modellnachweise beschreiben die
damalige Erkennung; aktuelle gemeinsame Tür- und Aufgussbestätigungen verwenden
die beschriebenen Originalformen.
Haltezähler zählen Prüfpunkte, keine Sekunden; ihr Abstand folgt dem Prüfschritt.
Ein Ereignis verweist über `trace_at` auf seinen auslösenden Diagnosepunkt.
Ältere Archive verwenden dafür den fachlichen Ereignisbeginn.

[Zeitliche Zuordnung](speicherung.md#nachträgliche-zuordnung-im-verlauf) · [Originaldaten](speicherung.md)
