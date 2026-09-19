# Prüfung nach der ersten HACS-Testinstallation

Die erste Installation durch den Benutzer zeigte Bedienungs- und Anzeigefehler.
Diese Rückmeldungen sind durch konkrete Korrekturen und zusätzliche Prüfungen
abgedeckt. Geräte und Ofen der Nutzeranlage wurden ausschließlich lesend geprüft;
Installation und Neustart erfolgen weiterhin durch den Benutzer.

Der Funktionsstand `7ba72f08eda9f5f2e6c55d4fc2afc88f5ecb94d5` hat
[CI 35458776770](https://github.com/grisey/HA_Sauna/actions/runs/35458776770)
mit **allen vier Jobs erfolgreich** abgeschlossen: 144 reine Tests (zwei private
Replays öffentlich übersprungen), 11 HA-API-Smokes, 19 echte HA-Integrationstests
und zwei vollständige Chromium-Abläufe. Der anschließende Abschlussstand ergänzt
sprachliche Fehlerbehandlung und erhält aufgeklappte Bedienelemente beim Aktualisieren;
sein eigener CI-Lauf ist beim Pull Request #3 nachvollziehbar.

Zusätzliche Nachweise:

- Ofentimer bei Betrieb-Aus angehalten, bei Wiedereinschalten fortgesetzt,
  nach leerer Sitzung erhalten und nach abgeschlossener Sitzung mit gezählten
  Gängen beim nächsten Start zurückgesetzt; angehaltene Attribute ohne Ausnahme.
- Temperatursteigerung mit frei gewählter Schrittweite und Endtemperatur,
  Deckelung, keine Erhöhung durch aufgehobene Gänge, erhaltene Stufe bei kurzer
  Unterbrechung und Startwert bei neuer Sitzung. Einstellung im echten Browser.
- Entkoppelter binärer Taster und Shelly-Ereignistaster; Loslassen und doppelte
  Zustandsmeldungen lösen kein erneutes Umschalten aus. Neu zugeordnete Quelle
  übernimmt, alte Quelle wirkt nicht weiter.
- Fehlende Einrichtung und veraltete Regeltemperatur verhindern einen Start,
  ohne eine Sitzung anzulegen oder die Einstellungen zu sperren.
- Protokollstufen ERROR, INFO und DEBUG werden gespeichert und wirken während
  einer Sitzung ohne Neuladen. Standardfilter und Unterdrückung identischer
  Wiederholungen sind geprüft.
- Übersicht mit dauerhaft sichtbaren Temperaturtasten, angehaltener Restzeit,
  getrennten Bereitschaftsdetails, verständlichen Fehlerhinweisen und Feldhilfen.
  Archiv bleibt nach Sitzungsende sichtbar; Breite bei 390 Pixeln geprüft.

Beide privaten Replay-Prüfungen wurden zusätzlich lokal erfolgreich ausgeführt:
ein vollständiger fortlaufender Vergleich und drei Kandidaten-/Referenzprüfungen,
jeweils Exit 0. Numerische Erkennungsparameter und eingefrorene Referenzdateien
blieben unverändert. Die Referenz bleibt Kalibrierungsmaterial, kein unabhängiger
Nachweis realer Saunagänge. HA selbst lief für diese Prüfungen ausschließlich in
der isolierten Linux-CI, nicht auf dem lokalen Mac.

Die Browserläufe melden keine JavaScript-Ausnahmen oder abgewiesenen WebSocket-
Antworten. Beim absichtlich ausgelösten Optionsneuladen kann die noch ladende
Instanz vorübergehend HTTP 503 liefern; die Oberfläche behält dabei ihre Anzeige.
Ein abgeschlossener CI-Lauf ersetzt keine Abnahme des realen Saunaofens.

---

## Vorheriger Prüfstand der ersten Testfassung


Stand 19.09.2026. Die Betriebsintegration ist implementiert und in einer echten,
isolierten Home-Assistant-Instanz einschließlich Browser und Backup/Restore
geprüft. Die Installation über HACS und die Abnahme am realen Saunaofen stehen
noch aus und werden vom Nutzer vorgenommen. SSH bleibt ausschließlich lesend.

Geprüfter Implementierungsstand:
[`51235fb10d077b751ae6e5bc3b76a7186a44943f`](https://github.com/grisey/HA_Sauna/commit/51235fb10d077b751ae6e5bc3b76a7186a44943f).
[CI 35452095567](https://github.com/grisey/HA_Sauna/actions/runs/35452095567)
ist tatsächlich abgeschlossen: **core, ha-api, ha-integration und browser success**.
Dieser Bericht ersetzt die früheren Zwischenberichte zu einzelnen Paketen.

Ein zusätzlicher Lauf nach Übernahme in `main` zeigte einen Fehler der
Browser-Testumgebung: Der HA-Rahmen fragte `recorder/info` ab, obwohl der Recorder
in dieser isolierten Instanz nicht gestartet war. Die Browserumgebung initialisiert
jetzt auch den echten Recorder samt eigener temporärer Datenbank und prüft diesen
WebSocket-Aufruf ausdrücklich. Fehler-Assertions bleiben erhalten und prüfen
zusätzlich abgewiesene Promises und WebSocket-Antworten. Der Produktivcode ist
durch diese Korrektur unverändert.

## Ausgeführte Prüfungen

| Prüfung | Ergebnis | Aussage und Grenze |
|---|---|---|
| Fachkern, Detektor und SQLite | 140 gesammelt, **138 bestanden, 2 übersprungen** | Die zwei privaten Replay-Prüfungen sind in öffentlicher CI nicht verfügbar; sie zählen dort nicht als bestanden. |
| HA-API-Smokes | **11 bestanden** | Echte HA-Schemas mit Manager-Testdoubles; kein Ersatz für den HA-Lebenszyklus. |
| Echte HA-Integration | **15 bestanden** | 4 Lebenszyklus-, 7 Gerätepfad-, 2 Archiv/Backup- und 2 Panel-API-Tests mit tatsächlichen Managern, Entitäten, Listenern, Services, HTTP und Authentifizierung. Externe Geräte sind kontrollierte Testaktoren. |
| Browser im echten HA-Frontend | **1 vollständiger Ablauf bestanden** | Chromium: Live-Aktualisierung, vier Messkurven, unveränderter Gangbeginn nach Bestätigung, Zoom, Tooltip, Fehler, getrennte Erkennungsansicht, Konfigurationssperre, authentifizierter ZIP-Download samt Inhalt, Archivauswahl und mobile Breite. Keine erfassten JavaScript-Fehler oder abgewiesenen Promises. |
| Privater fortlaufender Replay-Vergleich | **1 bestanden, Exit 0** | Lokal mit dem bereitgestellten Recorderexport; alle Rasterpunkte und Ereignisse in drei Sensormodi mit dem eingefrorenen Kandidaten verglichen. |
| Kandidat und vollständiges Referenz-Replay | **3 bestanden, Exit 0** | Zwei Integritätsprüfungen und der vollständige Replay-Vergleich; Prüfsummen unverändert. |
| Lokale reine Kerntests | **138 bestanden, 2 Replay-Skips, Exit 0** | Python mit `-S`, ohne HA oder dessen native Abhängigkeiten. Die privaten Prüfungen wurden separat tatsächlich ausgeführt. |

Der private Vergleich umfasst die 15 bekannten Türöffnungsepisoden, vier Gänge
und dokumentierten Vergleichszeiten. Diese Session bleibt Kalibrierungsmaterial;
sie ist keine unabhängige Validierung unter weiteren realen Saunabedingungen.
Rohdaten, private Entity-IDs und Zugangsdaten sind nicht veröffentlicht.

## Anforderung → Implementierung → Nachweis

| Funktion | Produktiver Pfad | Ausführbare Prüfung |
|---|---|---|
| Einrichtung, Rollen, einheitliche Parameter, wiederholtes Setup/Unload/Reload und Neustart | `config_flow.py`, `bindings.py`, `runtime.py`, eigene Plattformen | `tests/integration/test_lifecycle.py` |
| Session, vorläufiger Gang, Aufhebung ohne Nachlauf/Zählung, Aufgussbestätigung und einmaliger Abschluss | `core/controller.py`, `core/timeline.py` | `test_operation.py`, `test_timeline.py`, `test_foundation.py` |
| Heizbudget 90 Minuten, einmalige Verringerung um 30 Minuten, Nachlaufanrechnung und Kühl-/Gangstartsperre | `core/heating.py`, `core/controller.py` | `test_heating.py`, `test_cooling.py`; Nachlauf kürzer, gleich und länger als Kühlvorgabe |
| Bereitschaftsaufschlag, Hysterese, Cooldown, Mindestheizzeit, Türwartefrist, Zusatzkühlung ohne Sessionabbruch | `core/thermostat.py`, `core/controller.py` | `test_thermostat.py`, `test_cooling.py` |
| Fortlaufende Messungen, Ausfall, Wiederkehr und Quellenwechsel | `device.py`, `core/detector.py` | `test_detector.py`, `test_stream_replay.py`, `tests/integration/test_device_path.py` |
| Messwert → Erkennung → Gang → Heizentscheidung → HA-Service → unabhängige Testrückmeldung → Heizzeit | `device.py`, Controller und HA-Plattformen | Vollständige Messstromkette in `tests/integration/test_device_path.py`; keine direkte Ersatzsteuerung des Kerns |
| Optionaler Leistungsmesser, sichtbare Ersatzquelle und Sessionenergie | `core/power.py`, `core/energy.py`, `device.py`, `sensor.py` | `test_power.py`, `test_energy.py` und zwei zusätzliche echte HA-Gerätepfadtests |
| Technischer Dauerausfall, fehlende Aktorantwort und sichere Initialisierung | `device.py`, `runtime.py` | `tests/integration/test_device_path.py`; Setup/Unload senden Aus, keine automatische Betriebsfortsetzung |
| Originalauflösung, Ereignisrevisionen und Export während Erfassung | `archive.py`, `api.py` | `test_archive.py`, `tests/integration/test_archive_backup.py`, Browserdownload |
| Konsistentes HA-Backup und Wiederherstellung | `backup.py`, Archiv | Tatsächlicher HA-Core-Backup-Writer und offizielle Start-Restore-Routine in getrenntem Konfigurationspfad; neue HA-Instanz vergleicht Archiv, Optionen und Zuordnungen |
| Übersicht, separates Verlaufsblatt mit Archiv, sortierte Details und Erkennungskontrolle | `frontend.py`, `panel.js`, `api.py` | `tests/integration/test_panel_api.py`, `tests/browser/test_panel.py` |

Ohne Leistungsmesser läuft der Heizzähler wie zuletzt vereinbart bei Schütz EIN.
Die Energie wird mit der einstellbaren Ofenleistung, Standard **4,5 kW**, geschätzt.
Ein gültiger Leistungsmesser ersetzt diese Schätzung durch integrierte W-/kW-Werte,
einschließlich Standby. Tests prüfen Messausfall, gemischte Summen, erhaltene
Sessionenergie nach Heizzeitrücksetzung und Nullstellung erst bei neuer Session.
Der mechanische Timer ist ausschließlich Anzeige/Erinnerung und löst keine
Steuerung aus. Ein Schützsignal allein beweist kein tatsächliches Heizen.

## Umgebungen und Reproduktion

- Linux-CI: Home Assistant **2026.9.2**, Python **3.14.2**; reiner Kern zusätzlich
  Python **3.13**. Alle genannten Prozesse wurden erfolgreich beendet.
- Browser: dazugehöriges HA-Frontend **20260826.7**, Playwright **1.63.0** und
  Chromium. Abhängigkeiten: `tests/browser/requirements.txt`.
- Das reale Ziel meldete bei ausschließlich lesender Statusabfrage HA Core
  **2026.9.2** auf **aarch64 / Yellow**, HACS **2.0.5**. Dieser Versionsabgleich
  ist kein Test der Integration auf dieser Hardware.

In einer isolierten Linux-Umgebung, jeweils mit den Abhängigkeiten aus
[`.github/workflows/tests.yml`](../.github/workflows/tests.yml):

```sh
python -m unittest discover -s tests -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
python -m pip install -r tests/browser/requirements.txt
python -m playwright install --with-deps chromium
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/browser -v
```

Privates Replay separat, mit den Abhängigkeiten aus `requirements-replay.txt`:

```sh
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python -m unittest discover -s tests -p test_stream_replay.py -v
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder-2026-09-17.zip \
  python -m unittest discover -s tests -p test_candidate.py -v
```

Frühere lokale HA-Läufe unter macOS/Homebrew-Python endeten zweimal mit SIGSEGV
(Exit 139). Ein vorher ausgegebenes `unittest OK` wurde nicht als erfolgreicher
Gesamtprozess gewertet. Seitdem werden HA-Tests ausschließlich unter Linux
ausgeführt. Reine lokale Kerntests und Replay importieren Home Assistant nicht.

## Noch nicht geprüft beziehungsweise bewusst zurückgestellt

- HACS-Installation, Schaltwirkung, Sensorkalibrierung und Zusammenspiel mit den
  mechanischen Schutzkomponenten am echten Ofen. Installation, Neustart und
  betreuter erster Betrieb erfolgen durch den Nutzer: [Testinstallation](testinstallation.md).
- Ein optionaler realer Leistungsmesser ist noch nicht vorhanden. Sein Pfad ist
  mit HA-Testentitäten geprüft; die Genauigkeit eines späteren Geräts nicht.
- Temperaturkrümmung als Erkennung eines internen Ofen-Aus ist auf ausdrücklichen
  Nutzerwunsch für die erste Installation zurückgestellt. Keine verworfene feste
  0,5-Grad-/5-Minuten-Regel wurde ersatzweise eingebaut.
- Nicht festgelegte Fristwerte und örtliche Einstellungen müssen vor Heizfreigabe
  gesetzt werden. Ohne gültige obere Regeltemperatur wird kein unterer Ersatzwert
  angenommen. Die Ein-Sensor-Ereigniserkennung ist davon getrennt geprüft.

Es gab keine Installation, keinen Neustart und keine Schaltbefehle auf dem
Saunasystem. Versionsnummer und eingefrorener Kandidat sind unverändert; es
wurde kein Release erzeugt.
