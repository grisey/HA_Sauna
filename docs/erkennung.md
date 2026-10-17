# Türöffnung, Aufguss und Durchlüften erkennen

Die [wirksame Präsenzquelle](gangmodell.md#wirksame-präsenzquelle) bestimmt den
Anwesenheitsnachweis. Türereignisse und Aufgüsse stammen in beiden Verfahren
aus Temperatur und Feuchte.

| Erkennung | Aus Temperatur und Feuchte | Mit Präsenzsensor |
| --- | --- | --- |
| Türöffnung und Türschluss | Gemeinsame Erkennung aus den Messpaaren | Dieselbe Erkennung aus den Messpaaren |
| Anwesenheit | Personensignale; zunächst vorläufiger Gang | Belegungszustand der zugeordneten Entität |
| Aufguss | Bestätigt den Gang; weitere Aufgüsse werden erfasst | Erfassung im bereits bestätigten Gang |
| Durchlüften | Bestandteil der vorherigen Austrittsöffnung; Nachweis für das Gangende | Bestandteil derselben Austrittsöffnung; zusammen mit deren Abwesenheitsnachweis Beleg für das Gangende |

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
zusammenhängenden Quellenabschnitten. Je Messkanal bildet
`curve_original_gap_*` den anfänglichen empirischen Stützbereich. Solange
weniger als `2K − 1` abgeschlossene Originalabstände vorliegen, gilt nur
dieser Katalogwert. Danach ergibt sich die Grenze für den nächsten Anschluss
aus dem größeren Wert von Katalogwert und oberem Tukey-Innenzaun
`Q3 + 1,5 × IQR` der letzten `2K − 1` zuvor abgeschlossenen Abstände.
`Q1` und `Q3` sind die Tukey-Hinges; `IQR = Q3 − Q1`. Der gerade geprüfte
Abstand bestimmt seine eigene Grenze nicht. Ein langsamerer Messstrom
benötigt zunächst eine vollständige Abstandshistorie und anschließend einen
vollständigen Originalblock mit durchgängig zulässigen Abständen.

Die Anschlussprüfung umfasst auch den Übergang zwischen beiden Fenstern.
Jeder Anschluss behält die Entscheidung aus seinem damaligen Quellenpräfix;
ein später gelernter Takt macht eine frühere Lücke nicht rückwirkend zulässig.
Historische gültige Stützen bleiben als vergangener Nachweis erhalten.
Gesondert wird die Frische des jüngsten Originals mit der aus den bereits
abgeschlossenen Abständen bestimmten nächsten Grenze geprüft. Die Wartezeit
bis zur Auswertung vergrößert diese Grenze nicht. Diese Zeitstütze ist keine
Sensorausfallfrist. Ein Wechsel der Quelle oder Verfügbarkeit beginnt einen
neuen Quellenabschnitt.

Vorhandene gültige Messzeiten bleiben erhalten; ersatzweise verwendete
Empfangszeiten bilden eine eigene Zeitbasis. Fehlende oder nicht auswertbare
Stützen sind unbekannt. Sie bestätigen weder ein Ereignis noch dessen Ende.
Raster-Haltewerte liefern keine zusätzliche Evidenz; Korrekturen derselben
Originalprobe sind keine neuen Originale. Korrekturen tragender Originale
erfordern eine erneute Prüfung der gebundenen Belege; entwertete Stützen
liefern keine Bestätigung.

Die bisherigen Kurvenkandidaten können weiterhin einen früheren Beginn
belegen. Ihr Modellvergleich mit glatten Trends ist für die folgenden
Originalformen mit Ausnahme der reinen Temperaturbremswege kein zusätzlicher
Zulassungsfilter. Diese benötigen neben ihren nativen Kontrasten eine eigene
gehaltene lokale Steigungsänderungsform und die frischen Originalblöcke ihrer
Bestätigung. Der reine Heizweg besitzt seine eigene, unten beschriebene
Bestätigung. Fensteranfang, Basisoriginal und beobachtete Spitze sind
mathematische Stützen, keine gemessenen Zeitpunkte
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

Für Öffnung und Schließung wird der
lückenlose Anschluss je beteiligter Messposition und Messgröße an jedem
auswertbaren Eingangspräfix geprüft. Der Anschlussanker rückt mit dem jüngsten
Original weiter; die fachliche Referenz der Bindung bleibt dabei unverändert.
Damit muss der ursprüngliche Kandidatenbeginn bei einem langen Ast nicht
dauerhaft im Ringspeicher liegen. Eine Lücke zwischen den fortgeschriebenen
Ankern und den neuen Originalen kann auch dann den Kandidaten verwerfen, wenn
die jüngsten `2K` Originale für sich wieder gültige Fenster bilden.
Eine laufende Aufgusskomponente behält dagegen ihre bereits gebundenen
Originalbelege bei zeitlich unbekanntem Anschluss. Die Lücke selbst bestätigt
nichts; neue Entscheidungen benötigen weiterhin auswertbare neue Originalblöcke,
den ursprünglichen Formnachweis und positive Wasserbelege im selben Quellen-
und Ereigniskontext.

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
gemeinsamen Öffnungskandidaten. Diese erste Bindung verlangt weiterhin den
Fallbeginn an allen beteiligten RH-Positionen im selben Präfix. Ein Knick der
Temperatur ist dafür nicht erforderlich. Die damaligen Originalfenster
bleiben die Referenz. Je Temperatur- und Feuchterolle muss das aktuelle
`K`-Fenster bei der Bestätigung vollständig aus Originalen strikt nach dem
jüngsten Original ihrer Bindungsreferenz bestehen.

Für jede Messgröße `X` aus Temperatur `T` und relativer Feuchte `H` bezeichnet
`X̄⁰⁻_p` den festgehaltenen Mittelwert des Vorgängerfensters bei der Bindung.
Der unmittelbare Niveauvergleich mit dem aktuellen Fenstermittel lautet:

```text
L_Xp = (X̄⁰⁻_p − X̄_p)/(2ε_Xp)
L    = min_p min(L_Tp, L_Hp)
```

Der Bestätigungsnachweis verwendet je Rolle sämtliche verfügbaren Originale
strikt nach dem jüngsten Original ihrer Bindungsreferenz. Auf dieser mit
neuen Werten wachsenden Stützmenge werden die Steigung `a_Xp`, ihre Grenze
`Eᵃ_Xp` und der Mittelwert `X̄ᵃ_p` mit den beschriebenen OLS-Regeln bestimmt.
Zwei verschiedene Zeitpunkte sind das mathematische Minimum für diese
Gerade; zusätzlich gilt die vollständige neue `K`-Stütze jeder Rolle.

Für Temperatur und Feuchte wird der Niveauverlust gegen den bei der Bindung
beobachteten lokalen Hintergrund verglichen. Die historischen `2K`-Stützen
liefern die Beträge der Änderungen zwischen ihren beiden Fenstermitteln
`d_Xpj`. Mit der MAD-Umrechnung gilt:

```text
m_Xp = Median_j(d_Xpj)
S_Xp = max(2ε_Xp, 1/Φ⁻¹(0,75) · Median_j |d_Xpj − m_Xp|)
D_Xp = (X̄⁰⁻_p − X̄ᵃ_p − m_Xp)/S_Xp
```

`Φ⁻¹` ist die inverse Standardnormalverteilung; die Umrechnung macht daraus
keine Ereigniswahrscheinlichkeit. Bei zu wenigen historischen Stützen gelten
`m_Xp = 0` und `S_Xp = 2ε_Xp`. Die Mindestzahl stammt aus
`curve_original_kink_history_windows`. Bei der Temperatur verwendet derselbe
mathematische Hintergrundhelfer die allgemeine Auflösungseinheit `s`; der
RH-spezifische Faktor `curve_original_kink_factor` wird nicht auf T erweitert.

Die gemeinsame Absenkung benötigt an allen beteiligten Positionen:

```text
a_Tp < −γO Eᵃ_Tp
D_Tp > γO
D_Hp > γO
```

Ein weiterer Feuchtefall nach der Bindung ist dafür nicht erforderlich.
Die erste vollständig belegte gemeinsame Absenkung wird im Türvorgang
gehalten. Eine zunehmende Messwertzahl oder längere Wartezeit bestätigt für
sich kein Ereignis. Quellenwechsel, unterbrochener Anschluss und aufgelöste
Gegenverläufe bleiben Ausschlussgründe. Eine positiv aufgelöste letzte
Temperatursekante an einer beteiligten Position sperrt die Bestätigung im
betreffenden Präfix, auch wenn der längere Originalfit noch fällt. Ihr
Kontrast ist die letzte Temperaturdifferenz geteilt durch `2ε_Tp`; er muss
für diese Sperre strikt größer als `s` sein. Die einzelne Endkante verwirft
den gebundenen Kandidaten nicht.

Der zugeordnete Beginn stammt ausschließlich aus den Feuchteknicken der
gebundenen Messpositionen. Ein gültiger Beginnzeuge darf vor dem ersten
Bindungsfenster liegen oder erst danach hinzukommen. Sein eigener
unterstützter Originalfit, Quelle, Epoche und Türkontext bleiben maßgeblich;
sein jüngstes verwendetes Original liegt spätestens im aktuellen Präfix.
Der Originalanschluss muss erhalten sein.

Vor der Aufnahme eines Zeugen und erneut vor der gemeinsamen Bestätigung
wird geprüft, ob nach seinem Beginn eine vollständig gestützte RH-Erholung
liegt. Dazu werden die verfügbaren nativen `2K`-Blöcke geprüft: Besteht deren
aktueller `K`-Block vollständig aus Originalen nach dem Beginn und gilt
`v_Hp > s E_Hp`, gehört der alte Zeuge nicht mehr zum fortlaufenden Fallast.
Auch eine Erholung unterhalb des alten Vorlaufniveaus zählt. Sie setzt den
noch unbestätigten Kandidaten zurück; ein späterer Fall der anderen Position
darf ihn nicht mit dem alten Zeugen bestätigen.

Jede beteiligte Position benötigt einen solchen weiterhin verbundenen
RH-Zeugen. Die feste Beginnposition des jeweiligen Zeugen wird bei der
Bestätigung mit seinen eigenen Originalen und derselben Form erneut geprüft.
Sie wird nicht in das erste gemeinsame Bindungsfenster zurückgesucht.
Temperaturknicke verschieben den RH-Beginn nicht. Die Steuerung erhält das
Ereignis erst bei der späteren Bestätigung; die Beginnzuordnung datiert diese
Bestätigung nicht zurück.

### Abgrenzung zum Aufgussabklingen

Ein nativer starker oder bereits qualifizierter RH-Anstiegsbesitzer desselben
Quellen- und Epochenkontexts liefert seine gebundenen Temperaturbaselines.
Berücksichtigt werden die aktuelle Aufgusskomponente sowie Anfangs- und
aktuelle Komponente einer noch ausstehenden oder aktiven Originalepisode.
Die Referenzen bleiben beim Öffnungskandidaten erhalten. Jede Referenz kann
je T-Rolle durch einen vollständig neuen aktuellen `K`-Block mit aufgelöstem
Fall und eigenständigem Verlust unter ihr damaliges Vorlaufniveau erfüllt
werden. Dafür gelten `a_Tp < −γO Eᵃ_Tp` und `D_Tp > γO`, ausgewertet nur auf
den aktuellen `K`-Originalen mit dem lokalen Hintergrund dieser
Aufgussreferenz.

Eine Unterschreitung der Voraufgussbaseline ist keine zwingende Bedingung.
Alternativ kann jede T-Rolle einen eigenen neuen lokalen Abwärtsknick binden.
Die T-Knicktoleranz verwendet wie die RH-Toleranz den größeren Wert aus
Kataloggröße und historischer Steigungsänderung, jedoch mit Faktor `s`:

```text
Sᵏ_Tp = max(E_Tp + E⁻_Tp, Median_j |v_Tpj − b_Tpj|)
κ_Tp  = s · Sᵏ_Tp
δ_eff,Tp = sgn(v_Tp − b_Tp) · max(0, |v_Tp − b_Tp| − κ_Tp)
```

Bei zu kurzer Historie ist `Sᵏ_Tp = E_Tp + E⁻_Tp`. Der erste gebundene
T-Abwärtsknick benötigt `δ_eff,Tp < 0` und `v_Tp < −s E_Tp`. Seine feste
Vorgängersteigung heißt `bᵈ_Tp`, ihre Grenze `Eᵈ⁻_Tp`. Danach müssen je Rolle
beide disjunkten Fenster vollständig aus Originalen strikt nach dem jüngsten
Original dieser eigenen Knickreferenz bestehen und erfüllen:

```text
b_Tp/E⁻_Tp < −γO
v_Tp/E_Tp  < −γO
(b_Tp − bᵈ_Tp)/(E⁻_Tp + Eᵈ⁻_Tp) < −s
(v_Tp − bᵈ_Tp)/(E_Tp + Eᵈ⁻_Tp)  < −s
```

Die Prüfung verlangt somit entweder die unabhängigen Niveauverluste gegen
alle gebundenen Aufgussreferenzen oder den neuen lokalen T-Fall an allen
Positionen. Ohne gebundenen nativen Aufgussbesitzer entfällt diese zusätzliche
Prüfung. Der fortgeschriebene Anschluss des Türbesitzers bleibt maßgeblich;
eine alte Aufgussbaseline muss dafür nicht dauerhaft im Originalring liegen.
Die allgemeinen RH-Zeugen und die gemeinsame Absenkung bleiben in beiden
Fällen erforderlich.

Wird die Öffnung durch den neuen lokalen T-Fall zugelassen, benötigt jede
RH-Rolle einen eigenen unterstützten Abwärtsknick in der Originalzeitstütze
ihres gebundenen lokalen T-Knicks. Die Suche begrenzt die möglichen
Knickpositionen, erhält aber den vollständigen bisherigen Originalkontext des
Fits. Ein früherer Zeuge des Aufgussabklingens außerhalb dieser Stütze wird
ersetzt. Die Temperaturzeit selbst wird nicht als RH-Beginn ausgegeben.

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

### Temperaturbremse und Schlussbestätigung

Eine Schließung setzt eine erkannte Öffnung voraus. Die schwächste
beziehungsweise stärkste aktuelle Erwärmung und die native Bremse lauten:

```text
B     = min_p (v_Tp − b_Tp)/(E_Tp + E⁻_Tp)
W_min = min_p v_Tp/E_Tp
W_max = max_p v_Tp/E_Tp
```

Ein gemeinsamer Schließkandidat bindet die damaligen Temperaturfenster, wenn
entweder jede Rolle einen lokalen Bremsknick mit `δ_eff,Tp > 0` und
`b_Tp < −s E⁻_Tp` liefert oder `W_min > s` gilt. Für die T-Knicktoleranz gilt
der oben beschriebene allgemeine Faktor `s` einschließlich des beobachteten
Steigungsänderungshintergrunds.

Die feste Vorgängersteigung des gebundenen Fensters heißt `b⁰_Tp`, ihre Grenze
`E⁰⁻_Tp`. Für die Feuchteerholung ist `M_p` das kleinste bisher belegte Mittel
des aktuellen Feuchtefensters in derselben geöffneten Gruppe. Es gelten:

```text
B_f = min_p (v_Tp − b⁰_Tp)/(E_Tp + E⁰⁻_Tp)
U   = min_p (H̄_p − M_p)/(2ε_Hp)
D_fp = min((v_Tp − b⁰_Tp)/(E_Tp + E⁰⁻_Tp),
           (b_Tp − b⁰_Tp)/(E⁻_Tp + E⁰⁻_Tp))
D_f  = min_p D_fp
C   = max {min(B_f, U), min(B_f, W_max), W_min, D_f}
```

Für eine Rolle gilt die Formel für `D_fp` nur bei aufgelöst fallender
gebundener Vorgängersteigung `b⁰_Tp < −s E⁰⁻_Tp`. Der reine Bremszweig
`D_f` wird zusätzlich nur mit den unten beschriebenen gehaltenen lokalen
Steigungsänderungsformen aller verwendeten T-Rollen zugelassen; andernfalls
entfällt er. Bei nicht aufgelöst fallendem Vorlauf wird für die betreffende
Rolle `D_fp = −s` eingesetzt. Der Feuchtezweig `min(B_f, U)`
benötigt sämtliche ursprünglichen Positionen und ihre Quellenabschnitte;
bei gestörter Feuchte entfällt er.

Die Bestätigung verlangt an jeder verwendeten Temperaturposition zwei
vollständig neue disjunkte `K`-Blöcke nach der gemeinsamen Bindung sowie
`C > s`. Die ersten drei Formzweige verbinden die Bremse mit Feuchteerholung
oder Erwärmung. `D_f > s` belegt dagegen in beiden neuen Blöcken die
anhaltend schwächere Abkühlung gegenüber dem gebundenen Vorlauf. Ihre
Steigungen dürfen weiterhin negativ sein; waagerechte Fenster und eine
Verträglichkeit ihrer Mittel sind dafür keine Bedingungen.

Ein weiterer Schließweg bindet dieselbe lokale Bremse zeitversetzt je Rolle.
Dafür hält der Türbesitzer zunächst ein tatsächlich aufgelöst fallendes
T-Fenster. Bis zur lokalen Bremse ersetzt ein späteres Fallfenster diese
Referenz, wenn seine obere Steigungsgrenze `v + sE` kleiner ist. Das bloße
spätere Schwächerwerden gegenüber diesem gehaltenen Fall bestätigt keinen
Schluss. Erst ein natives Fenster mit `b_Tp < −s E⁻_Tp` und
`δ_eff,Tp > 0` bindet die feste Bremsreferenz dieser Rolle. Die beiden danach
vollständig neuen `K`-Blöcke müssen gegenüber deren Vorgängersteigung dieselben
beiden positiven Kontraste wie in `D_f` jeweils über `s` liefern.

Diese Rollenbremsen können zu verschiedenen Präfixen entstehen; ein
gemeinsamer momentaner Bremsknick und ein gemeinsamer Schließkandidat sind für
diesen Weg nicht erforderlich. Eine Feuchteerholung ist ebenfalls keine
Bedingung. Ein Wechsel der verwendeten Temperaturpositionsmenge erfordert im
gemeinsamen Kandidatenweg eine neue Bindung.

Beide reinen Temperaturbremswege benötigen zusätzlich je Rolle eine
unterstützte lokale Steigungsänderungsform des bestehenden Kurvenvergleichs
mit `hinge_order = 1`: Der Vorlauf fällt, die Steigungsänderung ist positiv,
und der Vergleich mit glatten Verläufen trägt die Änderung. Der Fallbesitzer
hält die erste solche Form aus einem eigenen neuen T-Originalpräfix samt
unveränderten Originalen, Quelle und Epoche. Ihr Beginn liegt frühestens am
jüngsten Original der gehaltenen Fallreferenz. Sie darf vor der späteren nativen
Bremsbindung derselben Rolle entstehen; ihr lückenloser Anschluss bleibt
maßgeblich. Eine bis zur Bremsbindung verstärkte Fallreferenz verwirft diese
Form. Die anschließend erforderlichen nativen Bremskontraste und zwei neuen
Originalblöcke bleiben eigenständige Bedingungen. Eine negative Nachsteigung
bleibt zulässig.

Trägt ein solcher reiner Bremsweg die Schlussbestätigung, stammt der frühere
Beginn aus genau diesen gehaltenen T-Formen. Die bestehende Prüfung des Tür-
und Betriebskontexts bleibt erhalten. Ein anderer früherer Kurvenkandidat
liefert für diesen Schluss keinen Beginn.

### Anhaltende Gegenform und Originalkorrektur

Jede gebundene Bremse führt je T-Rolle das bisherige Fenster mit der höchsten
belegten aktuellen Steigung als Erholungsreferenz `v_rec`, `E_rec`. Eine
anhaltende Gegenform benötigt einen aktuellen `K`-Block, dessen Originale
vollständig nach dem jüngsten Original dieser Referenz liegen. Er muss selbst
aufgelöst fallen und gegenüber derselben Erholungsreferenz stärker fallen:

```text
v_Tp/E_Tp < −s
(v_Tp − v_rec)/(E_Tp + E_rec) < −s
```

Eine solche Gegenform an einer Rolle verwirft den gemeinsamen
Schließkandidaten und entwertet deren alte Fall-/Bremsstütze samt gehaltener
Steigungsänderungsform. Das gilt auch, wenn die Gegenform zuerst gegen die
Erholungsreferenz des gemeinsamen Kandidaten belegt wird.
Ein noch überlappender Vorgängerblock hält diesen neuen Gegenbeleg nicht bis
über die mögliche Schlussbestätigung hinaus zurück.
Eine einzelne negative Endsekante wird weiterhin diagnostiziert, verwirft
aber für sich weder die Bindung noch den gehaltenen Fall. Wiederholte Ticks
und fremde neue Originale ersetzen keine frischen T-Blöcke der betroffenen
Rolle.

Eine Lücke oder ein Quellenbruch entwertet die davon abhängigen Nachweise.
Eine echte T-Korrektur bei gleichem Originalzeitpunkt entwertet auch dann den
gemeinsamen Schließkandidaten, wenn der korrigierte letzte Wert außerhalb der
früheren Referenzfenster liegt. Berührt die Korrektur ein Original des
gehaltenen Fall-, Brems- oder Erholungsfensters oder der gehaltenen
Steigungsänderungsform, entfällt die Fall-/Bremsstütze dieser Rolle.
Unberührte historische Belege bleiben erhalten. Die Korrektur liefert keine
neue Frische: Jeder Schlussweg benötigt an der korrigierten T-Rolle zwei
vollständige neue Originalblöcke strikt nach dem korrigierten Zeitstempel.
Neue Originale anderer Rollen ersetzen diese Stütze nicht.

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
Der native RH-Block kann seinen Anstieg auch innerhalb der gebundenen
Originalstütze lokalisieren. Dafür werden benachbarte Originalwerte des
aktuellen Blocks mit der Steigung des unveränderten Vorgängerblocks verglichen.
Die Sekantenskala beträgt `2 ε_H/Δt`; für die Steigungsänderung gilt das Maximum
aus dieser Skala plus der Fehlerschranke des Vorgängerblocks und der bereits
gebundenen Knicktoleranz. Der erste aufgelöst positive Schritt mit einer
signifikanten positiven Steigungsänderung liefert ein Beginnintervall zwischen
seinen beiden Originalzeitpunkten. Er bestätigt allein keinen Aufguss: Stärke,
Wasserqualifikation und frischer Anstiegs- oder Fallblock bleiben erforderlich.
Ohne gesonderten früheren Beginnzeugen wird die linke Originalstütze dieses
Intervalls als zugeordneter Beginn verwendet; die Intervallgrenzen bleiben in der
Diagnose erhalten. Ein exakter Zeitpunkt zwischen beiden Messungen wird nicht
behauptet. Liegen solche Intervalle an allen Positionen vollständig nach dem
vorherigen Ende, belegen sie einen neuen Ast auch dann, wenn ältere ruhige
Punkte seines Auswertungsfensters noch vor diesem Ende liegen. Ein überlappendes
Beginnintervall bleibt ungeklärt; ein früherer gültiger Beginn bleibt Fortsetzung.
Der erste gültige Beginnzeuge eines Anstiegs bleibt je Messposition gebunden.
Spätere Fits, deren Stützung bereits Spitze und Abfall umfasst, ersetzen ihn
nicht durch einen früheren Knick. Eine Korrektur seiner Originalwerte prüft
weiterhin denselben gebundenen Zeugen.
Bei einer Fortsetzung vor der ersten Bestätigung bleibt der Beginnbeleg des
ersten qualifizierten Anstiegs erhalten. Der neuere Ast liefert den weiteren
Verlauf, ersetzt aber nicht den Anfang der gemeinsamen Episode. Erst die
einmalige Ausgabe übernimmt diesen Beginn in das Aufgussereignis.

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
aktueller Feuchteblock mit `K` Originalen vorliegt. Dieser Abschluss benötigt
nur die gebundenen Feuchterollen; neue Temperaturoriginale sind dafür nicht
nötig. Ein widersprechender auswertbarer Feuchteverlauf verwirft den
Hintergrundkandidaten. Fehlende oder momentan nicht auswertbare Daten lassen
den bisherigen Beleg bestehen und bestätigen nichts zusätzlich. Nach einer
Messlücke muss der neue aktuelle Feuchteblock selbst vollständig auswertbar
sein; die unbeobachtete Zwischenzeit erhält keinen rückdatierten Endbeleg.
Ein unbestätigter Anstieg verfällt nicht allein nach einer Zeitfrist. Ein
vollständig belegter Hintergrund ohne bestätigten Puls schließt seinen
Kandidaten ab. Quellenwechsel und Korrekturen tragender Originale werden
weiterhin am jeweiligen Beleg geprüft.

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
verwendeten aktuellen `K` RH-Originale aller beteiligten Positionen nach dem
Abschluss liegen. Das vorherige Referenzfenster darf den alten Hintergrund
überlappen; es belegt den Steigungswechsel und gehört nicht zum neuen
Anstiegsblock. Zusätzliche Kurvenbeginnbelege sind dafür nicht erforderlich.
Auch gültige Beginnbelege aller Positionen nach dem Abschluss erlauben diese
Zuordnung. Überlappt der gebundene aktuelle RH-Block den Abschluss ohne solche
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

Durchlüften setzt eine vorherige Türöffnung voraus und bleibt Teil dieses
Türvorgangs. Ohne zugehörige Öffnung entsteht kein Lüftungsereignis.
Die beiden Lüftungsstufen werden bei allen erkannten Türvorgängen gleich
ausgewertet, unabhängig von einem laufenden Gang und der Präsenzquelle. Sie
benötigen im selben Türvorgang einen vorherigen Feuchtefall und dessen belegte
deutliche Abflachung, während die Temperatur weiter fällt, ohne merklich
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
| Lüften (`ventilation_confirmed`) | Die relative Feuchte flacht nach einem belegten Fall deutlich ab, während die Temperatur weiter fällt. Diese Stufe liefert bereits den Lüftungsnachweis für das Gangende; außerhalb eines Gangs wird sie statistisch erfasst. |
| Vollständig gelüftet (`ventilation_complete`) | Nach belegter erster Stufe flacht auch der absolute Wassergehalt deutlich ab, während die Temperatur weiter fällt. Diese Stufe ergänzt einmalig den statistischen Beleg derselben noch offenen Tür ohne zusätzliche Gangwirkung. |

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
v_Xp                  ≤ s E_Xp
v_Xp − b_Xp           > s (E_Xp + E⁻_Xp)
−v_Tp                 > s E_Tp
v_Tp − b_Tp           ≤ s (E_Tp + E⁻_Tp)
```

Beim Binden des jeweiligen Stufenkandidaten müssen alle fünf Vergleiche an
allen beteiligten Positionen erfüllt sein: vorheriger Feuchtefall, kein
aufgelöster Feuchteanstieg, aufgelöste Abbremsung, weiter fallende Temperatur
und keine aufgelöste Temperaturbremse. Die Feuchte darf weiterhin fallen.
Eine waagerechte Kurve ist für keine der beiden Lüftungsstufen erforderlich.
Bis zur Bestätigung wird die Abbremsung gegen die beim Stufenbeginn
festgehaltene frühere Feuchtesteigung `b_Xp` und deren Grenze `E⁻_Xp`
verglichen. Die aktuelle Steigung stammt weiterhin aus den neuen Originalen;
die beiden Temperaturbedingungen müssen erhalten bleiben. Die Bestätigung benötigt
außerdem seit der Bindung an sämtlichen Positionen spätere Temperatur- und
Feuchteoriginale; in der zweiten Stufe müssen die Feuchteoriginale vollständig
gepaart sein. Ein Zwischenstand außerhalb der erhaltenen Form verwirft den
jeweiligen noch unbestätigten Stufenkandidaten. Ein bereits vollständig
belegter und lediglich noch nicht veröffentlichter Nachweis bleibt erhalten.

Auch mehrere Wasserpaare, die mit einem verspäteten Temperaturpartner gemeinsam
verfügbar werden, werden einzeln gegen diese gebundene Form geprüft. Weiterer
Wassergehaltsabfall ist dabei zulässig, solange die Abflachung belegt bleibt.
Ein erneuter starker Fall, ein aufgelöster Anstieg oder die Temperaturgegenform
verwirft den noch unbestätigten Kandidaten.

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
und den festen gemeinsamen Absenkungsbeleg. Für die Bestätigung werden die
seit der Bindung hinzugekommenen Originale mit ihrem ersten und letzten
Zeitpunkt, ihrer Anzahl sowie den Richtungs- und Niveaukontrasten getrennt
ausgewiesen. Temperatur und Feuchte halten die beobachtete Absenkung,
den historischen Vergleichswert und die verwendete Streuung getrennt fest.
Die Feuchtebeginnzeugen jeder Position, ihre fortlaufende Astprüfung und die
zusätzlichen Aufgussreferenz- beziehungsweise lokalen T-Fallprüfungen bleiben
sichtbar. Die vollständigen neuen Stützblöcke gehören zu den jeweiligen
Bestätigungsbedingungen. Beim Türschluss werden die lokale Temperaturbremse,
die gespeicherten Rollenfall- und Bremsfenster, ihre Folgeblöcke und die
anhaltenden Gegenbelege getrennt geführt. Der jüngste Originalschritt bleibt
als gesonderte Kantendiagnose sichtbar.
Der zusätzlich gespeicherte ältere
Öffnungskontrast mit Temperaturknick ist keine Voraussetzung der gemeinsamen
Bestätigung. Schließkontrast sowie Anstiegs-, Fall- und Hintergrundbedingungen
bleiben getrennt ausgewiesen.

Beim Durchlüften werden RH- und AH-Stufe mit eigenen Kandidaten, Stützen und
Bestätigungen geführt. Die fünf Vergleiche zeigen jeweils vorherigen Fall,
fehlenden aufgelösten Feuchteanstieg, Feuchteabflachung, weiter fallende Temperatur und fehlende
Temperaturbremse. Die AH-Stufe enthält zusätzlich den Paarungsstand;
Hüllenstreuung und konstante Hüllenverträglichkeit bleiben Diagnosewerte.
Beim Aufguss werden der frische RH-Anstiegsbeginn, die gebundenen und
fortgesetzten Stützen, der gespeicherte starke Anstiegsbeleg, die aktuelle
Steigung, die begrenzte Wasserqualifikation und der nach der Spitze belegte
Abfall getrennt geführt.
Die Türöffnung benötigt den gemeinsamen Fall mit einem vollständig neuen
aktuellen Block je Rolle und die weiterhin verbundenen Feuchtebeginne. Der
lokale T-Fallweg bei vorhandenem Aufgusskontext und die Schlusswege verlangen
zwei vollständig neue Blöcke nach ihrer jeweiligen Referenz. Die
Aufgusszweige behalten ihre eigenen Frischebedingungen. Der
Hintergrundnachweis trennt den ersten Kandidaten vom durch neue Originale
bestätigten Abschluss.

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
