# Tests und Entwicklungsvorschau

[Arbeitsregeln zur Prüfung](../AGENTS.md#prüfung)

Tests laufen ausschließlich unter Linux in GitHub Actions auf GitHub-gehosteten
Runnern. Versionen, Prozessgrenzen, Browsergeometrien und synthetische Lastdaten
stehen in [`settings.json`](settings.json), die Python-Abhängigkeiten in
[`browser/requirements.txt`](browser/requirements.txt). Die
[CI-Konfiguration](../.github/workflows/tests.yml) konsumiert diese Quellen.
Alle folgenden Befehle werden in diesen GitHub-Jobs vom Repository-Hauptverzeichnis
aus und jeweils als eigener Prozess ausgeführt. Lokale oder per Remote-Shell
gestartete Tests sind nicht vorgesehen.
Für jeden Prozess wird sein tatsächlicher Exitcode festgehalten.

## Prüfebenen

| Ebene | Zweck | Dateien |
| --- | --- | --- |
| Sitzungen und Replay | Vollständige Nutzungsabläufe mit gebundenen Messwerten, Bedienungen und unabhängigen Bewertungen. | `tests/sessions/` |
| Fachkern und Runtime | Verbleibende Komponentenverträge, Datenintegrität, Fehler- und Zeitgrenzen. | `tests/test_*.py` |
| Panelmethoden | Datenaufbereitung, Bedienzustände und Diagrammverträge mit synthetischen Daten. | `tests/panel_*.test.js` |
| HA-Adapter | Einrichtung und Adapterverhalten mit HA-Datentypen und gezielt ersetzten Außenaufrufen. | `tests/ha/` |
| HA-Integration | Laufende Testinstanz mit HA-Listenern, Diensten, Rechten und HTTP-Schnittstellen; externe Geräte sind Testquellen. | `tests/integration/` |
| Browser | Bedienung im HA-Frontend über Chromium. | `tests/browser/` |

## Fachkern und JavaScript

Fachkern und Panelmethoden werden in GitHub Actions ausgeführt:

```sh
python -m unittest discover -s tests -v
```

```sh
node --test tests/panel_*.test.js
```

Die Benachrichtigungsgrundformen werden durch `test_notification_recipients.py`
und `test_notification_exchange.py` im Fachkernlauf erfasst. Die synthetischen
Eingaben prüfen Identität, gemeinsame Eignungsvalidierung, Mengenbildung und
Nachrichtenzuordnung. Diese Prüfungen enthalten keinen HA-Adapter und belegen
weder Empfang noch Rückmeldungen eines tatsächlichen Companion-Clients.

Die [Modellübergaben](../docs/modelluebergaben.md) werden durch
`test_model_handoff.py`, `test_control_handoff.py`,
`test_observation_handoff.py`, `test_thermal_handoff.py`,
`test_input_handoff.py`, `test_interaction_handoff.py`,
`test_system_handoff.py` und `test_presentation_handoff.py` mit vorhandenen
Kern-, Archiv- und Vorschaufixtures geprüft. Der vorhandene Integrationstest
`test_direct_presence_ids_are_private_in_live_and_archived_public_responses`
in `integration/test_panel_api.py` prüft zusätzlich die vollständige Übernahme
einer echten StateView-HTTP-Antwort und die unveränderte öffentliche Projektion.
Die Fälle verwenden tatsächliche Modellrückgaben und prüfen vollständige Belege,
Identitätsbindung, Feldtypen und Ablösung veränderlicher Quelldaten. Die
explizite Übergabe an einen Testverbraucher belegt den Datenvertrag; eine
automatische Ereignissammlung oder ein Benachrichtigungsablauf ist nicht
Bestandteil dieser Prüfungen.
Die Taster- und Korrelationsfälle prüfen auch, dass eine Beobachtung den
nächsten tatsächlichen Verarbeitungsschritt nicht verbraucht. Quellenfreie
Eingangsformen bewahren unbekannte Werte, unterscheidbare Vorkommnisse und
separate Zeiten. Die identische Übergabe zweier explizit gewählter Fachwerte
belegt keine bereits implementierte Quellenwahl oder Controllerumstellung.

Der HA-Job legt mit `HA_SAUNA_HANDOFF_EVIDENCE` zusätzlich das Artefakt
`ha-system-forms/system-forms.json` an. Es enthält die vollständigen tatsächlichen
Admin-/Public-StateView-, `Archive.read`- und Archiv-API-Rückgaben einschließlich
Verlaufsprojektion sowie ihre Form-/Wirewerte. Das Manifest nennt Testfall,
isolierte synthetische HA-Fixture und ausgecheckten GitHub-Commit. Jede
Beobachtung benennt ihren Erzeuger und die verwendete Form; Original und
Wirewert bleiben direkt vergleichbar. Die Aufbewahrung in Actions beträgt
sieben Tage. Diese Werte belegen die Übernahme vorhandener Produzentenwerte,
keinen angeschlossenen Laufzeitverbraucher und keine reale Geräte- oder
Companion-Verbindung.

Szenarien und die gebundenen realen Replay-Aufzeichnungen sind im Checkout enthalten.
Die Manifeste binden Daten und Bewertungen durch Prüfsummen. Fehlende oder
veränderte Dateien führen zu einem Fehler; private Pfade und Umgebungsvariablen
sind keine Ausführungsbedingung. Der eingefrorene historische Kandidat bleibt
als [Reproduktionswerkzeug](referenz-replay.md) erhalten. Seine alten
Programmausgaben sind kein Maßstab für die aktuellen Produktprüfungen.

[Sitzungsabläufe, Messkurven und Bewertungen](sessions/README.md)

Konfigurierbare Fristen, Schwellen, Eingabegrenzen und Standardwerte werden aus
`custom_components/ha_sauna/defaults.json` konsumiert. Komponenten verwenden
`Parameters({})`; Panel-Fixtures lesen denselben Katalog über `catalog_fixture.cjs`.
Prüfungen an einer Grenze leiten ihre Zeitpunkte oder Eingaben aus diesem Wert ab,
statt den Werkseinstellungswert zu wiederholen. Lange Fristen werden über die
virtuelle Uhr abgebildet; dafür werden keine kürzeren Regelparameter eingesetzt.

Synthetische Lastdaten, Browserabmessungen und Messabläufe werden über
`test_settings.py` beziehungsweise `settings_fixture.cjs` aus `settings.json`
gelesen. Die Entwicklungsvorschau konsumiert ihren eigenen
[`tools/preview/defaults.json`](../tools/preview/defaults.json). Konstruierte
Sitzungen verwenden die benannten Eingaben in
[`fixtures/scenarios/construction.json`](fixtures/scenarios/construction.json).
Diese Eingaben beschreiben Bedienfolgen und Messumgebung; die unabhängigen
Bewertungen bleiben in den gebundenen Szenarien. Einzelne Zahlen zur Prüfung
mathematischer Eigenschaften, ungültiger Eingaben oder einer konkreten
Bedienhandlung definieren das jeweilige Prüfziel.

`catalog_fixture.cjs` löst Parameter- und Darstellungsreferenzen des
Produktkatalogs auf und stellt den gleichen Panel-Bootstrap wie Home Assistant
bereit. Die Klassifikation `configuration_surface` bestimmt auch in Tests,
welche Werte aus dem Katalog und welche aus gespeicherten Einstellungen kommen.
Gezielte Metatests prüfen Defaultkonsum, abweichende gültige Werte, die
Schreibgrenzen sowie die Unveränderlichkeit des Katalogs beim Speichern.

Die Szenariobindung erfasst die effektiven Parameter- und Programmwerte.
Änderungen an Beschriftungen oder UI-Standards verändern diese Eingaben nicht.
Veränderte effektive Werte erfordern eine Prüfung der Bindung und betroffenen
Kurven; Materialisierung ist nur erforderlich, wenn Eingaben tatsächlich anders
werden sollen. Die Kurven, Bedienfolgen, Bewertungen und Konstruktionsdaten
bleiben separat durch SHA-256 gebunden. Historische Replays behalten ihre
aufgezeichnete Konfiguration.

## Home Assistant

Die HA-Prüfungen verwenden eine eigene Python-Umgebung gemäß Workflow.
Installation wie im HA-Job:

```sh
python tests/ci_settings.py install-ha
```

Danach werden Adapter und Integration getrennt ausgeführt. `HA_TEST_REQUIRED=1`
macht die erforderliche HA-Umgebung zur Ausführungsbedingung:

```sh
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
```

```sh
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
```

Die vollständigen Sitzungsprüfungen laufen mit derselben HA-Version. Sie verwenden
den echten Geräteadapter, die Laufzeit, Erkennung, Regelung und Archivierung;
nur der Diensttransport zu den Aktoren wird ersetzt. Im GitHub-gehosteten
Sitzungsjob wird das Artefaktverzeichnis vom Workflow gesetzt:

```sh
python -m unittest discover -s tests/sessions -v
```

`SAUNA_SESSION_EVIDENCE` speichert pro Lauf jede gebundene Bewertung mit Sollwert,
Ist-Abweichung, Bedienfolge und ausgegebenen Diensten. Die CI führt jede gebundene
Grund-ID einschließlich sämtlicher Varianten aus. Für die Fehlersuche in GitHub Actions kann
`SAUNA_SESSION_CASE` einzelne IDs auswählen.

Die vorhandenen synthetischen Fälle `G01` und `G01-proxy` enthalten zusätzlich
`producer_controller_trace`. Transparente Wrapper beobachten ausschließlich
tatsächliche `consume_*_input`-Aufrufe des Controllers; Rückgaben und FIFO bleiben
erhalten. Die Auswahl behält die erste Wert-/Unknown-Kategorie je Consumer und
Änderungen diskreter Steuerzustände. Fortlaufende Temperatur-, Zeit- und
Energieänderungen allein erzeugen keine weitere Zeile. Gesamt-, gespeicherte und
ausgelassene Aufrufzahlen sind getrennt ausgewiesen; dies ist kein vollständiges
Aufrufprotokoll. Jede gespeicherte Zeile zeigt den tatsächlichen Producer,
normalisierten Input einschließlich `null` und Originalzeiten sowie die
unmittelbar beobachtete kleine Controllerfolge. Der referenzierte abgeschlossene
Verarbeitungsschritt zeigt anschließend Heat/Grund, Phase, Gang, letztes
Heizintervall, Energieaggregat, Kühlbeginn und Bedienzustand. Bestehende
Originalereignisse bleiben in `detections` und `consumer_events` des Fallartefakts.
IDs werden nur im Trace durch stabile lokale Aliase in Reihenfolge ihres ersten
Auftretens ersetzt; Produktwerte bleiben unverändert. Der Trace verwendet die
gebundene virtuelle Uhr und deterministische JSON-Serialisierung.

`run-metadata.json` trennt PR-Head und dessen Tree vom tatsächlich ausgecheckten
Commit und Tree und nennt deren Vergleich, Ref, Run, Versuch, Job und Fall.
`process.log`, `exit-code.txt` und `process-completion.json` belegen den eigenen
Testprozess und seine Rückkehr; bei Abbruch vor Prozessende fehlt der
Abschlussbeleg. Der endgültige Jobstatus steht zusätzlich in GitHub Actions. Ein Fehler beim
Schreiben der Nachweise lässt den Schritt scheitern; `exit-code.txt` enthält
weiter den zuvor gesicherten tatsächlichen Testprozess-Exitcode.
Derselbe Prozessvertrag gilt für `core-tests`, `ha-adapter-tests` und
`ha-integration-tests`: eigener Log, Exitcode und JSON-Abschluss sowie
Commit-/Tree-Nachweis mit getrenntem PR-Head. Bei Dispatch oder Push steht
`pr_head` auf `null`, der Treevergleich auf `no_pr_head`. Profilierte Sitzungen
nennen den tatsächlich gestarteten Profilierungsbefehl. Die selektiven
HA-Adapter- und Integrationsprozesse werden jeweils höchstens einmal gestartet.
Der gemeinsame CI-Helfer [`ci_process.py`](ci_process.py) zeichnet jeden
aufgerufenen Testprozess einmal auf. Die Panel-Node-Probe liegt unter
`core-tests/panel`; die vier Browser-Node-Prozesse und der Python-Browserlauf
haben getrennte Verzeichnisse unter `browser-views/processes`. Deren Logs,
Exitcodes und JSON-Abschlüsse verwenden denselben Metadatenvertrag. Die
bestehenden Artefaktsicherungen laufen auch nach einem fehlgeschlagenen Schritt.
Die Sitzungsartefakte belegen synthetische Fixtures mit ersetztem HA-Transport,
keine Hardware- oder Companionausführung. `runtime_consumer_connected` ist nur
bei tatsächlich beobachteten Eingangsaufrufen wahr. Der getrennte
Systemformnachweis bleibt ein Formnachweis ohne diese Laufzeitaussage.

[`coverage_ownership.json`](coverage_ownership.json) bindet entfernte
Fachduplikate an ihre Sitzungsbewertungen. Ein Metatest prüft, dass die genannten
Bewertungen vorhanden sind und die ersetzten Methoden nicht erneut parallel
ausgeführt werden. Eigene Prüfungen für Rechte, Transport, Ausfälle, Zeitstempel,
Anzeige und Speicherformate behalten ihre jeweiligen Schnittstellenverträge.

`tests/integration/test_archive_backup.py` erzeugt über die offizielle
Home-Assistant-Routine ein Core-Backup und stellt es in einem getrennten
Konfigurationsverzeichnis wieder her. Der Test startet dort eine neue Instanz
und vergleicht Optionen, Originaldaten und Sitzungszuordnungen. So wird der
vollständige HA-Wiederherstellungsweg geprüft; die SQLite-Snapshotprüfung gehört
daneben zum Archivexport.

## Browser

Die Browserumgebung verwendet dieselbe Python-Version wie der HA-Job.
Ihre vollständigen Abhängigkeiten stehen in
[`tests/browser/requirements.txt`](../tests/browser/requirements.txt):

```sh
python -m pip install -r tests/browser/requirements.txt
```

```sh
python -m playwright install --with-deps chromium
```

```sh
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/browser -v
```

[Mess- und Interaktionsprogramme für den Sitzungsverlauf](#sitzungsverlauf-im-browser)

## Lokale Bedienvorschau

`python3 tools/local_preview.py` stellt das produktive Panel unter
`http://127.0.0.1:8765` bereit. Die Regelentscheidungen kommen aus dem produktiven
Controller. Sensoren, Uhr und Ofenrückmeldung sind simuliert; es gibt keinen
HA-Zugang und keine Aktorausgabe. Szenarien sind zurücksetzbar. Türbewegung,
Präsenz und Ofenrückmeldung können getrennt eingegeben werden.

Die Vorschau unterstützt Betrieb, Temperaturprogramme, Solltemperatur und manuelle
Ofenwahl. Die Lichtwerte folgen der produktiven Lichtplanung. „Steuerung folgen“
führt die simulierte Ofenrückmeldung nach; „Ein“, „Aus“ und „Unbekannt“ halten sie
fest. Beendete Sitzungen bleiben bis zum Zurücksetzen des Szenarios im Verlauf.
Nicht angebundene Einstellungsaktionen zeigen eine
Fehlermeldung. Automatisierte Prüfungen laufen ausschließlich in GitHub Actions.

## Sitzungsverlauf im Browser

Für Browserprüfungen werden Node, Playwright als Entwicklungswerkzeug und eine
installierte Browserlaufzeit benötigt. Die folgenden Aufrufe erfolgen aus dem
Repositoryverzeichnis.

```sh
node tests/browser/live_history/accept.cjs
node tests/browser/live_history/profile.cjs
node tests/browser/live_history/interaction.cjs
node tests/browser/live_history/details.cjs
```

| Skript | Gegenstand und Ausgabe |
|---|---|
| `accept.cjs` | Prüft Datenübernahme, dauerhafte Knoten und Browserinteraktionen anhand fester Erwartungen. |
| `profile.cjs` | Erfasst Kaltstart, Aktualisierung, Geometriezugriffe und `requestAnimationFrame`-Abstände. |
| `interaction.cjs` | Erfasst Zeigerbewegung, Zoom und Verschieben während eingehender Nachträge; trennt die Zeit bis zum Rendercallback-Ende von dessen synchroner Rechenzeit. |
| `details.cjs` | Prüft stabile Erkennungsansichten, Fokus, Textauswahl, Markerverknüpfung und Nachträge sowie schmale Container und Fehler bei Archivabrufen. |

Die Fixture liefert synthetische Status- und Archivantworten an das
Originalpanel. Die Browserläufe verwenden dessen tatsächliche Zeichen- und
Eingabemethoden. In der WebKit-Interaktionsprobe werden Zoomimpulse als
Browser-`WheelEvent` mit gesetztem Strg-Modifikator eingespeist. Die Ergebnisse
beschreiben die im Skript zugestellten Eingaben bis zur gemessenen
Browserverarbeitung. Eine Messung auf einem verwendeten Endgerät erfasst
zusätzlich dessen Eingabeweg und sichtbare Bildschirmausgabe.

`HISTORY_EVIDENCE` legt das Ausgabeverzeichnis von `accept.cjs` und
`profile.cjs` fest. `INTERACTION_EVIDENCE` benennt die Ergebnisdatei der
Interaktionsprobe. Der Standardbrowser wird in `settings.json` festgelegt; `ENGINE` und
`CHROMIUM_EXECUTABLE` wählen bei Bedarf eine vorhandene Browserlaufzeit.
`details.cjs` speichert Ergebnisse und
Ansichten im Verzeichnis `DETAILS_EVIDENCE`.

Alle vier Skripte prüfen den aktuellen Arbeitsstand von `panel.js`. Die vier
synthetischen Messreihen bleiben im Archiv erhalten; die aktuelle Verlaufsansicht
zeigt die führende Messposition. Eine frühere Messhöhenumschaltung und fest
codierte Gitvergleichsstände sind kein Bestandteil dieser Prüfungen.

`accept.cjs` bedient Zoomknöpfe, Verlaufsnavigation und die Übersicht mit
Browserinteraktionen. Die Tooltipprüfung verlangt die sichtbaren Messgrößen und
Einheiten; `interaction.cjs` prüft zusätzlich deren Werte gegen die unabhängig
festgelegten Bereiche der synthetischen Daten. Interne Aufrufzähler dienen nur
der Beobachtung von Zeichenarbeit und Knotenerhalt. Archivfehler und
Sitzungswechsel werden über das synthetische Transportfixture vorgegeben.

Der GitHub-Workflow `HA Sauna UI` prüft Zweige unter `codex/ui-*` auf Ubuntu.
`tests/browser/run_ui.py` führt die Panelmethoden, Panel-API-Tests, vier
Verlaufsproben und die Home-Assistant-Browsertests aus. Jeder Prozess erhält einen
eigenen Exitcode und Abschlussnachweis; übersprungene Tests lassen die Prüfung
fehlschlagen. Die Artefakte enthalten den geprüften Commit, Tree, Dateiprüfsummen,
Protokolle und Browserbilder.

Der getrennte Formatjob vergleicht mit der über `UI_BASE_REF` festgelegten
Entwicklungsbasis. Er formatiert geänderte JavaScript-Dateien mit Prettier
und neue Python-Dateien mit Ruff in den Versionen aus `settings.json` und liefert notwendige Änderungen als Patch.
Der Testjob prüft den unveränderten ausgecheckten Commit.

Gezielte Korrekturläufe können im manuellen Workflow über `checks` ausgewählt
werden: `panel`, `panel-api`, `details`, `accept`, `profile`, `interaction` und
`ha-browser`, durch Kommas getrennt. `browser_tests` begrenzt den HA-Browserlauf
auf durch Leerzeichen getrennte vollständige unittest-Namen. Leere Felder führen
den vollständigen UI-Prüfumfang aus; unbekannte Namen führen zu einem Fehler.
Die Prozessnachweise nennen die tatsächliche Auswahl. Bestandene Prüfungen
bleiben nur bei belegbar unveränderten Abhängigkeiten gültig. Reine Testkorrekturen
erfordern keine erneute Ausführung unveränderter Szenarien und Replays.
`format_only` führt ausschließlich den Formatjob aus, wenn lediglich dessen Patch
übernommen wurde; die vorhandenen Testnachweise werden dem Stand zugeordnet.

Der Workflow `HA Sauna` bietet für gezielte Wiederholungen `checks` mit den
Bereichen `core`, `ha`, `browser` und `sessions`. `session_cases` begrenzt den
Sitzungsbereich auf durch Kommas getrennte IDs aus den Szenario- und
Replaymanifesten. Ohne Auswahl laufen alle Bereiche und Fälle; unbekannte oder
doppelte IDs werden abgewiesen. Eine Szenario-ID umfasst ihre gebundenen
Varianten. Die Auswahl verändert weder Eingaben noch Bewertungskriterien.
`integration_tests` begrenzt innerhalb von `ha` die Integration auf durch Kommas
getrennte vollständige Namen der Form `test_module.Class.test_method`.
Unbekannte oder doppelte Namen sowie leere Einträge innerhalb einer Auswahl
werden abgewiesen. Bei gesetztem Filter laufen ausschließlich diese
Integrationstests. Ohne Auswahl laufen Adapterprüfung und vollständige Integration.

`core_tests` begrenzt `core` auf vollständige Python-Testnamen derselben Form.
Die Auswahl wird aus dem Testquelltext geprüft, ohne Testmodule zu importieren.
Bei gesetztem Filter laufen ausschließlich diese Python-Tests; die Paneltests
gehören dann nicht zum ausgewählten Umfang. Ohne Filter bleiben vollständige
Python- und Panelprüfung erhalten. Leere Testsuiten, Fehler und übersprungene
Python-Tests führen zu einem fehlgeschlagenen Core-Prozess. Das Artefakt
`core-tests` enthält Commit, Tree, Prozessprotokoll, Exitcode und Abschlussstatus.

Die manuelle Auslösung (`workflow_dispatch`) des Workflows `HA Sauna` mit
`proxy_calibration_analysis=true` führt ausschließlich die Proxy-Kalibration
aus. Diese Auswahl hat Vorrang vor allen anderen Analyseflags und Prüfbereichen.
[`analysis/proxy_calibration.py`](analysis/proxy_calibration.py) konsumiert die
gebundenen Originalmessungen und die separat geführten
[Kalibrationsvorgaben](analysis/proxy_calibration_policy.json) und
[Referenzen](analysis/proxy_calibration_references.json).

R01–R03 bilden die Trainingsgrundlage. R02 und R03 teilen die im
[`replay_manifest.json`](replay_manifest.json) festgelegte Zeitgruppe einschließlich
der tatsächlichen Lücke; lokale Messzeiten und Meldungsalter bleiben erhalten.
R04 wird erst mit eingefrorenen Profilen, Methoden und Fehlerbändern bewertet und
geht nicht in den Parameterfit ein. Die Aufzeichnung war bereits gesichtet und
ist deshalb keine unberührte unabhängige Validierungsmenge.

Das Artefakt `proxy-calibration` enthält `output/profile.json`,
`output/calibration.json`, `output/quality.json` und
`output/ambiguous-cases.json` sowie die zugehörigen Originalkurven unter
`output/curves/`. Commit, Tree, Produkt-Tree, stdout/stderr, eigener Exitcode und
Prozessabschluss liegen daneben. Die Auswertung prüft mathematische Kernelprofile
und Hypothesen; sie ist noch kein produktiver Controller-Replay mit Regelung,
Aktorausgaben und Archivierung.

Mit zusätzlichem `proxy_calibration_baseline_run` verwendet derselbe Auftrag das
Artefakt eines abgeschlossenen Kalibrationslaufs für begrenzte Ursachenvergleiche.
[`analysis/proxy_calibration_diagnosis.py`](analysis/proxy_calibration_diagnosis.py)
prüft zunächst die Reproduktion der Ausgangsergebnisse. Danach werden einzeln
festgelegte Einflussgrößen verändert. Das Entfernen einer Prüfschranke ist dabei
ein Diagnoseeingriff und keine Freigabe eines neuen Erkennungsverfahrens.
Produktiver Detector, Regelung und Aktorausgaben sind nicht Bestandteil dieses
Vergleichs. Eingangsprüfsummen, Varianten und Prozessabschluss werden im
Artefakt `proxy-calibration` mitgeführt.
`proxy_calibration_diagnosis_mode=gain_boundary` vergleicht ausschließlich die
Ausgangsprofile mit einer aus den Kalibrationsgegenbeispielen abgeleiteten
Mindestverbesserung gegenüber dem Verlauf ohne Ereignis. Formen und Fehlerbänder
bleiben dabei erhalten; R04 wird nicht zur Grenzwahl verwendet.
