# Implementierungsstand

Die erste Testinstallation erfolgt durch den Nutzer über HACS. Die Vorbereitung
ändert kein Produktions-HA; kein Versionssprung und kein Release. Maßgeblich für tatsächlich
ausgeführte Prüfungen ist der [Abnahmebericht](abnahme.md).

| Paket | Implementierung | Ausführbare Prüfung |
|---|---|---|
| A – Grundgerüst | Config Flow, Optionen, Rollen, eigene Entitäten, Setup/Unload/Reload | `tests/ha/test_adapter.py`, `tests/integration/test_lifecycle.py` |
| B – Betrieb | Session, Gang, Fristen, gezählte Heizzeit, Thermostat, Nachlauf/Kühlung, Türwartefrist | `tests/test_operation.py`, `test_timeline.py`, `test_heating.py`, `test_thermostat.py`, `test_cooling.py` |
| C – Messverarbeitung | Fortlaufender Kandidat, Sensorfehler/Rejoin/Quellenwechsel, Expertenwerte | `tests/test_detector.py`, `test_stream_replay.py`, `test_candidate.py` |
| D – Speicherung | SQLite, Revisionen, konsistenter ZIP-Export, HA-Backup-Hooks | `tests/test_archive.py`, `tests/integration/test_archive_backup.py` |
| E – Gerätepfad | Reale HA-Listener/Services, getrennte Rückmeldung, Licht, Fehler und Timerhinweise | `tests/integration/test_device_path.py` |
| F – Oberfläche | Übersicht/Details, phasenbezogene Zeit, live änderbares Temperaturprogramm, Sessionarchiv, Designvorlage, Erkennungskontrolle, Einstellungen/Export | `tests/integration/test_panel_api.py`, `tests/browser/test_panel.py` |
| Lichtnachlauf nach Sitzungsende | Standard 10 Minuten bei 50 %, danach aus; neue Sitzung verwirft die alte Lichtfrist | `tests/test_session_light.py`, `tests/integration/test_device_path.py` |
| Leistung und Energie | Optionaler Leistungsmesser, Schützschätzung und Sessionenergie mit Standardleistung 4,5 kW | `tests/test_power.py`, `tests/test_energy.py`, `tests/integration/test_device_path.py` |

## Umgebungen und Grenzen

Der Fachkern läuft mit Standardbibliothek ohne Home Assistant. API-Smokes verwenden
HA-Schemas und klar abgegrenzte Manager-Testdoubles. Die Integrationstests starten
einen tatsächlichen HA-Kern mit ConfigEntry-Manager, Registries, Entitäten,
Listenern, Services, Authentifizierung, HTTP, SQLite und offizieller Backup-Routine.
Nur externe Geräte und Messreihen sind synthetisch. Browserprüfungen laden das
zu HA 2026.9.2 gehörende echte Frontend in Chromium.

HA wird nach den zwei lokalen Python-Abstürzen ausschließlich in Linux-CI
ausgeführt. Lokale reine Kerntests und privates Offline-Replay importieren kein HA.
Ein grüner Einzeltest ersetzt keinen erfolgreich beendeten Gesamtprozess.

Noch keine reale Hardwareabnahme: tatsächliche Messkalibrierung, Heizrückmeldung,
Schutzkomponenten, sichere Schaltwirkung und Montagebedingungen müssen vor
Produktivbetrieb am konkreten Aufbau geprüft werden. Ohne gültige obere
Temperatur wird keine alternative Regeltemperatur angenommen. Die zusätzliche
temperaturabhängige Türregel ist eine begrenzte Ergänzung für das Aufheizen;
eine Aufheizzeitprognose ist nicht als neue Regel freigegeben.
