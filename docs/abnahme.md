# Prüfanleitung

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

Der Fachkern verwendet Python und seine Standardbibliothek. Die CI wählt dafür
Python 3.13. Die Oberfläche wird mit dem Testläufer von Node geprüft:

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

[Replaydatengrundlage und Reproduktion](kandidat.md)

## Home Assistant

Für die HA-Prüfungen wird eine eigene Python-Umgebung mit der im Workflow
festgelegten Version verwendet, derzeit Python 3.14.2. Die Installation entspricht
dem HA-Job:

```sh
python -m pip install 'homeassistant==2026.9.2' 'securetar==2026.4.1' 'cronsim==2.7'
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

[Mess- und Interaktionsprogramme für den Sitzungsverlauf](livekurve.md#reproduzierbare-prüfmethode)

## Prüfnachweis

Ein Prüfprotokoll verbindet den Quellstand mit Umgebung, exaktem Kommando,
Exitcode und den beobachteten Ergebnissen. Ausgeführte Fälle, übersprungene Fälle
und fehlgeschlagene Prozesse werden getrennt ausgewiesen. Der Nachweis erfasst
den vollständigen Prozessabschluss; ein nachfolgender Absturz gehört zum
Ergebnis desselben Laufs.

Bei einer PR-Prüfung werden der gemeldete PR-Head und der tatsächlich
ausgecheckte Commit getrennt festgehalten. Ein Checkout kann der von GitHub
erzeugte Merge-Commit mit der Zielbasis sein. Der Laufdatensatz mit `headSha`
bezeichnet den PR-Head; die Checkout-Ausgabe und `git rev-parse HEAD` belegen
den getesteten Commit. Zu beiden Commits wird der Tree erfasst:

```sh
git rev-parse '<PR-Head>^{tree}'
```

```sh
git rev-parse '<getesteter-Checkout>^{tree}'
```

Gleiche Tree-IDs belegen denselben versionierten Dateibaum trotz verschiedener
Commit-IDs. Bei unterschiedlichen Trees wird die tatsächliche Abweichung mit
`git diff <PR-Head> <getesteter-Checkout>` festgehalten und ihre Relevanz für die
Prüfung bewertet. Der Nachweis benennt den getesteten Baum; eine neue Ausführung
richtet sich nach relevanten Abweichungen und anschließend vorgenommenen
Änderungen. Historische Originalprotokolle behalten ihren ursprünglichen Wortlaut;
ihre Zuordnung zum geprüften Stand steht im aktuellen Arbeitsnachweis.

Prüfrunden, Befunde und konkrete Ergebnisse liegen im getrennten Arbeitsbereich
`arbeit/`.
Die CI führt die Befehle mit ihren im Workflow festgelegten Zeitgrenzen aus und
beendet überholte parallele Läufe.

Die Wirkung an einer realen Anlage wird in einer eigenen, ausdrücklich
beauftragten Prüfung durch den Benutzer beobachtet. Ihr Nachweis benennt die
verwendeten Geräte und Rückmeldungen. Die Softwareprüfungen beschreiben
ihre jeweiligen Testquellen und simulierten Außenwirkungen.

## Lokale Bedienvorschau

`python3 tools/local_preview.py` stellt das produktive Panel unter
`http://127.0.0.1:8765` bereit. Die Regelentscheidungen kommen aus dem produktiven
Controller. Sensoren, Uhr und Ofenrückmeldung sind simuliert; es gibt keinen
HA-Zugang und keine Aktorausgabe. Szenarien sind zurücksetzbar. Türbewegung,
Präsenz und Ofenrückmeldung können getrennt eingegeben werden.

Die Vorschau unterstützt Betrieb, Temperaturprogramme, Solltemperatur und manuelle
Ofenwahl. Beendete Sitzungen bleiben bis zum Zurücksetzen des Szenarios im Verlauf.
Lichtwerte sind simuliert. Nicht angebundene Einstellungsaktionen zeigen eine
Fehlermeldung. Automatisierte Prüfungen laufen weiterhin ausschließlich unter Linux.
