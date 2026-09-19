# HA Sauna

Home-Assistant-Integration mit eigenem Thermostat, fortlaufender Erkennung aus
Temperatur und Luftfeuchte zweier Höhen, Sessionarchiv und eigener Oberfläche.
Zielversion: **Home Assistant Core 2026.9.2**, Python **3.14.2 oder neuer**.
Die Software schaltet den ausgewählten Heizaktor. Einrichten und Prüfen erfolgt
zunächst mit Testentitäten; eine reale Hardwareabnahme gehört nicht zum
bisherigen Nachweis. Version `0.0.0`, kein Release und kein Produktivdeployment.

## Einrichtung und Bedienung

`custom_components/ha_sauna` in das HA-Konfigurationsverzeichnis kopieren,
Home Assistant neu starten und unter **Einstellungen → Geräte & Dienste**
**HA Sauna** hinzufügen. Temperatur und Feuchte oben/unten, Heizaktor,
physische Bedienquelle, dimmbares Licht und tatsächliche Heizrückmeldung werden
nach ihrer Rolle ausgewählt. Es gibt keine fest hinterlegten privaten Entitäten.

Die obere Temperatur regelt den Ofen. Ohne gültige obere Regeltemperatur wird
kein unterer Ersatzwert erfunden. Die Ereigniserkennung arbeitet bei Ausfall
mit der verbleibenden Messposition weiter und zeigt den Fehler an. Die
Heizrückmeldung muss tatsächliches Heizen abbilden; ein Relaisbefehl allein
beweist das insbesondere beim mechanischen Ofentimer nicht.

Vor Heizfreigabe sind Solltemperatur, Messwert-Gültigkeitsdauer, Rückmeldungsfrist
und Bestätigungsfrist zentraler Ausfälle zu konfigurieren. Auch Sessionfrist,
Aufgussbestätigungsfrist, lokale Heizzeit-Rücksetz-Auszeit und Nachlaufdauer
benötigen eine ausdrückliche Einstellung. Für diese noch nicht festgelegten
Ausgangswerte gibt es keine erfundenen Defaults.

In der Seitenleiste erscheint **Sauna** mit zwei Hauptansichten:

- **Normal:** einfache Steuerung; eigenes Blatt **Sessionverlauf & Archiv**.
- **Details:** Betrieb und Fristen, separate Erkennungskontrolle sowie
  Einstellungen und authentifizierter ZIP-Export.

Die Verlaufsgestaltung folgt der Nutzervorlage: orange Temperatur, blaue
Feuchte auf gemeinsamer Zeitachse, gelbe Türöffnungen, magentafarbene Gänge,
weiße Aufgüsse und farbige Betriebsbereiche. Oben/unten bleiben getrennte
Kurven. Konfiguration wird ausschließlich in den HA-Entry-Optionen gespeichert
und bleibt während einer gesamten Session gesperrt, auch bei kurzem Betrieb-Aus.

## Vereinbarter Ablauf

Personenerkennung legt einen vorläufigen Gang an; Aufguss bestätigt denselben
Gang. Beginn ist die passende Türschließung, Erkennung und Bestätigung bleiben
separate Zeiten. Ohne Bestätigung wird der vorläufige Gang vollständig
aufgehoben: keine Zählung und kein Nachlauf. Dasselbe gilt bei bestätigtem
Durchlüften vor Aufguss. Ausdrückliches Ausschalten beendet einen laufenden
Gang sofort. Jeder beendete Gang mit Aufguss zählt einmal.

| Einstellbarer Ausgangswert | Standard |
|---|---:|
| Bereitschaftsaufschlag auf Solltemperatur | 5 °C |
| Bereitschaftshysterese | 3 °C |
| Thermostat-Cooldown | 5 Minuten |
| Mindestheizzeit nach tatsächlichem Einschalten | 10 Minuten |
| Heizbudget vor erster Kühlung | 90 Minuten tatsächliches Heizen |
| Einmalige Verringerung nach erster Kühlung | 30 Minuten, danach konstant |
| Zwangskühlung | 15 Minuten |
| Personenerkennung nach Türschließung abwarten | 4 Minuten |
| Kühlaufschub bei offen bleibender Tür | 10 Minuten ab Öffnung |
| Temperaturregel für Zusatzkühlung | länger als 10 Minuten über 105 °C |
| Zusatzkühlung | doppelte konfigurierte Kühlzeit |
| Geschätzte Laufzeit des mechanischen Ofentimers | 4 Stunden |

Ein laufender Gang bleibt bei fälliger Kühlung erhalten. Danach folgen
Nachlauf mit Ofen-Aus und nur die verbleibende Kühlzeit. Nachlauf und laufende
Kühlung sperren neue Gänge. Türöffnung beim Aufheizen/in Bereitschaft lässt
zunächst Zeit für Personenerkennung. Ohne Signal beginnt anschließend eine
fällige Kühlung. Mindestheizzeit verzögert weder Nachlauf/Kühlung noch Betrieb-Aus
oder bestätigte technische Schutzabschaltung.

Setup/Neustart startet keinen Betrieb; Setup und Unload senden Ofen-Aus.
Historische Daten bleiben erhalten. Übertemperatur erzeugt die vereinbarte
Zusatzkühlung ohne Sessionabbruch. Bestätigte zentrale technische Ausfälle
verriegeln die Heizung; Quittierung benötigt Betrieb-Aus und bestätigten Ofen-Aus.

## Nachweise und Dokumentation

Der aktuelle, nach Testart getrennte Nachweis steht in [Abnahme](docs/abnahme.md).
Softwaretests sind keine Abnahme des realen Ofens. Insbesondere sind reale
Temperaturkalibrierung, Ausfallzeiten, tatsächliche Schalt-/Stromrückmeldung und
Zusammenwirken mit vorhandenen mechanischen Schutzfunktionen örtlich zu prüfen.

[Architektur](docs/architektur.md) · [Betrieb](docs/betrieb.md) ·
[Gangmodell](docs/gangmodell.md) · [Zeitmodell](docs/zeitmodell.md) ·
[Parameter](docs/parameter.md) · [Oberfläche](docs/darstellung.md) ·
[Archiv und Backup](docs/speicherung.md) · [Umsetzung](docs/umsetzung.md) ·
[Entscheidungen](docs/entscheidungen.md) · [Unveränderter Kandidat](docs/kandidat.md)

```sh
# Reiner Fachkern und SQLite, kein HA erforderlich:
python -m unittest discover -s tests -v
# Installierte Zielversion in isolierter Linux-Umgebung:
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/browser -v
```

CI installiert HA 2026.9.2, für den Browser zusätzlich das dazugehörige Frontend
20260826.7 und Playwright 1.63.0/Chromium. Die genauen Befehle stehen im
[Workflow](.github/workflows/tests.yml). API-Smokes mit Testdoubles, tatsächliche
HA-Integrationstests und Browserprüfungen werden getrennt ausgewiesen.

Privater Replay-Export bleibt lokal und außerhalb des Repos:

```sh
python -m pip install -r requirements-replay.txt
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python -m unittest discover -s tests -p 'test_*replay.py' -v
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python -m unittest discover -s tests -p test_candidate.py -v
```

`candidate/parameter.json` und `candidate/provenienz.json` bleiben eingefrorene
Referenzen. Produktive Parameter stammen ausschließlich aus den Entry-Optionen.
Die Referenzsession dient der Kalibrierung, nicht der unabhängigen Feldvalidierung.
