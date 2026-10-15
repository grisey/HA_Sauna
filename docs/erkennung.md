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
Messwertaufbereitung, Türerkennung, Personenerkennung und Aufgüsse. Personenschwellen und
Aufgussbestätigungsfrist erscheinen nur beim indirekten Verfahren.
Ihre gespeicherten Werte bleiben beim Verfahrenswechsel erhalten.
Die Übergangsregeln beschreibt die [Gangerkennung](gangmodell.md).

## Kurvenbeginn und Bestätigung

Die gemeinsame Türerkennung und die Aufgusserkennung verwenden je Messkanal
die letzten `2K` Originalmessungen: ein aktuelles Fenster und sein unmittelbar
vorhergehendes, disjunktes Fenster mit jeweils `K` Punkten. Die Lüftungsform
verwendet dieselbe Fensteraufteilung für kausal gepaarte Wasserbelege. In jedem
Fenster wird eine Gerade durch gewöhnliche kleinste Quadrate (OLS) bestimmt. Für
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
Q = min_p (v_Hp − b_Hp)/(E_Hp + E⁻_Hp)
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

Eine qualifizierte neue Episode kann bereits im Anstieg bestätigt werden:
Der aktuelle Kontrast muss `R > γN` und zugleich `Q > s` erfüllen. Seit der
Komponentenbindung müssen an allen beteiligten Positionen spätere Temperatur-
und Feuchteoriginale vorliegen; der verfügbare Wasserbeleg muss weiterhin
positiv sein. `Q` verlangt eine Zunahme der Feuchtesteigung gegenüber dem
Vorgängerfenster. Eine gleichmäßig steigende Gerade erfüllt diesen zusätzlichen
Formbeleg nicht. Die tatsächlich bestätigenden Fenster werden gebunden und
bei Originalkorrekturen erneut geprüft.

Fehlt dieser frühe Nachweis, bleibt die Bestätigung über den beobachteten
Abfall `F > s` möglich. Dabei müssen an jeder beteiligten Position sämtliche
Feuchteoriginale des aktuellen Fensters nach ihrer geführten Spitze liegen.
Der Fallbeleg kann gespeichert werden, während die Wasserpaarung noch eintrifft.
Ein höherer Peak vor der abschließenden Beurteilung benötigt einen neuen
nachfolgenden Fallbeleg. Die Bestätigung erfolgt pro Episode einmal;
der spätere Abfall erzeugt nach einer frühen Bestätigung kein zweites Ereignis.
Die Steuerung reagiert bei der tatsächlichen Bestätigung. Ein gesondert
belegter früherer Beginn bleibt der historischen Zuordnung vorbehalten.

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
benötigt erneut starken Anstieg und Wasserqualifikation sowie einen der
beschriebenen Bestätigungswege. Im ununterbrochenen Nachlauf bleibt der
nachfolgende Abfall Voraussetzung für den Abschluss der Episode.

