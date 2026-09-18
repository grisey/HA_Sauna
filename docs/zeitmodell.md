# Gangbeginn, Erkennung, Bestätigung und Fristen

## Drei unterschiedliche Zeitpunkte

| Feld | Bedeutung |
|---|---|
| `Gang.started_at` | Zugeordneter Beginn bei der Türschließung derselben Episode und Session. |
| `Gang.detected_at` | Erste Erkennung durch Personenmuster oder direkt durch Aufguss. |
| `Gang.confirmed_at` | Erkennungszeit des ersten zugeordneten Aufgusses; zuvor nicht gesetzt. |

Die Phase Saunagang wird bereits bei der ersten Erkennung angezeigt,
gegebenenfalls vorläufig. Der spätere Aufguss bestätigt dasselbe Intervall,
ohne `started_at` oder `detected_at` zu überschreiben. Seine Zeit stammt aus
dem ersten Aufgussereignis. Fachliche Übergänge: [Gangmodell](gangmodell.md).

## Beispiel aus dem festgehaltenen Kandidaten

Für Gang 2 ergibt sich im Zwei-Sensor-Replay:

| Ereignis | Zeitpunkt am 17.09.2026, MESZ | Einordnung |
|---|---|---|
| Durchlüften bestätigt | 21:15:20 | Vorbereitung, noch kein Gang. |
| Türschließung erkannt | 21:15:51 | Zeitlicher Bezug des folgenden Gangs. |
| Personenmuster erkannt | 21:19:15 | Vorläufig aktiv; Beginn 21:15:51, Dauer 3:24 Minuten. |
| Erster Aufguss erkannt | 21:22:52 | Derselbe Gang bestätigt; Dauer 7:01 Minuten. |

