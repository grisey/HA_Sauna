# HA Sauna

Vorbereitung einer eigenen Home-Assistant-Integration mit eigenem Thermostat,
Zwei-Sensor-Erkennung und Recorder-unabhaengigen Sessiondaten.

**Stand:** festgehaltener Erkennungskandidat, reproduzierbares Offline-Replay und
getesteter Fachkern fuer die zeitliche Gangzuordnung. Noch keine installierbare
Heizungssteuerung. Es gibt keinen Aktorzugriff, kein Deployment und keinen
implizit ausgewaehlten Sessiondatenspeicher.

## Einstieg

| Datei | Inhalt |
|---|---|
| [Entscheidungen](docs/entscheidungen.md) | Vereinbarte Anforderungen und ausdruecklich offene Punkte. |
| [Architektur](docs/architektur.md) | Trennung von Messung, Erkennung, Gangablauf, Thermostat und HA-Anbindung. |
| [Zeitmodell](docs/zeitmodell.md) | Nachtraegliche Gangzuordnung zur passenden Tuerschliessung. |
| [Speicherung](docs/speicherung.md) | Eigener Besprechungsblock: Betriebszustand, Sessionarchiv, GUI. |
| [Erkennungskandidat](docs/kandidat.md) | Unveraenderter festgehaltener Kandidat mit Messbefunden und Grenzen. |
| [Arbeitsregeln](AGENTS.md) | Vorgaben fuer die weitere Umsetzung. |

## Vorbereiteter Code

`custom_components/ha_sauna/core/timeline.py` verarbeitet erkannte Ereignisse
ohne HA-Abhaengigkeit und ohne Nebenwirkungen. Ein spaeter erkannter Gang kann
seine Startzeit aus der zugehoerigen Tuerschliessung erhalten. Erkennung,
Aufgussbestaetigung und Gangabschluss bleiben getrennt. Kurze Tuerbetätigungen
setzen den Gangbeginn nicht zurueck.

`candidate/replay.py` und `candidate/parameter.json` sind unveraenderte Kopien
des akzeptierten Offline-Kandidaten. Der Parameterstand ist ein Testartefakt,
keine zweite produktive Einstellungsquelle.

## Tests

Fachkern und Artefaktintegritaet, ohne HA und ohne Recorderdaten:

```sh
python3 -m unittest discover -s tests -v
```

Zusaetzliche lokale Rueckpruefung mit dem privaten Originalexport:

```sh
python3 -m pip install -r requirements-replay.txt
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python3 -m unittest discover -s tests -v
```

Vollstaendiges Erkennungsreplay mit Ergebnisdatei:

```sh
mkdir -p .local
python3 candidate/replay.py /pfad/sauna-recorder-2026-09-17.zip \
  --parameters candidate/parameter.json --out .local/ergebnis.json --tests
```

Die Ausgangsdaten bleiben ausserhalb dieses oeffentlichen Repositorys.
`candidate/provenienz.json` dokumentiert Hashes und Reproduktionsumfang.
Eine Lizenzentscheidung und der produktive Integrationsstand sind noch offen.