Personen- und Aufgusssignale eröffnen bei Erkennung aus Temperatur und Feuchte
nur nach erkannter Türöffnung und anschließender Schließung einen Gang;
es gelten die [Startbedingungen](gangmodell.md#gang-aus-temperatur-und-feuchte-erkennen).
Während eines Gangs werden weitere Aufgüsse erkannt; solange der Präsenzsensor
führt, ersetzen Personensignale ihn nicht.

## Durchlüften

Die absolute Feuchte wird aus frischen, gültigen Temperatur-/Feuchtepaaren
berechnet und als Diagnosewert je Messposition bereitgestellt.

Die Lüftungsform wird bei allen erkannten Türvorgängen gleich ausgewertet,
unabhängig von einem laufenden Gang und der Präsenzquelle. Während eines Gangs
unterscheidet sie die kurze Öffnung vom Durchlüften als Nachweis für das
Gangende; außerhalb eines Gangs wird sie statistisch erfasst.

Der Nachweis benötigt nach der Öffnung einen vorherigen Fall des absoluten
Wassergehalts und dessen aufgelösten Übergang in einen abgeflachten Verlauf, während
die Temperatur weiter signifikant fällt. Dazu werden an jeder anfangs
gebundenen Messposition die letzten genau `2K` aufeinanderfolgenden
Feuchteoriginale mit ihren kausalen Temperaturpartnern verwendet. Die Paarung
und ihre Dichtehüllen `ρ⁻`, `ρ⁺` entsprechen der Wasserqualifikation beim
Aufguss; fehlende oder ungültige Paare werden nicht übersprungen.

Für die Hüllmitte `m_i = (ρ⁻_i + ρ⁺_i)/2` und Halbbreite
`a_i = (ρ⁺_i − ρ⁻_i)/2` werden die Steigungen `b_ρ`, `v_ρ` der beiden
`K`-Fenster durch OLS bestimmt. Ihre Grenzen folgen aus den einzelnen Hüllen:

```text
E_ρ = Σ|t_i − t̄| · a_i / S_tt
```

Mit der entsprechenden Steigungsgrenze `E⁻_ρ` des Vorgängerfensters gelten je
Position `p` die folgenden Bedingungen:

```text
−b_ρp                 > s E⁻_ρp
v_ρp − b_ρp           > s (E_ρp + E⁻_ρp)
|v_ρp|                ≤ s E_ρp
−v_Tp                 > s E_Tp
```

Die ersten beiden Bedingungen belegen Fall und Abflachung beim Binden des
Lüftungskandidaten. Die letzten beiden müssen bis zur Bestätigung erhalten
bleiben: aktuelle Wassersteigung innerhalb der normierten Auflösung und
weiter fallende Temperatur. Schwankungen um einen abgeflachten Verlauf sind
keine zusätzliche Ablehnungsbedingung. Eine flache relative Feuchte oder eine
einzelne kleine Wassersteigung ohne vorherigen Fall und belegte Abflachung
genügt nicht. Die gemeinsame Formeinheit `s` und die aus den vorhandenen
empirischen Skalen abgeleiteten Hüllen sind weder garantierte Einzelmessfehler
noch eine Ereigniswahrscheinlichkeit.

Als Diagnose beschreibt `D_ρ` die gesamte normierte Streuung des aktuellen
Fensters um ein frei angepasstes Niveau `c`. Bei positiven Halbbreiten ist
dessen Gewichtung `1/a_i²`:

```text
c   = Σ(m_i/a_i²) / Σ(1/a_i²)
D_ρ = √(Σ((m_i − c)/a_i)² / K)
```

Die Streuung und die Schnittmenge der Einzelwerthüllen entscheiden nicht über
die Bestätigung. Eine Halbbreite null bindet das Diagnoseniveau an diesen Wert;
widersprüchliche Bindungen oder nicht auswertbare Rechnungen lassen den
Diagnosewert offen.

Die Bestätigung benötigt an sämtlichen anfangs gebundenen Positionen spätere
Temperatur- und Feuchteoriginale mit vollständiger Wasserpaarung. Für eine
oder zwei Positionen gilt dieselbe Form ohne zusätzliche Mindestöffnungsdauer
oder feste Temperatur- und Wasserverlustschwelle. Die fünf historischen
Lüftungsparameter bleiben für ältere Archive und Referenzadapter im
[Parameterkatalog](../custom_components/ha_sauna/defaults.json); sie bestimmen
den aktuellen Nachweis nicht.

Die Auswertung folgt der Reihenfolge der empfangenen Originale. Eine bereits
bestätigbare Schließung hat am selben Eingangspräfix Vorrang vor dem
Lüftungsnachweis. Ein an einem früheren Präfix bestätigtes Durchlüften und ein
späterer Türschluss bleiben auch innerhalb desselben Rasters zwei Ereignisse.
Für Unterbrechungen und Korrekturen gelten die
[Regeln bei Messlücken](#erkennungen-bei-messlücken-prüfen).

Ein bestätigter Lüftungsnachweis bleibt seiner Türöffnung auch nach
Türschluss zugeordnet. Ob damit ein Gang endet und welche Regelwirkung folgt,
bestimmt die [Gangerkennung](gangmodell.md); bei direkter Präsenzführung gehört
dazu außerdem eine gültige Abwesenheitsmeldung.

## Erkennungen bei Messlücken prüfen

Personenhaltezähler beginnen nach einem Wechsel oder Ausfall der beteiligten
vollständigen Messpositionen neu. Unbestätigte Kurvennachweise bleiben an ihre
jeweiligen Quellenabschnitte gebunden.

Bei der Lüftungsform bleibt das reguläre Warten auf einen rechten
Temperaturpartner offen. Noch ungepaarte Feuchteoriginale liefern keine
Bestätigungsfrische; bereits vollständige, aufeinanderfolgende Paarfenster
bleiben auswertbar. Eine Messlücke, ein
Quellenwechsel oder eine Korrektur tragender Originale verwirft die davon
abhängigen Stützen und den unbestätigten Kandidaten. Mit denselben Quellen
können im noch offenen Türvorgang Fall und Abflachung aus zusammenhängenden
Originalen erneut vollständig belegt werden. Ein tatsächlicher Wechsel der
anfangs gebundenen Quellen erlaubt diesen Wiedererwerb für dieselbe Öffnung
nicht.

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
Anstiegs-, Fall- und Hintergrundbedingungen aus. Beim Durchlüften werden
Wasserfall, Abflachung, aktuelles Fehlerband, konstante Hüllenverträglichkeit
und fallende Temperatur getrennt geführt; offene Paarungen und die spätere
Bestätigung bleiben erkennbar. Beim Aufguss werden der
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
