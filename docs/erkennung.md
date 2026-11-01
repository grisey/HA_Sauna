# Türöffnung, Aufguss und Durchlüften erkennen

Die [wirksame Präsenzquelle](gangmodell.md#wirksame-präsenzquelle) bestimmt den
Anwesenheitsnachweis. Türvorgänge, Lüften und Aufgüsse stammen in beiden
Verfahren aus Temperatur und Feuchte.

| Erkennung | Aus Temperatur und Feuchte | Mit Präsenzsensor |
| --- | --- | --- |
| Türöffnung und Türschluss | Zustandsmodell der Kabine | Dasselbe Zustandsmodell |
| Anwesenheit | Keine eigenen Personensignale; der erste Aufguss nach einem Eintritt eröffnet den Gang | Belegungszustand der zugeordneten Entität beim Türschluss; eröffnet einen vorläufigen Gang |
| Aufguss | Eröffnet oder ergänzt den Gang | Bestätigt den vorläufigen Gang oder ergänzt ihn |
| Lüften und Durchgelüftet | Kurvenform der Feuchte während der offenen Tür; Nachweis für das Gangende | Dasselbe |

Die Übergangsregeln beschreibt die [Gangerkennung](gangmodell.md). Die Erkennung
kennt weder Sitzung noch Gang; sie erhält Messwerte und die Rückmeldung, ob
der Ofen heizt, und liefert Ereignisse mit physikalischem Zeitpunkt und
Bestätigungszeitpunkt.

## Messkanäle

Je Messposition (oben, unten) gehen Temperatur und die daraus und aus der
relativen Feuchte berechnete absolute Feuchte ein, zusammen bis zu vier
Kanäle. Takt der Auswertung ist die Temperaturmeldung der oberen Position,
ersatzweise der unteren; die übrigen Kanäle gelten mit ihrem letzten Wert.
Ein Messschritt wird ausgewertet, wenn alle Werte seines Zeitpunkts vorliegen
(Temperatur und Feuchte eines Sensorpakets gehören zusammen). Werte von vor
dem Sitzungsbeginn gelten ab dem Beginn. Mit nur einer Position arbeitet das
Modell mit deren beiden Kanälen.

Zeitachse ist die Messzeit: bei Sensorpaketen mit eigener Uhr deren
monotone Zeit ab dem ersten Wert (eine Korrektur der Sensoruhrzeit verschiebt
die Kurve nicht), sonst die Messzeit, wenn sie nicht nach dem Empfang liegt,
sonst der Empfang. Ein erneut geliefertes Original mit demselben Wert zählt
nicht noch einmal; ein verspätetes älteres Original ersetzt keinen neueren
Wert.

Die Luft der Umgebung (wohin die Kabinenluft bei offener Tür entweicht und
woher Frischluft kommt) ist die Außenluft. Sie wird beim Sitzungsstart aus der
konfigurierten Wetterquelle oder den Außensensoren übernommen: Temperatur und
absolute Feuchte (direkt, sonst aus Temperatur und relativer Feuchte, sonst
aus dem Taupunkt). Fehlt ein Wert, gilt der typische Wert des europäischen
Winterhalbjahrs: 4 °C (Mittel Oktober bis März, DWD 1991–2020) und 6,0 g/m³
(nahezu gesättigt, 95 % relative Feuchte). Die Kabine beim Start sagt über die
Außenluft nichts aus und wird dafür nicht verwendet. Welche Werte gelten und
woher sie stammen, steht im Entscheidungsprotokoll der Sitzung.

## Zustandsmodell der Kabine

Die Kabine befindet sich zu jedem Zeitpunkt in genau einem Zustand. Jeder
Zustand beschreibt, wie sich Temperatur und Feuchte physikalisch ändern:

| Zustand | Verlauf |
| --- | --- |
| Zu | Grunddrift `r` (Heizen oder Abkühlen) |
| Offen | Temperatur: Luftaustausch mit der Umgebung `λ·(L − außen)` und Nachschub aus Wand und Steinen `β·(L − W)`; `W` ist der Pegel beim Öffnen. Feuchte: Luftaustausch `λ·(L − außen)` und Feuchtequelle `s` (Holz, Steine, Personen) |
| Aufguss | Feuchte steigt mit der Rate `exp(z)` |
| Abklingen | Feuchte ändert sich mit `r + d`; die große negative Zusatzsteigung `d` lässt mit der Rate `exp(z)` nach, hin zum normalen Steigungsverhalten der geschlossenen Kabine |
| Erholung | Nach dem Schließen steigt die Temperatur mit eigener Rate `e ≥ 0` |
| Heizwechsel | Wie zu; die Grunddrift der Temperatur springt (Heizschütz) |

Je Kanal führt das Modell Pegel `L`, Grunddrift `r`, Ereignisgröße `z` und
bei der Temperatur den Wandpegel `W`, bei der Feuchte die Feuchtequelle `s`.
Die Feuchtequelle einer Öffnung beginnt mit ihrer gelernten Verteilung (oben
und unten gemeinsam, eine Ursache) und wandert während der Öffnung je Sensor
mit der gelernten Stärke `qs`; so bleibt ein kurzer Feuchteanstieg bei offener
Tür, etwa durch eine Person in der Tür, Teil der Öffnung. Physikalisch mögliche Übergänge sind: zu → offen, zu →
Aufguss, zu ↔ Heizwechsel, offen → Erholung, Aufguss → Abklingen,
Abklingen → zu/offen/Aufguss und Erholung → zu/offen/Aufguss. Alle anderen
Übergänge haben die Wahrscheinlichkeit null; die Übergangswahrscheinlichkeit
für einen Messabstand folgt aus den gelernten Raten über das
Matrixexponential.

Die Erkennung folgt dem Ablauf in der Kabine: Zu Beginn einer Sitzung ist nur
eine Türöffnung möglich. Ein Aufguss braucht jemanden in der Kabine und ist
erst nach der ersten bestätigten Türöffnung möglich; weil eine offene Tür nur
über das Schließen weitergeht, liegt er immer nach einem Schließen. Lüften
und Durchlüften werden nur während einer offenen Tür geprüft, das Abflachen
eines Abklingens nur im Abklingen.

Ein Ereignis hat eine Ursache für alle Kanäle: eine Tür, eine Dampfquelle.
Deshalb sind die Ereignisgrößen der Kanäle beim Beginn und in ihrer zeitlichen
Wanderung korreliert, und der Zustand aller Kanäle wird gemeinsam geschätzt.
Steigt die Feuchte nur an einem Sensor, ohne dass der andere dem Verhältnis
eines Aufgusses folgt, ist das kein Aufguss.

Beim Öffnen der Tür und beim Übergang ins Abklingen beginnt die Grunddrift
mit der gelernten Verteilung der Steigungen der geschlossenen Kabine, nicht
mit der Steigung vor dem Ereignis. Die Zusatzsteigung des Abklingens beginnt
mit ihrer gelernten Verteilung. Während Tür, Aufguss und Abklingen wird die
Grunddrift nicht aus dem Ereignisverlauf geschätzt. Bei geschlossener Tür
kehrt sie zu ihrer gelernten Verteilung zurück; zu Beginn gilt die Steigung
der ersten 30 Messschritte. Das
Messrauschen ist Student-t-verteilt; die Feuchtesensoren messen relative
Feuchte, darum wächst das Rauschen der absoluten Feuchte mit dem
Sättigungswert am selben Sensor. Bei offener Tür und während des Dampfes
schwankt der Pegel zusätzlich mit der Stärke des Ereignisses (Turbulenz).

Mit beiden Messpositionen geht zusätzlich die Feuchtedifferenz oben − unten
ein. Bei geschlossener Tür ist sie nahezu null, beim Aufguss ist die obere
Feuchte höher, bei offener Tür die untere. Je Zustand hat sie eine gelernte
Student-t-Verteilung (Lage, Streuung, Form) und eine Korrelationszeit; ein
Messschritt zählt mit dem Gewicht `min(1, Δt/Korrelationszeit)`, sodass ein
langsam veränderlicher Wert nicht bei jedem Schritt neu als Beleg gilt.

Ein Filter für schaltende Zustandsraummodelle (IMM) berechnet nach jedem
Messschritt die Wahrscheinlichkeit jedes Zustands.

## Entscheidung

Nach jedem Messschritt berechnet die Erkennung für alle noch nicht
entschiedenen Messschritte die Zustandswahrscheinlichkeiten mit allen
bisherigen Messwerten (Glättung rückwärts vom neuesten Schritt). Kandidat ist
ein Zustand, sobald er an einem Schritt die wahrscheinlichste Erklärung ist.
Bestätigt ist er, sobald er an einem Schritt seiner Episode mindestens
150-mal so wahrscheinlich ist wie die nächstbeste Erklärung (Bayes-Faktor
150, „sehr starke Evidenz“ nach Kass und Raftery). Eine feste Wartezeit gibt
es nicht. Der physikalische Zeitpunkt ist der erste Schritt der Episode,
bestimmt mit allen Messwerten bis zur Bestätigung. Das Ende einer Episode
wird ebenso entschieden.

| Ereignis | Zustand |
| --- | --- |
| Tür auf | Beginn des Zustands offen, zusätzlich durch die Öffnungsform belegt (siehe unten) |
| Tür zu | Ende des Zustands offen: Übergang in einen Zustand der geschlossenen Kabine (Erholung, zu, Heizwechsel) oder in Aufguss bzw. Abklingen |
| Aufguss | Beginn des Zustands Aufguss |
| Aufgussende | Ende von Aufguss und Abklingen zusammen; öffnet sich vorher die Tür, endet der Aufguss mit der Öffnung |

Eine Öffnung wird in zwei Stufen bestätigt. Das Zustandsmodell liefert den
Kandidaten und seinen Zeitpunkt (Beginn des Zustands offen, 150 : 1). Gemeldet
wird die Öffnung erst, wenn auch die gelernte Öffnungsform sie zeigt: Die
Steigung der geschlossenen Kabine läuft weiter, ab dem Knick kommt der Abfall
der offenen Tür hinzu, der mit der Rate `k` einsetzt und mit der Rate `p`
nachlässt. Belegt ist das Öffnen, wenn in beiden Temperaturkanälen dieser
zusätzliche Abfall mit der Wahrscheinlichkeit 150/151 vorliegt. Die
Unsicherheit folgt aus der Restabweichung, bezogen auf die Zahl unabhängiger
Messwerte. Vor dem Knick zählt die Kabine höchstens so lange, wie im Mittel
kein Heizwechsel kommt (gelernte Rate zu → Heizwechsel), und nicht vor dem
letzten Ereignis. Die Feuchte zählt hier nicht: Vor einem Austritt fällt sie
oft schon aus dem Abklingen, und beim Eintritt steigt die untere Feuchte
kurz an. Endet die Episode, bevor die Form das Öffnen zeigt, gilt sie nicht
als Öffnung; weder Tür auf noch Tür zu wird gemeldet. Der Zeitpunkt bleibt
der Kandidat; die Meldung folgt kausal danach.

Der Zeitpunkt von Tür zu ist der Knick, an dem der Abfall der offenen Tür
endet. Die Bestätigung des Übergangs bleibt die Entscheidung und löst die
Steuerung aus; sie liegt danach. Bei der Bestätigung wird die gelernte
Vergleichsform des Schließens an alle Kanäle (Temperatur und Feuchte oben und
unten) gelegt:
`y = a − s·(e^(p·(t_k − t)) − 1)/p` vor dem Knick `t_k` (offene Tür, der
Abfall wird mit der Rate `p` langsamer) und
`y = a + e·u + (s − e)·(1 − e^(−k·u))/k` mit `u = t − t_k` danach (die
Steigung geht mit der Rate `k` stetig in die Steigung `e` der geschlossenen
Kabine über). Die Raten `k` und `p` sind je Kanal fest gelernt; angepasst
werden nur Niveau, Steigungen und der gemeinsame Knick `t_k`.

Der Vergleich beginnt beim Öffnen, bei einer gelüfteten Öffnung beim letzten
bestätigten Lüften oder Durchlüften. Er endet beim aktuellen Messschritt oder
dort, wo nach dem Schließen schon eine neue Öffnung oder ein Aufguss beginnt
(der Zustand ist an einem Schritt die wahrscheinlichste Erklärung). Gewählt
wird der Knick mit der kleinsten Restquadratsumme, an dem die Temperatur
vorher fällt (`s < 0`) und danach wieder steigt (`e ≥ 0`). War der Ofen
während der ganzen Öffnung aus (im Sinne des folgenden Absatzes), wird keine
Erholung erwartet; dann genügt, dass der Temperaturabfall langsamer wird
(`e > s`). Ohne
gelernte Form oder ohne solchen Knick gilt der erste Schritt des Übergangs.

Nach dem Schließen steigt die Temperatur nicht immer wieder an: beim Aufheizen
läuft der Anstieg mit der Heizsteigung weiter, bei kurz zuvor ausgeschaltetem
oder gerade eingeschaltetem Ofen fällt sie langsam weiter. Die Öffnung endet
deshalb in jeden Zustand der geschlossenen Kabine. Für die Vergleichsform des
Schließens gilt der Ofen erst als aus, wenn er so lange als aus gemeldet ist
wie die gelernte Rückkehrzeit der Grunddrift der Temperatur (die Zeit, in der
eine Änderung der Heizwirkung in der geschlossenen Kabine nachwirkt); bis
dahin heizen Steine und Wände weiter.

Mehrere Güsse gehören zu einem Aufguss, solange das Abklingen des vorigen
Gusses nicht erkennbar abgeflacht ist. Abgeflacht heißt: Die Feuchtesteigung
im Zustand Abklingen (Summe der Feuchtekanäle) ist mit dem Verhältnis 150 : 1
weniger steil als ihr bisher steilster Wert in diesem Abklingen. Eine
Türöffnung und das vollständige Abklingen trennen Aufgüsse ebenfalls.

Ein Aufguss, der bestätigt wird, solange eine Türöffnung noch nicht
entschieden ist, folgt dem bestätigten Türschluss; er liegt dann nicht vor
diesem. Wird die Öffnung nicht bestätigt, gilt der Aufguss mit seinem
eigenen Zeitpunkt.

Jede Entscheidung wird protokolliert: Ereignis, physikalischer Zeitpunkt,
Bestätigungszeitpunkt, Zustand und nächstbeste Erklärung, deren Verhältnis,
Anzahl der Messschritte bis zur Bestätigung und der Beitrag der
Feuchtedifferenz oben − unten. Zusammengefasste Güsse und das Abflachen eines
Abklingens stehen mit ihren Größen ebenfalls im Protokoll.

## Lüften und Durchgelüftet

Während der offenen Tür prüft die Erkennung die Kurvenform der Feuchte am
führenden Sensor:

| Stufe | Kurve | Bedeutung |
| --- | --- | --- |
| Lüften | ln relative Feuchte flacht ab | Die feuchte Kabinenluft ist ausgetauscht |
| Durchgelüftet | ln absolute Feuchte flacht ab (nach dem Lüften) | Die Feuchte hat ihr neues Gleichgewicht erreicht |

Modell ist eine stetige Gerade mit einem Knick:
`y = b0 + b1·(x − τ) + b2·max(x − τ, 0)` bei unbekanntem Rauschen und
flacher A-priori-Verteilung; über den Knickzeitpunkt `τ` wird mit seiner
Evidenz gemittelt. Abgeflacht heißt: vorher fallend (`b1 < 0`) und danach
nicht mehr steil fallend und nicht wieder steigend
(`b1/e < b1 + b2 < 0,05·|b1|`). Steigt die Feuchte nach dem Knick wieder an
(kurze Öffnung als V-Form), ist das kein Abflachen. Die Wahrscheinlichkeit
muss dem Verhältnis 150 : 1 entsprechen (150/151), die Tür muss nach dem Knick
an jedem Schritt die wahrscheinlichste Erklärung bleiben (ein Schließen
beendet den Feuchteabfall ebenfalls), und die Temperatur muss nach dem Knick
mit derselben Wahrscheinlichkeit weiter fallen. Jedes Segment umfasst
mindestens 20 Messschritte.
Der Zeitpunkt der Stufe ist der Knick.

Lüften und Durchgelüftet sind Eigenschaften der Türöffnung. Ob sie einen Gang
beenden, entscheidet das [Gangmodell](gangmodell.md).

## Gelernte Größen und Nachlernen

Rauschen, Austauschkoeffizienten, Aufguss-, Abkling- und Erholungsraten,
deren Korrelation über die Kanäle, Turbulenz, Übergangsraten und die
Student-t-Formen stammen aus bewerteten Aufzeichnungen
(`recognition_parameters.json`, erzeugt mit `tools/recognition_learn.py`).
Verwendet werden Sitzungen ohne Türöffnung während eines Gangs.

| Größe | Schätzung |
| --- | --- |
| Messrauschen | Negative Autokovarianz der Messdifferenzen in ruhigen Abschnitten |
| Austausch `λ` und Nachschub `β` | Temperatur: Zweiknotenverlauf je Öffnung; `β` aus langen Öffnungen, dann `λ` je Öffnung mit seinem Standardfehler. Feuchte: `λ` und Quellstärke `s` je Öffnung mit ihren Standardfehlern |
| Verteilung der Ereignisgrößen | Zufallseffektmodell über alle Ereignisse (unsicher bestimmte Ereignisse zählen entsprechend wenig) |
| Korrelation zwischen Kanälen | Robuste Paarkorrelation der Ereignisgrößen und der Quellstärken |
| Wanderung der Feuchtequelle | Maximum der Likelihood von Fenstern um die bezeichneten Öffnungen (nur Lernaufzeichnungen) |
| Turbulenz bei Aufguss und Abklingen | Maximum der Likelihood von Fenstern um die bezeichneten Aufgüsse (nur Lernaufzeichnungen) |
| Heizwechsel | Zweikomponenten-Mischung der Steigungsänderungen ruhiger Minuten fern von Ereignissen |
| Übergangsraten | Zählungen und Verweildauern der bezeichneten Zustände |
| Abklingen | Je Aufguss Zusatzsteigung und Nachlassrate aus dem Verlauf nach dem Gipfel; es dauert, bis die Zusatzsteigung in der Streuung der normalen Steigungen liegt |
| Normale Steigungen der geschlossenen Kabine | Steigungen ruhiger Minuten fern von Ereignissen: Mittel, Streuung, Rückkehrzeit |
| Feuchtedifferenz oben − unten | Student-t je bezeichnetem Zustand; Korrelationszeit aus der Autokorrelation um den Median des Zustands |
| Student-t-Formen | Innovationen je bezeichnetem Zustand |
| Vergleichsform des Schließens | Je Kanal die Raten `k`, `p` mit der kleinsten Summe der Restquadrate über alle bezeichneten Schließungen (Fenster ab Öffnen bzw. letztem Lüften bis gleich lange nach dem Schließen, höchstens bis zum nächsten Ereignis). Untypisch heißt eine Schließung, die mit eigenen Raten mit dem Verhältnis 150 : 1 besser erklärt ist; sie wird genannt, nicht ausgeschlossen |
| Vergleichsform des Öffnens | Wie beim Schließen, je Kanal gemeinsam über alle bezeichneten Öffnungen (Fenster so lange vor dem Öffnen, wie die offene Tür bis zum Schließen oder Lüften dauert, nicht vor dem vorigen Ereignis) |

Nach jeder abgeschlossenen Sitzung mit beiden Messpositionen zieht die
Integration aus deren Messwerten und eigenen bestätigten Erkennungen neue
Belege. Gespeichert werden die Belege der letzten 40 Sitzungen, jeweils mit
dem Status „nicht freigegeben“. In eine Neuschätzung gehen neben den Belegen
der Lernaufzeichnungen nur ausdrücklich freigegebene Sitzungen ein.

Die Freigabe gilt je Sitzung und lässt sich zurücknehmen:

- Panel: **Einstellungen → Daten und Wartung → Lernsitzungen**. Die Liste
  nennt je gespeicherter Sitzung Beginn, erkannte Türöffnungen und Aufgüsse
  sowie den Status.
- Aktion **Sitzung für das Nachlernen freigeben**
  (`ha_sauna.recognition_release`) mit Sitzungskennung und `released`
  (`true` freigeben, `false` zurücknehmen).

Beide Wege verlangen das Bedienrecht am Betriebsschalter der Instanz; der
Bereich **Daten und Wartung** im Panel steht Administratoren zur Verfügung.
Erzeugt dieselbe Sitzung erneut Belege, ersetzen sie die gespeicherten und
sind wieder nicht freigegeben. Belege eines Lernstands ohne Status gelten als
nicht freigegeben.

Neu geschätzt werden die Größen erst mit der Aktion **Erkennung
nachlernen** (`ha_sauna.recognition_learn`); die Rechnung läuft im
Hintergrund und darf dauern. Die Belege der Lernaufzeichnungen bleiben als
Startwissen erhalten; die freigegebenen Sitzungen ergänzen sie. Die neuen
Größen gelten ab der nächsten Sitzung. Der Lernstand liegt im
Home-Assistant-Speicher der Instanz. Die Antwort der Aktion nennt die Zahl
der seit dem letzten Nachlernen hinzugekommenen Sitzungen, Freigaben,
Rücknahmen und Rückmeldungen, der gespeicherten und der freigegebenen
Sitzungen und der abgelehnten Ereignisse.

Die Aktion **Rückmeldung zur Erkennung** (`ha_sauna.recognition_feedback`)
lehnt ein erkanntes Ereignis ab (`reject`) oder hebt die Ablehnung auf
(`confirm`). Angegeben wird die Ereigniskennung oder Ereignisart und Zeitpunkt;
gewählt wird dann das nächstgelegene Ereignis dieser Art. Abgelehnte
Ereignisse fließen mit dem nächsten Nachlernen nicht mehr in das Lernen ein. Was als Tür, Lüften oder
Aufguss gilt, lernt die Integration nicht selbständig; dafür ist diese
Rückmeldung vorgesehen.

## Diagnose

Die Zustandswahrscheinlichkeiten des letzten Messschritts stehen als
Erkennungsdiagnose zur Verfügung; im Archivprofil für Analysen wird jeder
Schritt gespeichert. Jedes gebuchte Ereignis wird mit seiner
Wahrscheinlichkeit archiviert, ein vom Gangmodell nicht übernommenes
Ereignis mit dem Grund.
