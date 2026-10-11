# Tests und Entwicklungsvorschau

[Arbeitsregeln zur Prüfung](../AGENTS.md#prüfung)

Tests laufen ausschließlich unter Linux. Die maßgeblichen Versionen,
Abhängigkeiten und Zeitgrenzen stehen in der
[CI-Konfiguration](../.github/workflows/tests.yml). Alle folgenden Befehle werden
vom Repository-Hauptverzeichnis aus und jeweils als eigener Prozess ausgeführt.
Für jeden Prozess wird sein tatsächlicher Exitcode festgehalten.

## Prüfebenen

| Ebene | Zweck | Dateien |
| --- | --- | --- |
| Fachkern und Runtime | Regeln und Zustandsübergänge mit kontrollierter Zeit; Originalarchiv und Fehlergrenzen. | `tests/test_*.py` |
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

Die Variable `SAUNA_RECORDER_ARCHIVE` aktiviert die privaten Vergleichsfälle
mit einem freigegebenen Recorderexport. Der Standardlauf kennzeichnet diese
Fälle als übersprungen. Die Auswertung einer zur
Kalibrierung verwendeten Sitzung belegt den Vergleich an genau dieser Sitzung.

[Replaydatengrundlage und Reproduktion](referenz-replay.md)

## Home Assistant

Die HA-Prüfungen verwenden eine eigene Python-Umgebung gemäß Workflow.
Installation wie im HA-Job:

```sh
python -m pip install 'homeassistant==2026.10.0' 'securetar==2026.4.1' 'cronsim==2.7'
```

Danach werden Adapter und Integration getrennt ausgeführt. `HA_TEST_REQUIRED=1`
macht die erforderliche HA-Umgebung zur Ausführungsbedingung:

```sh
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
```

```sh
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
```

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
```

| Skript | Gegenstand und Ausgabe |
|---|---|
| `accept.cjs` | Prüft Datenübernahme, dauerhafte Knoten und Browserinteraktionen anhand fester Erwartungen. |
| `profile.cjs` | Erfasst Kaltstart, Aktualisierung, Geometriezugriffe und `requestAnimationFrame`-Abstände. |
| `interaction.cjs` | Erfasst Zeigerbewegung, Zoom und Verschieben während eingehender Nachträge; trennt die Zeit bis zum Rendercallback-Ende von dessen synchroner Rechenzeit. |

Die Fixture liefert synthetische Status- und Archivantworten an das
Originalpanel. Die Browserläufe verwenden dessen tatsächliche Zeichen- und
Eingabemethoden. In der WebKit-Interaktionsprobe werden Zoomimpulse als
Browser-`WheelEvent` mit gesetztem Strg-Modifikator eingespeist. Die Ergebnisse
beschreiben die im Skript zugestellten Eingaben bis zur gemessenen
Browserverarbeitung. Eine Messung auf einem verwendeten Endgerät erfasst
zusätzlich dessen Eingabeweg und sichtbare Bildschirmausgabe.

`HISTORY_EVIDENCE` legt das Ausgabeverzeichnis von `accept.cjs` und
`profile.cjs` fest. `INTERACTION_EVIDENCE` benennt die Ergebnisdatei der
Interaktionsprobe. Standardbrowser ist WebKit; `ENGINE=chromium` und
`CHROMIUM_EXECUTABLE` wählen eine vorhandene Chromium-Laufzeit.

Alle drei Skripte prüfen den aktuellen Arbeitsstand von `panel.js`. Die vier
synthetischen Messreihen bleiben im Archiv erhalten; die aktuelle Verlaufsansicht
zeigt die führende Messposition. Eine frühere Messhöhenumschaltung und fest
codierte Gitvergleichsstände sind kein Bestandteil dieser Prüfungen.

`accept.cjs` bedient Zoomknöpfe, Verlaufsnavigation und die Übersicht mit
Browserinteraktionen. Die Tooltipprüfung verlangt die sichtbaren Messgrößen und
Einheiten; `interaction.cjs` prüft zusätzlich deren Werte gegen die unabhängig
festgelegten Bereiche der synthetischen Daten. Interne Aufrufzähler dienen nur
der Beobachtung von Zeichenarbeit und Knotenerhalt. Archivfehler und
Sitzungswechsel werden über das synthetische Transportfixture vorgegeben.
