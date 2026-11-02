# Kalibrierung

Die Kalibrierung legt ein Protokoll über eine Sitzung. Die Integration sendet
Anweisungen als Companion-Benachrichtigung; jede Antwort schließt das
Zeitfenster ihres Vorgangs. Die Fenster sind eine Referenz, die nicht aus der
Erkennung stammt: für den Präsenzsensor, die Tür- und Aufgusserkennung und das
Gangmodell. Steuerung, Erkennung und Gangmodell bleiben dabei unverändert.
Antworten bedienen die Sauna nicht.

## Start und Abbruch

| Weg | Start | Ablauf |
| --- | --- | --- |
| Kalibriersitzung | `ha_sauna.start_calibration` ohne laufende Sitzung | Startet den Saunabetrieb im Automatikbetrieb, aus dem manuellen Modus mit Wechsel auf Automatik. Nach **Bereit** laufen die Module der Reihe nach: P, A1, A2, A3, C/B, D. `modules` schränkt die Auswahl ein. |
| Einzelnes Modul | `ha_sauna.start_calibration` mit `module` in einer laufenden Sitzung | Startet, sobald kein Gang aktiv ist. Ein vorläufiger Gang zählt als aktiv. |

Voraussetzungen:

- Ein zugeordnetes Ziel **Companion: Daten und Antworten** für den startenden
  Benutzer ([Benachrichtigungen](benachrichtigungen.md)). Ohne ein solches Ziel
  und ohne Benutzeridentität wird der Start abgewiesen.
- Steuerberechtigung für die Betriebsentität, wie bei den übrigen Diensten.
- Es läuft keine andere Kalibrierung derselben Instanz.
- Ohne Präsenzsensor entfällt Modul P; ein ausdrücklich angefordertes Modul P
  wird abgewiesen. Die übrigen Module laufen ohne Präsenzmessung.

Die Dienstantwort enthält `run_id`, `kind` (`session` oder `module`),
`session_id`, die gewählten Module und die Ziele.

`ha_sauna.cancel_calibration` beendet die laufende Kalibrierung. Sitzung und
Saunabetrieb laufen weiter.

## Gültigkeit

Eine Kalibrierung gilt nur ununterbrochen: Alle Schritte der gewählten Module
sind abgeschlossen, durch Antwort, **Abweichend** oder Ende der Wartezeit.
Unterbrechungen sind:

- **Abbrechen** in einer Nachricht oder über den Dienst
- Betrieb AUS
- der manuelle Modus
- eine manuelle Ofenwahl, solange sie gilt
- mit Präsenzsensor die Meldung „unbekannt“ oder „nicht verfügbar“ und ein
  Start ohne bekannten Präsenzzustand
- Entladen oder Neustart der Integration
- ein Sitzungsende vor dem letzten Schritt

Vor dem letzten Schritt liegen auch die Pause vor **Nächster Gang** und eine
angeforderte, noch nicht gelaufene Wiederholung. Ein Modul, das bei Betrieb AUS
in der Wiederaufnahmefrist, im manuellen Modus oder während einer manuellen
Ofenwahl gestartet wird, ist damit sofort unterbrochen.

