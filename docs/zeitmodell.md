# Zeitbezüge

| Größe | Führender Bezug |
|---|---|
| Gangbeginn | Passende Türschließung; ersatzweise ausdrücklich gekennzeichnete erste Erkennung. |
| Erste Erkennung | Tatsächlicher Entscheidungszeitpunkt des Personen-/Aufgusssignals. |
| Bestätigung | Tatsächliche Erkennungszeit des ersten zugeordneten Aufgusses. |
| Gangdauer | Vom zugeordneten Beginn bis Ende beziehungsweise jetzt. |
| Bestätigungsfrist | Beginn plus konfigurierter Fristwert. |
| Sessionfrist | Ausdrückliches Betrieb-Aus plus Session-Unterbrechungsfrist. |
| Heizzeit | Gezählte Heizintervalle seit Rücksetzung; gemessen oder aus Schützstellung geschätzt. |
| Heizbudget | Anfangswert; nach erster abgeschlossener Kühlung einmalig verringert. |
| Lokale Heizzeitrücksetzung | Durchgehend rückgemeldetes Ofen-Aus über konfigurierte Dauer. |
| Thermostat-Cooldown | Reguläre temperaturbedingte Abschaltentscheidung plus Cooldown. |
| Mindestheizzeit | Beginn des tatsächlichen Heizintervalls. |
| Nachlauf | Tatsächliches Ende des bestätigten Gangs plus Nachlaufdauer. |
| Kühlende | Start der Kühlung plus um verbrauchten Nachlauf verminderte Kühlvorgabe. |
| Türwartefrist | Öffnung plus Offenfrist; nach rechtzeitiger Schließung diese plus Personenwartefrist. |
| Temperatur-Zusatzkühlung | Durchgehender gültiger oberer Temperaturbeleg über Grenze und Auslösezeit. |
| Mechanischer Ofentimer | Geschätzte Laufzeit bei eingeschaltetem Saunabetrieb. Betrieb-Aus hält die Anzeige an; Thermostatpausen und Kühlung nicht. Neue volle Laufzeit erst nach beendeter Sitzung mit gezählten Gängen. Keine Messung seiner Stellung. |

Alle Zeitstempel tragen eine Zeitzone und werden intern in UTC geführt.
`effective_at` kennzeichnet die zugeordnete Ereigniszeit, `detected_at` die
wirkliche Entscheidungszeit. Messungen bewahren den tatsächlichen Empfangszeitpunkt;
ein unbekannter Gerätezeitpunkt wird nicht aus einem HA-Zustandswechsel erfunden.

Fristen gehören zu einer Session und tragen Zweck sowie Vorgangstoken. Alte oder
ersetzte Fristen wirken nicht weiter. Wiederholte Ereignis-IDs sind idempotent;
widersprüchliche Inhalte unter derselben ID werden abgewiesen. Die Laufzeituhr
läuft nicht rückwärts. Eingänge am Ende einer Bestätigungs-/Personenwartefrist
werden vor deren genau gleichzeitigem Ablauf verarbeitet.

Ein vorläufiger Gang ist ab Erkennung sichtbar, im eigenen Verlauf aber ab der
zugeordneten Türschließung. Spätere Bestätigung verändert denselben Abschnitt.
HA-Zustandswechsel und reale Schaltbefehle werden nicht zurückdatiert. Reguläres
Gangende bleibt die Durchlüftungsbestätigung, nicht rückwirkend die Öffnung.

Der laufende Nachlauf behält sein Ende bei Aus/Ein. Seine verstrichene Dauer wird
vollständig und einmal angerechnet; künftige Zeit wird nicht vorweggenommen.
Konfiguration ist während einer Session gesperrt, daher gibt es keine laufende
Neuberechnung von Grundfristen durch Parameteränderung. Nach HA-Neustart bleibt
Historie erhalten, Betrieb und Fristen werden nicht automatisch fortgesetzt.

Im privaten Kalibrierungsvergleich liegt das schwache Personensignal eines Gangs
3 Minuten 24 Sekunden nach der Türschließung. Die standardmäßige vierminütige
Personenwartefrist berücksichtigt diesen Befund. Das ist Kalibrierung und keine
unabhängige Bestätigung realer Anwesenheit. [Kandidat](kandidat.md).
