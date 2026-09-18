# Gangmodell: Vorbereitung, vorläufige Erkennung und Bestätigung

Stand: 18.09.2026. Diese Fassung präzisiert die Gangzuordnung der Erstfassung.
Die Messverfahren und Prüfwerte des festgehaltenen [Kandidaten](kandidat.md)
bleiben unverändert. Gegenstand ist die Bedeutung seiner Ereignisse im Ablauf.

## 1. Begriffe und Zuständigkeit

Die **Session** umfasst den gesamten Saunabetrieb. Ein **Saunagang** ist ein
Nutzungsabschnitt innerhalb der Session. Ein tatsächlicher Gang beinhaltet
nach Nutzerangabe einen Aufguss.

Die Sensoren liefern Hinweise auf diesen Ablauf. Deshalb unterscheiden wir
zwischen **vorläufig erkanntem Gang** und **durch Aufguss bestätigtem Gang**.
Beides ist derselbe Gang mit derselben ID und Startzeit, nicht zwei aufeinander
folgende Saunagänge.

Eine **Durchlüftungsbestätigung** bestätigt nur die Einordnung einer
Türöffnungsepisode. Eine **Gangbestätigung** entsteht ausschließlich durch einen
erkannten Aufguss. Auch ein starkes Personenmuster bleibt eine Vorstufe.

## 2. Ablauf und Anzeige

| Erkenntnis | Verarbeitung | Vorgesehene Anzeige |
|---|---|---|
| Durchlüften erkannt und Tür anschließend geschlossen | Vorbereitung und zugehörige Schließung festhalten. Es besteht noch kein Gang. | Noch keine Anzeige als Saunagang. |
| Personenmuster erkannt | Einen vorläufigen Gang anlegen; Beginn der zugehörigen Türschließung zuordnen. | **Saunagang**, Bestätigungsstand **vorläufig**; Dauer ab zugeordnetem Beginn. |
| Erster Aufguss erkannt | Den bestehenden Gang bestätigen. ID, Beginn und erste Erkennungszeit bleiben erhalten. | **Saunagang**, Bestätigungsstand **bestätigt**. |
| Aufguss ohne vorherige Personenfrüherkennung | Den Gang unmittelbar bestätigt anlegen; Beginn derselben Schließungsepisode zuordnen. | **Saunagang**, Bestätigungsstand **bestätigt**. |
| Weitere Aufgüsse | Ereignisse demselben Gang zuordnen. | Derselbe bestätigte Gang. |

Die Phase lautet damit bereits ab Personenfrüherkennung **Saunagang**. Der
Bestätigungsstand ist eine zusätzliche, aus den Belegen abgeleitete Eigenschaft.
Er ist kein unabhängig bedienbarer Phasenschalter. Die endgültige Bestätigung
betrifft den ganzen zugeordneten Gang, setzt dessen Laufzeit aber nicht neu an.

## 3. Rolle der vorbereitenden Durchlüftung

Durchlüften allein belegt keine Personenanwesenheit. Der zuletzt abgeschlossenen
Lüftungsepisode wird erst zusammen mit dem anschließenden Personenmuster Bedeutung
für einen Gang gegeben. Ihre Ereignisreferenz bleibt am angelegten Gang erhalten.

Die im Kandidaten geprüfte Zulassung bleibt bestehen:

- **Schwaches Personenmuster:** benötigt vorheriges bestätigtes Durchlüften und
  anschließende Türschließung.
- **Starkes Personenmuster:** kann ohne diesen Zusatzkontext einen vorläufigen Gang
  auslösen. Es bestätigt den Gang dennoch nicht endgültig.
- **Aufguss:** bestätigt unabhängig von Personenfrüherkennung und Vorbereitung.

Eine neue Öffnung ersetzt den noch ungenutzten Vorbereitungskontext. Der Beginn
und die Herkunft eines bereits angelegten Gangs werden dadurch nicht verändert.
Es wird keine neue Wartefrist und kein zusätzliches Vorbereitungszeitfenster eingeführt.

## 4. Heizbehandlung, Fortsetzung und Abschluss

Bereits der **vorläufige Gang** erhält ab seiner Erkennung die vereinbarte
Gang-Heizbehandlung: reguläre Hysterese- und betriebliche Ablaufabschaltungen
werden unterdrückt. Der Aufguss bestätigt den Gang; er ist keine zusätzliche
Voraussetzung, die den Beginn dieser Heizbehandlung verzögert. Übergeordnete
Schutzabschaltung und ausdrückliches Ausschalten bleiben getrennte Eingriffe.
Der vorliegende Code führt keine Heizbefehle aus.

Eine Türöffnung allein beendet weder einen vorläufigen noch einen bestätigten
Gang. Kurze Türbetätigung erhält ID, Startzeit, Bestätigungsstand und Aufgüsse.
Sie besagt nicht, wie viele Personen die Sauna verlassen haben.

**Erst bestätigtes Durchlüften nach einem zugeordneten Aufguss schließt den Gang
regulär ab.** Nur dieser Abschluss erhöht die Zahl abgeschlossener Gänge. Ein
vorläufiger Gang und die spätere Aufgussbestätigung erhöhen diesen Zähler nicht.

Noch offen ist, wann und wie ein vorläufiger Gang ohne nachfolgenden Aufguss
aufgehoben wird, insbesondere bei erneutem Durchlüften oder Fristablauf. Hier
wird weder die alte Zwölf-Minuten-Frist übernommen noch automatisch ein
regulärer Abschluss oder ein Nachlauf ausgelöst. Im Offline-Modell kennzeichnet
`UnresolvedTransition` diesen noch nicht definierten Übergang.

## 5. Datenmodell

`Timeline.active` enthält den laufenden Gang, vorläufig oder bestätigt.
`Gang.confirmation` wird ausschließlich aus den zugeordneten Aufgussereignissen
abgeleitet. Auch `confirmed_at` und `confirmation_event_id` stammen aus dem
ersten Aufguss; sie sind keine zusätzlichen schreibbaren Wahrheiten.

Vorbereitung, Personenmuster und Aufguss bleiben verschiedene Belege:

| Bezug | Feld |
|---|---|
| Vorbereitendes Durchlüften, soweit vorhanden | `preparation_event_id` |
| Für den Beginn verwendete Türschließung | `start_source_event_id` |
| Auslöser der ersten, gegebenenfalls nur vorläufigen Erkennung | `recognition_event_id`, `recognition_kind` |
| Erster bestätigender Aufguss | `confirmation_event_id`, `confirmed_at` |
| Weitere Aufgüsse | `infusion_events` |

Eine erfolgreich übergebene Durchlüftungsbestätigung bleibt bis zur Schließung
in `open_ventilation`, danach in `preparation`. Die nächste Öffnung ersetzt nur
diesen ungenutzten Kontext. `Gang.preparation_event_id` bleibt unverändert.

Die Gegenwart und die rückwirkend zugeordnete Dauer verwenden unterschiedliche
Zeitangaben; siehe [Zeitmodell](zeitmodell.md). Die Speichertechnik dafür wird
im eigenen [Speicherblock](speicherung.md) entschieden.
