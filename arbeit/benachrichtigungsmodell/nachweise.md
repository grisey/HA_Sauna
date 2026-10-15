# Nachweise und Abschlusskriterien der Grundformen

## Aussageumfang

Am 15. Oktober 2026 wurden der lokale Projektstand und die unten genannten
öffentlichen HA-Quellen tatsächlich gelesen. Die Quellbefunde belegen vorhandene
Schnittstellen und konkrete Lücken im Projekt. Sie belegen keine bereits
funktionierende Benachrichtigung in HA Sauna. Ein erfolgreich ausgeführter
automatisierter Test würde diese Aussage nicht ersetzen.

Die bisherigen Nachweise sind Quellen- und Bestandsnachweise. Ein Zugriff auf
eine laufende HA-Instanz, ein Geräteempfang und eine Companion-Rückmeldung wurden
in diesem Auftrag nicht erhoben. Dafür wurden auch keine Geräte angenommen.

## Projektbestand

Ausgangscommit: `3b31ccb9a1c9e6362d7c58acc9cef417a253117b`.
Ausgangstree: `f1a6538dc025db5473fae67768864ceffc94307c`.
Der Arbeitsbaum war vor der Branchanlage sauber.

| Befund | Nachvollziehbarer Quellpfad | Bedeutung |
| --- | --- | --- |
| Benutzeridentität ist am HTTP-Eingang vorhanden und wird beim Steueraufruf nicht mitgegeben. | `api.py`, `can_control` ab Zeile 45 und `ControlView.post` ab Zeile 297; Aufruf Zeile 308. | Die selektive Auswahl kann noch nicht verlässlich auf dem Runtime-Aufruf beruhen. |
| Das Entitätsmodell besitzt bereits Zugriff auf HA-Benutzerkontext. | `number.py`, `async_set_native_value`, Zeilen 64–72. | Es gibt einen konkreten Herkunftsnachweis, der beim anschließenden Settings-Aufruf verloren geht. |
| Sitzungsidentität und gemeinsame Serialisierung sind vorhanden. | `core/models.py`, `Session` ab Zeile 230; `runtime.py`, `serialized` ab Zeile 630. | Akkumulation kann an den vorhandenen Sitzungsbegriff angeschlossen werden. |
| `notify()` ist kein Versand an die Companion-App. | `runtime.py`, `notify` ab Zeile 924. | Ein ähnlich benannter interner Mechanismus ist noch kein Funktionsnachweis. |

Weitere Eingänge und Verbraucher stehen in den
[Anschlusspunkten](anschlusspunkte.md). Alle Projektpfade in diesen Tabellen sind
relativ zu `custom_components/ha_sauna/`.

## Home Assistant Schnittstellen

Geprüft wurden die im Projekt dokumentierte Mindestversion **2026.9.0** und die
in CI verwendete Version **2026.10.0**. Das sind konkrete Vergleichsstände,
keine Beschränkung auf bestimmte Endgeräte. Die Quelldateien wurden öffentlich
heruntergeladen; URLs und SHA-256-Werte stehen in
[quellenbelege.json](quellenbelege.json). Die vollständigen Kopien bleiben lokal
unter `quellen/` und gehören nicht zum versionierten Paket.

| Tatsächlicher Quellbefund | Primärquelle | Grenze des Nachweises |
| --- | --- | --- |
| `webhook_id_from_device_id` löst eine HA-Geräteregister-ID auf; `get_notify_service` nutzt die registrierte Zielzuordnung. `supports_push` prüft Pushregistrierungsdaten. | [mobile_app/util.py, HA 2026.10.0](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/util.py) | Eine Pushregistrierung beweist weder Zustellung noch jede interaktive Clientfähigkeit. |
| Die Geräteaktion `notify` wird bei Pushfähigkeit angeboten. Der Versand löst das konkrete Ziel auf und transportiert auch `data`. | [mobile_app/device_action.py, HA 2026.10.0](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/device_action.py) | Die Integration stellt einen Anschluss bereit; eine tatsächliche Telefonantwort ist damit nicht beobachtet. Die Hilfsfunktionen sind versionsgebundene Integrationsdetails. |
| Es existieren sowohl `MobileAppNotifyEntity` als auch `MobileAppNotificationService`. Der normale Entitätsaufruf nimmt Nachricht und Titel entgegen; der Dienstpfad übernimmt Zusatzdaten. Einige Fehler des entfernten Versands werden protokolliert statt an den Aufrufer weitergereicht. | [mobile_app/notify.py, HA 2026.10.0](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/notify.py) | Die Auswahl einer Notify-Entität bestimmt noch keinen geeigneten Versandweg für interaktive Nutzdaten. Rückkehr des Dienstaufrufs ist kein Empfangsbeleg. |
| Der Companion-Ereigniseingang versieht Ereignisse mit `registration_context`; dieser wird aus der Benutzer-ID der Registrierung gebildet. | [mobile_app/webhook.py](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/webhook.py), [mobile_app/helpers.py](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/mobile_app/helpers.py) | Der Kontext benennt den registrierten HA-Benutzer. Ein beliebiges Ereignisfeld ersetzt diese Herkunft nicht; eine Empfangszuordnung allein erteilt keine Bedienrechte. |
| Die Companion-Dokumentation beschreibt Zusatzdaten für interaktive Nachrichten, den Ereignistyp `mobile_app_notification_action` und eindeutige Antwortzuordnung. | [Actionable notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/) | Dies belegt die allgemeine Protokollmöglichkeit und bestimmt keine Produktinteraktion von HA Sauna. |

