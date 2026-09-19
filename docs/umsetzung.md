# Umsetzungspaket 1: Grundgerüst

## Umfang

Die erste Implementierung umfasst Einrichtung, spätere Konfigurationsänderung,
validierte Rollen und Parameter sowie den Sessionrahmen um das vorhandene
Gangmodell. Sie enthält keine Geräteansteuerung und keinen produktiven
Saunabetrieb. Es werden auch beim Laden und Entladen keine Ausschaltbefehle
an bestehende Geräte geschickt.

Die Einrichtung legt einen HA-Konfigurationseintrag und dessen Laufzeitobjekt
an. Thermostat-/Anzeige-/Parameterentitäten, Messlistener, physische
Tasterauswertung, Betriebsaktionen, automatische Fristauslösung und Speicherung
werden in den folgenden Paketen ergänzt. Der Config Flow ist bereits Code;
eine vollständig gestartete HA-Installation bleibt eine eigene Abnahme.

## Struktur

| Datei | Aufgabe |
|---|---|
| `manifest.json`, `__init__.py` | Integration beschreiben sowie Laufzeit laden und entladen. |
| `config_flow.py` | Einrichtung und Optionsdialog mit HA-Entitätsselektoren und Zahlenfeldern. |
| `bindings.py` | Funktionale Rollen, Zuordnungsprüfung, Messklassen, Einheiten und Dimmbarkeit. |
| `core/parameters.py` | Ein Parameterkatalog mit Labels, Einheiten und Werteprüfung. |
| `runtime.py` | Validierter Konfigurationsstand, serielle Kernzugriffe, injizierbare Uhr und Abmeldung. |
| `core/models.py` | Messschnittstelle, Session, Heizzeit-Anfangsdaten und sessiongebundene Fristidentität. |
| `core/controller.py` | Sessionrahmen, Delegation an den vorhandenen Gangkern und Schutz gegen veraltete Fristaufrufe. |
| `core/timeline.py` | Unverändert übernommene, bereits geprüfte Gangzuordnung. |

`ConfigEntry.options` ist die persistente Quelle für `bindings` und `parameters`.
`entry.data` enthält keine parallelen Kopien. Der Laufzeitkern erhält einen
unveränderlichen, validierten Stand. Die Parameterprüfung wird in Einrichtung,
Optionsänderung und Laufzeitaufbau verwendet. Eine spätere Number-Entität muss
über denselben Parameterweg schreiben, nicht über eine neue Ablage.

Das Manifest erhält erstmals die technisch erforderliche Versionsangabe
`0.0.0`. Es gibt keine bestehende Version, die erhöht wird, keinen Release-Tag
und keine Installation auf dem Gerät des Nutzers.

## Konfiguration

Ausgewählt werden Temperatur und Feuchte an oberer und unterer Messposition,
Heizaktor, physische Bedienquelle und dimmbares Licht. Statusquellen und eine
zusätzliche Heizrückmeldung sind optional zuordenbar. Zwei Messpositionen dürfen
nicht dieselbe Temperatur-/Feuchteentität verwenden. Ein Heizaktor darf nicht
zugleich zwei Einträgen dieser Integration zugeordnet werden.

Die derzeitigen Messrollen verlangen die Geräteklassen Temperatur/Feuchte und
Einheiten °C/%. Abweichende Einheiten werden nicht stillschweigend als gleiche
Messwerte behandelt. Für die erste Auswahl müssen Entitätsmetadaten vorliegen;
ein Messzustand `unavailable` mit vorhandenen Metadaten löscht die Zuordnung nicht.
Automatische Ausfalldiagnose und Live-Messübernahme gehören nicht zu Paket 1.

Neun Ablauf-/Thermostatwerte sind konfigurierbar: Session-Unterbrechungsfrist,
Aufgussbestätigungsfrist, einheitliche Heizzeitgrenze, Heizzeit-Rücksetz-Auszeit,
Thermostat-Cooldown, Zwangskühlungsdauer, Nachlaufdauer sowie obere und untere
Hysterese. Es werden keine noch unvereinbarten numerischen Defaults eingesetzt.
Die Werte werden bei Einrichtung ausdrücklich angegeben. Zeitwerte werden in
Minuten eingegeben und bei Bedarf aus genau diesen Werten in Sekunden umgerechnet.

Die Prüfung umfasst endliche Zahlen, Vollständigkeit und fachliches Vorzeichen.
Dies ist noch keine Auswahl sicherer Betriebsgrenzen. Erkennungsparameter des
festgehaltenen Kandidaten bleiben im Offline-Artefakt; sie werden erst mit der
laufenden Erkennung über diesen Katalog angebunden, nicht parallel nachgebaut.

