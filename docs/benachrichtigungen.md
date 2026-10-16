# Benachrichtigungen

## Zuordnung und Versand

Die [Integrationskonfiguration](einrichtung.md#benachrichtigungsempfänger-zuordnen)
verknüpft Home-Assistant-Benutzer mit angebotenen registrierten Zielen.
**Nur Nachricht** kennzeichnet den einfachen Versand über eine Notify-Entität.
Er benötigt keine Antwortfunktion. **Companion: Daten und Antworten** bezeichnet
die vorhandene Mobile-App-Geräteaktion mit zusätzlichem Daten- und Antwortpfad.
Bei jeder Speicherung werden die Ziele erneut auf ihre Versandfähigkeit geprüft.
Die Registrierung und eine erfolgreiche Übergabe an Home Assistant belegen weder
Erreichbarkeit noch Anzeige oder Empfang am Endgerät.

Die Integration stellt expliziten Versand und auswertbare Antworten bereit.
Nachrichtentexte, angebotene Aktionen und deren fachliche Bedeutung sind keine
Folge der Empfängerzuordnung. Eine Antwort ist keine automatische
Saunabedienung und erteilt keine Steuerberechtigung.

## Native Companion-Daten und Voraussetzungen

Der Companion-Pfad reicht das offene native `data`-Objekt weiter. Die Integration
führt keinen eigenen Katalog erlaubter App-Funktionen. Native Aktionskennungen
und Eingabedaten behalten ihre Bedeutung. Einfache Notify-Entitäten unterstützen
diesen Datenpfad nicht; ein dafür ungeeignetes Ziel muss als solches erkennbar sein.

Die Companion-App benötigt eine eingerichtete Home-Assistant-Verbindung und
Benachrichtigungsberechtigungen des Betriebssystems. Unterstützte Datenfelder,
Anzeige und Antworten hängen vom jeweiligen Betriebssystem, der installierten
App-Version sowie den für die verwendete Funktion erforderlichen Berechtigungen
ab. Einzelne Funktionen haben zusätzliche Voraussetzungen, beispielsweise
kritische Benachrichtigungen unter iOS oder die Android-Benachrichtigungskanäle.
Die jeweils gültigen Voraussetzungen stehen in der
[offiziellen Companion-Dokumentation](https://companion.home-assistant.io/docs/notifications/notifications-basic/)
und bei den
[aktionsfähigen Benachrichtigungen](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).
Eine vorhandene Geräteaktion bestätigt keine bestimmte Client-Funktion.

Die Auswahl- und Speicherlogik gehört zur Integration. Die konkrete Zuordnung
von Benutzern zu eigenen Geräten, App-Einrichtung und Geräteberechtigungen sind
Konfiguration der jeweiligen Installation. Beliebige Dienstnamen oder aus
Gerätenamen konstruierte Versandziele sind nicht Teil der Auswahl.

Technische Grundlage sind die
[Notify-Entität](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/notify/__init__.py)
und die bestehende
[Mobile-App-Geräteaktion](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/device_action.py).

## Expliziter Aufruf und Ergebnis

Der Home-Assistant-Dienst `ha_sauna.send_notification` versendet ausschließlich
bei einem expliziten Aufruf. Er benötigt `entry_id` der geladenen Sauna-Instanz
und `message`; `title` und das native `data`-Objekt sind optional. Einfache
Notify-Ziele erhalten kein natives `data`; ein nicht unterstützter Titel oder
Datenpfad ergibt für dieses Ziel einen Fehler. Andere Ziele werden unabhängig
weiter bearbeitet.

`audience: session` ist die Vorgabe und verwendet die akkumulierten Ziele der
aktuellen Sitzung. `audience: users` verwendet stattdessen die expliziten
Zuordnungen der in `user_ids` genannten Home-Assistant-Benutzer und ist auch
ohne Sitzung möglich. `user_ids` ist nur bei `audience: users` zulässig und darf
dann nicht leer sein. Benutzergebundene Aufrufe benötigen Steuerberechtigung
für die Betriebsentität der gewählten Sauna.

Die optionale Dienstantwort enthält `correlation_id` und `handoffs`. Jeder
Versandversuch enthält `target`, `message_id`, `accepted`, `evidence` und
`error`. Nicht mehr auflösbare zugeordnete Ziele werden bei expliziter
Benutzerauswahl mit `reference` und Fehlernachweis ausgewiesen. `accepted: true`
bedeutet abgeschlossene Übergabe an Home Assistant; eine Empfangsbestätigung
ist damit nicht verbunden. Eine leere Zielliste enthält keine Versandversuche.

`ha_sauna.forget_notification` entfernt mit `entry_id` und `message_id` eine
flüchtige Antwortzuordnung der gewählten Instanz. Die Nachrichtenkennung stammt
aus dem betreffenden `handoffs`-Ergebnis des Sendedienstes. Der Aufruf verwendet
dieselbe Steuerberechtigung wie der Versand und ist auch ohne laufende Sitzung
möglich. Er löscht keine Nachricht auf dem Endgerät. Ohne Sitzung bleibt eine
Antwortzuordnung bis zu diesem expliziten Aufruf oder dem Entladen erhalten;
eine erste Antwort entfernt sie nicht automatisch.

## Sitzung und Antworten

Erfolgreiche Änderungen einer laufenden Sitzung, einschließlich Temperatur,
Programm, Ofen und Licht, ergänzen die zugeordneten Ziele des authentifizierten
Bedienenden. Wirkungslose und abgewiesene Aufrufe ergänzen keine Ziele. Fehlende
Benutzeridentität bleibt unbekannt. Die Zielmenge wächst innerhalb derselben
Sitzung; eine spätere Änderung der Zuordnung entfernt bereits aufgenommene Ziele
nicht rückwirkend. Gewöhnliches Ausschalten beendet diese Sammlung nicht.
Sitzungsende und Entladen bereinigen die flüchtige Sammlung und ihre
Antwortzuordnung.

Antworten werden als `ha_sauna_notification_reply` auf dem Home-Assistant-Eventbus
bereitgestellt. Das Event enthält `entry_id`, `context_id`, `time_fired`,
`actor_user_id`, `source`, `message_id`, `correlation_id`, `session_id`, `target`,
`ambiguous`, `handoff_accepted` und die unveränderten nativen Antwortdaten unter `data`. Die
Benutzeridentität stammt aus dem ursprünglichen HA-Eventkontext. `source` kann
unbekannt bleiben. Ein zugeordnetes `target` bezeichnet das Versandziel und
beweist nicht die Herkunft der Antwort. Bei instanzübergreifend mehrdeutiger Zuordnung wird genau ein Event mit leeren
Instanz-, Nachrichten- und Zielangaben und `ambiguous: true` ausgegeben. Eine
frühe Antwort während des laufenden Versandaufrufs kann bereits zugeordnet
werden; `handoff_accepted` bleibt bis dessen Abschluss unbekannt. Antworten lösen
keine Saunabedienung aus.

Alle geladenen Instanzen verwenden einen gemeinsamen Korrelationsowner für
native Aktionskennungen. Der Eingang bewahrt die native Antwort als
`ReplyEnvelope`; die resultierende `ReplyAssociation` verbindet sie gegebenenfalls
mit der gesendeten Nachricht. Unbekannte Antwort-, Nachrichten-, Instanz- und
Quellidentitäten bleiben im Eingang unbekannt. Die HA-Kontextkennung und die
Ereigniszeit sind eigene Metadaten und keine Antwortidentität. Mehrere native
Ereignisse mit derselben Kontextkennung bleiben einzeln beobachtbar. Die
Eventausgabe projiziert die gemeinsame Zuordnung; sie führt keinen eigenen
Zuordnungsbestand.
