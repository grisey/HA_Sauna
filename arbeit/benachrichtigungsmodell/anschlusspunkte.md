# Anschlusspunkte im vorhandenen Projekt

Der Befund bezieht sich auf Commit
`3b31ccb9a1c9e6362d7c58acc9cef417a253117b`. Die genannten Symbole wurden im
Quelltext gelesen. Die rechte Spalte beschreibt eine mögliche spätere Änderung,
keine bereits implementierte Funktion und keine Reihenfolge von Arbeitsschritten.

| Bestehender Eingang oder Verbraucher | Beobachteter Stand | Anschluss einer Grundform |
| --- | --- | --- |
| `api.py`: `can_control`, `ControlView.post` | Die HTTP-Anfrage enthält `hass_user`; `set_operation` erhält nur den Schaltwert. | `ActorRef` aus dem authentifizierten Eingang bis zur zugeordneten Bedienung erhalten. Eine Benutzer-ID aus dem JSON-Körper wäre keine gleichwertige Quelle. |
| `switch.py`: `SaunaOperation`; `climate.py`: `SaunaThermostat` | Die Entitätsmethoden rufen Runtime beziehungsweise Settings ohne Benutzerkontext auf. | Den HA-Kontext am Entitätseingang erfassen und durch denselben fachlichen Pfad führen. |
| `number.py`: `async_set_native_value` | `_context.user_id` wird bereits für eine Rechteprüfung gelesen, aber nicht an `async_set_entity_parameter` übergeben. | Vorhandenen Kontext auch für die Zuordnung nutzbar machen. |
| `api.py`, `settings.py`: Parameter, Programm, Temperaturstufen, Betriebsart; `button.py`: Schutzquittierung | Weitere verändernde Eingänge existieren. Eine zentrale Definition für die Benachrichtigungsbeteiligung fehlt. | Jeden als Eingriff definierten Eingang berücksichtigen. Der Entwurf erklärt diese Bedienungen noch nicht pauschal zu Eingriffen. |
| `runtime.py`: `set_light_override`, `set_heater_override`, `finish_phase`, `finish_session_gap` | Die Laufzeit serialisiert die Bedienung; einige bereits erreichte Wahlen kehren ohne Änderung zurück. | Identität und fachliche Annahme der Bedienung an derselben Stelle zusammenbringen. Ein HTTP-Erfolg allein entscheidet noch nicht über die Beteiligung. |
| `device.py`: `physical_action`, `external_light_selection`; `runtime.py`: `_drain_device_inputs` | Physische Eingänge und externe Lichtänderungen gelangen über Geräteereignisse hinein; eigene Lichtechos werden bereits herausgefiltert. | Herkunft erhalten, vorhandene Echounterdrückung respektieren, fehlende Benutzeridentität ausdrücken. |
| `runtime.py`: `serialized`, `session`; `core/models.py`: `Session` | Die Laufzeit hat eine gemeinsame Sperre; die Sitzung besitzt eine eigene ID, aber keine Benutzer- oder Empfängermenge. | `SessionRecipients` an `(entry_id, session_id)` binden und Mengenänderungen mit den fachlichen Zustandsänderungen koordinieren. |
| `config_flow.py`: `device_schema`, `binding_schema`, `SaunaOptionsFlow` | Es existieren gefilterte Auswahlen und ein Optionsmenü. Änderungen der Anlagenkonfiguration sind bei bestehender Sitzung gesperrt. | Einen eigenen Zuordnungsbereich und eine gemeinsame Eignungsprüfung für Anzeige und Speicherung anschließen. Die bestehende Konfigurationssperre nicht beiläufig verändern. |
| `runtime.py`: `Configuration.from_options`, `as_options`; `__init__.py`: `async_options_updated` | Optionen werden ausdrücklich zugelassen, serialisiert und auf Reload beziehungsweise laufende Änderung geprüft. | Ein späterer Zuordnungswert benötigt vollständiges Lesen, Schreiben und einen kompatiblen Altbestand ohne diesen Wert. |
| `runtime.py`: `notify`, `_publish_consumer`, `_publish_controller_events`; `core/consumer_events.py` | `notify()` aktualisiert Log, Archiv und Abonnenten. Bestehende Consumer-Ereignisse betreffen vor allem Anwesenheit und Gänge. Es gibt keinen Companion-Versand. | Einen Transportadapter eindeutig benennen. Bestehende Ereignisse können spätere Erzeuger nutzen; der Grundformenentwurf bestimmt keine Versandereignisse. |
| `__init__.py`: `async_setup_entry`; `runtime.py`: `_close_once` | Beim Laden entsteht eine neue Runtime. Laufender Ofenbetrieb wird nach Neustart nicht wiederhergestellt. | Listener und Transportressourcen an die bestehende Lebensdauer binden. Keine Wiederherstellung von Empfängern aus einer anderen Sitzung unterstellen. |
| `archive.py`; `presentation.py`: `public_session`, `public_state`; `api.py`: Archiv und Export | Die Integration speichert und exportiert Sitzungs- und Konfigurationsdaten; öffentliche Projektionen entfernen ausgewählte interne Inhalte. | Vor Persistierung von Benutzer- oder Empfängerreferenzen alle Projektionen prüfen. Eine neue Eigenschaft in einer serialisierten Struktur kann sonst unbeabsichtigt in Ausgaben erscheinen. |

Die Grundformen können fachlich im Python-unabhängigen Sinne getrennt bleiben:
Die Mengenbildung braucht keine HA-Dienstaufrufe; der HA-Adapter braucht keine
zweite Saunasteuerung. Ob dafür neue Module oder bestehende Strukturen verwendet
werden, ist noch nicht festgelegt. Netzwerkversand darf die bestehende
Serialisierung der Saunasteuerung nicht durch Warten auf eine Antwort belegen.

## Bestehende Prüfansätze

Die vorhandenen Fixtures unter `tests/ha/` und `tests/integration/` bieten
Anschlüsse für Konfiguration, Rechte, Eingangskontext und Laufzeit. Die
[Prüfanleitung](../../tests/README.md) trennt bereits Komponenten-, HA-, Browser-
und Sitzungsprüfungen. Neue Prüfungen sollen diese Fixtures verwenden.

Für die hier vorliegenden Arbeitsunterlagen sind Fachkonsistenz, Quellenbezüge
und der unveränderte Programmstand relevant. Es wurde weder eine neue
Testarchitektur noch ein vollständiger Abnahmedurchlauf eingeführt.