Optionsänderungen laden das ausschließlich passive Gerüst neu. Solange über
Tests eine Session im Laufzeitobjekt angelegt wurde, werden solche Änderungen
abgewiesen, statt deren Session unbemerkt zu ersetzen. Das ist eine Begrenzung
dieses Gerüsts, keine festgelegte Produktivpolitik für laufende Fristen.

## Session und Zeit

`Session` besitzt das vorhandene `Timeline`-Objekt, eigene Heizzeit-Anfangsdaten
und eigene Fristen. Session-ID und Beginn werden aus der Timeline gelesen;
es gibt keine zweite unabhängig schreibbare Identität.

Der Controller erzeugt eine Session ausdrücklich über seinen Kernaufruf. Er
entscheidet in Paket 1 noch nicht über Betriebsschalter, Sessionunterbrechung
oder Sessionwechsel. Die Vereinbarungen hierzu bleiben im Betriebsmodell und
werden im nächsten Paket implementiert. Eine bestehende Session wird nicht
beiläufig durch einen zweiten Initialisierungsaufruf überschrieben.

Fristen tragen Session, Zweck, Token und Ablaufzeitpunkt. Alte Sessions, ersetzte
Fristen, doppelte oder zu frühe Aufrufe werden erkannt. Das technische Konsumieren
einer Frist löst noch keinen fachlichen Übergang aus. Die späteren Fachmodule
müssen deren Fristfolge liefern. Uhr und Erkennungszeiten sind explizite Eingaben;
Tests warten keine echte Gang- oder Kühlzeit ab.

## Prüfungen

```sh
python3 -m unittest discover -s tests -p 'test_foundation.py' -v
python3 -m unittest discover -s tests -v
```

Die **47 neuen HA-unabhängigen Tests** prüfen Parameter, Zuordnung, Datenobjekte,
Sessioneigentum, Delegation an den Gangkern, Fristidentität, serielle Verarbeitung,
Abmeldung und das Fehlen von Aktor-Serviceaufrufen. Sie wurden lokal erfolgreich
ausgeführt. Zusätzlich bestanden die 32 unverändert übernommenen Gangmodelltests;
insgesamt damit 79 lokale Tests. Der Original-Gangkern wurde anhand seines Git-Blob-Hashes unverändert
übernommen. Das gesamte eingefrorene Replay wurde mit dem privaten Originalexport
erneut ausgeführt; der Ergebnis-SHA-256 bleibt
`593ab8f604a66c9cbecaf647fa211372c7ea98558748308ac11715f72f035373`.

```sh
HA_TEST_REQUIRED=1 python3 -m unittest discover -s tests/ha -v
```

Elf zusätzliche API-Smoketests verwenden die echten HA-Flow-, Schema- und
Selektorklassen, aber isolierte Manager-/Entitäts-Testdoubles. Sie ersetzen
keinen vollständigen HA-Start oder Backup-/Restore-Test. Die lokale Umgebung
enthält kein Home Assistant; diese Tests werden dort ausdrücklich übersprungen.
Der vorbereitete CI-Job sieht dafür Home Assistant 2026.9.2 unter Python 3.14.2 vor.
Der Workflow gehört zu diesem Umsetzungspaket. Sein tatsächlicher CI-Status
wird getrennt von den lokalen Prüfergebnissen ausgewertet.
Ein CI-Ergebnis wird erst nach seinem tatsächlichen Abschluss als bestanden gewertet.

## Nächste Pakete

1. Betriebs- und Regelungskern: zuerst Sessionlebenszyklus und Ausschalten,
   anschließend Heizzeit, Nachlauf und Kühlungsregeln; weiterhin ohne Gerätezugriff.
2. Laufende Erkennung: den Kandidaten in einen kausalen Messstrom übertragen.
3. Archivierung: SQLite, Vollauflösung, konsistente Sicherung und Export.
4. Geräteanbindung: reale Rückmeldungen und abgesicherte Aktorsteuerung.
5. Sessionansicht auf den erprobten Daten- und Archivschnittstellen.

Die bereits festgelegten Regeln werden dabei nicht erneut zur Diskussion gestellt.
Adaptive Detektion, Sicherheitsgrenzen oder Ersatztemperaturen werden nicht durch
unbesprochene Zahlen ergänzt. Gegenstand dieses Pakets bleibt Paket 1.

## Technische Grundlagen

Geprüft am 18.09.2026 anhand offizieller Dokumentation und Core 2026.9.2:

- https://developers.home-assistant.io/docs/core/integration/config_flow/
- https://developers.home-assistant.io/docs/core/integration/options_flow/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/
- https://developers.home-assistant.io/docs/creating_integration_manifest/
- https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/helpers/selector.py
- https://github.com/home-assistant/core/blob/2026.9.2/pyproject.toml
