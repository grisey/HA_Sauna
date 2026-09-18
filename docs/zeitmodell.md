# Fachlicher Beginn und Erkennungszeit

## Regel

Bei Personen- oder Aufgusserkennung wird ein Gang angelegt, sofern noch keiner
aktiv ist. Sein fachlicher Beginn ist die zugeordnete Tuerschliessung der
aktuellen Episode innerhalb derselben Session. Der Zeitpunkt der Erkenntnis
wird separat gespeichert. Es wird keine beliebige alte Schliessung gesucht.

| Feld | Bedeutung |
|---|---|
| `effective_at` am Eingangsereignis | Vom Detektor gelieferter Ereigniszeitpunkt. Beim eingefrorenen Kandidaten gleich `detected_at`. |
| `detected_at` | Zeitpunkt, ab dem die Entscheidung tatsaechlich vorlag. |
| `Gang.started_at` | Der nachtraeglich zugeordnete fachliche Beginn. |
| `start_source_event_id` | Nachpruefbarer Verweis auf genau die verwendete Tuerschliessung. |
| `recognition_event_id` | Personen- oder Aufgussereignis, das die Erkennung ausloeste. |
| `start_basis` | `door_close` oder bei fehlender Historie `recognition_only`. |

Es werden Zeitzonen tragende Zeitstempel angenommen und intern nach UTC
normalisiert. Messreaktion, Detektionsentscheidung und mechanischer Kontakt
sind unterschiedliche Sachverhalte. Ohne zusaetzlichen Nachweis wird keine
mechanische Schliesszeit aus dem Temperaturminimum erfunden.

## Beispiel aus dem festgehaltenen Kandidaten

Gang 2: Tuerschliessung erkannt 21:15:51 MESZ, Personenmuster erkannt 21:19:15.
Ab 21:19:15 existiert der erkannte Gang; `started_at` lautet 21:15:51.
Die Daueranzeige beginnt bei 3 Minuten 24 Sekunden. Erkennt erst der Aufguss
um 21:22:52 den Gang, wird derselbe Anker benutzt; die angezeigte Dauer ist
dann 7 Minuten 1 Sekunde. Quelle: `kandidat.md`, gemeinsames Replay.

Die reale Heizbehandlung kann fruehestens ab Erkennung greifen. Eine vorher
bereits ausgefuehrte Abschaltung wird durch diese Zuordnung nicht rueckgaengig
oder nachtraeglich verhindert. Eine vorsorgliche Heizbehandlung zwischen
Tuerschliessung und Personenerkennung waere eine eigene, bisher nicht
vereinbarte Regel und wird nicht eingefuehrt.

## Fortsetzung und Abschluss

Ein bereits aktiver Gang behaelt ID und Beginn ueber eine kurze Tuerbetätigung
hinweg. Aufguesse bestaetigen beziehungsweise ergaenzen denselben Gang.
Bestaetigtes Durchlueften bei bereits erfasstem Aufguss schliesst genau einmal ab.
Der jetzige Fachkern setzt den Abschluss auf den Bestätigungszeitpunkt; eine
rueckwirkende Verschiebung des Endes ist nicht Gegenstand der Startzeitentscheidung.
Durchlueften im unbestaetigten Gang ist ausdrücklich noch offen.

Ein neuer Oeffnungsvorgang ersetzt den ungenutzten Startanker. Ein bestehender
Gang behaelt seinen bereits zugeordneten Anker. Fehlt bei bekannt geschlossenem
Tuerzustand eine Schliessungshistorie, wird der Erkennungszeitpunkt verwendet und
diese eingeschraenkte Zeitbasis gekennzeichnet. Keine Rueckdatierung ueber die
Sessiongrenze. Eine Wiederaufnahme nach Neustart benoetigt gesonderte gespeicherte
Ereignis-/Sessionzuordnung; der Fallback bestimmt keine Neustartpolitik.

## GUI, Historie und Timer

Die eigene Sessiondarstellung kann nach Erkennung das Intervall ab `started_at`
als Gang anzeigen. Die native HA-Zustandshistorie bleibt ein Protokoll realer
Zustandsaenderungen: ein heute gesetztes Attribut aendert keine schon erfassten
historischen `last_changed`-Werte. Fuer die gewuenschte historische Flaeche muss
die Anzeige die fachlichen Sessionintervalle statt nur den HA-Zustandsverlauf lesen.

Fachliche Dauern werden ab `started_at` berechnet. Aktor-/Schutzfristen verwenden
weiterhin die tatsaechlichen Heiz-/Schaltzeiten. Welche noch offenen Gangfristen
ab fachlichem Beginn und welche ab Erkennung laufen, wird separat festgelegt;
der Code startet noch keine solchen Timer.

Quelle zur HA-Zustandszeit, eingesehen am 18.09.2026:
https://www.home-assistant.io/docs/configuration/state_object/
