# HA Sauna

Entwicklungsgrundlage für eine eigene Home-Assistant-Integration mit eigenem
Thermostat, Zwei-Sensor-Erkennung und Sessiondaten unabhängig vom Recorder.

## Gangablauf

**Vorbereitendes Durchlüften → vorläufig erkannter Saunagang → Bestätigung durch Aufguss.**

Die Personenfrüherkennung zeigt bereits einen Saunagang an. Erst ein erkannter
Aufguss bestätigt ihn. Zugeordneter Beginn bei der Türschließung, erste Erkennung
und Bestätigung bleiben getrennt. Ein Aufguss kann einen vorher verpassten Gang
auch unmittelbar bestätigt anlegen.

Durchlüften allein startet keinen Gang. Kurze Türbetätigung erhält einen
laufenden Gang; der reguläre Abschluss benötigt Aufguss und anschließendes
bestätigtes Durchlüften. Ausdrückliches Ausschalten beendet den Gang sofort.
Eine konfigurierbare Bestätigungsfrist bezieht sich auf den zugeordneten
Beginn, nicht auf die spätere Personenfrüherkennung. Details und offene
Fristfolgen stehen im [Gangmodell](docs/gangmodell.md).

## Betrieb und Zeitrechnung

Nach Ablauf der konfigurierten Frist seit dem Ausschalten beginnt beim
nächsten Einschalten eine vollständig neue Session. Bei früherem Einschalten
bleibt es dieselbe Session; ein ausgeschalteter Gang bleibt beendet.
Der Shelly-Schalter bedient den Saunabetrieb, nicht die momentane Heizaktivität.

Heizlaufzeit ist ausschließlich die Summe tatsächlicher Heizzeiten seit der
letzten Rücksetzung. Eine genügend lange zusammenhängende Auszeit setzt diese
Summe zurück; kurze Pausen zählen nicht mit und schenken keine neue Heizdauer.

## Bearbeitungsstand

Implementiert sind der eingefrorene Erkennungskandidat und ein getesteter
Python-Kern für Vorbereitung, vorläufigen/bestätigten Gang, Zeitzuordnung und
regulären Abschluss. Die jüngsten Vereinbarungen zu Bestätigungsfrist,
Ausschalten, Sessiongrenze, Heizzeitsumme und Parametrierung sind dokumentiert,
noch nicht als zusätzliche Funktionen umgesetzt. Thermostat, HA-Anbindung und
dauerhafte Speicherung folgen; der aktuelle Code schaltet keine Geräte.

| Dokument | Inhalt |
|---|---|
| [Gangmodell](docs/gangmodell.md) | Vorstufen, Bestätigung, Frist und Gangende. |
| [Betrieb](docs/betrieb.md) | Shelly-Bedienung, Sessiongrenze, Ausschalten und Heizlaufzeit. |
| [Parameter](docs/parameter.md) | Einheitliche Konfiguration, Relationen und abgeleitete Werte. |
| [Zeitmodell](docs/zeitmodell.md) | Zeitbezüge, Fristen und historische Darstellung. |
| [Entscheidungen](docs/entscheidungen.md) | Vereinbarungen, Vorschläge und nächste Besprechungspunkte. |
| [Architektur](docs/architektur.md) | Zuständigkeiten und Implementierungsstand. |
| [Speicherung](docs/speicherung.md) | Betriebswiederaufnahme und Sessionarchiv als eigener Besprechungsblock. |
| [Erkennungskandidat](docs/kandidat.md) | Unveränderte Kalibrierung und Prüfgrenzen. |
| [Arbeitsregeln](AGENTS.md) | Vorgaben für Änderungen. |

Der Fachkern liegt in `custom_components/ha_sauna/core/timeline.py` und benötigt
kein Home Assistant. `candidate/replay.py` und `candidate/parameter.json`
bleiben unveränderte Ausgangsartefakte. Der Parameterschnappschuss ist eine
Testeingabe, keine zweite produktive Einstellungsquelle. Relative, automatisch
angepasste Erkennungsgrenzen sind bislang ein besprochener Prüfansatz.

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