Mit der Unterbrechung endet die Kalibrierung und sendet keine Anweisung mehr.
Das Protokoll trägt sie als ungültig mit dem Grund. Statt der Auswertung folgt
nur eine Nachricht, dass die Kalibrierung ungültig ist; nach Entladen oder
Neustart entfällt auch diese. Die Sitzung bleibt mit dem Protokoll erhalten
([Aufbewahrung](#protokoll-und-aufbewahrung)).

## Ablauf

- Jede Handlung ist eine eigene Nachricht an alle Companion-Ziele mit Antworten
  des startenden Benutzers. Jede Kopie trägt eigene Aktionskennungen.
- Die nächste Anweisung folgt nach der Antwort oder nach der Wartezeit des
  Ablaufs. Eine Wartezeit wie „+6 min“ zählt ab dem Abschluss des vorigen
  Schritts, also ab dessen Antwort.
- Jede Anweisung bietet zusätzlich **Abweichend** und **Abbrechen**.
  **Abweichend** schließt einen Schritt mit Aktion und nimmt ihn aus der
  Referenz. Ein Schritt ohne Aktion wird damit nur gekennzeichnet und endet mit
  seiner Wartezeit.
- Nach jedem Modul meldet eine Nachricht das Ergebnis der Präsenzmessung. In der
  Kalibriersitzung folgt eine Pause; weiter geht es mit **Nächster Gang**. Nach
  Modul P steht zusätzlich **Wiederholen** zur Verfügung. Als einzelnes Modul
  läuft P nach **Wiederholen** unter derselben Startbedingung erneut, sofern
  keine andere Kalibrierung läuft.
- Eine zweite Antwort auf eine bereits beantwortete Nachricht, etwa von einem
  zweiten Gerät, gilt als doppelt. Eine Antwort auf einen beendeten Schritt,
  eine beendete Pause oder eine beendete Kalibrierung gilt als verspätet. Beide
  werden protokolliert und ändern den Ablauf nicht.
- Nach dem Sitzungsende folgt für eine gültige Kalibrierung die Auswertung mit
  einer zusammenfassenden Nachricht.

## Module

Jeder Gang eines Moduls hat einen Aufguss. „Wann“ nennt die Wartezeit vor der
Anweisung.

**P Leere Kabine** (vor dem ersten Gang, nur mit Präsenzsensor)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Start | Kabine leer, Tür zu, nicht an die Kabine. | Kabine ist leer | Beginn „leer“ |
| +10 min | 2 min direkt vor der geschlossenen Tür stehen und bewegen. | Fertig | Person vor der Tür |
| sofort | Weggehen, 2 min nicht an die Kabine. | – (2 min) | leer |

**A1–A3 Austritt nach Aufguss** (je ein Gang)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Start | Gang A1: hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +6 min | Aufguss, eine Kelle. | Gegossen | Aufguss |
| A1 +30 s, A2 +60 s, A3 +90 s | Hinaus, Tür offen lassen. In A3 zusätzlich 1 min vor der offenen Tür stehen. | Draußen, Tür offen | Austrittsöffnung, Gangende |
| A1 +45 s, A2 +90 s, A3 +3 min | Tür schließen. | Tür zu | Austrittsschluss |

**C/B Kurze Öffnungen** (ein Gang)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Start | Tür von außen 10 s öffnen, nicht eintreten, schließen. | Tür zu | Öffnung ohne Gang |
| +1 min | Hinein, Tür zu. | Drin, Tür zu | Eintritt |
| +3 min | Tür 5 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung vor dem Aufguss |
| +2 min | Aufguss, eine Kelle. | Gegossen | Aufguss |
| +1 min | Tür 15 s öffnen, drinbleiben, schließen. | Tür zu | kurze Öffnung im Abklingen |
| +2 min | Hinaus, Tür offen lassen. | Draußen, Tür offen | Austrittsöffnung |
| +1 min | Tür schließen. | Tür zu | Austrittsschluss |

**D Aufgüsse** (ein Gang)

| Wann | Anweisung | Aktion | Vorgang |
| --- | --- | --- | --- |
| Start | Hinein, Tür zu. | Drin, Tür zu | Eintritt |
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
| Erkennung | Gebuchte Türöffnungen, Türschlüsse und Aufgüsse von der ersten Anweisung bis zum Ende des letzten Fensters der Kalibrierung, den Referenzen zugeordnet wie unten beschrieben. Jede Referenz ist im Fenster gefunden, außerhalb des Fensters zugeordnet oder fehlt. Eine Zuordnung außerhalb trägt ihre Lage zum Fenster: Sekunden davor negativ, danach positiv. Nicht zugeordnete Ereignisse gelten als zusätzlich. Die Summen weisen Treffer im Fenster und außerhalb getrennt aus. Abweichende Schritte bleiben ohne Referenz; Ereignisse in ihrem Fenster bleiben außer Betracht. |
| Präsenz | Aussetzer nach Zahl und Dauer zwischen dem Abschluss von **Drin, Tür zu** und der Anweisung zum Austritt. Nachlauf: Präsenz aus minus **Tür zu** nach dem Austritt, negativ bei Abwesenheit schon vor dem Schluss. In A3 die Anwesenheit in der Minute vor der offenen Tür. Anwesenheit bei leerer Kabine in Modul P und im ersten Schritt von C/B. |
| Gang | Der Gang, dessen Beginn das dem Eintrittsschluss zugeordnete Ereignis ist, sonst der Gang mit Beginn ab der Anweisung zum Eintritt und vor der Anweisung zum Austritt; fehlend, weitere Gänge mit Beginn im Modul zusätzlich. Beginn, Bestätigung und Ende mit Lage zu den Fenstern von Eintritt und Austrittsöffnung, Aufgusszahl gegen die Referenz und Gangheizung nach dem Austritt: gebuchte Heizzeit mit der Ganganforderung als Grund der Heizentscheidung, ab **Draußen, Tür offen** bis zum Gangende. |

Zuordnung der Erkennung, ohne Toleranz und ohne feste Größe:

1. Die Referenzen einer Kalibrierung bilden eine Folge in der physikalischen
   Reihenfolge der Schritte: die Öffnung vor ihrem Schluss, ein Aufguss bei
   geschlossener Tür. Die Erkennungen bilden eine Folge in Zeitfolge.
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

Die Präsenzwerte stehen nach jedem Modul fest. Türen, Aufgüsse und Gänge werden
erst nach dem Sitzungsende ausgewertet, weil ihre Bestätigung keine feste
Wartezeit hat. Die Auswertung liest dann nur die gebuchten Ereignisse; die
Fenster wirken nicht auf Erkennung, Gangmodell oder Steuerung. Ungültige
Kalibrierungen werden nicht ausgewertet, auch nicht im Replay.

## Protokoll und Aufbewahrung

Das Protokoll steht im Archiv der Sitzung als Datensatzart `calibration` und
ist im Export in `records.jsonl` enthalten. Die Datensätze sind wie
Diagnosedaten Administratoren vorbehalten.

| Ereignis | Inhalt |
| --- | --- |
| `start` | Art, Module, startender Benutzer |
| `module` | Beginn eines Moduls mit laufender Nummer |
| `message` | Modul und Schritt, `message_id`, Ziel, Übergabezeit, `accepted` und Nachweis der Übergabe |
| `reply` | Aktion, `time_fired`, Benutzer, Zuordnung `accepted`, `duplicate` oder `late` |
| `step` | Abgeschlossener Schritt mit Fenster, Ausgang (`reply`, `hold`, `deviate`, `cancelled`), Aktion und Benutzer |
| `repeat` | Wiederholung |
| `end` | Ende mit Grund und `valid`: gültig nur bei `finished`; ungültig bei `cancelled`, `operation_off`, `manual_mode`, `heater_override`, `presence_unavailable`, `session_ended`, `integration_unloaded` |
| `evaluation` | Auswertung einer gültigen Kalibrierung nach dem Sitzungsende |

Eine Sitzung mit Kalibrierprotokoll bleibt auch ohne bestätigten Gang erhalten
([Archiv](speicherung.md#welche-sitzungen-erhalten-bleiben)). Sie lässt sich wie
jede andere Sitzung löschen. Sensorzeiten werden aus dem Protokoll nicht
automatisch gelernt.
