# Kalibrierung

Die Kalibrierung legt ein Protokoll über eine Sitzung. Die Integration sendet
Anweisungen als Companion-Benachrichtigung; jede Antwort schließt das
Zeitfenster ihres Vorgangs. Die Fenster sind eine Referenz, die nicht aus der
Erkennung stammt: für den Präsenzsensor, die Tür- und Aufgusserkennung und das
Gangmodell. Steuerung, Erkennung und Gangmodell bleiben dabei unverändert.
Antworten bedienen die Sauna nicht.

Die Kalibrierung besteht aus einem Grundablauf in zwei Saunagängen, wahlweise
mit einem dritten Gang, und aus Zusatzmodulen, die später einzeln in einer
laufenden Sitzung starten. Jeder Teil gilt für sich
([Gültigkeit](#gültigkeit)).

## Start und Abbruch

Die Kalibrierung startet im Sauna-Panel unter **Details → Kalibrierung** oder
über den Dienst `ha_sauna.start_calibration`. Beide Wege verwenden denselben
Ablauf und dieselbe Berechtigungsprüfung; startender Benutzer ist im Panel der
angemeldete Benutzer. **Details** steht Administratoren zur Verfügung.

| Weg | Start im Panel | Start per Dienst | Ablauf |
| --- | --- | --- | --- |
| Grundablauf | **Kalibrierung starten** ohne laufende Sitzung | `ha_sauna.start_calibration` ohne laufende Sitzung und ohne `module` | Startet den Saunabetrieb im Automatikbetrieb, aus dem manuellen Modus mit Wechsel auf Automatik. P1 beginnt sofort beim Aufheizen, Gang 1 nach **Bereit**, P2 in der Pause nach Gang 1, Gang 2 nach **Nächster Gang**, danach wahlweise Gang 3 ([Grundablauf](#grundablauf)). |
| Zusatzmodul | **Zusatzmodul starten** in einer laufenden Sitzung, mit Auswahl eines Moduls | `ha_sauna.start_calibration` mit `module` in einer laufenden Sitzung | Startet, sobald kein Gang aktiv ist. Ein vorläufiger Gang zählt als aktiv ([Zusatzmodule](#zusatzmodule)). |

Voraussetzungen:

- Ein zugeordnetes Ziel **Companion: Daten und Antworten** für den startenden
  Benutzer ([Benachrichtigungen](benachrichtigungen.md)). Ohne ein solches Ziel
  und ohne Benutzeridentität wird der Start abgewiesen.
- Steuerberechtigung für die Betriebsentität, wie bei den übrigen Diensten.
- Es läuft keine andere Kalibrierung derselben Instanz.
- Ohne Präsenzsensor entfallen P1 und P2 im Grundablauf und das Zusatzmodul P;
  ein ausdrücklich angefordertes Modul P wird abgewiesen. Die übrigen Teile
  laufen ohne Präsenzmessung. Das Panel bietet nur die verfügbaren Teile an.

Fehlt eine Voraussetzung, ist der Start im Panel mit dem Grund gesperrt. Die
Companion-Ziele prüft das Panel nach dem Laden und auf **Companion-Ziele erneut
prüfen**.

**Zusatzmodul starten** ist außerdem gesperrt, solange ein Zustand besteht, der
das Modul sofort ungültig beenden würde ([Gültigkeit](#gültigkeit)): Betrieb AUS
in der Wiederaufnahmefrist, der manuelle Modus, eine manuelle Ofenwahl und, mit
Präsenzsensor, ein Präsenzzustand „unbekannt“ oder „nicht verfügbar“ oder noch
keine Meldung. Ebenso gesperrt ist es, bis die Sitzung zum ersten Mal die
Mindesttemperatur erreicht: Vorher zählt kein Eintritt
([Gangmodell](gangmodell.md)), der Gang des Moduls fehlte in der Auswertung.
**Kalibrierung starten** ist mit Präsenzsensor gesperrt, solange kein bekannter
Präsenzzustand vorliegt; im manuellen Modus bleibt sie startbar, weil der
Grundablauf auf Automatik wechselt. Gang 1 beginnt frühestens, wenn die Sitzung
die Mindesttemperatur erreicht hat. Die Karte nennt jeweils den Grund. Der
Dienst startet in diesen Zuständen weiterhin; ein solcher Lauf endet sofort
ungültig, ein Zusatzmodul vor der Mindesttemperatur wertet seinen Gang als
fehlend.

Die Antwort auf den Start enthält `run_id`, `kind` (`procedure` für den
Grundablauf, `module` für ein Zusatzmodul), `session_id`, die Teile und die
Ziele.

**Kalibrierung abbrechen** im Panel oder `ha_sauna.cancel_calibration` beendet
die laufende Kalibrierung. Ungültig wird nur der dabei laufende Teil; Sitzung
und Saunabetrieb laufen weiter. Das Panel nennt dabei die Kennung (`run_id`)
des angezeigten Laufs: Ist dieser inzwischen beendet und läuft ein anderer,
bleibt der andere unberührt. Der Dienst beendet den zur Zeit des Aufrufs
laufenden Lauf.

Während eines Laufs zeigt die Karte **Kalibrierung** die Art, den Teil, den
Schritt (n von m) und worauf der Lauf wartet: **Bereit**, **Gangende**,
**Antwort**, **Wartezeit** oder in einer Pause die möglichen Antworten. In einer
Pause stehen **Nächster Gang** und, nach Gang 2, **Beenden** auch als Tasten in
der Karte; sie wirken wie die Antwort in der Nachricht
(`POST /api/ha_sauna/{entry_id}/calibration/pause` mit `run_id` und `action`
`next` oder `finish`). Die Steuerungsansicht zeigt dazu einen kurzen Hinweis,
etwa „Kalibrierung läuft: Gang 1, Schritt 2 von 6“.

Neben der Karte steht der **Letzte Lauf** aus dem Archiv; **Lauf wählen** zeigt
jeden gespeicherten Lauf mit seiner Gültigkeit: gültig, teilweise gültig oder
ungültig. Der laufende Lauf erscheint dort erst nach seinem Ende. Da Lauf und
Ergebnis aus dem Archiv kommen, bleiben sie nach einem Neustart oder Neuladen
der Integration sichtbar. Ein Lauf nennt Zeit, Art, Teile und Gültigkeit, ein
teilweise gültiger den ungültigen Teil mit seinem Grund. Nach der Auswertung
stehen die Summen der gültigen Teile **Gänge gefunden**, **Türen und Aufgüsse im
Fenster**, **Außerhalb des Fensters** und **Anwesend bei leerer Kabine** und je
begonnenem Teil eine Zelle je Schritt:

| Zelle | Bedeutung |
| --- | --- |
| im Fenster | Die zugeordnete Erkennung liegt im Fenster des Schritts. |
| außerhalb | Zugeordnet mit Lage zum Fenster in Sekunden davor oder danach. |
| fehlt | Keine Erkennung zugeordnet. |
| zusätzlich | Nicht zugeordnete Erkennung, in Zeitfolge zwischen den Schritten. |

Ein Schritt mit mehreren Erwartungen (Tür auf, Tür zu) nennt jede einzeln.
Abweichende, bei der Unterbrechung offene und nicht erreichte Schritte sind
gekennzeichnet, ebenso Schritte ohne erwartete Erkennung und die Schritte eines
ungültigen Teils; bei leerer Kabine steht die gemessene Anwesenheit. Unter den
Zellen stehen der Gang des Teils mit Lage von Beginn und Ende zu den Fenstern,
Aufgusszahl und Gangheizung nach dem Austritt, die Präsenzwerte und
zusammengelegte Türvorgänge. Vor der Auswertung nennen die Zellen nur die
erwarteten Erkennungen. Ein Klick auf eine Zelle zeigt die Sitzung des Laufs
unter **Details → Erkennung** mit dem Ausschnitt um das Fenster des Schritts;
eine zugeordnete Erkennung ist dort gewählt. Die Werte kommen aus
`GET /api/ha_sauna/{entry_id}/archive/calibration`
([Archiv](speicherung.md#technische-referenz-api-zugriff)). Läufe aus früheren
Abläufen zeigen ihre gespeicherten Schritte mit den Referenzen zur Zeit des
Laufs.

Ohne gespeicherten Lauf, während eines Laufs und unter **Lauf wählen → Module
und Schritte** steht das Raster **Module und Schritte**: der Grundablauf und die
Zusatzmodule mit ihren Schritten, Referenz und erwarteten Erkennungen (Tür auf,
Tür zu, Aufguss). Während eines Laufs ist der laufende Schritt hervorgehoben.

## Gültigkeit

Jeder Teil gilt für sich: P1, Gang 1, P2, Gang 2 und Gang 3 im Grundablauf, das
Zusatzmodul und jede Wiederholung von P. Ein Teil ist gültig, wenn alle seine
Schritte abgeschlossen sind, durch Antwort, **Abweichend** oder Ende der
Wartezeit. Eine Unterbrechung beendet den Lauf und macht nur den dabei
laufenden Teil ungültig; abgeschlossene Teile bleiben gültig und werden
ausgewertet. Unterbrechungen sind:

- **Abbrechen** in einer Nachricht, im Panel oder über den Dienst
- Betrieb AUS
- der manuelle Modus
- eine manuelle Ofenwahl, solange sie gilt
- mit Präsenzsensor die Meldung „unbekannt“ oder „nicht verfügbar“ und ein
  Start ohne bekannten Präsenzzustand
- Entladen oder Neustart der Integration
- ein Sitzungsende vor dem letzten Schritt

In einer Pause, beim Warten auf **Bereit** und bei einer angeforderten, noch
nicht gelaufenen Wiederholung läuft kein Teil; eine Unterbrechung macht dann
keinen Teil ungültig. Ein Zusatzmodul, das bei Betrieb AUS in der
Wiederaufnahmefrist, im manuellen Modus oder während einer manuellen Ofenwahl
gestartet wird, ist sofort unterbrochen.

**Gang beenden** auf der Steuerungsseite ist während einer Kalibrierung gesperrt,
weil sie gegen das Gangmodell prüft ([Gang von Hand beenden](gangmodell.md#gang-von-hand-beenden)).

Mit der Unterbrechung endet die Kalibrierung und sendet keine Anweisung mehr.
Das Protokoll trägt den ungültigen Teil mit dem Grund. Die Nachricht dazu nennt
den ungültigen Teil und dass die abgeschlossenen Teile nach dem Sitzungsende
ausgewertet werden; ohne gültigen Teil meldet sie, dass die Kalibrierung
ungültig ist und nicht ausgewertet wird. Nach Entladen oder Neustart entfällt
die Nachricht. Die Laufzeit hält einen Lauf nicht über das Entladen hinaus; seine
gültigen Teile bleiben im Protokoll, werden aber nur im Replay ausgewertet. Die
Sitzung bleibt mit dem Protokoll erhalten
([Aufbewahrung](#protokoll-und-aufbewahrung)).

## Ablauf

- Jede Handlung ist eine eigene Nachricht an alle Companion-Ziele mit Antworten
  des startenden Benutzers. Jede Kopie trägt eigene Aktionskennungen. Der Titel
  nennt den Teil, etwa „Sauna-Kalibrierung Gang 1“.
- Die nächste Anweisung folgt nach der Antwort oder nach der Wartezeit des
  Ablaufs. Eine Wartezeit wie „+5 min“ zählt ab dem Abschluss des vorigen
  Schritts, also ab dessen Antwort; „sofort“ folgt unmittelbar auf die Antwort.
- Jede Anweisung bietet zusätzlich **Abweichend** und **Abbrechen**.
  **Abweichend** schließt einen Schritt mit Aktion und nimmt ihn aus der
  Referenz. Ein Schritt ohne Aktion wird damit nur gekennzeichnet und endet mit
  seiner Wartezeit.
- Nach jedem Teil meldet eine Nachricht das Ergebnis der Präsenzmessung. Folgt
  der nächste Teil nach **Nächster Gang**, ist dies eine Pause: nach P2 mit
  **Nächster Gang** und **Abbrechen**, nach Gang 2 mit **Nächster Gang** für den
  freiwilligen Gang 3 und **Beenden**. **Beenden** schließt die Kalibrierung ab.
  Ohne Präsenzsensor folgt die Pause direkt auf Gang 1.
- Nach dem Zusatzmodul P steht **Wiederholen** zur Verfügung; P läuft dann unter
  derselben Startbedingung erneut, sofern keine andere Kalibrierung läuft.
- Eine zweite Antwort auf eine bereits beantwortete Nachricht, etwa von einem
  zweiten Gerät oder nach der Antwort im Panel, gilt als doppelt. Eine Antwort
  auf einen beendeten Schritt, eine beendete Pause oder eine beendete
  Kalibrierung gilt als verspätet. Beide werden protokolliert und ändern den
  Ablauf nicht.
- Nach dem Sitzungsende folgt für die gültigen Teile die Auswertung mit einer
  zusammenfassenden Nachricht.

## Grundablauf

Jeder Gang hat Aufgüsse; „Wann“ nennt die Wartezeit vor der Anweisung.

**P1 Leere Kabine beim Aufheizen** und **P2 Leere Kabine nach Gang 1** (nur mit
Präsenzsensor). P1 beginnt mit dem Start, P2 direkt nach dem Austrittsschluss
von Gang 1. Beide sind gleich aufgebaut.

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Kabine leer, Tür zu, nicht an die Kabine. | Kabine ist leer | Beginn „leer“ |
| +10 min | 2 min direkt vor der geschlossenen Tür stehen und bewegen. | Fertig | Person vor der Tür |
| sofort | Weggehen, 2 min nicht an die Kabine. | – (2 min) | leer |

**Gang 1** (nach **Bereit**)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Gang 1: hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +5 min | Aufguss, eine Kelle. | Gegossen | Aufguss 1 |
| +3 min | Tür 5 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung im Gang |
| +2 min | Zwei Kellen, die zweite 10–15 s nach der ersten. | Gegossen | Aufguss 2, Doppelguss |
| +30 s | Hinaus, Tür offen lassen. | Draußen, Tür offen | Austrittsöffnung = Gangende |
| +45 s | Tür schließen. | Tür zu | Austrittsschluss |

**Gang 2** (nach **Nächster Gang**)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Gang 2: Tür öffnen, kurz hinein, etwas ablegen, wieder hinaus, Tür zu. | Tür zu | kurzer Türvorgang ohne Gang |
| +1 min | Hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +5 min | Aufguss, eine Kelle. | Gegossen | Aufguss 1 |
| +1 min | Tür 15 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung im Abklingen |
| +4 min | Aufguss, eine Kelle. | Gegossen | Aufguss 2 |
| +90 s | Hinaus, Tür offen lassen und 1 min vor der offenen Tür stehen. | Draußen, Tür offen | Austrittsöffnung = Gangende |
| +3 min | Tür schließen. | Tür zu | Austrittsschluss |

**Gang 3** (freiwillig, nach **Nächster Gang** in der Pause nach Gang 2)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Gang 3: hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +1 min | Aufguss, eine Kelle. | Gegossen | Aufguss 1 kurz nach dem Schluss |
| sofort | 30 s wedeln. | Fertig | Wedeln, keine Tür |
| +4 min | 2–3 min ruhig liegen, nicht bewegen. | Fertig | ruhig liegen, Präsenz durchgehend |
| sofort | Aufguss, eine Kelle. | Gegossen | Aufguss 2 |
| +60 s | Hinaus, Tür nach etwa 20 s schließen. | Draußen, Tür zu | Austrittsöffnung = Gangende, Austrittsschluss |
| +2 min | Tür öffnen, kurz hinein, Fenster öffnen, hinaus, Tür offen lassen. | Draußen, Tür offen | Lüften, kurzes Betreten ohne Gang |
| +1 min | Kurz hinein, Fenster schließen, hinaus, Tür zu. | Tür zu | Lüften Ende |

Eine Türöffnung geht stets mit einem kurzen Betreten einher; das Fenster wird
stets zusammen mit der Tür wieder geschlossen. Der Doppelguss in Gang 1 zählt
als ein Aufguss, ein Guss nach dem Plateau als neuer. In Gang 3 schließt eine
Antwort Austrittsöffnung und Austrittsschluss; der Schritt ist die Referenz des
Gangendes.

## Zusatzmodule

Jedes Zusatzmodul ist ein Gang mit Aufguss, außer P. „Wann“ nennt die Wartezeit
vor der Anweisung.

**P Leere Kabine** (nur mit Präsenzsensor, wiederholbar): Schritte wie P1.

**A1–A3 Austritt nach Aufguss**

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Gang A1: hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +6 min | Aufguss, eine Kelle. | Gegossen | Aufguss |
| A1 +30 s, A2 +60 s, A3 +90 s | Hinaus, Tür offen lassen. In A3 zusätzlich 1 min vor der offenen Tür stehen. | Draußen, Tür offen | Austrittsöffnung = Gangende |
| A1 +45 s, A2 +90 s, A3 +3 min | Tür schließen. | Tür zu | Austrittsschluss |

**C/B Kurze Öffnungen**

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Tür öffnen, kurz hinein, etwas ablegen oder holen, wieder hinaus, Tür zu. | Tür zu | kurzer Türvorgang ohne Gang |
| +1 min | Hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +3 min | Tür 5 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung vor dem Aufguss |
| +2 min | Aufguss, eine Kelle. | Gegossen | Aufguss |
| +1 min | Tür 15 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung im Abklingen |
| +2 min | Hinaus, Tür offen lassen. | Draußen, Tür offen | Austrittsöffnung |
| +1 min | Tür schließen. | Tür zu | Austrittsschluss |

**D Aufgüsse**

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Beginn | Hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +3 min | Eine Kelle. | Gegossen | Aufguss 1, erster Guss |
| sofort | Zweite Kelle, 10–15 s nach der ersten. | Gegossen | Aufguss 1, zweiter Guss |
| +5 min | Eine Kelle. | Gegossen | Aufguss 2 |
| +1 min | Hinaus, Tür offen lassen. | Draußen, Tür offen | Austrittsöffnung |
| +1 min | Tür schließen. | Tür zu | Austrittsschluss |

Referenz in D sind zwei Aufgüsse: Der Doppelguss zählt als einer, ein Guss nach
dem Plateau als neuer. Ein zweiter erkannter Aufguss zum Doppelguss gilt deshalb
als zusätzlich, wenn beide Referenzen eine Erkennung haben.

## Zeitfenster und Auswertung

Ein Zeitfenster reicht von der Übergabe der Anweisung bis zur Antwort
(`time_fired` des Home-Assistant-Ereignisses), bei einem Schritt ohne Aktion bis
zum Ende seiner Wartezeit. Die Sekunde innerhalb des Fensters liefert die
Erkennung.

| Bereich | Auswertung |
| --- | --- |
| Erkennung | Gebuchte Türöffnungen, Türschlüsse und Aufgüsse von der ersten Anweisung bis zum Ende des letzten Fensters der gültigen Teile, den Referenzen zugeordnet wie unten beschrieben. Jede Referenz ist im Fenster gefunden, außerhalb des Fensters zugeordnet oder fehlt. Eine Zuordnung außerhalb trägt ihre Lage zum Fenster: Sekunden davor negativ, danach positiv. Nicht zugeordnete Ereignisse gelten als zusätzlich, auch in den Teilen ohne Türerwartung wie P1, P2 und den Schritten „Wedeln“ und „ruhig liegen“. Die Summen weisen Treffer im Fenster und außerhalb getrennt aus. Abweichende Schritte bleiben ohne Referenz; Ereignisse in ihrem Fenster bleiben außer Betracht. |
| Präsenz | Aussetzer nach Zahl und Dauer zwischen dem Abschluss von **Drin, Tür zu** und der Anweisung zum Austritt. Nachlauf: Präsenz aus minus **Tür zu** nach dem Austritt bis zum Beginn des nächsten Teils, negativ bei Abwesenheit schon vor dem Schluss; in Gang 3 mit gemeinsamer Antwort für Austrittsöffnung und -schluss entfällt er. In Gang 2 und A3 die Anwesenheit in der Minute vor der offenen Tür. Anwesenheit bei leerer Kabine in P, P1 und P2. |
| Gang | Der Gang, dessen Beginn das dem Eintrittsschluss zugeordnete Ereignis ist, sonst der Gang mit Beginn ab der Anweisung zum Eintritt und vor der Anweisung zum Austritt; fehlend, weitere Gänge mit Beginn im Teil zusätzlich, etwa aus dem kurzen Türvorgang ohne Gang. Beginn, Bestätigung und Ende mit Lage zu den Fenstern von Eintritt und Austrittsöffnung, Aufgusszahl gegen die Referenz und Gangheizung nach dem Austritt: gebuchte Heizzeit mit der Ganganforderung als Grund der Heizentscheidung, ab dem Abschluss der Austrittsöffnung bis zum Gangende. |

Zuordnung der Erkennung, ohne Toleranz und ohne feste Größe:

1. Die Referenzen der gültigen Teile eines Laufs bilden eine Folge in der
   physikalischen Reihenfolge der Schritte: die Öffnung vor ihrem Schluss, ein
   Aufguss bei geschlossener Tür. Die Erkennungen bilden eine Folge in
   Zeitfolge.
2. Jede Erkennung wird einzeln einer Referenz derselben Art zugeordnet, jede
   Referenz erhält höchstens eine Erkennung. Zuordnungen kreuzen die Reihenfolge
   nicht, auch nicht zwischen Öffnungen, Schlüssen und Aufgüssen.
3. Eine Erkennung kommt für eine Referenz nur in Frage, wenn sie nach dem Ende
   des Fensters der vorigen und vor dem Beginn des Fensters der nächsten
   Referenz derselben Art liegt.
4. Gewählt wird die Zuordnung mit den meisten Paaren, bei Gleichstand die mit
   der kleinsten Summe der Abstände zu den Fenstern. Bei vollem Gleichstand gilt
   die frühere Erkennung und die frühere Referenz.

Ob eine Zuordnung außerhalb des Fensters dasselbe Ereignis ist, bewertet die
Auswertung nicht. Ein Referenz-Türvorgang ist eine Öffnung mit dem folgenden
Schluss, etwa Austrittsöffnung und Austrittsschluss. Folgt unter den erkannten
Türereignissen auf eine Öffnung, die einem Referenz-Türvorgang zugeordnet ist,
ein Schluss, der einem späteren Referenz-Türvorgang zugeordnet ist, hat die
Erkennung diese Türvorgänge zusammengelegt. Das Ergebnis weist das als Hinweis
aus; die Zuordnung bleibt unverändert.

Die Präsenzwerte stehen nach jedem Teil fest. Türen, Aufgüsse und Gänge werden
erst nach dem Sitzungsende ausgewertet, weil ihre Bestätigung keine feste
Wartezeit hat. Die Auswertung liest dann nur die gebuchten Ereignisse; die
Fenster wirken nicht auf Erkennung, Gangmodell oder Steuerung. Ungültige Teile
werden nicht ausgewertet, auch nicht im Replay; das Replay nennt sie unter
`skipped`.

## Protokoll und Aufbewahrung

Das Protokoll steht im Archiv der Sitzung als Datensatzart `calibration` und
ist im Export in `records.jsonl` enthalten. Die Datensätze sind wie
Diagnosedaten Administratoren vorbehalten.

| Ereignis | Inhalt |
| --- | --- |
| `start` | Art (`procedure` oder `module`), geplante Teile, freiwillige Teile (`optional`), startender Benutzer, `validity` `part` für die Gültigkeit je Teil |
| `module` | Beginn eines Teils mit laufender Nummer |
| `message` | Teil und Schritt, `message_id`, Ziel, Übergabezeit, `accepted` und Nachweis der Übergabe |
| `reply` | Aktion, `time_fired`, Benutzer, Zuordnung `accepted`, `duplicate` oder `late`; eine Pausenantwort aus dem Panel mit `source` `panel` |
| `step` | Abgeschlossener Schritt mit Fenster, Ausgang (`reply`, `hold`, `deviate`, `cancelled`), Aktion und Benutzer |
| `module_end` | Ende eines Teils mit `valid` und, für den ungültigen, dem Grund der Unterbrechung |
| `repeat` | Wiederholung |
| `end` | Ende mit Grund (`finished`, `cancelled`, `operation_off`, `manual_mode`, `heater_override`, `presence_unavailable`, `session_ended`, `integration_unloaded`), `valid` (mindestens ein gültiger und kein ungültiger Teil) und den gültigen Teilen (`valid_executions`) |
| `evaluation` | Auswertung der gültigen Teile nach dem Sitzungsende |

Läufe aus früheren Abläufen ohne `validity` gelten als Ganzes: gültig nur mit
dem Ende `finished`. Eine Sitzung mit Kalibrierprotokoll bleibt auch ohne
bestätigten Gang erhalten
([Archiv](speicherung.md#welche-sitzungen-erhalten-bleiben)). Sie lässt sich wie
jede andere Sitzung löschen. Sensorzeiten werden aus dem Protokoll nicht
automatisch gelernt.
