# Gangbeginn, Erkennung und Bestätigung

## Drei unterschiedliche Zeitpunkte

| Feld | Bedeutung |
|---|---|
| `Gang.started_at` | Zugeordneter Beginn bei der Türschließung derselben Episode und Session. |
| `Gang.detected_at` | Zeitpunkt, an dem der Gang erstmals erkannt wurde: durch Personenmuster oder direkt durch Aufguss. |
| `Gang.confirmed_at` | Erkennungszeit des ersten zugeordneten Aufgusses; zuvor nicht gesetzt. |

Die Phase **Saunagang** wird bereits bei der ersten Erkennung angezeigt, zunächst
gegebenenfalls vorläufig. Der spätere Aufguss bestätigt dasselbe Intervall. Er
überschreibt weder `started_at` noch `detected_at`. Die Bestätigungszeit wird aus
dem ersten Aufgussereignis abgeleitet. Fachliche Übergänge: [Gangmodell](gangmodell.md).

## Beispiel aus dem festgehaltenen Kandidaten

Für Gang 2 ergibt sich im Zwei-Sensor-Replay:

| Ereignis | Zeitpunkt am 17.09.2026, MESZ | Einordnung |
|---|---|---|
| Vorbereitendes Durchlüften bestätigt | 21:15:20 | Vorbereitung; noch kein Gang. |
| Türschließung erkannt | 21:15:51 | Möglicher zeitlicher Bezug für den folgenden Gang. |
| Personenmuster erkannt | 21:19:15 | Gang vorläufig aktiv; Beginn 21:15:51, angezeigte Dauer 3:24 Minuten. |
| Erster Aufguss erkannt | 21:22:52 | Derselbe Gang bestätigt; Dauer inzwischen 7:01 Minuten. |

Quelle der Messentscheidungen: [gemeinsames Replay](kandidat.md#7-ergebnisse-des-gemeinsamen-replay).
Die Einordnung als vorläufig beziehungsweise bestätigt ist die darauf angewandte
Ablaufregel, kein zusätzlicher unabhängig gemessener Personenzeitpunkt.

Bleibt die Personenfrüherkennung aus, wird der Gang um 21:22:52 unmittelbar
bestätigt angelegt. Dann sind `detected_at` und `confirmed_at` gleich; der
zugeordnete Beginn bleibt 21:15:51.

## Ereigniszeit und Herkunft

Am Eingang führt jedes Ereignis `effective_at` und `detected_at`. Ersteres ist
der vom Detektor zugeordnete Ereigniszeitpunkt, Letzteres dessen tatsächliche
Entscheidungszeit. Im eingefrorenen Kandidaten sind beide gleich. Ereignisse
werden in Erkennungsreihenfolge verarbeitet; Zeitstempel tragen eine Zeitzone
und werden intern nach UTC umgerechnet.

`start_source_event_id` verweist auf die konkret verwendete Schließung. Eine
neue Öffnung ersetzt einen noch ungenutzten Zeitbezug. Ein bereits angelegter
Gang behält dagegen seinen ursprünglichen Beginn auch bei kurzer Türbetätigung.
Eine beliebige frühere Schließung oder ein Zeitpunkt vor der Session wird nicht
als Ersatz gewählt.

Bei bekannt geschlossenem Türzustand ohne Schließungshistorie verwendet das
Modell `detected_at` als Beginn und kennzeichnet `start_basis=recognition_only`.
Das ist eine eingeschränkte Zeitzuordnung, keine rekonstruierte Schließung und
keine Entscheidung über den Wiederanlauf nach einem Neustart.

## Gegenwart und rückwirkende Darstellung

Die eigene Sessionansicht kann den vorläufigen Gang ab `started_at` darstellen
und nach dem Aufguss dasselbe Intervall als bestätigt kennzeichnen. Die
ursprüngliche Erkennungszeit und der damalige Bestätigungsstand bleiben im
Ereignisverlauf nachvollziehbar.

Heizentscheidungen wirken erst ab der tatsächlichen Erkennung. Eine zuvor
ausgeführte Abschaltung wird nicht rückwirkend verändert. Eine vorsorgliche
Heizbehandlung allein nach Türschließung wurde nicht vereinbart.

Die native HA-Zustandshistorie wird nicht umgeschrieben. Die gewünschte
historische Gangdarstellung liest deshalb die eigenen zugeordneten Intervalle.
Fachliche Dauern verwenden `started_at`; Heiz- und Schutzfristen verwenden die
tatsächlichen Schaltzeiten. Für noch offene Gangfristen ist die Zeitbasis
zusammen mit der jeweiligen Regel festzulegen.

## Abschlusszeit

Das bestehende Modell verwendet beim regulären Gangabschluss die Erkennungszeit
des bestätigten Durchlüftens. Eine Rückzuordnung des Gangendes zur Türöffnung
ist weiterhin offen. Die nachträgliche Startzuordnung entscheidet diese Frage nicht.

Referenz zur HA-Zustandszeit, in der Erstfassung am 18.09.2026 eingesehen:
https://www.home-assistant.io/docs/configuration/state_object/
