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
vorhergehendes, disjunktes Fenster mit jeweils `K` Punkten. Die beiden
Lüftungsstufen verwenden dieselbe Fensteraufteilung für relative Feuchte und
kausal gepaarte Wasserbelege. In jedem Fenster wird eine Gerade durch
gewöhnliche kleinste Quadrate (OLS) bestimmt. Für
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

### Neue Originalblöcke nach der Bindung

Ein Kandidat behält die bei seiner Bindung verwendeten Originalfenster als
feste Referenz. Je Messposition und Messgröße zählen nur Originale mit einer
Zeit strikt nach dem jüngsten Original dieser Referenz als neu. Ein
vollständig neuer aktueller Block enthält `K` solche Originale; zwei neue
Blöcke benötigen sie sowohl im aktuellen als auch im Vorgängerfenster.
Überlappende Fenster bleiben auswertbar, liefern aber vor Erreichen dieser
Anzahl keine vollständige neue Stützmenge. Wiederholte Auswertungen derselben
Originale und Raster-Haltewerte erhöhen die Anzahl nicht.
Ein gesondert gebundener Folgezeuge bildet eine weitere feste Referenz;
die Frische seiner Folgeblöcke wird gegen seine eigenen Originalzeiten geprüft.

Für Öffnung, Schließung und eine laufende Aufgusskomponente wird der
lückenlose Anschluss je beteiligter Messposition und Messgröße an jedem
auswertbaren Eingangspräfix geprüft. Der Anschlussanker rückt mit dem jüngsten
Original weiter; die fachliche Referenz der Bindung bleibt dabei unverändert.
Damit muss der ursprüngliche Kandidatenbeginn bei einem langen Ast nicht
dauerhaft im Ringspeicher liegen. Eine Lücke zwischen den fortgeschriebenen
Ankern und den neuen Originalen kann auch dann den Kandidaten verwerfen, wenn
die jüngsten `2K` Originale für sich wieder gültige Fenster bilden.

### RH-Beginn und Knicktoleranz

Unter den beschriebenen Gültigkeits- und Positionsregeln führt ein frischer
Steigungswechsel der relativen Feuchte mit passender aktueller Richtung den
Tür- beziehungsweise Aufgusskandidaten. Für die beteiligten Positionen `p`
gelten mit `H` als relativer Feuchte:

```text
q_Hp = (v_Hp − b_Hp)/(E_Hp + E⁻_Hp)
A↑   = min_p min(v_Hp/E_Hp, q_Hp)
A↓   = min_p min(−v_Hp/E_Hp, −q_Hp)
```

Zusätzlich wird die Steigungsdifferenz `δ_p = v_Hp − b_Hp` je Position gegen
eine Knicktoleranz geprüft. Ihr historischer Maßstab stammt ausschließlich aus
Originalen strikt vor dem Anfang des aktuellen `2K`-Blocks. Verwendet wird der
jüngste zusammenhängende Abschnitt derselben Quelle und Zeitbasis innerhalb
der gespeicherten Kurvenoriginale. Die zulässigen Originalabstände gelten
innerhalb dieser Historie und an ihrem Anschluss an den aktuellen Block;
eine größere Lücke schneidet ältere Historie ab.

Aus diesem Abschnitt werden alle um jeweils ein Original verschobenen
vollständigen `2K`-Blöcke gebildet. Jedes Paar aus zwei disjunkten
`K`-Fenstern liefert mit denselben OLS-Regeln und den tatsächlichen
Originalzeiten eine historische Steigungsdifferenz `v_j − b_j`. Der Median
ihrer Beträge beschreibt die normale Kurvenänderungsstärke der relativen
Feuchte in Prozentpunkten pro Sekunde. Auch ein glatter gekrümmter Verlauf
kann dazu beitragen.

Die Mindestzahl vollständiger historischer Blöcke steht in
`curve_original_kink_history_windows`. Ab dieser Zahl gilt je Position der
größere Wert aus dem historischen Median und der aktuellen Kataloggröße
`E_Hp + E⁻_Hp`; bei kürzerer Historie gilt allein die Kataloggröße. Mit dem
Faktor `f` aus `curve_original_kink_factor` werden Toleranz und
vorzeichenbehaftete wirksame Steigungsänderung berechnet:

```text
S_p     = max(E_Hp + E⁻_Hp, median_j |v_j − b_j|)  bei ausreichender Historie
S_p     = E_Hp + E⁻_Hp                           bei kürzerer Historie
κ_p     = f · S_p
δ_eff,p = sgn(δ_p) · max(0, |δ_p| − κ_p)
```