Quelle: [gemeinsames Replay](kandidat.md#7-ergebnisse-des-gemeinsamen-replay).
Vorläufige beziehungsweise bestätigte Zuordnung ist die darauf angewandte
Ablaufregel, kein unabhängig gemessener Personenzeitpunkt.

Ohne Personenfrüherkennung wird der Gang um 21:22:52 unmittelbar bestätigt
angelegt. Dann sind `detected_at` und `confirmed_at` gleich; der Beginn
bleibt 21:15:51.

## Ereigniszeit und Herkunft

Eingangsereignisse führen `effective_at` und `detected_at`: zugeordneten
Ereigniszeitpunkt und tatsächliche Entscheidungszeit. Im eingefrorenen
Kandidaten sind beide gleich. Die Verarbeitung erfolgt in Erkennungsreihenfolge;
Zeitstempel tragen eine Zeitzone und werden intern nach UTC umgerechnet.

`start_source_event_id` verweist auf die verwendete Schließung. Neue Öffnung
ersetzt einen ungenutzten Bezug; ein angelegter Gang behält seinen Beginn
bei kurzer Türbetätigung. Es wird keine beliebige ältere Schließung und kein
Zeitpunkt vor Sessionbeginn als Ersatz gewählt.

Bei bekannt geschlossenem Türzustand ohne Schließungshistorie verwendet der
vorhandene Kern `detected_at` als Beginn und kennzeichnet
`start_basis=recognition_only`. Das ist eine eingeschränkte Zeitzuordnung,
keine rekonstruierte Schließung und keine Neustartentscheidung.

## Zeitliche Beziehungen der vereinbarten Regeln

| Größe | Bezug |
|---|---|
| Angezeigte Gangdauer | Zeit seit `started_at`. |
| Aufgussbestätigungsfrist | `started_at` plus konfigurierter Fristwert; Orientierung 12 oder 13 Minuten. |
| Sessiongrenze nach Betrieb aus | Ausschaltzeitpunkt des Betriebs plus konfigurierte Sessionfrist. |
| Heizlaufzeit | Summe tatsächlicher Heizintervalle seit der letzten Rücksetzung; bei idle pausiert. |
| Heizzeitgrenze | Ein einstellbarer Wert für sämtliche Heizabschnitte, ohne eigene Anheiz- und Folgedauer. |
| Rücksetzung der Heizlaufzeit | Zusammenhängende tatsächliche Auszeit erreicht die konfigurierte Rücksetzdauer. |
| Laufender Nachlauf | Behält bei Betriebsunterbrechung seinen ursprünglichen Endzeitpunkt. |
| Nachlaufanrechnung | Bereits verstrichene Nachlaufzeit wird vollständig auf die zugehörige Zwangskühlungsdauer angerechnet. |

Personenerkennung, weitere Personenmeldungen oder kurze Türbetätigung desselben
Gangs gewähren keine neue volle Bestätigungsfrist. Die Folgen eines Fristablaufs
sind im Gangmodell nach ihrem Entscheidungsstatus ausgewiesen.

Bei Wiedereinschalten vor Ablauf der Sessionfrist bleibt es dieselbe Session;
bei Wiedereinschalten nach deren Ablauf beginnt eine vollständig neue Session
mit neu initialisierten Unterobjekten. Ein durch Ausschalten beendeter Gang
wird in beiden Fällen nicht wieder aktiviert. Normale Thermostatpausen und
Zwangskühlung bestimmen diese Sessionfrist nicht.

Eine kurze tatsächliche Auszeit pausiert die Heizzeitsumme. Eine ausreichend
lange zusammenhängende Auszeit setzt sie zurück. Die bloße verstrichene Zeit
seit dem ersten Einschalten ist keine Heizlaufzeit. Idle verlängert nicht
zusätzlich das Heizbudget und begründet keine weitere prozentuale Kühlgutschrift.

Nachlauf und Zwangskühlung bleiben getrennt. Für den zugehörigen Kühlvorgang
wird jeder bereits verstrichene Nachlaufzeitabschnitt nur einmal angerechnet;
noch zukünftige Nachlaufzeit wird nicht vorweggenommen. Die Anrechnung kann
die verbleibende Kühlzeit bis auf null vermindern, aber nicht negativ machen.
Die Betriebsunterbrechung selbst verschiebt den Nachlaufendzeitpunkt nicht.
Eine neue Session übernimmt keine Nachlaufobjekte der alten Session.

Diese Fristen und Summen sind Vorgaben für die nächste Implementierung, nicht
bereits hinzugefügte Timer des vorhandenen Fachkerns. Parameteränderungen bei
laufenden Fristen und HA-Neustarts werden gesondert festgelegt.

## Gegenwart und rückwirkende Darstellung

Die eigene Sessionansicht kann den vorläufigen Gang ab `started_at` darstellen
und später dasselbe Intervall als bestätigt kennzeichnen. Ursprüngliche
Erkennungszeit und damaliger Bestätigungsstand bleiben nachvollziehbar.
Heizentscheidungen wirken erst ab tatsächlicher Erkennung. Eine frühere
Abschaltung wird nicht rückwirkend verändert. Eine vorsorgliche Heizbehandlung
allein nach Türschließung wurde nicht vereinbart.

Die native HA-Zustandshistorie wird nicht umgeschrieben. Die historische
Gangdarstellung verwendet eigene zugeordnete Intervalle. Fachliche Gangdauer
und tatsächliche Heizdauer bleiben unterschiedliche Größen.

## Abschlusszeiten und Zählung

Beim regulären Abschluss verwendet der bestehende Kern den Zeitpunkt der
Durchlüftungsbestätigung. Rückzuordnung des Gangendes zur Türöffnung bleibt
offen. Beim ausdrücklich ausgeschalteten Gang ist der Ausschaltzeitpunkt
als Ende vereinbart, mit dem Beendigungsgrund „ausgeschaltet“.

Unabhängig vom Endgrund zählt jeder beendete Gang mit zugeordnetem Aufguss
genau einmal. Die Bestätigungszuordnung, nicht die Art des Endereignisses,
entscheidet über die Zählung. Die Erweiterung des bisherigen regulären
Abschlussmodells folgt noch. Einzelheiten: [Betrieb](betrieb.md).

Referenz zur HA-Zustandszeit, in der Erstfassung am 18.09.2026 eingesehen:
https://www.home-assistant.io/docs/configuration/state_object/
