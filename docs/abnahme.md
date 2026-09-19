# Abnahmeprotokoll

Arbeitsbeginn 19.09.2026: `main`, HEAD `50e072e19c2bc1be6f207ab56044bb7509a80e2b`,
frischer sauberer Klon. Keine fremden Arbeitsänderungen. Die dokumentierte
Paket-1-CI ist tatsächlich abgeschlossen (Run 35421892074, success), aber kein
Nachweis einer vollständigen Betriebsintegration.

## Anforderung → Code → ausführbare Prüfung → fehlender Nachweis

| Anforderung | Implementierung | Prüfung | Zu Beginn fehlend |
|---|---|---|---|
| Einrichtung/Zuordnung/Parameter | config_flow, bindings, parameters | tests/ha/test_adapter.py | Echte Manager, Entitäten, Persistenz, Reload |
| Session/Gang/Fristen | core/controller, models, timeline | test_foundation, test_timeline | Betriebsaktionen, fachliche Fristfolgen, Heizzeit, Regelung |
| Fortlaufende Erkennung | nur candidate/replay.py | test_candidate | HA-Messstrom, Ausfall/Rejoin, Quellenwechsel |
| Archiv/Export/Backup | bisher kein Code | bisher keine | SQLite, authentifizierter Abruf, echtes Backup/Restore |
| Gerätepfad | bisher passive runtime | bisher nur API-Smokes | Service plus tatsächliche Rückmeldung |
| Sessionansicht | bisher kein Code | bisher keine | Live-/Historienansicht und Browserprüfung |

## Umgebung und Ausgangsbefund

Home Assistant **2026.9.2** mit allen Paketabhängigkeiten installiert; Python
**3.14.7**, macOS arm64. Offizielle Paketmetadaten verlangen Python >=3.14.2.
Schnittstellen werden gegen den installierten Quellcode dieser Version geprüft.
CI verwendet separat Linux und Python 3.14.2. Diese Umgebungen werden nicht als
Hardwareabnahme ausgegeben.

Ausgangsprüfung: 82 Tests gesammelt, **81 bestanden**, **1 Replay übersprungen**;
11 API-Smokes bestanden. Nach Angabe des Dateipfads wurde der private Export
erneut vollständig geprüft: 82 Tests bestanden, kein Skip, Prozess-Exitcode 0.
Quellprüfsumme und vollständiger Replay-Ergebnisvergleich stimmen unverändert.
Der Export und seine personenbezogenen Rohdaten bleiben außerhalb des Repos.

Die lokalen HA-Lebenszyklustests führten beim Beenden des Homebrew-Python zu
zwei nativen SIGSEGV-Abstürzen (Exitcode 139). Ein zuvor ausgegebenes unittest
OK ist deshalb **kein bestandener Gesamtprozess**. Der Crash-Stack liegt in
`dictkeys_decref` / `finalize_modules`; die verursachende native Komponente ist
noch unbestimmt. Lokale HA-Läufe sind gestoppt. Weitere HA-Nachweise erfolgen
in der isolierten Linux-CI. Ein neuer Testfall wurde zudem an HA 2026.9.2s
tatsächliche InvalidData-Schemaprüfung angepasst; negative Werte werden vor
dem Flow abgewiesen, Nullwerte vom fachlichen Validator im Flow.

## Paket A: tatsächlicher HA-Lebenszyklus

Eigene Number-Entitäten und Phasenanzeige, stabile Geräte-/Entitätsidentitäten.
Config Flow, Optionsdialog und Number-Service schreiben dieselben options.
Ein Update-Listener lädt Änderungen; kein zusätzlicher OptionsFlow-Reload.
Die Integration legt beim Setup keine Session an und schaltet keine Geräte.

`tests/integration/test_lifecycle.py` startet einen echten HA-Kern mit echten
ConfigEntry-Managern, Registries, Plattformen und Services in temporärer Ablage.
Nur externe Sensorzustände sind synthetisch. Geprüft werden Flow, eigene
Entitäten, Options-/Number-Änderung, dreifaches Unload/Setup, Quellenwechsel,
Persistenz in einer neu gestarteten HA-Instanz und ungültige Eingaben.

Reproduzierbare Befehle (Python aus Umgebung mit exakt installiertem HA):

```sh
python -m unittest discover -s tests -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
```

Noch keine Gesamtfreigabe: Betriebssteuerung, Messpfad, Archiv, Backup/Restore,
Browser und private Replay-Prüfung folgen separat. Reale Hardware ist nicht
Teil der autorisierten Prüfungen.
