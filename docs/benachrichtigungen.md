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
Folge der Empfängerzuordnung. Eine Antwort erteilt keine Steuerberechtigung.
Aus eigenem Anlass sendet die Integration die Nachrichten der
[Anlässe der Sauna](#anlässe-der-sauna) und einer ausdrücklich gestarteten
[Kalibrierung](kalibrierung.md).

## Anlässe der Sauna

In der Sauna trägt niemand ein Gerät; die Kalibrierung ist die Ausnahme.
Nachrichten an die Teilnehmer einer Sitzung gehen deshalb vor dem ersten Gang,
in den Pausen und nach der Sitzung. Während eines wirkenden Gangs geht an die
Teilnehmer nichts; eine fällige Erinnerung zur Zeitschaltuhr folgt nach dem
Gang. Die Rückfrage zum Gangende und Sicherheitsmeldungen sind davon
ausgenommen.

Während eines [Kalibrierlaufs](kalibrierung.md), im Grundablauf wie in einem
Zusatzmodul, gehen keine Anlässe außer den Sicherheitsmeldungen; Bereit, Gänge
und ein Sitzungsende in dieser Zeit werden nicht nachgemeldet. Die
Zeitschaltuhr ist Teil des Kalibrierablaufs
([Grundablauf](kalibrierung.md#grundablauf)); ihre Erinnerung vor Ablauf kommt
während eines Laufs als Nachricht des Laufs.

| Anlass | Auslöser | Text | Antwort | Empfänger |
| --- | --- | --- | --- | --- |
| Testnachricht | **Testnachricht senden** unter **Einstellungen → Benachrichtigungen** | „Testnachricht der Sauna“ | **Empfangen** | eigene Ziele des Benutzers |
| Zeitschaltuhr beim Start | Sitzungsbeginn, über die Oberfläche, Taster oder Schalter; ohne Antwort erneut nach der Wiederholzeit, bis zur Antwort, zu Betrieb-AUS oder zum Sitzungsende | „Sauna gestartet – Zeitschaltuhr aufziehen“ | **Uhr ist gestellt** | Ziele des startenden Benutzers; ohne bekannten Benutzer die anwesenden Geräte; ohne anwesendes Gerät alle Zugeordneten |
| Zeitschaltuhr läuft ab | gezählte Ofenzeit seit der letzten Bestätigung erreicht Laufzeit minus Vorlauf | „Zeitschaltuhr läuft in etwa 15 min ab“ | **Uhr ist gestellt** | wie beim Start |
| Bereit | Übergang in Bereit, vor dem ersten und nach jedem Gang | „Sauna bereit, 85 °C“, „Bereit für Gang 2, 88 °C“ | – | Teilnehmer |
| Nächster Gang voraussichtlich | Temperatur steigt nach dem Austritt wieder gleichmäßig, die Dauer bis Bereit ist abschätzbar und seit dem Tiefpunkt ist mindestens so viel Zeit vergangen, wie bis Bereit noch fehlt; ebenso beim Aufheizen vor dem ersten Gang; einmal je Pause | „Gang 2 möglich ab etwa 20:41 (in 23 min), 88 °C“ | – | Teilnehmer |
| Rückfrage Gangende | Präsenzsensor meldet nach einer Tür im Gang Abwesenheit, der Gang läuft nach dem Türschluss weiter; einmal je Gang | „Gang 1 läuft laut Sauna noch – bist du draußen?“ | **Gang beenden** | Teilnehmer |
| Sauna läuft ohne Nutzung | im Bereit zwei Abschaltungen an der oberen Regeltemperatur ohne Gang und ohne Anwesenheit seit Bereit bzw. seit dem letzten Gangende | „Sauna läuft seit 19:12 ohne Nutzung“ | **Ausschalten** | Teilnehmer |
| Sauna aus | Sitzungsende | „Sauna aus · Ofen aus bestätigt“ mit Gängen, Aufgüssen, Saunazeit, Energie und Kosten | – | Teilnehmer |
| Sicherheit | Ofenrückmeldung „an“ bei Befehl „aus“ über die Störungsbestätigungszeit; obere Messung fällt aus, während der Ofen heizt; obere Temperatur ab der Übertemperaturgrenze | „Achtung: …“ | – | alle Zugeordneten, auch ohne Sitzung |

Teilnehmer sind die [Sitzungsempfänger](#sitzung-und-antworten). Beginnt eine
Sitzung ohne bekannten Benutzer, etwa per Taster oder Schalter, gehen die
Nachrichten an die Teilnehmer zusätzlich an die anwesenden Geräte, ohne
anwesendes Gerät an alle Zugeordneten. Bedient danach ein Benutzer über die
Oberfläche, kommen seine Ziele wie üblich hinzu. Bereit folgt
der [Bereitschaftsregel](betrieb.md#aufheizen-und-bereitschaft) der Betriebsart;
im manuellen Modus gilt das gespeicherte Bereit. Die Zusammenfassung „Sauna aus“
verwendet die [Kennzahlen](kennzahlen.md) der Sitzung; ohne auswertbare
Sitzung bleibt es bei der ersten Zeile. „Ofen aus bestätigt“ setzt die
Ofenrückmeldung „aus“ voraus.

Jede Sicherheitsmeldung geht einmal je Ereignis: erneut erst, nachdem der
Zustand geendet hat, bei der Übertemperatur nach Unterschreiten der Grenze bei
ausgeschaltetem Ofen. Die Übertemperaturgrenze meldet nur und schaltet nichts ab.

### Anwesenheit

Ein Ziel gilt als anwesend, wenn die Standortentität (`device_tracker`) der
Companion-App auf demselben Home-Assistant-Gerät „home“ meldet. Ein Ziel ohne
solche Standortentität gilt als nicht anwesend. Die Anwesenheit wird bei jedem
Versand neu gelesen.

### Zeitschaltuhr

Die mechanische Zeitschaltuhr im Stromkreis des Ofens läuft nur, solange der
Ofen tatsächlich an ist. Gezählt wird deshalb die gebuchte Heizzeit aus der
Ofenrückmeldung, wie bei der [Heizzeit](betrieb.md#heizzeit-und-energie), ab der
letzten Antwort **Uhr ist gestellt**, auch auf den Schritt der
[Kalibrierung](kalibrierung.md#grundablauf). Ohne Bestätigung beim Start gibt
es keine Zählung und keine Erinnerung. Eine Antwort auf die Erinnerung beginnt
die Zählung neu.

### Nächster Gang voraussichtlich

Betrachtet wird die obere Temperatur ab dem Tiefpunkt seit dem Austrittsschluss,
beim Aufheizen seit dem Heizbeginn; ein weiterer Türschluss beginnt den
Abschnitt neu. Voraussetzungen sind geschlossene Tür und laufende Heizung laut
Rückmeldung. Zwei Erklärungen werden verglichen:

- **Erholung oder Krümmung:** eine einzige Kurve über den ganzen Abschnitt, eine
  abklingende Erholung mit bis zu zwei Zeitkonstanten bis zur Länge des
  Abschnitts oder eine einzelne abklingende oder beschleunigende Kurve.
- **Wieder linear:** bis zu einem unbekannten Zeitpunkt eine solche Kurve, danach
  eine Gerade.

Die Schätzung gilt, sobald „wieder linear“ den Abschnitt mindestens 150-mal
besser erklärt (Bayes-Faktor mit der effektiven Zahl unabhängiger Messwerte) und
der Anstieg der Geraden mit 150 : 1 positiv ist. Uhrzeit und Dauer sind der
Zeitpunkt, an dem die Gerade den aktuellen Sollwert erreicht. Gesendet wird erst,
wenn seit dem Tiefpunkt, beim Aufheizen seit dem Heizbeginn, mindestens so viel
Zeit vergangen ist, wie bis zu diesem Zeitpunkt noch fehlt; bis dahin wird die
Schätzung mit jedem Messwert erneuert. Bereit selbst meldet der Anlass
**Bereit**.

### Einstellungen

**Einstellungen → Benachrichtigungen** schaltet jeden Anlass einzeln ein und aus
und führt die Werte:

| Wert | Vorgabe | Bedeutung |
| --- | --- | --- |
| Laufzeit der Zeitschaltuhr | 240 min | Ofenzeit bis zum Ablauf der Uhr |
| Erinnerung vor Ablauf | 15 min | Vorlauf der Erinnerung |
| Wiederholung ohne Antwort | 10 min | Abstand der Wiederholungen beim Start |
| Übertemperatur | 105 °C | Grenze der Sicherheitsmeldung |

Änderungen wirken sofort, auch während einer Sitzung, und benötigen
Administratorrechte. Weicht ein gespeicherter Wert oder Anlass von der Vorgabe ab,
steht darunter **Abweichend von der Werkseinstellung**; einen gespeicherten
Standard gibt es für diese Einstellungen nicht
([Werkseinstellungen](parameter.md#werkseinstellungen)). Die Testnachricht steht allen Benutzern mit
Steuerberechtigung offen; angezeigt werden Versand, Übergabe an Home Assistant
und die Antwort mit Uhrzeit und Benutzer.

### Antworten mit Wirkung

**Gang beenden** wirkt wie die Taste [Gang beenden](bedienung.md#gang-von-hand-beenden),
**Ausschalten** wie **Ausschalten** auf der Steuerungsseite. Beide wirken nur mit
der Steuerberechtigung des antwortenden Benutzers aus dem Ereigniskontext.
Läuft der Gang nicht mehr, ist der Betrieb schon aus oder ist der Gang während
einer Kalibrierung gesperrt, bleibt die Antwort wirkungslos. **Uhr ist gestellt**
und **Empfangen** bedienen die Sauna nicht.

### Speicherung

Jede Nachricht mit Empfängerwahl und Übergabe sowie jede Antwort mit Ergebnis
wird im Archiv der Sitzung gespeichert. **Details → Steuerung** zeigt sie unter
**Benachrichtigungen** und im Protokoll, etwa „Antwort „Uhr ist gestellt“ 18:12:
Zeitschaltuhr bestätigt“. Nachrichten ohne Sitzung, etwa eine Testnachricht oder
eine Sicherheitsmeldung außerhalb einer Sitzung, werden nicht archiviert; den
Stand der Testnachricht zeigen die Einstellungen.

## Native Companion-Daten und Voraussetzungen

Der Companion-Pfad reicht das offene native `data`-Objekt weiter. Die Integration
führt keinen eigenen Katalog erlaubter App-Funktionen. Native Aktionskennungen
in `action` und dem unter iOS verwendeten `identifier` sowie Eingabedaten bleiben
unverändert. Auch `URI` kann eine native Antwortkennung sein; eine Antwort setzt
ein tatsächlich eingehendes natives Ereignis voraus. Einfache Notify-Entitäten
unterstützen diesen Datenpfad nicht; ein dafür ungeeignetes Ziel muss als solches
erkennbar sein.

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

Ziele, Sitzungsbezug und Nachrichteninhalt werden beim Aufruf für den Versand
festgelegt. Während einer laufenden Übergabe bleiben Bedienänderungen möglich.

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
Antwortzuordnung. Nach Sitzungsende können Nachrichten an weitere bereits
ausgewählte Ziele noch an Home Assistant übergeben werden; dabei entstehen keine
neuen Antwortzuordnungen für die beendete Sitzung. Entladen verhindert noch
nicht begonnene Übergaben an Home Assistant. Eine bereits laufende externe
Übergabe kann abschließen.

Antworten werden als `ha_sauna_notification_reply` auf dem Home-Assistant-Eventbus
bereitgestellt. Das Event enthält `entry_id`, `context_id`, `time_fired`,
`actor_user_id`, `source`, `message_id`, `correlation_id`, `session_id`, `target`,
`ambiguous`, `handoff_accepted` und die unveränderten nativen Antwortdaten unter `data`. Die
Benutzeridentität stammt aus dem ursprünglichen HA-Eventkontext. `source` kann
unbekannt bleiben. Ein zugeordnetes `target` bezeichnet das Versandziel und
beweist nicht die Herkunft der Antwort. Bei instanzübergreifend mehrdeutiger Zuordnung wird genau ein Event mit leeren
Instanz-, Nachrichten- und Zielangaben und `ambiguous: true` ausgegeben. Eine
frühe Antwort während des laufenden Versandaufrufs kann bereits zugeordnet
werden; `handoff_accepted` bleibt bis dessen Abschluss unbekannt. Eine Antwort
mit einer Aktion der [Kalibrierung](kalibrierung.md) oder eines
[Anlasses der Sauna](#anlässe-der-sauna) geht zusätzlich an dessen Ablauf und
erscheint ebenfalls als Event. Nur **Gang beenden** und **Ausschalten** bedienen
die Sauna ([Antworten mit Wirkung](#antworten-mit-wirkung)).

Alle geladenen Instanzen verwenden einen gemeinsamen Korrelationsowner für
native Aktionskennungen. Der Eingang bewahrt die native Antwort als
`ReplyEnvelope`; die resultierende `ReplyAssociation` verbindet sie gegebenenfalls
mit der gesendeten Nachricht. Unbekannte Antwort-, Nachrichten-, Instanz- und
Quellidentitäten bleiben im Eingang unbekannt. Die HA-Kontextkennung und die
Ereigniszeit sind eigene Metadaten und keine Antwortidentität. Mehrere native
Ereignisse mit derselben Kontextkennung bleiben einzeln beobachtbar. Die
Eventausgabe projiziert die gemeinsame Zuordnung; sie führt keinen eigenen
Zuordnungsbestand.
