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

Paket A: Remote-Commit `cfcb28db484f6b5792d674ddd8773837c7b8a796`,
[CI 35444407353](https://github.com/grisey/HA_Sauna/actions/runs/35444407353)
tatsächlich abgeschlossen: core, ha-api und ha-integration jeweils success.
Drei echte HA-Lebenszyklustests bestanden unter Linux/Python 3.14.2.

## Paket B1: Betrieb, Session und Gangfortschreibung

Der eigene Betriebsschalter verwendet den Controller. Aus beendet den Gang
sofort; bestätigte beendete Gänge zählen anhand ihrer Aufgüsse genau einmal.
Kurze Unterbrechung erhält nur die Session. Nach Sessionfrist entsteht beim
Einschalten eine neue Session mit eigenen Unterobjekten. Alte Fristen können
nicht wirken. Eine HA-Zeitregistrierung führt fällige Übergänge aus und wird
beim Unload abgemeldet.

Die am 19.09. geklärte Aufhebung bei Fristablauf bzw. Durchlüften vor Aufguss
ist im bestehenden Timeline-Kern implementiert. Neue Personensignale derselben
Episode starten keine neue Frist; Aufguss bleibt unabhängig verwertbar.
Grundkonfiguration ist während der gesamten Session zentral gesperrt.
`tests/test_operation.py` und der zusätzliche echte HA-Betriebsschaltertest
prüfen diese Ketten. Der CI-Abschluss dieses Pakets wird separat nachgetragen.

## Paket B2: Heizzeit, Bereitschaft, Nachlauf und Kühlung

`core/heating.py` zählt ausschließlich rückgemeldetes Heizen, trennt unbekannt
von nachgewiesen aus und erhält Heizintervalle bei lokaler Rücksetzung.
`core/thermostat.py` verwendet Solltemperatur plus Bereitschaftsaufschlag,
Hysterese und separaten Cooldown. Sicherheitsgrenze und Betrieb-Aus bleiben
auch im Gang wirksam. Fehlende Temperaturkonfiguration sperrt die Heizung.

`tests/test_cooling.py` führt vollständige Gang-/Fristketten aus: fortgesetzter
Gang bei Budgetende, Nachlauf kürzer/gleich/länger als Kühlung, einmalige
Gutschrift, Sperre neuer Gänge einschließlich rückwirkender Startzuordnung,
unveränderter Nachlauf bei Aus/Ein, einmalige Budgetreduktion und neue Session.
`test_heating.py` und `test_thermostat.py` prüfen Rückmeldung und Regellogik.

Lokaler reiner Standardbibliothek-Lauf mit `python3.14 -S -m unittest discover
-s tests -v`: 109 gesammelt, 108 bestanden, privates Replay in diesem Lauf
nicht aktiviert (ein Skip), Prozess beendet mit Exit 0. Kein HA-Import und
keine nativen HA-Abhängigkeiten geladen. HA-Nachweis ausschließlich Linux-CI;
deren Ergebnis wird erst nach tatsächlichem Abschluss ergänzt.
Noch kein Aktorpfad, Messdetektor, Archiv oder Browsernachweis in diesem Paket.

Paket B1: Commit `5f9cf332e71fb11a41fdc19b285cde7e9a9fb55b`,
[CI 35444853634](https://github.com/grisey/HA_Sauna/actions/runs/35444853634): alle Jobs success.
Paket B2: Commit `39530a5ef42aff6609e808de56b7457029ae2da8`,
[CI 35445966589](https://github.com/grisey/HA_Sauna/actions/runs/35445966589): alle Jobs success.

## Paket C: kausale Erkennung und Expertenparameter

`core/detector.py` verwendet nur empfangene Daten und begrenzte Arbeitsfenster,
getrennt nach funktionaler Sensorrolle. Zeitstempelprüfung, Gültigkeitsfrist,
fehlende/plausible Werte, dynamischer Ausfall, Wiederkehr und Quellenwechsel
sind synthetisch geprüft. Kein Höhenoffset, keine Mittelung beider Positionen.
Ein Quellenwechsel verwirft die zugehörige Historie und alte Haltebelege.

`test_stream_replay.py` wurde lokal mit dem privaten Export ausgeführt:
**bestanden, Exit 0**. Jeder Rasterpunkt der Türlage, Personenpfade und
Aufgussbedingung sowie sämtliche Tür-/Lüftungsereignisse stimmen in allen drei
Sensormodi mit der unveränderten Referenz überein. 15 Öffnungsepisoden je Modus.
Der Vergleich umfasst die vier bekannten Gänge und sämtliche Vergleichszeiten;
dieselbe Kalibrierung bleibt keine unabhängige Feldvalidierung.
Nur NumPy/Pandas für den Referenzcode, keine lokalen HA-Imports/-Prozesse.

Alle numerischen Detektorwerte stammen aus den zentralen options. Experten-
Standardwerte entsprechen exakt `candidate/parameter.json` (separater Test).
Das Ein-Sekunden-Grundraster ist eine feste Algorithmusvoraussetzung und wird
als solche validiert. Die eingefrorenen Kandidatendateien bleiben unverändert.
Zusätzliche Mindestheizzeitprüfungen belegen regulären Stop erst nach Ablauf,
aber sofortigen Vorrang von Nachlauf/Kühlung/Aus und bisherigen Schutzregeln.
Die zuletzt gewünschte neue Temperatur-Schutzregel ist noch nicht festgelegt.