`S_p`, `κ_p` und `δ_eff,p` haben die Einheit Prozentpunkte pro Sekunde.
Ein Anstiegsbeginn benötigt weiterhin `A↑ > s` und zusätzlich an jeder
beteiligten Position `δ_eff,p > 0`; ein Fallbeginn benötigt `A↓ > s` und
zusätzlich überall `δ_eff,p < 0`. Faktor `0` schaltet die zusätzliche
Knickprüfung ab. Originalmesswerte und Rohkontraste bleiben erhalten;
die nachfolgenden Bestätigungsbedingungen gelten weiterhin.

Bei der Bindung werden die Originalfenster und die zugehörigen Knickbelege
gespeichert: Faktor, Kataloggröße, Historienstatus, historischer Median,
verwendeter Maßstab, Toleranz, rohe und wirksame Steigungsänderung sowie Anzahl
und Zeitbereich der verfügbaren historischen Blöcke. Damit bleibt der Beginn
an den damaligen Beleg gebunden.

## Öffnen und Schließen der Tür

Ein neues Original mit einem Fallbeginn gemäß
[RH-Beginn und Knicktoleranz](#rh-beginn-und-knicktoleranz) bindet einen
gemeinsamen Öffnungskandidaten. Ein Knick der Temperatur ist dafür nicht
erforderlich. Die Bestätigung vergleicht die aktuellen Temperatur- und
Feuchteniveaus mit dem gebundenen Vorlauf. Für jede Messgröße `X` aus
Temperatur `T` und relativer Feuchte `H` ist `X̄⁰⁻_p` der Mittelwert des
Vorgängerfensters bei der Bindung und `X̄_p` der Mittelwert des aktuellen
Fensters:

```text
L_Xp = (X̄⁰⁻_p − X̄_p)/(2ε_Xp)
L    = min_p min(L_Tp, L_Hp)
```

Der beobachtete Vorlaufmittelwert bleibt fest; seine damalige Steigung wird
nicht in die Zukunft fortgeschrieben. Der erste gemeinsame Absenkungszeuge
benötigt `L > γO`, seit der Bindung an jeder beteiligten Position einen
vollständig neuen aktuellen Temperatur- und Feuchteblock mit jeweils `K`
Originalen und an allen Temperaturpositionen `v_Tp < −γO E_Tp`. Seine Fenster
werden einmalig festgehalten. Dieser Zeuge bestätigt die Öffnung noch nicht.

Nach dem Absenkungszeugen benötigt jede beteiligte Temperaturposition zwei
vollständig neue `K`-Blöcke: Sämtliche Originale beider Fenster müssen strikt
nach ihrem jüngsten Temperaturoriginal im Zeugen liegen. Die aktuellen
Temperaturmittel müssen weiterhin `L_Tp > γO` gegenüber dem ursprünglichen
Vorlauf erfüllen. Hinzu kommt einer von zwei gemeinsamen Temperaturverläufen:

| Zweig | Erforderlicher Verlauf an allen Temperaturpositionen |
| --- | --- |
| Fortgesetzter Fall | Beide neuen Fenster fallen mit `v_Tp < −γO E_Tp` und `b_Tp < −γO E⁻_Tp` |
| Abgeschlossene Absenkung | Beide neuen Fenster sind flach und ihre Mittel miteinander verträglich; Frischebezug ist das aktuelle Temperaturfenster des Absenkungszeugen |

Die RH-Absenkung bleibt durch den festen gemeinsamen Zeugen belegt. Eine
RH-Abflachung, RH-Erholung oder ein Temperaturplateau verwirft den Kandidaten
nur, solange weder die gemeinsame Niveauabsenkung `L > γO` noch der feste
Absenkungszeuge vorliegt. Ist die Temperatur seit der Bindung bereits
aufgelöst gefallen, beendet ihre signifikante Erwärmung
(`v_Tp > s E_Tp`) den Kandidaten auch nach dem Zeugen. Ein späterer
Öffnungsnachweis benötigt danach einen neuen RH-Fallbeginn. Die drei
Vergleiche für Fall, Plateau und Abflachung stehen unter
[Durchlüften](#durchlüften) und gelten hier entsprechend für die jeweilige
Messgröße.

Gültige Kurvenbeginnzeugen der ursprünglichen Bindung bleiben bis zur
Bestätigung an deren Originalstützen gebunden. Fehlt ein solcher Zeuge,
darf später aufgelöste Temperaturgeometrie einen früheren Beginn nur innerhalb
dieser ursprünglichen Stützen ergänzen. Ein späterer RH-Kurvenfit verschiebt
die bereits gebundene Phase nicht. Die Bestätigung durch die neuen
Temperaturblöcke und die frühere Zuordnung des Beginns bleiben getrennt.
Die notwendige Belegfolge kann kurze RH-Fälle direkt nach einem vorausgehenden
Temperaturanstieg unbestätigt lassen.

Eine vollständige Messposition genügt; bei zwei beteiligten Positionen gelten
die gemeinsamen Kontraste für beide. Das überträgt keinen empirischen
Nachweis von vier Kanälen auf eine andere Sensoranordnung. Die Regeln gelten
auch bei hoher Temperatur.

Ein zusätzlicher Öffnungsweg verwendet allein die Temperaturform bei
durchgehend bestätigtem Heizen. Er behält den bisherigen BIC-Modellvergleich
und die erneute Bestätigung seiner festen Originalbelege durch spätere
Originalmessungen. Sämtliche Originalmessungen des Vergleichs und sein
belegter Beginn müssen aus diesem Heizabschnitt stammen; die
Temperaturquellen müssen zur Öffnungsgruppe passen. Eine Heizunterbrechung
verwirft den Nachweis. Liegen gültige RH-Originalfenster vor, bleibt dieser
Heizweg an denselben RH-Kandidaten und die gemeinsame Originalbestätigung
gebunden; sein BIC-Nachweis ersetzt dann keinen frischen RH-Beginn.

Eine Schließung setzt eine erkannte Öffnung voraus. Die Temperaturbremse `B`
und die schwächste beziehungsweise stärkste aktuelle Erwärmung lauten:

```text
B     = min_p (v_Tp − b_Tp)/(E_Tp + E⁻_Tp)
W_min = min_p v_Tp/E_Tp
W_max = max_p v_Tp/E_Tp
```

Ein früher Schließkandidat benötigt `B > s` oder `W_min > s`. Er bindet die
damaligen Temperaturfenster. Deren Vorgängersteigung `b⁰_Tp` und
Steigungsgrenze `E⁰⁻_Tp` bleiben der Bezug für die spätere Temperaturbremse
`B_f`. Für die Feuchteerholung ist `M_p` das kleinste bisher belegte Mittel des
aktuellen Feuchtefensters in derselben geöffneten Gruppe. Mit dem aktuellen
Mittel `H̄_p` gilt:

```text
B_f = min_p (v_Tp − b⁰_Tp)/(E_Tp + E⁰⁻_Tp)
U   = min_p (H̄_p − M_p)/(2ε_Hp)
C   = max {min(B_f, U), min(B_f, W_max), W_min}
```

Die Bestätigung benötigt seit der Bindung an jeder dafür verwendeten
Temperaturposition einen vollständig neuen aktuellen Block mit `K` Originalen
und `C > s`. Die drei hinreichenden Formzweige sind:

| Zweig | Erforderlicher Verlauf |
| --- | --- |
| `min(B_f, U)` | Temperaturabfall bremst gegenüber dem gebundenen Fall an allen beteiligten Positionen ab; die Feuchte hat sich an allen bereits vom Gruppenminimum erholt |
| `min(B_f, W_max)` | Temperaturabfall bremst gegenüber dem gebundenen Fall an allen beteiligten Positionen ab; mindestens eine Position erwärmt sich bereits |
| `W_min` | Alle beteiligten Temperaturpositionen erwärmen sich |

Der Feuchtezweig benötigt sämtliche ursprünglichen Positionen mit denselben
Quellenabschnitten wie bei der Öffnung. Bei gestörter Feuchtemessung bleiben
die beiden thermischen Zweige für die noch auswertbaren Temperaturpositionen
der Öffnungsgruppe verfügbar. Ein Wechsel dieser Positionsmenge erfordert
einen neuen Schließkandidaten. Im zweiten Zweig dürfen die andere Temperatur
und die Feuchte noch fallen. Bloßes Abflachen der Feuchte bei weiter fallender
Temperatur und ohne bereits belegte Feuchteerholung genügt nicht.

Ein weiterer Schließweg verwendet ein neues stabiles Temperaturniveau nach
einem tatsächlich belegten Fall. Dafür wird je Temperaturposition bereits im
selben Türvorgang ein gültiges aufgelöst fallendes aktuelles Fenster
gespeichert. Bis zur ersten Erholung ersetzt ein späteres vollständiges
Fallfenster die Referenz, wenn seine obere Steigungsgrenze `v + sE` kleiner
ist. Seine tatsächlichen Originalzeiten werden dabei mit übernommen. Sobald
die Temperatur nicht mehr aufgelöst fällt (`v ≥ −sE`), bleibt diese
Fallreferenz fest. Die Fallfenster können zeitlich auseinanderliegen und schon
vor der bestätigten Öffnung entstehen. Der Niveauzweig benötigt keinen
gemeinsamen momentanen Bremsknick und keinen gewöhnlichen Schließkandidaten.
Eine Lücke, ein Quellenbruch oder eine Korrektur einer tragenden Stütze
entwertet den betroffenen Fallbeleg. Ein aufgelöster erneuter Fall nach einer
Erholung erfordert einen neuen Fallbeleg, sobald das aktuelle `K`-Fenster
diesen Fall selbst auflöst. Eine einzelne aufgelöst negative Endsekante mit
negativer Änderung gegenüber der Erholung sperrt bereits den Schluss; sie
ersetzt für sich allein das feste Fallfenster nicht. Dieser Gegenbeleg bleibt
an die Zeiten und Werte des letzten Temperatursegments gebunden. Ein
Feuchtepräfix ohne geänderte Temperaturoriginale löst ihn nicht. Ein neues
Temperaturoriginal oder ein korrigierter Wert dieses Endsegments führt zur
erneuten Kantenprüfung; eine Korrektur liefert dabei weder zusätzliche Frische
noch einen neu gebundenen Fallbeleg.

Die gemeinsame Plateauform für Öffnung und Schließung benötigt je Position
zwei vollständig neue `K`-Fenster nach ihrer jeweiligen festen Referenz.
Beide Fenster müssen flach und ihre Mittel miteinander verträglich sein:

```text
|v_Tp| ≤ s E_Tp                |b_Tp| ≤ s E⁻_Tp
|T̄_p − T̄⁻_p| ≤ 2s ε_Tp
```

Der Plateauschluss ergänzt diese Geometrie um die aufgelöste Bremse beider
neuen Fenster gegenüber der festgehaltenen tatsächlichen Fallsteigung
`vᶠ_Tp` mit Steigungsgrenze `Eᶠ_Tp`. Je Position gelten zusätzlich:

```text
vᶠ_Tp < −s Eᶠ_Tp
v_Tp − vᶠ_Tp > s(E_Tp + Eᶠ_Tp)
b_Tp − vᶠ_Tp > s(E⁻_Tp + Eᶠ_Tp)
```

Dieser Niveauzweig benötigt keine Feuchteerholung. Weiter fallende Temperatur
erfüllt ihn auch bei flacher Feuchte nicht.

Ein Schließkandidat führt je Temperaturposition zusätzlich das bisherige
Fenster mit der höchsten belegten Steigung als Erholungsreferenz. Ein
aufgelöster erneuter Fall verwirft den Kandidaten, wenn die aktuelle
Fenstersteigung gegenüber dieser Referenz signifikant abnimmt. Dasselbe gilt
bereits für den jüngsten Originalschritt: Für seine Sekantensteigung `a` und
Grenze `E_a` gelten mit der Erholungsreferenz `v_rec`, `E_rec`:

```text
a   = (T_n − T_(n−1))/(t_n − t_(n−1))
E_a = 2ε_Tp/(t_n − t_(n−1))
a/E_a < −s  und  (a − v_rec)/(E_a + E_rec) < −s
```

Der Schritt muss neuer als die Erholungsreferenz sein und selbst aufgelöst
fallen. Das Abbremsen einer Erwärmung zum Plateau erfüllt diesen Gegenbeleg
nicht. Ein aktuelles aufgelöst fallendes Fenster verwirft den Kandidaten auch,
wenn es gegenüber einem gebundenen Fall keine aufgelöste Bremse mehr besitzt.
Für den gewöhnlichen Schließweg ist danach ein neuer Kandidat erforderlich.

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
Q = min_p q_Hp
```

Ein neues Original mit einem Anstiegsbeginn gemäß
[RH-Beginn und Knicktoleranz](#rh-beginn-und-knicktoleranz) bindet eine
Anstiegskomponente. Ihre Basis ist je Position das früheste Minimum des
aktuellen `K`-Originalfensters bei der Bindung. Als Spitze wird das höchste
danach beobachtete Feuchteoriginal geführt; bei gleichen Werten bleibt das
frühere maßgeblich. Diese
Originalbezüge legen keine physische Beginnzeit fest.

Ein früherer Kurvenbeginn bleibt an die Originale desselben neuen RH-Astes
gebunden. Seine festen Stützen müssen die aktuellen RH-Originale tragen;
bereits der erste Originalblock rechts dieses Beginns muss einen aufgelösten
Anstieg belegen. Dieser historische Block wird bezogen auf sein jüngstes
Original und seine eigenen zulässigen Originalabstände geprüft. Ein älterer
Beginn vor einem dazwischenliegenden Plateau wird einem späteren Ast dadurch
nicht nachträglich zugeordnet.

Die beobachtete Anstiegsstärke wird gespeichert, sobald seit der Bindung an
jeder beteiligten Position mindestens ein späteres Temperatur- und
Feuchteoriginal vorliegt. Der stärkste so beobachtete Kontrast muss `R > γN`
für eine neue Episode beziehungsweise `R > γC` für eine Fortsetzung erfüllen.
Dieser starke Beleg benötigt noch keinen vollständig neuen `K`-Block und
bestätigt allein keinen Aufguss. Er bleibt innerhalb derselben gültigen
Komponente erhalten, wenn der aktuelle Anstieg bereits zurückgeht und die
Temperaturpaarung noch aussteht. Eine nur schwache Komponente wird bei `R ≤ s`
verworfen.

Die Wasserqualifikation verwendet zu jedem Feuchteoriginal die unmittelbar
benachbarten Temperaturoriginale. Bei gleicher Zeit genügt das eine
Temperaturoriginal; sonst wird die Temperatur dazwischen linear zugeordnet.
Das Paar ist erst verfügbar, wenn auch das rechte Temperaturoriginal vorliegt
und der zulässige Stützabstand eingehalten ist. Aus den Wertskalen `ε_Tp` und
`ε_Hp` werden mit dem vorhandenen Modell für absolute Feuchte eine untere
Dichte `ρ⁻` und eine obere Dichte `ρ⁺` berechnet. Der Wasserbeleg bleibt auf
die RH-Originale des gebundenen Anstiegsastes begrenzt. Je Position endet
seine Stützmenge beim jüngsten RH-Original des ersten gültigen Endbelegs
`v_Hp ≤ s E_Hp`; andere Positionen dürfen dabei noch steigen. Spätere
Temperaturpartner zu diesen RH-Originalen bleiben zulässig. Eine Korrektur
tragender Originale prüft diesen Endbeleg erneut. Wird er widerlegt, folgt
die Stützmenge an der betroffenen Position wieder demselben Anstiegsast bis
zu einem neuen gültigen Endbeleg; ausstehende Paarungen bleiben erhalten. Später
hinzukommende RH-Originale im Plateau und ein zugehöriger Temperaturanstieg
erweitern die Wasserqualifikation nicht. Je Position gilt innerhalb dieser Stützmenge:

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
Komponentenbindung müssen an allen beteiligten Positionen ein vollständig
neuer aktueller Feuchteblock mit `K` Originalen und mindestens ein späteres
Temperaturoriginal vorliegen; der verfügbare Wasserbeleg muss weiterhin
positiv sein. `Q` verlangt eine Zunahme der Feuchtesteigung gegenüber dem
Vorgängerfenster. Eine gleichmäßig steigende Gerade erfüllt diesen zusätzlichen
Formbeleg nicht. Die tatsächlich bestätigenden Fenster werden gebunden und
bei Originalkorrekturen erneut geprüft.

Fehlt dieser frühe Nachweis, bleibt die Bestätigung über den beobachteten
Abfall `F > s` möglich. Der gespeicherte starke Anstiegsbeleg und die positive
Wasserqualifikation müssen weiterhin vorliegen. An jeder beteiligten Position
müssen sämtliche `K` Feuchteoriginale des aktuellen Fensters nach ihrer
geführten Spitze liegen. Die Bestätigungsfrische stammt in diesem Zweig aus
dem vollständigen Fallblock nach der Spitze; der gespeicherte starke Anstieg
darf kürzer als ein neuer Block nach der Bindung sein. Der Fallbeleg kann
gespeichert werden, während die Wasserpaarung noch eintrifft.
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
bestehen bleiben, bis an allen beteiligten Positionen ein vollständig neuer
aktueller Feuchteblock mit `K` Originalen und mindestens ein späteres
Temperaturoriginal vorliegen. Der neue Feuchteblock muss lückenlos an die
gebundenen Hintergrundoriginale anschließen. Ein widersprechender oder nicht
auswertbarer Zwischenstand verwirft diesen Hintergrundkandidaten. Nach einer
Unterbrechung gelten die unten beschriebenen Regeln zum Wiedererwerb.

Der Hintergrund wird über die aktuelle Feuchtesteigung und die Restfehler
beider Fenster belegt. Die vorherige Steigung muss dafür nicht bereits flach
sein. Eine Rückkehr zum Feuchteniveau vor dem Aufguss oder eine zusätzliche
Wartezeit ist nicht erforderlich. Der Abschluss (`infusion_background`)
zählt selbst keinen Aufguss.

Der Endbeleg gehört ausschließlich zum Quellereignis des tatsächlich
bestätigten Einzelaufgusses. Der normale Hintergrundabschluss verwendet die
Zeitbasis `evidence_grid`: Seine Endzeit ist der Zeitpunkt des belegten
Abschlussrasters, keine physisch genau bestimmte Endzeit der Aufgussepisode.
Bestätigungszeit und zugehöriger Diagnosezeitpunkt bleiben gesondert erhalten.

Eine erkannte Türöffnung beendet eine noch aktive bestätigte Aufgussepisode
am fachlich zugeordneten Türbeginn (`physical_effective`). Dieser Beginn muss
mindestens beim zugehörigen Aufgussbeginn liegen; ein widersprechender
früherer Türbeginn liefert keinen solchen Endbeleg. Ein bereits belegter
Hintergrundabschluss wird dadurch nicht ersetzt.

Eine Fortsetzung oder eine Korrektur, die den Abschlussbeleg entwertet, nimmt
das Ende dieses Aufgusses ausdrücklich zurück. Das bloße Zurücksetzen des
Erkennungszustands behauptet kein Ende. Diese Endbelege ändern weder
Gangübergänge noch die Aufgusszählung.

Ein später erkannter Anstieg setzt denselben Aufguss fort, sobald an einer
Position ein gültiger Beginnbeleg spätestens beim Abschluss liegt. Andernfalls
wird er einer neuen Episode zugeordnet, wenn sämtliche bei der Bindung
verwendeten RH-Originale aller beteiligten Positionen nach dem Abschluss
liegen; zusätzliche Kurvenbeginnbelege sind dafür nicht erforderlich.
Auch gültige Beginnbelege aller Positionen nach dem Abschluss erlauben diese
Zuordnung. Überlappen die gebundenen RH-Fenster den Abschluss ohne solche
Belege, bleibt die Zuordnung offen. Jeder weitere Aufguss
benötigt erneut starken Anstieg und Wasserqualifikation sowie einen der
beschriebenen Bestätigungswege. Im ununterbrochenen Nachlauf bleibt der
nachfolgende Abfall Voraussetzung für den Abschluss der Episode.

Personen- und Aufgusssignale eröffnen bei Erkennung aus Temperatur und Feuchte
nur nach erkannter Türöffnung und anschließender Schließung einen Gang;
es gelten die [Startbedingungen](gangmodell.md#gang-durch-einmalige-personenbelege-erkennen).
Während eines Gangs werden weitere Aufgüsse erkannt; solange der Präsenzsensor
führt, ersetzen Personensignale ihn nicht.

## Durchlüften

Die absolute Feuchte wird aus frischen, gültigen Temperatur-/Feuchtepaaren
berechnet und als Diagnosewert je Messposition bereitgestellt.

Die beiden Lüftungsstufen werden bei allen erkannten Türvorgängen gleich
ausgewertet, unabhängig von einem laufenden Gang und der Präsenzquelle. Sie
benötigen im selben Türvorgang einen vorherigen Feuchtefall und dessen belegten
Übergang in ein Plateau, während die Temperatur weiter fällt, ohne merklich
abzuflachen.

Nach dem festen gemeinsamen Absenkungszeugen können diese Formen bereits
während der noch ausstehenden Öffnungsbestätigung im selben Türvorgang
vorgemerkt werden. Unbestätigte Kandidaten bleiben an die
Erhaltungsbedingungen ihrer Form gebunden. Ein vollständig durch spätere
Originale belegter RH- oder AH-Verlauf bleibt als vergangener Nachweis auch
bei anschließendem Nachrauschen erhalten. Quellen, zusammenhängende Stützen
und tragende Originalkorrekturen werden weiterhin geprüft. Ihre Untergrenze ist
der Empfangszeitpunkt der aktuellen Öffnungskandidatenbindung, frühestens
jedoch der Beginn der beteiligten Quellenabschnitte. Dieser Bezug bleibt bei
der Öffnungsbestätigung erhalten.

Die Veröffentlichung benötigt nach bestätigter Öffnung ein weiteres neues
gültiges Eingangspräfix ohne vorrangige Schließung. Zuerst wird die RH-Stufe,
danach die AH-Stufe veröffentlicht. Erst dann entstehen Lüftungsereignisse und ihre
Wirkung auf den Gang. Die öffentliche Ereigniszeit bleibt der aktuelle
Auswertungszeitpunkt; die frühere Originalbelegzeit datiert sie nicht zurück.
Bereits veröffentlichte Lüftungsbelege bleiben derselben Türöffnung zugeordnet.

| Stufe | Form und Wirkung |
| --- | --- |
| Lüften (`ventilation_confirmed`) | Die relative Feuchte geht vom Fall ins Plateau über. Diese Stufe liefert bereits den Lüftungsnachweis für das Gangende; außerhalb eines Gangs wird sie statistisch erfasst. |
| Vollständig gelüftet (`ventilation_complete`) | Nach belegter erster Stufe wird auch der Übergang des absoluten Wassergehalts vom Fall ins Plateau belegt. Diese Stufe ergänzt einmalig den statistischen Beleg derselben noch offenen Tür ohne zusätzliche Gangwirkung. |

Die erste Stufe verwendet die gültigen RH- und Temperatur-Originalfenster der
anfangs gebundenen Messpositionen. Eine vollständige Wasserpaarung ist dafür
nicht erforderlich. Die zweite Stufe verwendet an jeder dieser Positionen
die letzten genau `2K` aufeinanderfolgenden Feuchteoriginale mit ihren kausalen
Temperaturpartnern. Die Paarung und ihre Dichtehüllen `ρ⁻`, `ρ⁺` entsprechen
der Wasserqualifikation beim Aufguss; fehlende oder ungültige Paare werden
nicht übersprungen.

Für die Hüllmitte `m_i = (ρ⁻_i + ρ⁺_i)/2` und Halbbreite
`a_i = (ρ⁺_i − ρ⁻_i)/2` werden die Steigungen `b_ρ`, `v_ρ` der beiden
`K`-Fenster durch OLS bestimmt. Ihre Grenzen folgen aus den einzelnen Hüllen:

```text
E_ρ = Σ|t_i − t̄| · a_i / S_tt
```

Für `X = H` (relative Feuchte, erste Stufe) beziehungsweise `X = ρ`
(absoluter Wassergehalt, zweite Stufe) gelten je Position `p` die folgenden
fünf Vergleiche:

```text
−b_Xp                 > s E⁻_Xp
|v_Xp|                ≤ s E_Xp
v_Xp − b_Xp           > s (E_Xp + E⁻_Xp)
−v_Tp                 > s E_Tp
v_Tp − b_Tp           ≤ s (E_Tp + E⁻_Tp)
```

Beim Binden des jeweiligen Stufenkandidaten müssen alle fünf Vergleiche an
allen beteiligten Positionen erfüllt sein: vorheriger Feuchtefall, aktuelles
Plateau, belegte Abflachung, weiter fallende Temperatur und keine aufgelöste
Temperaturbremse. Bis zur Bestätigung müssen das aktuelle Plateau und die
beiden Temperaturbedingungen erhalten bleiben. Die Bestätigung benötigt
außerdem seit der Bindung an sämtlichen Positionen spätere Temperatur- und
Feuchteoriginale; in der zweiten Stufe müssen die Feuchteoriginale vollständig
gepaart sein. Ein Zwischenstand außerhalb der erhaltenen Form verwirft den
jeweiligen noch unbestätigten Stufenkandidaten. Ein bereits vollständig
belegter und lediglich noch nicht veröffentlichter Nachweis bleibt erhalten.

Schwankungen innerhalb des abgeflachten Verlaufs sind keine zusätzliche
Ablehnungsbedingung. Eine einzelne kleine Feuchtesteigung ohne vorherigen
Fall und belegte Abflachung genügt keiner Stufe. Die gemeinsame Formeinheit
`s` und die aus den vorhandenen empirischen Skalen abgeleiteten Hüllen sind
weder garantierte Einzelmessfehler noch eine Ereigniswahrscheinlichkeit.

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

Für eine oder zwei Positionen gilt dieselbe Form ohne zusätzliche
Mindestöffnungsdauer oder feste Temperatur- und Wasserverlustschwelle. Die
fünf historischen Lüftungsparameter bleiben für ältere Archive und
Referenzadapter im [Parameterkatalog](../custom_components/ha_sauna/defaults.json); sie bestimmen
den aktuellen Nachweis nicht.

Die Auswertung folgt der Reihenfolge der empfangenen Originale. Eine bereits
bestätigbare Schließung hat am selben Eingangspräfix Vorrang vor dem
Lüftungsnachweis. Ein an einem früheren Präfix bestätigtes Durchlüften und ein
späterer Türschluss bleiben auch innerhalb desselben Rasters zwei Ereignisse.
Für Unterbrechungen und Korrekturen gelten die
[Regeln bei Messlücken](#erkennungen-bei-messlücken-prüfen).

Beide bestätigten Lüftungsstufen bleiben derselben Türöffnung auch nach
Türschluss zugeordnet. Für die [Gangerkennung](gangmodell.md) führt die erste
Stufe: Nach einer Austrittsöffnung beendet sie bei Führung aus Temperatur und
Feuchte den Gang. Bei direkter Präsenzführung gehört dazu außerdem eine
gültige Abwesenheitsmeldung. Die zweite Stufe ergänzt den Verlauf ohne
zusätzliche Regelwirkung auf den Gang.

## Erkennungen bei Messlücken prüfen

Personenhaltezähler beginnen nach einem Wechsel oder Ausfall der beteiligten
vollständigen Messpositionen neu. Unbestätigte Kurvennachweise bleiben an ihre
jeweiligen Quellenabschnitte gebunden.

Die neuen Originalblöcke von Öffnung, Schließung und Aufguss müssen zur
gebundenen Positionsmenge, Quelle und Zeitbasis passen. Der fortgeschriebene
Anschlussanker ersetzt keine Prüfung des gesamten neu hinzugekommenen
Originalabschnitts. Eine überschrittene rollenbezogene Abstandgrenze verwirft
den davon abhängigen Kandidaten; spätere wieder zusammenhängende Fenster
übernehmen seine früheren Stützen nicht. Eine Korrektur derselben Probe liefert
keine Bestätigungsfrische und entwertet davon abhängige gebundene Belege.

Bei der zweiten Lüftungsstufe bleibt das reguläre Warten auf einen rechten
Temperaturpartner offen. Noch ungepaarte Feuchteoriginale liefern dieser
Stufe keine Bestätigungsfrische; bereits vollständige, aufeinanderfolgende
Paarfenster bleiben auswertbar. Die erste Stufe benötigt die gültigen RH- und
Temperaturoriginale ohne Wasserpaarung. Eine Messlücke, ein
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

Die Türdiagnose trennt den frischen RH-Fallbeginn, die gebundenen
Kandidatenstützen, den aktuellen gemeinsamen T/RH-Fall (`opening_support`),
die Niveauabsenkung gegenüber dem gebundenen Vorlauf (`reference_level_loss`)
und den festen gemeinsamen Absenkungszeugen. Dessen zwei neue
Temperaturblöcke, der fortgesetzte Fall beziehungsweise das abgeschlossene
Temperaturniveau und die tatsächliche Originalbestätigung werden getrennt
ausgewiesen. Der aktuelle gemeinsame T/RH-Fallkontrast ist keine zusätzliche
Voraussetzung der Öffnungsbestätigung. Die Folgebelege zeigen je Messposition
und Messgröße die festen Referenzzeiten, die Zahl neuer Originale in beiden
Fenstern sowie Richtungs-, Änderungs- und Niveaukontraste. Beim Türschluss
werden die gebundene Temperaturbremse, die gespeicherten Rollenfallfenster,
das neue Temperaturniveau und die Gegenbelege aus Fenstersteigung und
jüngstem Originalschritt getrennt geführt.
Der zusätzlich gespeicherte ältere
Öffnungskontrast mit Temperaturknick ist keine Voraussetzung der gemeinsamen
Bestätigung. Schließkontrast sowie Anstiegs-, Fall- und Hintergrundbedingungen
bleiben getrennt ausgewiesen.

Beim Durchlüften werden RH- und AH-Stufe mit eigenen Kandidaten, Stützen und
Bestätigungen geführt. Die fünf Vergleiche zeigen jeweils vorherigen Fall,
aktuelles Plateau, Feuchteabflachung, weiter fallende Temperatur und fehlende
Temperaturbremse. Die AH-Stufe enthält zusätzlich den Paarungsstand;
Hüllenstreuung und konstante Hüllenverträglichkeit bleiben Diagnosewerte.
Beim Aufguss werden der frische RH-Anstiegsbeginn, die gebundenen und
fortgesetzten Stützen, der gespeicherte starke Anstiegsbeleg, die aktuelle
Steigung, die begrenzte Wasserqualifikation und der nach der Spitze belegte
Abfall getrennt geführt.
Ein passender Kontrast ohne die erforderlichen vollständig neuen Blöcke genügt
nicht zur Bestätigung. Entsprechend trennt der Hintergrundnachweis den ersten
Kandidaten vom durch neue Originale bestätigten Abschluss.

Die normalen Originalbestätigungen verwenden die einzelnen nativen
Formkontraste und die erforderlichen neuen Originale. Sie bilden keine
kumulierte Evidenzsumme oder kalibrierte Ereigniswahrscheinlichkeit.
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
