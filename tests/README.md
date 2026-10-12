# Tests und Entwicklungsvorschau

[Arbeitsregeln zur Prüfung](../AGENTS.md#prüfung)

Tests laufen ausschließlich unter Linux. Die maßgeblichen Versionen,
Prozessgrenzen, Browsergeometrien und synthetischen Lastdaten stehen in
[`settings.json`](settings.json). Die Python-Abhängigkeiten werden ausschließlich
in [`browser/requirements.txt`](browser/requirements.txt) festgelegt; die
[CI-Konfiguration](../.github/workflows/tests.yml) konsumiert diese Quellen. Alle folgenden Befehle werden
vom Repository-Hauptverzeichnis aus und jeweils als eigener Prozess ausgeführt.
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

Fachkern und Panelmethoden werden wie in der CI ausgeführt:

```sh
python -m unittest discover -s tests -v
```

```sh
node --test tests/panel_*.test.js
```

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
nur der Diensttransport zu den Aktoren wird ersetzt.

```sh
SAUNA_SESSION_EVIDENCE=/tmp/ha-sauna-sessions python -m unittest discover -s tests/sessions -v
```

`SAUNA_SESSION_EVIDENCE` speichert pro Lauf jede gebundene Bewertung mit Sollwert,
Ist-Abweichung, Bedienfolge und ausgegebenen Diensten. Die CI führt jede gebundene
Grund-ID einschließlich sämtlicher Varianten aus. Für die Fehlersuche auf Linux kann
`SAUNA_SESSION_CASE` einzelne IDs auswählen.

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
Fehlermeldung. Automatisierte Prüfungen laufen weiterhin ausschließlich unter Linux.

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
getrennte vollständige Namen der Form `test_module.Class.test_method`. Ohne
Auswahl läuft die vollständige Integration; unbekannte oder doppelte Namen sowie
leere Einträge innerhalb einer Auswahl werden abgewiesen. Die Adapterprüfung
bleibt Teil des Bereichs `ha`.
