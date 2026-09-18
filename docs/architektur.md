# Architektur und Umsetzungsschnitt

## Vorhanden

- `candidate/`: eingefrorenes Offline-Replay samt Parameterschnappschuss.
- `custom_components/ha_sauna/core/timeline.py`: reiner Zustandsuebergang fuer
  zeitliche Gangzuordnung, Aufguss und bestaetigten Abschluss.
- `tests/`: synthetische Zeitmodelltests, Integritaetspruefung des Kandidaten
  und optionaler Reproduktionstest mit externem Recorderexport.

Es gibt noch keinen HA-Setup-Code, kein Manifest mit Releaseversion, keinen
Config Flow, keinen Thermostat-Aktorzugriff und keinen gewaehlten Datenspeicher.
Das Verzeichnis ist eine Entwicklungsgrundlage, keine installierbare Integration.

## Zielaufteilung

| Modul | Verantwortung |
|---|---|
| Messadapter | Vorhandene Sensorentitaeten lesen; Messzeit, Empfangszeit, Kanal und Gueltigkeit erhalten. |
| Signalverarbeitung | Kausale Fenster und getrennte Temperatur-/Feuchtemerkmale beider Hoehen. |
| Erkennung | Tuerereignisse, bestaetigtes Durchlueften, Personenmuster, Aufguss; keine Aktorbefehle. |
| Ablaufkern | Session, Gang, Aufgussbestaetigung, Fristen und zeitliche Zuordnung; einmalige Entscheidungen. |
| Thermostatkern | Hysterese, Heizlaufzeit und Schaltfreigabe unter Gang-/Schutzbedingungen. |
| HA-Adapter | Entitaeten, Aktionen, geordnete Ereignisverarbeitung, Timeranbindung und Aktorrueckmeldungen. |
| Speicherung | Betriebswiederaufnahme und Sessionauswertung; Verfahren noch offen. |

Das spaetere `climate` stellt die Temperaturregelung dar; `number` stellt
veraenderliche numerische Parameter bereit. Sensor-/Binaersensorentitaeten sind
Projektionen des Fachzustands. Ihre Anzeige ist keine zweite Steuerungswahrheit.
Entitaetsnamen fuer eine installierte Integration werden noch nicht vorausgesetzt.

## Parametrierung

Genau ein validierter Parametersatz wird durch die eigenen Parameterentitaeten
veraendert und vom Kern konsumiert. Numerische Zeitwerte werden als Eingaben
uebergeben, nicht in Automationen und Code nochmals hinterlegt. Abgeleitete
Heizdauer und Fristrestzeit sind keine unabhaengig editierbaren Parameter.
Der `candidate/parameter.json`-Stand ist nur eine reproduzierbare Testeingabe.

## Ereignisse und Nebenwirkungen

Fachkern: bisheriger Zustand + Ereignis + gueltige Parameter -> neuer Zustand.
Aktoraktionen laufen erst ueber den HA-Adapter und werden getrennt von ihrer
Rueckmeldung protokolliert. Rueckdatierte Zuordnung erzeugt keine nachtraeglichen
Serviceaufrufe. Duplikate sind anhand von Ereignis-IDs idempotent; abweichender
Inhalt unter derselben ID wird zurueckgewiesen.

Die Aufguss- und Gangzeitlogik wird nicht parallel in HA-Automationen nachgebaut.
Licht, Musik und Meldungen koennen auf abgeleitete Ereignisse reagieren.

## Weiteres Vorgehen

Zuerst die verbleibenden fachlichen Entscheidungen abschliessen, danach den
Offline-Detektor in eine schrittweise arbeitende Implementierung ueberfuehren
und gegen den eingefrorenen Replay pruefen. Kein Code, der bereits anliegende
Messwerte aus der Zukunft, fest bekannte Gangfenster oder Recorderphasen fuer
seine Detektionsentscheidung verwendet. Die alten Phasen sind nur Vergleichsdaten.

Vor einem Betrieb muessen unter anderem ungueltige/fehlende Messwerte,
dynamischer Kanalwechsel, Wiederbeitritt mit frischer Historie, Neustart,
veraltete Timer und Aktorrueckmeldungen getestet werden. Ein fester Kanaloffset
und ein veraenderter Regeltemperaturbezug werden nicht stillschweigend eingesetzt.

## Technische Referenzen

Am 18.09.2026 eingesehene offizielle HA-Dokumentation, als Adaptergrundlage:

- https://developers.home-assistant.io/docs/creating_integration_file_structure/
- https://developers.home-assistant.io/docs/core/entity/number/
- https://developers.home-assistant.io/docs/integration_listen_events/

Diese Quellen beschreiben HA-Schnittstellen, nicht die vereinbarten Saunaregeln.
