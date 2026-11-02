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
Saunabetrieb laufen weiter. Betrieb AUS beendet die Kalibrierung nicht; sie
endet spätestens mit der Sitzung. Entladen der Integration bricht sie ab.

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
- Nach dem Sitzungsende folgt die Auswertung mit einer zusammenfassenden
  Nachricht.

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
dem Plateau als neuer. Ein zweiter erkannter Aufguss im Abschnitt des zweiten
Gusses gilt deshalb als zusätzlich.

## Zeitfenster und Auswertung

Ein Zeitfenster reicht von der Übergabe der Anweisung bis zur Antwort
(`time_fired` des Home-Assistant-Ereignisses), bei einem Schritt ohne Aktion bis
zum Ende seiner Wartezeit. Die Sekunde innerhalb des Fensters liefert die
Erkennung.

| Bereich | Auswertung |
| --- | --- |
| Erkennung | Gebuchte Türöffnungen, Türschlüsse und Aufgüsse. Ein Ereignis gehört zu dem Schritt, in dessen Abschnitt seine Zeit liegt: ab der Übergabe der Anweisung bis zur Übergabe der nächsten Anweisung derselben Kalibrierung. Der letzte Schritt endet mit seinem Fenster. Das erste passende Ereignis gilt als gefunden, mit seiner Lage zum Fenster (0 im Fenster, sonst Sekunden danach); weitere Ereignisse im Abschnitt gelten als zusätzlich. Abweichende und abgebrochene Schritte bleiben ohne Referenz. |
| Präsenz | Aussetzer nach Zahl und Dauer zwischen dem Abschluss von **Drin, Tür zu** und der Anweisung zum Austritt. Nachlauf: Präsenz aus minus **Tür zu** nach dem Austritt, negativ bei Abwesenheit schon vor dem Schluss. In A3 die Anwesenheit in der Minute vor der offenen Tür. Anwesenheit bei leerer Kabine in Modul P und im ersten Schritt von C/B. |
| Gang | Der Gang mit Beginn im Eintrittsabschnitt; fehlend oder zusätzlich. Beginn, Bestätigung und Ende mit Lage zu den Fenstern von Eintritt und Austrittsöffnung, Aufgusszahl gegen die Referenz und Gangheizung nach dem Austritt: gebuchte Heizzeit mit der Ganganforderung als Grund der Heizentscheidung, ab **Draußen, Tür offen** bis zum Gangende. |

Die Präsenzwerte stehen nach jedem Modul fest. Türen, Aufgüsse und Gänge werden
erst nach dem Sitzungsende ausgewertet, weil ihre Bestätigung keine feste
Wartezeit hat.

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
| `repeat`, `end` | Wiederholung und Ende mit Grund (`finished`, `cancelled`, `session_ended`, `integration_unloaded`) |
| `evaluation` | Auswertung nach dem Sitzungsende |

Eine Sitzung mit Kalibrierprotokoll bleibt auch ohne bestätigten Gang erhalten
([Archiv](speicherung.md#welche-sitzungen-erhalten-bleiben)). Sie lässt sich wie
jede andere Sitzung löschen. Sensorzeiten werden aus dem Protokoll nicht
automatisch gelernt.
