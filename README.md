# HA Sauna

Entwicklungsgrundlage für eine eigene Home-Assistant-Integration mit eigenem
Thermostat, Zwei-Sensor-Erkennung und Sessiondaten unabhängig vom Recorder.

## Gangablauf

**Vorbereitendes Durchlüften → vorläufig erkannter Saunagang → Bestätigung durch Aufguss.**

Die Personenfrüherkennung zeigt bereits einen Saunagang an. Erst ein erkannter
Aufguss bestätigt ihn. Beginn, Erkennungszeit und Bestätigungszeit bleiben
getrennt: Ein später erkanntes Personenmuster kann den Gangbeginn der
zugehörigen Türschließung zuordnen. Ein Aufguss kann einen vorher verpassten
Gang auch unmittelbar bestätigt anlegen.

Durchlüften allein startet keinen Gang. Kurze Türbetätigung erhält einen
laufenden Gang. Der reguläre Abschluss setzt einen Aufguss und anschließendes
bestätigtes Durchlüften voraus. Die genaue Bedeutung der Vorstufen und die
unveränderten Zulassungsregeln stehen im [Gangmodell](docs/gangmodell.md).

## Bearbeitungsstand

Vorhanden sind ein eingefrorener Erkennungskandidat, dessen Offline-Replay und
ein getestetes Python-Modell für Gangzuordnung und Bestätigungsstand. Die
Dokumentation unterscheidet vereinbarte Regeln, Messbefunde und offene Fragen.
Thermostat, HA-Anbindung und dauerhafte Speicherung sind noch umzusetzen;
der aktuelle Code schaltet keine Geräte.

| Dokument | Inhalt |
|---|---|
| [Gangmodell](docs/gangmodell.md) | Vorbereitung, vorläufiger Gang, Aufgussbestätigung und Abschluss. |
| [Zeitmodell](docs/zeitmodell.md) | Beginn, Erkennungszeit, Bestätigungszeit und historische Darstellung. |
| [Entscheidungen](docs/entscheidungen.md) | Vereinbarte Anforderungen und verbleibende Besprechungspunkte. |
| [Architektur](docs/architektur.md) | Zuständigkeiten und Stand der Implementierung. |
| [Speicherung](docs/speicherung.md) | Betriebswiederaufnahme und Sessionarchiv als eigener Besprechungsblock. |
| [Erkennungskandidat](docs/kandidat.md) | Unveränderte Kalibrierung mit Messbefunden und Prüfgrenzen. |
| [Arbeitsregeln](AGENTS.md) | Vorgaben für Änderungen. |

Der Ablaufkern liegt in `custom_components/ha_sauna/core/timeline.py` und
benötigt kein Home Assistant. `candidate/replay.py` und `candidate/parameter.json`
sind die unveränderten Ausgangsartefakte. Ihr Parameterschnappschuss ist eine
Testeingabe, keine zweite produktive Einstellungsquelle.

## Tests

Gangmodell und Integrität der eingefrorenen Artefakte:

```sh
python3 -m unittest discover -s tests -v
```

Zusätzliche lokale Prüfung mit dem privaten Originalexport:

```sh
python3 -m pip install -r requirements-replay.txt
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python3 -m unittest discover -s tests -v
```

Vollständiges Erkennungsreplay mit Ergebnisdatei:

```sh
mkdir -p .local
python3 candidate/replay.py /pfad/sauna-recorder-2026-09-17.zip \
  --parameters candidate/parameter.json --out .local/ergebnis.json --tests
```

Die Ausgangsdaten bleiben außerhalb des öffentlichen Repositorys.
`candidate/provenienz.json` enthält die Prüfsummen und den Reproduktionsumfang.