`util.py`, `helpers.py` und `notify.py` sind in den heruntergeladenen beiden
Versionen bytegleich. `device_action.py` und `webhook.py` unterscheiden sich;
die hier beschriebenen Anschlüsse sind in beiden Ständen vorhanden. Die
Prüfsummen ermöglichen eine unabhängige Kontrolle der gelesenen Quellen.

## Erreichbare Abschlusskriterien

Die Vorbereitung ist abgeschlossen, wenn die folgenden Unterlagen vorliegen
und auf ihren Aussageumfang geprüft sind:

| Kriterium | Positiver Abschluss dieser Vorbereitung |
| --- | --- |
| Anforderungen abgebildet | Jede der drei Anforderungen ist einer Grundform zugeordnet; die jüngste Begrenzung auf Grundformen ist ausdrücklich berücksichtigt. |
| Umsetzung anschließbar | Vorhandene Eingänge, gemeinsame Laufzeit, Konfiguration und Datenverbraucher sind mit konkreten Symbolen benannt. |
| Evidenz prüfbar | Repositorybasis und öffentliche Quellstände sind angegeben, tatsächliche Befunde von künftiger Funktion unterschieden. |
| Entscheidungen sichtbar | Offene fachliche Entscheidungen enthalten ihre Auswirkung, ohne erfundene Aktionen oder Geräteannahmen. |
| Branch abgegrenzt | Nur dieses Vorbereitungspaket ist geändert; Programmcode, bestehende Tests und Produktdokumentation entsprechen dem Ausgangsstand. |

Der spätere Nachweis einer **implementierten einzelnen Grundform** benötigt ein
beobachtetes Ergebnis. Er muss keinen vollständigen Saunabetrieb durchlaufen:

| Baustein | Positiv abschließbarer Funktionsnachweis |
| --- | --- |
| Identität | Ein tatsächlich authentifizierter HA-Eingang liefert dieselbe Benutzer-ID an den Baustein; ein Eingang ohne Benutzer wird sichtbar als unbekannt behandelt. |
| Eignung und Zuordnung | Die reale HA-Konfigurationsansicht bietet ein anhand seines Fähigkeitsvertrags geeignetes Ziel an, speichert dessen Benutzerzuordnung und zeigt sie nach erneutem Öffnen unverändert. Ein ungeeignetes Ziel erhält bei direkter Eingabe eine verständliche Ablehnung. |
| Akkumulation | Über tatsächliche HA-Eingänge werden unterschiedlich zugeordnete Benutzer in derselben Sitzungsidentität erfasst. Beobachtete Zielmengen entsprechen Vereinigung, Wiederholung und Trennung einer anderen Sitzung. Die Anzahl und Art der Endgeräte ist Bestandteil des Nachweisdatensatzes, keine Vorgabe des Modells. |
| Austausch | Eine technische Referenznachricht wird auf einem tatsächlich verwendeten kompatiblen Companion-Client sichtbar. Eine zugehörige technische Rückmeldung erreicht den Adapter mit passender Nachrichtenreferenz und nachgewiesener Herkunft. Das ist eine isolierte Protokollprobe, keine einzubauende Produktinteraktion. |

Für einen solchen Beleg werden geprüfter Commit beziehungsweise Dateihashes,
HA- und Clientversion, Eingang, Vorbedingungen, erwartetes Ergebnis, beobachtetes
Ergebnis und Artefaktverweis festgehalten. Private Identitäten und Zugangsdaten
bleiben im geschützten Arbeitsbereich. Ein HA-Protokoll kann Übergabe und
Rückkanal belegen; sichtbarer Empfang braucht zusätzlich eine tatsächliche
Endgerätebeobachtung. Die Reichweite des Befunds entspricht den untersuchten
Schnittstellen und Clients, ohne aus einem Beispiel allgemeine Kompatibilität
zu behaupten.

Tests unterstützen die Prüfung der Mengenregeln, Zuordnungsfehler und
Schnittstellen. Ihre Ergebnisse bleiben als Testbelege gekennzeichnet. Nach
Vorliegen der beschriebenen Beobachtungen ist der jeweilige Bausteinnachweis
abgeschlossen; ein unbegrenzter Dauerbetrieb ist kein Abschlusskriterium.

## Ablage

Arbeitsunterlagen liegen entsprechend `AGENTS.md` unter `arbeit/`. Für den
ausdrücklich gewünschten separaten Branch wird ausschließlich dieses kleine,
nicht private Vorbereitungspaket gezielt versioniert. Die allgemeine
Ignore-Regel, historische Nachweise und lokale Quellenkopien bleiben erhalten.
Es gibt keine Produktcodeänderung, Versionsänderung oder Bereitstellung.

## Ergebnis der Vorbereitungsprüfung

Die fünf Kriterien der Vorbereitung sind erfüllt. Die lokalen Dokumentverweise
wurden auf vorhandene Ziele geprüft; die zwölf Projektdateien und zehn
öffentlichen Quelldateien stimmen mit den SHA-256-Werten im Quellenmanifest
überein. Der Git-Vergleich des vorhandenen Bestands gegen den Ausgangscommit
außerhalb dieses Pakets endete vollständig mit Exitcode 0 und ohne Differenz.
Dies ist eine Dokument- und Bestandsprüfung, kein Laufzeit- oder Gerätetest.

Der unveränderte Programmbaum `custom_components` hat die Git-Tree-ID
`cdce9a66c644cef1adac277fcf38e078eb08f171`; der vorhandene Testbaum `tests` hat
`aed9eee9988d7dfd3076e917a73a2e0f9a4c72a9`. Damit bleibt überprüfbar, dass die
Vorbereitung selbst noch keine Benachrichtigungsfunktion implementiert.
