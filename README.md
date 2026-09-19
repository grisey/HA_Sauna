# HA Sauna

Eigene Home-Assistant-Integration mit eigenem Thermostat, Zwei-Sensor-Erkennung
und langfristigen Sessiondaten unabhängig vom Recorder.

## Laufende Fertigstellung und Abnahme

Das Gerüst wird in geprüften Paketen erweitert. Paket A stellt eigene Parameter-
und Statusentitäten bereit und prüft den Lebenszyklus mit einem echten HA-Kern.
Der aktuelle Nachweis und noch ausstehende Prüfungen stehen in
[Abnahme](docs/abnahme.md). Die nachfolgende Beschreibung des ersten Pakets
bleibt als Ausgangsstand erhalten, bis die Betriebsumsetzung abgeschlossen ist.

## Ausgangsstand: erstes Umsetzungspaket

Das Grundgerüst enthält Einrichtung und Optionsdialog, frei wählbare
Entitätszuordnungen, zentrale Parameterprüfung, eine Sauna-Laufzeit und einen
Sessionrahmen um das vorhandene Gangmodell. **Es schaltet keine Geräte.**
Es werden noch keine Messlistener, Tasteraktionen, Thermostat-/Number-Entitäten
oder Archivschreiber aktiviert. Die Betriebslogik folgt im nächsten Paket.

Alle neun Ablauf-/Thermostatwerte werden bei Einrichtung ausdrücklich gesetzt;
noch nicht vereinbarte Defaults werden nicht erfunden. Spätere Änderungen laufen
über denselben Parametersatz. Rollen werden nach Entitätstyp, Geräteklasse,
Einheit und benötigter Dimmbarkeit geprüft. Referenz-Entity-IDs stehen nicht im
neuen Produktivcode. Das Manifest beginnt technisch bei `0.0.0`; es gibt kein Release.

**Umfang, Grenzen und Testanleitung:** [Umsetzungspaket 1](docs/umsetzung.md).
Die Ziel-API ist Home Assistant 2026.9.2; für HA-Tests ist Python 3.14.2 vorgesehen.
Der reine Fachkern lässt sich auch ohne Home Assistant testen.

## Vereinbarter Ablauf

Vorbereitendes Durchlüften kann die Personenfrüherkennung stützen. Sie führt
bereits einen vorläufigen Saunagang; Aufguss bestätigt denselben Gang.
Beginn ab zugeordneter Türschließung, erste Erkennung und Bestätigung bleiben
getrennt. Ein Aufguss kann die ausgebliebene Früherkennung nachholen.

Kurze Türbetätigung erhält den Gang. Bestätigtes Durchlüften nach Aufguss
beendet ihn regulär; ausdrückliches Ausschalten beendet ihn sofort.
Jeder beendete Gang mit zugeordnetem Aufguss zählt einmal.

Die Session ist das übergeordnete Laufzeitobjekt. Nach Ablauf der Frist seit
Betrieb-Aus beginnt beim nächsten Einschalten eine neue Session samt Unterobjekten.
Heizzeit zählt ausschließlich tatsächliches Heizen. Laufende Zwangskühlung sperrt
neue Gänge; laufende Gänge werden nicht unterbrochen. Bei im Gang fälliger Kühlung
folgen Gangende, Nachlauf und nur noch die um den Nachlauf reduzierte Restkühlung.
Diese Betriebsregeln sind Anforderungen, nicht bereits der vollständige Code.

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [Umsetzung](docs/umsetzung.md) | Paket 1, Prüfungen und folgende Umsetzungsschritte. |
| [Architektur](docs/architektur.md) | Sessioneigentum, Datenfluss und Zuständigkeiten. |
| [Gangmodell](docs/gangmodell.md) | Vorstufen, Aufgussbestätigung, Frist und Gangende. |
| [Betrieb](docs/betrieb.md) | Bedienung, Sessiongrenze und Heiz-/Kühlablauf. |
| [Parameter](docs/parameter.md) | Konfigurierbarkeit und auswählbare externe Entitäten. |
| [Zeitmodell](docs/zeitmodell.md) | Zeitbezüge und nachträgliche Zuordnung. |
| [Darstellung](docs/darstellung.md) | Vereinbarte Sessionansicht. |
| [Speicherung](docs/speicherung.md) | Vollauflösung, SQLite, Backup und Export. |
| [Entscheidungen](docs/entscheidungen.md) | Verbindliche Anforderungen und Abgrenzungen. |
| [Erkennungskandidat](docs/kandidat.md) | Eingefrorene Kalibrierung und Prüfgrenzen. |
| [Arbeitsregeln](AGENTS.md) | Vorgaben für Änderungen. |

## Tests

```sh
python3 -m unittest discover -s tests -v
```

79 HA-unabhängige Tests wurden lokal erfolgreich ausgeführt: 47 neue
Grundgerüsttests und 32 unveränderte Gangmodelltests.
Der vorhandene Gangkern und der eingefrorene Kandidat bleiben unverändert.
Elf zusätzliche HA-API-Smoketests werden getrennt ausgeführt:

```sh
HA_TEST_REQUIRED=1 python3 -m unittest discover -s tests/ha -v
```

Diese Tests benötigen tatsächlich installiertes Home Assistant; Manager und
Entitätsbestand sind isolierte Testdoubles. Der CI-Workflow prüft Fachkern und
HA-API getrennt. Sein tatsächliches Ergebnis wird separat ausgewiesen. Ein
übersprungener HA-Test ist kein bestandener HA-Nachweis.

Der private Recorderexport bleibt außerhalb des Repositorys. Optional lässt
sich das vollständige eingefrorene Replay erneut vergleichen:

```sh
python3 -m pip install -r requirements-replay.txt
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python3 -m unittest discover -s tests -v
```

Direktes Replay mit Ergebnisdatei:

```sh
mkdir -p .local
python3 candidate/replay.py /pfad/sauna-recorder-2026-09-17.zip \
  --parameters candidate/parameter.json --out .local/ergebnis.json --tests
```

`candidate/provenienz.json` enthält die Prüfsummen. `candidate/parameter.json`
ist ein eingefrorener Teststand, keine zweite produktive Parameterquelle.
