# Zeitmodell

> Ergänzender Vorrang: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> beschreibt den Produktivstand des Folgeauftrags vom 26.09.2026.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

Diese Bezüge ergänzen [Betrieb](betrieb.md) und [Gangmodell](gangmodell.md).
Alle Dauern sind einstellbare Standardwerte; die Solltemperatur-Obergrenze von
100 °C ist davon ausgenommen.

| Größe | Bezug und Standard |
| --- | --- |
| Gangbeginn | Passende Türschließung, sonst gekennzeichnete Erkennung. |
| Aufgussbestätigung | Tatsächliche Zeit des ersten zugeordneten Aufgusses; Frist: Beginn + 12 min. |
| Sitzungswiederaufnahme und Lichtnachlauf | Betrieb-Aus + 15 min, mit gemeinsamem Endzeitpunkt. Wiedereinschalten beendet den Lichtnachlauf. Nach langem Tasterdruck beginnt nur der Lichtnachlauf mit dem Loslassen, ebenfalls mit dieser Dauer. |
| Nachlauf | Ende des bestätigten Gangs + 8 min tatsächliche Laufzeit. Manuelles Heizen pausiert die Uhr. |
| Heizbudget | 90 min; nach erster abgeschlossener Kühlung einmalig 60 min. |
| Mindestheizzeit | Tatsächlicher Beginn eines Heizintervalls + 10 min. |
| Thermostat-Cooldown | Reguläre Temperaturabschaltung + 5 min. |
| Türwartefrist für fällige Kühlung | Öffnung + 10 min; bei rechtzeitiger Schließung Schließung + 4 min. |
| Zwangskühlung | Kühlstart + noch nicht gelaufene oder gutgeschriebene Restdauer; Vorgabe 15 min. |
| Übertemperatur | Durchgehend gültige obere Temperatur über 105 °C + 10 min. |
| Lichtübergang | 30 s; Lichtnachlauf bei 50 %, am Ende unmittelbar aus. |
| Vorübergehende Übersteuerung | Wahl + höchstens 10 min, mit früheren Rückkehrpunkten gemäß Betrieb. |
| Mechanischer Timer | Zählt maximal 240 min nur bei Betrieb-Ein und Schütz-Ein. |

Die Nachlaufzeit läuft nur, solange sie nicht manuell pausiert ist. Ihre echte
verstrichene Dauer wird einmalig einer folgenden Kühlung gutgeschrieben; die
Pause selbst zählt weder als Nachlauf noch als Kühlung. Bei einem vorläufigen
Gang während des pausierten Nachlaufs bleibt der Rest eingefroren. Erst dessen
Aufgussbestätigung storniert den alten Nachlauf. Aufhebung oder Fristablauf
setzen den alten Rest fort, ohne eine zweite Gutschrift.

Zeitstempel tragen eine Zeitzone und werden intern in UTC geführt.
`effective_at` bezeichnet die zugeordnete Ereigniszeit, `detected_at` den
tatsächlichen Entscheidungszeitpunkt. Messungen bewahren ihren Empfangszeitpunkt;
ein unbekannter Gerätezeitpunkt wird nicht erfunden. Fristen gehören zu einer
Sitzung und einem Vorgangstoken. Ersetzte Fristen wirken nicht weiter,
wiederholte Ereignisse sind idempotent, und die Laufzeituhr läuft nicht zurück.
Eingänge genau am Ende einer Bestätigungs- oder Türwartefrist werden zuerst
verarbeitet.
