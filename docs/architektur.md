# Architektur und Implementierungsstand

## Vorhanden

| Bereich | Implementiert |
|---|---|
| `candidate/` | Eingefrorenes Offline-Replay und Parameterschnappschuss. |
| `custom_components/ha_sauna/core/timeline.py` | Vorbereitungskontext, vorläufiger beziehungsweise bestätigter Gang, Zeitzuordnung und regulärer Abschluss. |
| `tests/` | Synthetische Übergangstests, Artefaktprüfung und optionales Replay mit lokalem Recorderexport. |

Der Fachkern verarbeitet erkannte Ereignisse. Live-Messadapter, Thermostat,
HA-Setup, Entitäten und dauerhafter Datenspeicher sind noch nicht implementiert.

## Zielaufteilung

| Baustein | Verantwortung |
|---|---|
| Messadapter | Messwerte beider Kanäle mit Messzeit, Empfangszeit und Gültigkeit übernehmen. |
| Signalverarbeitung | Kausale Zeitfenster und getrennte Temperatur-/Feuchtemerkmale bilden. |
| Erkennung | Türereignisse, Durchlüften, Personenmuster und Aufguss erkennen. |
| Ablaufkern | Session, Gang, Bestätigungsstand, Fristen und Zeitzuordnung verwalten. |
| Thermostatkern | Hysterese, Heizlaufzeit und zulässige Schaltungen unter Gang- und Schutzbedingungen bestimmen. |
| HA-Adapter | Parameter und Bedienaktionen entgegennehmen, Entitäten anzeigen, Aktorbefehle ausführen und Rückmeldungen verarbeiten. |
| Speicherung | Betriebswiederaufnahme und Sessionauswertung; Verfahren im eigenen Besprechungsblock festlegen. |

Die fachliche Bedeutung der Ereignisse steht im [Gangmodell](gangmodell.md).
Die Detektoren entscheiden nicht über Heizbefehle. Insbesondere ist ein starkes
Personensignal eine vorläufige Gangerkennung, keine Aufgussbestätigung.

## Eine Quelle je Zustand und Parameter

Die GUI leitet die Phase **Saunagang** aus dem laufenden Gang ab und ergänzt
dessen Bestätigungsstand. Im Datenmodell wird dieser Stand ausschließlich aus
den zugeordneten Aufgüssen berechnet. Ein zweiter schreibbarer Bestätigungsmerker
oder ein eigenständig geführter GUI-Phasenzustand ist nicht vorgesehen.

Ein validierter Parametersatz wird über die eigenen Parameterentitäten geändert
und vom Kern konsumiert. Abgeleitete Heizdauer und Restzeiten sind lesbare
Ergebnisse. Die JSON-Datei des eingefrorenen Replays ist nur eine Testeingabe.

## Ereignisse und Wirkungen

Der Fachkern berechnet aus Zustand und Ereignis einen neuen Zustand, ohne den
alten zu verändern. Ereignis-IDs verhindern Doppelverarbeitung; abweichender
Inhalt unter derselben ID wird zurückgewiesen. Die Vorbereitung ist konkret der
letzten abgeschlossenen Öffnungsepisode zugeordnet, nicht nur als beliebiger
früherer Merker gespeichert.

Aktorbefehle und tatsächliche Rückmeldungen werden getrennt behandelt. Eine
rückwirkende Gangzuordnung erzeugt keine historischen Schaltbefehle. Licht,
Musik und Meldungen können auf abgeleitete Ereignisse reagieren; sie führen
keine parallele Gang- oder Aufgusslogik.

## Weitere Umsetzung

Zunächst werden die noch offenen Ablaufregeln geklärt. Danach wird der
Offline-Detektor in eine schrittweise arbeitende Implementierung übertragen
und gegen den unveränderten Kandidaten geprüft. Die Erkennung verwendet nur
bereits vorliegende Messungen; bekannte Gangfenster dienen allein dem Vergleich.

Vor dem Betrieb sind insbesondere Messausfälle, dynamischer Kanalwechsel,
Wiederbeitritt mit frischer Historie, Neustart, veraltete Timer und
Aktorrückmeldungen zu prüfen. Die andere Einbauhöhe von Kanal 6 erfordert einen
gesondert festzulegenden Regeltemperaturbezug bei Ersatz von Kanal 3.

Referenzen der Erstfassung zur HA-Anbindung, eingesehen am 18.09.2026:

- https://developers.home-assistant.io/docs/creating_integration_file_structure/
- https://developers.home-assistant.io/docs/core/entity/number/
- https://developers.home-assistant.io/docs/integration_listen_events/

Diese Quellen beschreiben Schnittstellen; die Saunaregeln stammen aus der Besprechung.
