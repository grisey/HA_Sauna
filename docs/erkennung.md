# Türöffnung, Aufguss und Durchlüften erkennen

Die [wirksame Präsenzquelle](gangmodell.md#wirksame-präsenzquelle) bestimmt den
Anwesenheitsnachweis. Türereignisse und Aufgüsse stammen in beiden Verfahren
aus Temperatur und Feuchte.

| Erkennung | Aus Temperatur und Feuchte | Mit Präsenzsensor |
| --- | --- | --- |
| Türöffnung und Türschluss | Erkennung aus dem führenden Messpaar | Dieselbe Erkennung aus dem führenden Messpaar |
| Anwesenheit | Personensignale; zunächst vorläufiger Gang | Belegungszustand der zugeordneten Entität |
| Aufguss | Bestätigt den Gang; weitere Aufgüsse werden erfasst | Erfassung im bereits bestätigten Gang |
| Durchlüften | Bestandteil der vorherigen Austrittsöffnung; Nachweis für das Gangende | Bestandteil derselben Austrittsöffnung; zusammen mit deren Abwesenheitsnachweis Beleg für das Gangende |

**Sensoren und Erkennung** in der Integrationskonfiguration trennt
Messwertaufbereitung, Türerkennung, Personenerkennung und Aufgüsse. Personenschwellen und
Aufgussbestätigungsfrist erscheinen nur beim indirekten Verfahren.
Ihre gespeicherten Werte bleiben beim Verfahrenswechsel erhalten.
Die Übergangsregeln beschreibt die [Gangerkennung](gangmodell.md).

## Führender Temperatur-/Feuchtesensor

Eine Messposition bezeichnet einen physischen Sensor mit den beiden
Messgrößen Temperatur und relativer Feuchte. Daraus berechneter absoluter
Wassergehalt ist ebenfalls kein zusätzlicher Sensor. Temperatur und Feuchte
werden innerhalb derselben Position zusammen ausgewertet; Werte verschiedener
Positionen bilden kein künstliches Messpaar.

Standardmäßig führt der obere Sensor. Ein eigener klarer Beleg benötigt keine
Bestätigung durch den unteren Sensor. Abweichende Werte, Ausfälle oder
Korrekturen an der unbenutzten zweiten Position verändern diesen Beleg nicht.
Ist ein benötigter oberer Messwert nicht mehr auswertbar, kann ein gültiger
unterer Sensor mit seinen eigenen Originalen übernehmen. Ein lediglich noch
nicht wieder auswertbares Kurvenfenster macht den führenden Sensor nicht
unverfügbar und erlaubt für sich keinen Ersatz.

Der zweite Sensor wird nur bei einem bereits vorhandenen, noch unklaren
Muster des führenden Sensors zur Klärung herangezogen. Ein vollständig
belegter eigener Verlauf kann dieses Muster bestätigen; eine zugehörige
belegte Gegenform kann es widerlegen. Ein fehlender Messwert, ein schwächeres
Signal oder ein nicht erfüllter positiver Formtest sind allein keine
Widerlegung. Ohne zuordenbaren klaren Zweitbefund bleibt der Kandidat offen.
Ein unterer Einzelverlauf ohne oberen Kandidaten eröffnet bei auswertbarem
oberem Sensor keinen zusätzlichen Ereignisweg.

Entscheidet ein vollständiger unterer Eigenbeleg den unklaren Vorgang, bleiben
seine Quellen, Originale und eigenen Beginn- und Bestätigungszeiten zusammen
gebunden. Eine spätere obere Meldung verändert die bereits gebuchte
Entscheidung nicht. Beim nächsten neuen Vorgang gilt wieder oberer Vorrang.
Türöffnung, Türschluss und jede Lüftungsstufe besitzen jeweils eine eigene
Führungsbindung. Diese gilt für den konkreten Beleg mit seiner Bindezeit,
seinem Türkontext und seinen Quellenabschnitten. Eine unten bestätigte
Lüftungsstufe bestimmt daher nicht die Führung einer neuen Schlussentscheidung.
Jedes erkannte Ereignis wird einmal veröffentlicht. Die Messpositionen erzeugen
keine doppelten Buchungen.

Bei Ausfall des tragenden Sensors bleiben bestätigte Ereignisse und ihre
Zeiten erhalten. Ein Ersatzsensor kann einen eigenen Folgeabschnitt derselben
Tür- oder Aufgussfolge belegen. Er benötigt dafür eigene Fall-, Anstiegs- und
Bestätigungsreferenzen; fremde Stützen werden nicht übernommen. Fehlen diese,
bleibt die betreffende Folgeentscheidung offen. Die Rückkehr des oberen
Sensors schreibt den bereits gebundenen Ersatzabschnitt nicht um.

Diese Auswahl gilt für Türöffnung, Türschluss, beide Lüftungsstufen, Aufgüsse
und klimatische Personenhinweise. Die direkte Präsenzführung und die
Heizschutzregeln bleiben eigenständige Verfahren. Ein Temperatur-Schlussweg
kann weiterhin ohne Feuchtebeleg arbeiten; ein bereits gebundener
Aufgussabschluss benötigt weiterhin keine neuen Temperaturmeldungen.

Beide Messreihen bleiben im Verlauf und Archiv erhalten. Die Diagnose
unterscheidet die verfügbare Position, den tatsächlichen Entscheidungsträger
und eine gegebenenfalls zur Klärung verwendete zweite Position. Sämtliche
folgenden Formbedingungen werden jeweils für einen Sensor berechnet. Der
Index `p` bezeichnet diese Position; aus den beiden Geräten wird kein
positionsübergreifender Mindestbeleg gebildet. Die getrennten Originalzeiten
der beiden Messgrößen bleiben erhalten.

Die Fensterdiagnose hält für beide Positionen und Messgrößen die tatsächlich
verwendete Quelle, ihren Abschnitt, die Originalanzahl und die Entscheidung
über die Zeitstütze fest. Bei getrennten Stützen bleiben die betroffenen
Originalzeiten und die damals verwendete Lückengrenze erhalten; eine spätere
Meldung berechnet diese Grenze nicht rückwirkend neu. Fehlende Originale,
unterbrochene Stützen und abgelaufene Frische sind getrennte Ablehnungsgründe.
Diese Angaben bleiben auch ohne gebundenes Muster sichtbar. Sie beschreiben
die Auswertbarkeit des Eingangs und ändern weder Sensorvorrang noch Formregeln.

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
Sensorausfallfrist. Ein Wechsel der Quelle oder Zeitbasis sowie ein ausdrücklich
ungültiger Messwert beginnen einen neuen Quellenabschnitt. Ein bloßer
Sensor-Timeout ändert die aktuelle Verfügbarkeit, erhält aber gültige
historische Stützen.

Fehlen neue Originale, warten bereits gültig gebundene Fall-, Schluss- und
Lüftungsbelege unverändert. Ein fehlendes aktuelles Auswertefenster
ist weder eine Gegenform noch eine Bestätigung. Auch ein späterer Messwert
mit größerem Abstand belegt zunächst nur einen unbekannten Anschluss.
Die Lücke darf weder überbrückt noch allein als Widerlegung verwendet werden.
Ein Quellenwechsel, ein ungültiger Wert oder die Korrektur tragender Originale
wird unabhängig davon geprüft.

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

Für Öffnung, Schließung und die Lüftungsstufen wird der
lückenlose Anschluss je verwendeter Messgröße des Sensors an jedem
auswertbaren Eingangspräfix geprüft. Der Anschlussanker rückt mit dem jüngsten
Original weiter; die fachliche Referenz der Bindung bleibt dabei unverändert.
Damit muss der ursprüngliche Kandidatenbeginn bei einem langen Ast nicht
dauerhaft im Ringspeicher liegen. Eine Lücke zwischen den fortgeschriebenen
Ankern und den neuen Originalen unterbricht den nachgewiesenen Anschluss,
auch wenn die jüngsten `2K` Originale für sich wieder gültige Fenster bilden.
Die bereits gültigen gebundenen Originalbelege bleiben als Referenz erhalten.
Die Lücke selbst bestätigt nichts; neue Entscheidungen benötigen weiterhin
passende neue eigene Originalblöcke und den fachlichen Formnachweis im selben
Quellen- und Ereigniskontext. Bei Aufgüssen gehören dazu die positiven
Wasserbelege. Solange diese Voraussetzungen fehlen, bleibt die Entscheidung
offen.

### RH-Beginn und Knicktoleranz

Unter den beschriebenen Gültigkeits- und Positionsregeln führt ein frischer
Steigungswechsel der relativen Feuchte mit passender aktueller Richtung den
Tür- beziehungsweise Aufgusskandidaten. Für den betrachteten Sensor `p`
gelten mit `H` als relativer Feuchte:

```text
q_Hp = (v_Hp − b_Hp)/(E_Hp + E⁻_Hp)
A↑   = min(v_Hp/E_Hp, q_Hp)
A↓   = min(−v_Hp/E_Hp, −q_Hp)
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
Ein Anstiegsbeginn benötigt weiterhin `A↑ > s` und zusätzlich
`δ_eff,p > 0`; ein Fallbeginn benötigt `A↓ > s` und
zusätzlich `δ_eff,p < 0`. Faktor `0` schaltet die zusätzliche
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
Öffnungskandidaten dieses Sensors. Diese erste Bindung verlangt dessen
Feuchtefallbeginn im aktuellen Präfix. Ein Knick der
Temperatur ist dafür nicht erforderlich. Die damaligen Originalfenster
bleiben die Referenz. Je Temperatur- und Feuchterolle muss das aktuelle
`K`-Fenster bei der Bestätigung vollständig aus Originalen strikt nach dem
jüngsten Original ihrer Bindungsreferenz bestehen.

Für jede Messgröße `X` aus Temperatur `T` und relativer Feuchte `H` bezeichnet
`X̄⁰⁻_p` den festgehaltenen Mittelwert des Vorgängerfensters bei der Bindung.
Der unmittelbare Niveauvergleich mit dem aktuellen Fenstermittel lautet:

```text
L_Xp = (X̄⁰⁻_p − X̄_p)/(2ε_Xp)
L    = min(L_Tp, L_Hp)
```

Im allgemeinen Originalfall verwendet der Bestätigungsnachweis je Rolle
sämtliche verfügbaren Originale strikt nach dem jüngsten Original ihrer
Bindungsreferenz und deren festes Vorgängermittel `X̄⁰⁻_p`. Die unten
beschriebene aktuell unterstützte lokale T-Fallreferenz bildet die Ausnahme
für die jeweilige T-Rolle. Auf der mit neuen Werten wachsenden Stützmenge
werden die Steigung `a_Xp`, ihre Grenze
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

Die Absenkung des betrachteten Sensors benötigt:

```text
a_Tp < −γO Eᵃ_Tp
D_Tp > γO
D_Hp > γO
```

Ein weiterer Feuchtefall nach der Bindung ist dafür nicht erforderlich.
Die erste vollständig belegte gemeinsame Absenkung wird im Türvorgang
gehalten. Eine zunehmende Messwertzahl oder längere Wartezeit bestätigt für
sich kein Ereignis. Quellenwechsel, unterbrochener Anschluss und aufgelöste
Gegenverläufe bleiben Ausschlussgründe. Je T-Rolle wird zusätzlich der erste
eigene Fallbeleg mit vollständiger neuer `K`-Stütze, mindestens `K` Originalen
im Fallfit, `a_Tp < −γO Eᵃ_Tp` und `D_Tp > γO` gehalten. Sein Erwerb verlangt
außerdem eine auswertbare, noch nicht positiv aufgelöste letzte
Temperatursekante. Deren Kontrast ist die letzte Temperaturdifferenz geteilt
durch `2ε_Tp`; beim Erwerb darf er höchstens `s` sein. Der Beleg gilt nur für
dasselbe Referenzobjekt und dieselbe Wahl zwischen dessen Vorgänger- und
aktuellem Niveau (`reference_current`). Ein Wechsel entwertet ihn. Ohne einen
solchen erhaltenen Beleg für jede T-Rolle sperrt eine letzte Temperatursekante mit
Kontrast strikt über `s` die Bestätigung im betreffenden Präfix, auch wenn
der längere Originalfit noch fällt. Der vollständige gehaltene T-Satz
überwindet allein diese spätere Endkantensperre; der aktuelle gemeinsame
RH-/T-Absenkungsbeleg, die eigenen RH-Beginne und die zusätzliche
Aufgussprüfung bleiben erforderlich. Die
einzelne Endkante verwirft den gebundenen Kandidaten nicht.

Der zugeordnete Beginn stammt ausschließlich aus dem Feuchteknick des
gebundenen Sensors. Ein gültiger Beginnzeuge darf vor dem ersten
Bindungsfenster liegen oder erst danach hinzukommen. Sein eigener
unterstützter Originalfit, Quelle, Epoche und Türkontext bleiben maßgeblich;
sein jüngstes verwendetes Original liegt spätestens im aktuellen Präfix.
Der Originalanschluss muss erhalten sein.

Ein aufgelöstes natives Fallintervall kann den bereits beobachteten eigenen
RH-Abwärtsknick genauer lokalisieren. Die geprüften Intervalle reichen vom
letzten Vorgängeroriginal durch die aktuellen Originale des eigenen
RH-Fensters. Für die Sekantensteigung `u_Hpi` zwischen zwei Originalen im
Abstand `Δt_i` und ihre Grenze `Eⁱ_Hpi = 2ε_Hp/Δt_i` gelten beide Vergleiche:

```text
u_Hpi/Eⁱ_Hpi < −s
(u_Hpi − b_Hp)/max(Eⁱ_Hpi + E⁻_Hp, κ_p) < −s
```

Bei einem gebundenen Kandidaten gelten diese Sekantenvergleiche mit derselben
Knicktoleranz unabhängig vom effektiven Gesamtknick.
Das erste passende Intervall, sein RH-Fenster und sein Knickhintergrund werden
gehalten. Bei einem Formbeginn innerhalb dieser Sekantenstütze verlangt die
Übernahme des linken Originalpunkts als Beginn einen eigenen vollständigen
Formbeleg an genau diesem Punkt mit seinen unveränderten Originalen. Dieser
Fit bleibt bei der späteren Zuordnung zu einem lokalen T-Fall die erste Wahl.
Die Sekante allein bestätigt kein Ereignis. Ohne ein aufgelöstes
Einzelintervall bleibt der eigene aggregierte Originalfit
verfügbar. Ein unterstützter eigener Formbeginn außerhalb der Sekantenstütze
vom letzten Vorgängeroriginal bis zum jüngsten Original wird nicht auf das
Intervall verschoben; das gilt auch bei einem erneuten Fit im aktuellen
Originalpräfix.

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
Die Referenzen bleiben beim Öffnungskandidaten erhalten. Der diagnostische
Niveauvergleich prüft je T-Rolle einen vollständig neuen aktuellen `K`-Block
mit aufgelöstem Fall und Verlust unter ihr damaliges Vorlaufniveau:
`a_Tp < −γO Eᵃ_Tp` und `D_Tp > γO`, ausgewertet auf den aktuellen
`K`-Originalen mit dem lokalen Hintergrund dieser Aufgussreferenz.
Ein solcher Niveauverlust allein belegt keinen zusätzlichen Türvorgang.

Die Temperaturreihe des Sensors benötigt einen eigenen neuen lokalen
Abwärtsknick. Dessen Verlauf darf oberhalb der Voraufgussbaseline liegen.
Die T-Knicktoleranz verwendet wie die RH-Toleranz den größeren Wert aus
Kataloggröße und historischer Steigungsänderung, jedoch mit Faktor `s`:

```text
Sᵏ_Tp = max(E_Tp + E⁻_Tp, Median_j |v_Tpj − b_Tpj|)
κ_Tp  = s · Sᵏ_Tp
δ_eff,Tp = sgn(v_Tp − b_Tp) · max(0, |v_Tp − b_Tp| − κ_Tp)
```

Bei zu kurzer Historie ist `Sᵏ_Tp = E_Tp + E⁻_Tp`. Der erste gebundene
T-Abwärtsknick benötigt `δ_eff,Tp < 0` und `v_Tp < −s E_Tp`. Seine feste
Vorgängersteigung heißt `bᵈ_Tp`, ihre Grenze `Eᵈ⁻_Tp`. Danach muss je Rolle
das aktuelle Fenster vollständig aus Originalen strikt nach dem jüngsten
Original dieser eigenen Knickreferenz bestehen und erfüllen:

```text
v_Tp/E_Tp < −γO
(v_Tp − bᵈ_Tp)/(E_Tp + Eᵈ⁻_Tp) < −s
```

Mit erhaltenem Originalanschluss, dem vollständig neuen aktuellen Block und
beiden erfüllten Vergleichen ist der lokale T-Fall der jeweiligen Rolle
im aktuellen Präfix unterstützt. Genau dann verwendet der gemeinsame
T-Absenkungsbeweis für diese Rolle den Mittelwert `T̄ᵈ_p` des aktuellen
`K`-Fensters der gehaltenen T-Knickreferenz und den genau bei deren Bindung
gespeicherten Hintergrund:

```text
D_Tp = (T̄ᵈ_p − T̄ᵃ_p − mᵈ_Tp)/Sᵈ_Tp
```

`mᵈ_Tp` und `Sᵈ_Tp` folgen derselben Hintergrundnormierung wie oben. Der
ausgewertete Originalsuffix beginnt strikt nach dem jüngsten Original dieser
T-Knickreferenz; Richtung und Schwellen bleiben unverändert.
Ohne aktuell unterstützten lokalen T-Fall
bleibt das Vorgängermittel der ursprünglichen Bindungsreferenz maßgeblich;
die RH-Referenz und die bisherigen Frischebedingungen ändern sich nicht.

Die zusätzliche Prüfung verlangt den neuen lokalen T-Fall desselben Sensors.
Ohne gebundenen nativen Aufgussbesitzer entfällt diese zusätzliche
Prüfung. Der fortgeschriebene Anschluss des Türbesitzers bleibt maßgeblich;
eine alte Aufgussbaseline muss dafür nicht dauerhaft im Originalring liegen.
Die allgemeinen RH-Zeugen und die gemeinsame Absenkung bleiben
erforderlich. Das reale Abklingen nach einem Aufguss begründet keine
zusätzliche Sperre oder Mindestwartezeit. Ein Abschluss- oder Abklingstatus
ist kein Veto; eine belegte Türöffnung kann einen noch aktiven Aufguss beenden.

Ein tatsächlich unterstützter neuer lokaler T-Fall löst die Prüfung eines
späteren eigenen RH-Abwärtsknicks unabhängig vom priorisierten Diagnosegrund
der zusätzlichen Aufgussprüfung aus. Sein erstes signifikantes natives
RH-Intervall wird mit denselben Vergleichen lokalisiert und festgehalten.
Der erste eigene RH-Schritt wird bereits vor der T-Zuordnung gehalten, auch
wenn keine passende T-Stütze vorliegt. Ein eigenes qualifiziertes Intervall
hat Vorrang; nur ohne ein solches Intervall beendet ein gültiges aktuelles
eigenes RH-Fenster mit `δ_eff,p ≥ 0` die aktive lokale Transition und ermöglicht
einen neuen Knickanker. Fehlende oder ungültige Knickstützen eröffnen keine
neue Transition. Ein bereits zuordenbarer Intervallvorschlag bleibt für
später eintreffende T-Belege erhalten; ein eigener neuer zulässiger RH-Knick
kann ihn ablösen. Das bestätigt für sich kein Ereignis.
Nur der linke Intervallpunkt muss in der Originalzeitstütze des gebundenen
lokalen T-Knicks liegen; der rechte darf später folgen. Maßgeblich bleibt das
erste signifikante RH-Intervall vor dieser Zeitprüfung; ein späterer Schritt
desselben Knicks ersetzt es nicht. Ein neuer eigener vollständiger
RH-Formbeleg am festen linken Originalpunkt hat bei dieser späteren
Zuordnung Vorrang. Ist allein dieser Formfit mathematisch nicht unterstützt,
kann innerhalb desselben unabhängigen lokalen T-Fallasts ein bereits
aktueller unterstützter eigener RH-Formbeleg mit seinem unveränderten Beginn
und seinen unveränderten Formmerkmalen übernommen werden. Quelle, Epoche,
Türkontext und sämtliche eigenen Originale müssen zum aktuellen Präfix passen. Der
gehaltene Beginn und der Beginn des Formfits müssen übereinstimmen und sowohl
in der Originalzeitstütze des gebundenen lokalen T-Knicks als auch in der
nativen RH-Stütze vom letzten Vorgängeroriginal bis zum jüngsten Original
liegen. Frische, fortlaufender Originalanschluss und der Ausschluss einer
vollständig gestützten RH-Erholung bleiben erforderlich. Die spätere
Zuordnung ersetzt einen älteren Zeugen des Aufgussrücklaufs nur durch einen
eigenen späteren RH-Beginn. Die vollständige eigene Originalstütze des Fits
bleibt erhalten. Ein Formbeleg mit anderem Beginn rechtfertigt keine Ausgabe
des linken Intervallpunkts als Beginn. Der T-Beleg allein löscht keine legitime
frühe oder asynchrone RH-Zuordnung; die Temperaturzeit wird nicht als RH-Beginn
ausgegeben.

Ein vollständiger eigener Positionsbeleg genügt gemäß der beschriebenen
Sensorwahl. Die zweite Position muss seine Kontraste nicht ebenfalls
erfüllen. Die Regeln gelten auch bei hoher Temperatur.

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

Eine Schließung setzt eine erkannte Öffnung voraus. Für den ausgewerteten
Sensor sind `W_min` und `W_max` derselbe Erwärmungskontrast; die native Bremse
und diese Kontraste lauten:

```text
B     = (v_Tp − b_Tp)/(E_Tp + E⁻_Tp)
W_min = v_Tp/E_Tp
W_max = v_Tp/E_Tp
```

Ein Schließkandidat bindet die damaligen Temperaturfenster dieses Sensors, wenn
entweder seine Temperaturreihe einen lokalen Bremsknick mit `δ_eff,Tp > 0` und
`b_Tp < −s E⁻_Tp` liefert oder `W_min > s` gilt. Für die T-Knicktoleranz gilt
der oben beschriebene allgemeine Faktor `s` einschließlich des beobachteten
Steigungsänderungshintergrunds.

Die feste Vorgängersteigung des gebundenen Fensters heißt `b⁰_Tp`, ihre Grenze
`E⁰⁻_Tp`. Für die Feuchteerholung ist `M_p` das kleinste bisher belegte Mittel
des aktuellen Feuchtefensters in derselben geöffneten Gruppe. Es gelten:

```text
B_f = (v_Tp − b⁰_Tp)/(E_Tp + E⁰⁻_Tp)
U   = (H̄_p − M_p)/(2ε_Hp)
D_fp = min((v_Tp − b⁰_Tp)/(E_Tp + E⁰⁻_Tp),
           (b_Tp − b⁰_Tp)/(E⁻_Tp + E⁰⁻_Tp))
D_f  = D_fp
C   = max {min(B_f, U), min(B_f, W_max), W_min, D_f}
```

Für eine Rolle gilt die Formel für `D_fp` nur bei aufgelöst fallender
gebundener Vorgängersteigung `b⁰_Tp < −s E⁰⁻_Tp`. Der reine Bremszweig
`D_f` wird zusätzlich nur mit den unten beschriebenen gehaltenen lokalen
Steigungsänderungsform der verwendeten Temperaturreihe zugelassen; andernfalls
entfällt er. Bei nicht aufgelöst fallendem Vorlauf wird für die betreffende
Rolle `D_fp = −s` eingesetzt. Der Feuchtezweig `min(B_f, U)`
benötigt die ursprünglichen Quellenabschnitte beider Messgrößen
sowie die gehaltene lokale Steigungsänderungsform der Temperatur. Bei gestörter Feuchte oder fehlender T-Form entfällt er. Ein Anstieg
der relativen Feuchte über ihr lokales Minimum kann auch bei weiter offener,
abkühlender Kabine auftreten und trägt deshalb allein keinen Schluss.

Die Bestätigung verlangt für die verwendete Temperaturreihe zwei
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

Die beiden Wege mit weiterhin fallender Temperatur und der
Feuchte-Erholungszweig benötigen je Rolle eine
unterstützte lokale Steigungsänderungsform des bestehenden Kurvenvergleichs
mit `hinge_order = 1`: Der Vorlauf fällt, die Steigungsänderung ist positiv,
und der Vergleich mit glatten Verläufen trägt die Änderung. Der Fallbesitzer
hält die erste solche Form aus einem eigenen neuen T-Originalpräfix samt
unveränderten Originalen, Quelle und Epoche. Ihr Beginn liegt frühestens am
jüngsten Original der gehaltenen Fallreferenz. Sie darf vor der späteren nativen
Bremsbindung derselben Rolle entstehen; ihr lückenloser Anschluss bleibt
maßgeblich. Erst ein späteres eigenes T-Original darf dieselbe Form am festen
Beginn und mit unveränderter Formordnung bestätigen. Die ursprünglichen
Stützpunkte bleiben erhalten und werden durch kausal anschließende Originale
ergänzt; das rollende Suchfenster begrenzt die Erstsuche, nicht die Lebensdauer
eines bereits gebundenen Belegs. Eine nicht auswertbare Fortsetzung liefert
keine Bestätigung, erhält aber die gebundene Identität und ihre Originale.
Eine aufgelöste Gegenform verwirft die Form. Eine bis zur Bremsbindung
verstärkte Fallreferenz verwirft sie ebenfalls. Die anschließend erforderlichen
nativen Bremskontraste und zwei neuen Originalblöcke bleiben eigenständige
Bedingungen; ihre Frische ersetzt keine spätere Bestätigung des Formbelegs.
Eine negative Nachsteigung bleibt zulässig.

Erreichen beide vollständig neuen Temperaturblöcke nach der eigenen
Bremsbindung ein aufgelöst flaches Niveau, kann dieses den Schluss unmittelbar
belegen. Dafür gelten zusätzlich zu den positiven Bremskontrasten
`|b_Tp/E⁻_Tp| ≤ s` und `|v_Tp/E_Tp| ≤ s`. Die eigenen neuen Originale,
der Quellenanschluss und die Korrektur- und Gegenformprüfungen bleiben
erforderlich. Ein verlorener Knickfit verhindert diesen belegten
Plateauabschluss nicht.

Trägt die bestätigte Steigungsänderungsform die Schlussbestätigung, stammt der frühere
Beginn aus genau diesen gehaltenen T-Formen. Das gilt auch, wenn der
Feuchte-Erholungszweig trägt und die Erwärmungszweige keinen Schluss belegen.
Die bestehende Prüfung des Tür- und Betriebskontexts bleibt erhalten.
Ein anderer früherer Kurvenkandidat
liefert für diesen Schluss keinen Beginn. Fehlt ein an die native
Schlussbestätigung gebundener T-Formbeleg, bleibt der Ereignisbeginn am
Bestätigungsraster. Ein nur gleichzeitig verfügbarer, ungebundener
Kurvenvorschlag darf diesen Beginn nicht rückdatieren.

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
gebundenen Schließkandidaten, wenn der korrigierte letzte Wert außerhalb der
früheren Referenzfenster liegt. Berührt die Korrektur ein Original des
gehaltenen Fall-, Brems- oder Erholungsfensters, entfällt die davon abhängige
Fall-/Bremsstütze dieser Rolle. Betrifft sie ausschließlich einen späteren
Stützpunkt der gehaltenen Steigungsänderungsform, wird dessen Wert an der
unveränderten Originalzeit berichtigt. Formbeginn und unberührte
Referenzfenster bleiben erhalten; die Form muss mit späteren eigenen
Originalen erneut bestätigt werden. Die Korrektur liefert keine
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

Für Anstieg und Abfall der relativen Feuchte des betrachteten Sensors gilt:

```text
R = v_Hp/E_Hp
F = −v_Hp/E_Hp
Q = q_Hp
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

Die beobachtete Anstiegsstärke wird gespeichert, sobald seit der Bindung am
betrachteten Sensor mindestens ein späteres Temperatur- und
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
`v_Hp ≤ s E_Hp`; ein Anstieg an einer unbenutzten Position verändert ihn nicht. Spätere
Temperaturpartner zu diesen RH-Originalen bleiben zulässig. Eine Korrektur
tragender Originale prüft diesen Endbeleg erneut. Wird er widerlegt, folgt
die Stützmenge an der betroffenen Position wieder demselben Anstiegsast bis
zu einem neuen gültigen Endbeleg; ausstehende Paarungen bleiben erhalten. Später
hinzukommende RH-Originale im Plateau und ein zugehöriger Temperaturanstieg
erweitern die Wasserqualifikation nicht. Je Position gilt innerhalb dieser Stützmenge:

```text
Q_p = max(verfügbare ρ⁻ seit dem Basisoriginal) − ρ⁺ des Basisoriginals
```

Erst der gespeicherte starke Beleg und `Q_p > 0` am betrachteten Sensor
qualifizieren die Komponente. Fehlende Paarungen bleiben unbekannt;
die Paarung der momentan höchsten Feuchtespitze muss dabei noch nicht
vorliegen. Ein vollständig gepaarter, nichtpositiver Wasserbeleg nach Spitze
und Abfall kann eine noch unqualifizierte Komponente verwerfen. Die
Dichtegrenzen beruhen auf den angenommenen Wertstörungen; eine zusätzliche
physische Grenze des Temperaturverlaufs zwischen den Originalen ist damit
nicht belegt. Der berechnete Wassergehalt ist kein unabhängiger Sensor und
beweist für sich allein keine Wasserzugabe.

Eine qualifizierte neue Episode kann bereits im Anstieg bestätigt werden:
Der aktuelle Kontrast muss `R > γN` und zugleich `Q > s` erfüllen. Seit der
Komponentenbindung müssen am betrachteten Sensor ein vollständig
neuer aktueller Feuchteblock mit `K` Originalen und mindestens ein späteres
Temperaturoriginal vorliegen; der verfügbare Wasserbeleg muss weiterhin
positiv sein. `Q` verlangt eine Zunahme der Feuchtesteigung gegenüber dem
Vorgängerfenster. Eine gleichmäßig steigende Gerade erfüllt diesen zusätzlichen
Formbeleg nicht. Die tatsächlich bestätigenden Fenster werden gebunden und
bei Originalkorrekturen erneut geprüft.

Fehlt dieser frühe Nachweis, bleibt die Bestätigung über den beobachteten
Abfall `F > s` möglich. Der gespeicherte starke Anstiegsbeleg und die positive
Wasserqualifikation müssen weiterhin vorliegen. Am betrachteten Sensor
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
behauptet. Liegt dieses Intervall des tragenden Sensors vollständig nach dem
vorherigen Ende, belegt es einen neuen Ast auch dann, wenn ältere ruhige
Punkte seines Auswertungsfensters noch vor diesem Ende liegen. Ein überlappendes
Beginnintervall bleibt ungeklärt; ein früherer gültiger Beginn bleibt Fortsetzung.
Der erste gültige Beginnzeuge eines Anstiegs bleibt am tragenden Sensor gebunden.
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
Z   = |v_Hp/E_Hp|
Z_r = max {r_Hp, r⁻_Hp}
G   = (Z ≤ β) und (Z_r ≤ η) und (R ≤ s)
```

Im ununterbrochenen Nachlauf muss die letzte qualifizierte Komponente
abgefallen sein; ein offener Anstiegskandidat sperrt den Abschluss.
Der erste Befund `G` bindet nur einen
Hintergrundkandidaten. `G` muss an jedem folgenden auswertbaren Eingangspräfix
bestehen bleiben, bis am tragenden Sensor ein vollständig neuer
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

Ein später erkannter Anstieg setzt denselben Aufguss fort, sobald am
tragenden Sensor ein gültiger Beginnbeleg spätestens beim Abschluss liegt. Andernfalls
wird er einer neuen Episode zugeordnet, wenn sämtliche bei der Bindung
verwendeten aktuellen `K` RH-Originale des tragenden Sensors nach dem
Abschluss liegen. Das vorherige Referenzfenster darf den alten Hintergrund
überlappen; es belegt den Steigungswechsel und gehört nicht zum neuen
Anstiegsblock. Zusätzliche Kurvenbeginnbelege sind dafür nicht erforderlich.
Auch ein gültiger eigener Beginnbeleg nach dem Abschluss erlaubt diese
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

Die erste Stufe verwendet die gültigen RH- und Temperatur-Originalfenster des
gebundenen Sensors. Eine vollständige Wasserpaarung ist dafür
nicht erforderlich. Die zweite Stufe verwendet an dieser Position
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

Beim Binden des jeweiligen Stufenkandidaten müssen alle fünf Vergleiche am
betrachteten Sensor erfüllt sein: vorheriger Feuchtefall, kein
aufgelöster Feuchteanstieg, aufgelöste Abbremsung, weiter fallende Temperatur
und keine aufgelöste Temperaturbremse. Die Feuchte darf weiterhin fallen.
Eine waagerechte Kurve ist für keine der beiden Lüftungsstufen erforderlich.
Bis zur Bestätigung wird die Abbremsung gegen die beim Stufenbeginn
festgehaltene frühere Feuchtesteigung `b_Xp` und deren Grenze `E⁻_Xp`
verglichen. Die aktuelle Steigung stammt weiterhin aus den neuen Originalen;
die beiden Temperaturbedingungen müssen erhalten bleiben. Die Bestätigung benötigt
außerdem seit der Bindung am tragenden Sensor spätere Temperatur- und
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

Für den ausgewerteten Sensor gilt dieselbe Form ohne zusätzliche
Mindestöffnungsdauer oder feste Temperatur- und Wasserverlustschwelle. Die
fünf historischen Lüftungsparameter bleiben für ältere Archive und
Referenzadapter im [Parameterkatalog](../custom_components/ha_sauna/defaults.json); sie bestimmen
den aktuellen Nachweis nicht.

Die Auswertung folgt der Reihenfolge der empfangenen Originale. Benachbarte,
bereits vorliegende Meldungen mit derselben Empfangszeit und derselben
Originalzeit samt Zeitbasis bilden eine gemeinsame Ausgabeentscheidung.
Ihre Formen werden weiterhin je Eingangspräfix ausgewertet. Eine neue
Lüftungsstufe wird erst am Ende dieser Gruppe bestätigt und ausgegeben;
ein darin bereits bestätigbarer Türschluss hat Vorrang. Es wird weder auf
künftige Meldungen gewartet noch ein späterer Messwert vorweggenommen.
Eine abschließende Korrektur oder ungültige Meldung des unbenutzten Sensors
verdeckt keine bereits vorliegenden eigenen Originale dieser Gruppe.
Korrekturen entfernen ihre betroffene Rolle aus der Gruppenfrische;
die Bestätigung benötigt weiterhin neue gültige Originale beider Messgrößen
des tatsächlich verwendeten Sensors.

Verschiedene Empfangs- oder Originalzeiten bleiben getrennt. Ein zuvor
bestätigtes Durchlüften und ein späterer Türschluss bleiben daher auch innerhalb
desselben Rasters zwei Ereignisse. Späteres Wissen nimmt bereits ausgegebene
Ereignisse nicht zurück.
Für Unterbrechungen und Korrekturen gelten die
[Regeln bei Messlücken](#erkennungen-bei-messlücken-prüfen).

Beide bestätigten Lüftungsstufen bleiben derselben Türöffnung auch nach
Türschluss zugeordnet. Für die [Gangerkennung](gangmodell.md) führt die erste
Stufe: Nach einer Austrittsöffnung beendet sie bei Führung aus Temperatur und
Feuchte den Gang. Bei direkter Präsenzführung gehört dazu außerdem eine
gültige Abwesenheitsmeldung. Die zweite Stufe ergänzt den Verlauf ohne
zusätzliche Regelwirkung auf den Gang.

## Erkennungen bei Messlücken prüfen

Personenhaltezähler beginnen nach einem Wechsel oder Ausfall des tatsächlich
benutzten Sensors neu. Änderungen einer unbenutzten Position setzen sie nicht zurück. Unbestätigte Kurvennachweise bleiben an ihre
jeweiligen Quellenabschnitte gebunden.

Die neuen Originalblöcke von Öffnung, Schließung und Aufguss müssen zur
gebundenen Position, Quelle und Zeitbasis passen. Der fortgeschriebene
Anschlussanker ersetzt keine Prüfung des gesamten neu hinzugekommenen
Originalabschnitts. Eine überschrittene rollenbezogene Abstandgrenze macht den
Anschluss unbekannt. Bereits gebundene Originalbelege bleiben erhalten;
spätere Fenster überbrücken die unbeobachtete Zwischenzeit nicht. Eine neue
eigenständige Form erhält ihre eigene Bindung und ihren eigenen Beginn.
Eine Korrektur derselben Probe liefert keine Bestätigungsfrische und prüft
die davon abhängigen gebundenen Belege erneut.

Bei der zweiten Lüftungsstufe bleibt das reguläre Warten auf einen rechten
Temperaturpartner offen. Noch ungepaarte Feuchteoriginale liefern dieser
Stufe keine Bestätigungsfrische; bereits vollständige, aufeinanderfolgende
Paarfenster bleiben auswertbar. Die erste Stufe benötigt die gültigen RH- und
Temperaturoriginale ohne Wasserpaarung. Eine Messlücke lässt den historischen
Beleg bestehen, ohne eine neue Bestätigung zu liefern. Ein Quellenwechsel,
ein ungültiger Messwert oder eine widersprechende Korrektur tragender Originale
entwertet die betroffenen Stützen. Mit denselben Quellen
können im noch offenen Türvorgang Fall und Abflachung aus zusammenhängenden
Originalen erneut vollständig belegt werden. Bei einem Quellenwechsel beginnt
auch der Folgebeleg mit einem neuen eigenen Quellenabschnitt. Die Zuordnung
zur offenen Tür bleibt bestehen; alte und neue Quellenstützen werden nicht
zu einer Bestätigung verbunden.

Ein noch unbestätigter Aufgusskandidat wird bei einem Bruch seiner Quellen
oder seines Tür- und Betriebskontexts verworfen. Fehlende spätere Meldungen
allein entwerten ihn nicht. Eine später vollständig beobachtete eigenständige
Form übernimmt seinen früheren Beginn nicht.

Bei einem bereits gezählten Aufguss pausieren Messlücken, Quellenwechsel und
vorübergehend fehlende Auswertbarkeit den Abschluss. Die Zuordnung bleibt
bestehen; ein unbekannter Verlauf belegt weder Abklingen noch ein neues Niveau.
Nach Wiedererwerb muss der Abschluss auf dem eigenen zusammenhängenden
Verlauf des tragenden Sensors oder eines gültigen Ersatz-Folgeabschnitts
erneut belegt werden. Auch nach einem Quellenwechsel kann ein vollständig
neu beobachteter RH-Hintergrund mit anschließendem frischem Originalblock
die bereits bestätigte Episode beenden. Dieser Verlauf bestätigt keinen
alten Peak oder Abfall; das Ende liegt am neuen belegten Abschlussraster.
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
Der gemeinsame Originalfallnachweis (`original_fall_evidence`) weist davon
getrennt das gewählte Referenzniveau, seine Fensterzuordnung sowie die darauf
bezogene beobachtete und normierte Absenkung aus.
Die Feuchtebeginnzeugen jeder Position, gegebenenfalls ihre nativen
Beginnintervalle mit eigenem RH-Fenster und Knickhintergrund, ihre fortlaufende
Astprüfung und die zusätzlichen Aufgussreferenz- beziehungsweise lokalen
T-Fallprüfungen bleiben
sichtbar. Der gehaltene T-Fall (`retained_temperature_fall`) weist je Rolle
Empfangs-, Original- und Referenzbezug sowie Richtung, Niveau und damalige
Endkante aus; die aktuelle Endkante bleibt getrennt sichtbar. Die
vollständigen neuen Stützblöcke gehören zu den jeweiligen
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
lokale T-Fallweg bei vorhandenem Aufgusskontext benötigt einen vollständig
neuen aktuellen Block nach seiner eigenen Knickreferenz. Die Schlusswege
behalten ihre jeweils beschriebenen Frischebedingungen. Die
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
