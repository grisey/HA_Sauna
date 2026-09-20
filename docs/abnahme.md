# Funktionsprüfung der aktuellen Testfassung

## Gemeinsame Heiz- und Lichtsteuerung vom 20.09.2026

Der neue Stand umfasst Temperaturprogramme, die Lichtkurve, pausierbaren
Nachlauf und Kühlung sowie die getrennte Übersicht und Detailansicht. Die
Rücksetzung der Einstellungen erhält Gerätezuordnungen und Tasterkonfiguration.
Sie ist für HA-Administratoren erst nach Sitzungsende verfügbar.

Die lokale Prüfung besteht aus **272 bestandenen Kerntests** und **fünf
JavaScript-Prüfprogrammen**. Zwei zusätzliche Tests benötigen ein privates
Recorderarchiv und werden im öffentlichen Lauf übersprungen. Die HA-Prüfungen
laufen unter Linux; ihr Ergebnis ist beim jeweiligen Commit in den
[GitHub-Prüfläufen](https://github.com/grisey/HA_Sauna/actions/workflows/tests.yml)
sichtbar. Browser und HA testen dabei dieselben Schnittstellen wie die Bedienung.

Ein unabhängiger Szenariolauf am Steuerungskern von
[`706622b`](https://github.com/grisey/HA_Sauna/commit/706622b3b3fe6d46d9e58d1d5f1958bd77550b21)
bestätigt insbesondere diese Übergänge. Die Heizrückmeldung folgt dabei den
tatsächlich ausgegebenen Befehlen:

| Szenario | Beobachtetes Ergebnis |
|---|---|
| 5 Minuten Heizbudget verbleiben bei 10 Minuten Mindestheizzeit; anschließend 8 Minuten Nachlauf | Die Kühlung folgt ohne Zwischenheizen. |
| Nach 2 Minuten Nachlauf wird 5 Minuten manuell geheizt | Die Nachlaufuhr hält an. Danach laufen die übrigen 6 Minuten; von 15 Minuten Kühlung bleiben 7 Minuten. |
| 5 Minuten Kühlung, danach manuelles Heizen mit Gang und 8 Minuten Nachlauf | 2 Minuten Restkühlung bleiben erhalten. |
| Heizbudget erreicht, Türöffnung und möglicher Gangstart | Die Kühlung wartet auf die Personenentscheidung; ein erkannter Gang bleibt erhalten. |
| Nachlauf oder Kühlung manuell beenden | Nur der aktuelle Phasenaufruf wirkt. Ausschalten und Schutzsperren behalten Vorrang. |

Die Lüftungserkennung wurde zusätzlich mit drei privaten Aufzeichnungen
nachgerechnet. Der Lauf verwendet Messwerte und aufgezeichnete Heizrückmeldungen,
keine nachträglich eingespeisten Tür-, Personen- oder Aufgussereignisse. Neue
Parameter werden aus den aktuellen Standards ergänzt. Für eine Aufzeichnung
fehlt die Heizrückmeldung; dort wird nur der Detektor mit heutigen Standards
verglichen. Dieser Vergleich rekonstruiert nicht die damalige gesamte Steuerung.
Die Aufzeichnungen dienen der Kalibrierung und sind keine unabhängige
Feldvalidierung. Originaldaten bleiben privat.

Die automatische Prüfung vermeidet doppelte Branch- und Pull-Request-Läufe und
bricht veraltete Läufe ab. Einzelne Testschritte sind auf zwei Minuten begrenzt;
der vollständige HA- oder Browserjob auf fünf Minuten einschließlich Einrichtung.
Browserprüfungen schreiben keine Bilddaten mehr in das Konsolenprotokoll.

Die reale Installation und Hardwareprüfung erfolgen weiterhin durch den
Benutzer über HACS. Die Versionsnummer bleibt unverändert.

## Türanzeige nach Sitzungsende vom 20.09.2026

Die Türerkennung endet mit der Sitzung. Das bisherige „Tür unbekannt“ war danach
missverständlich. Übersicht und Details zeigen nun „Türerkennung ruht“; die
Details erklären die sitzungsgebundene Auswertung. Während der
Betriebsunterbrechungsfrist bleibt der Türzustand der laufenden Sitzung sichtbar.
Erkennungsregeln, Sitzungslaufzeit und Steuerung sind unverändert.

Geprüfter Stand:
[`e96d3a7`](https://github.com/grisey/HA_Sauna/commit/e96d3a7e7e22ff5d1ec02a73fe376330c1626253).
[CI 35479522357](https://github.com/grisey/HA_Sauna/actions/runs/35479522357)
ist vollständig erfolgreich: **175 Kerntests bestanden, 2 private Replay-Skips;
11 HA-API-Smokes; 36 echte HA-Integrationstests; 4 Browserabläufe**. Der erweiterte
Browserablauf prüft die Türanzeige vor dem Start, im Betrieb, während der
Unterbrechung und nach Sitzungsende im tatsächlichen HA-Frontend. Keine
JavaScript-Ausnahmen oder abgewiesenen WebSocket-Antworten. Lokal bestehen die
175 Kerntests (zwei private Replay-Skips), JavaScript-Syntax und Diff-Prüfung.
Ein neues privates Replay wurde für diese reine Anzeigeänderung nicht ausgeführt.
Die Installation auf dem Saunasystem erfolgt weiterhin durch den Benutzer.

## Messlücken und Mindestheizzeit vom 20.09.2026

Der untersuchte Aus-/Ein-Zyklus entstand durch eine zu kurze gespeicherte
Messwert-Gültigkeit, obwohl gültige Sensormeldungen weiter eintrafen. Eine passende
Gültigkeitsfrist genügt; die zwischenzeitlich geprüfte zusätzliche Überbrückung
nach Gültigkeitsende wurde auf Nutzerhinweis wieder entfernt. Der Standard von
180 Sekunden bleibt erhalten, ebenso vorhandene abweichende Einstellungen.
Die Feldhilfe erklärt den Unterschied zur späteren Fehlerverriegelung.

Die HA-Regressionen vergleichen denselben synthetischen Meldeabstand mit
5 Sekunden und 30 Sekunden Gültigkeit: zu kurze Gültigkeit erzeugt einen echten
Aus-/Ein-Zyklus samt neuer Mindestheizzeit; passende Gültigkeit erhält den
Heizintervallstart auch nach bereits abgelaufener Mindestheizzeit. Weitere
Gerätepfade prüfen sofortiges Pausieren nach tatsächlichem Gültigkeitsende,
spätere Verriegelung bei Dauerausfall und den gesperrten Neustart nach einer
Sollwerterhöhung ohne gültige Temperatur. Bereits gestartete Kühlung und Nachlauf
gehen auch bei widersprüchlichem Gangsignal vor; noch nicht gestartete Kühlung
wartet weiterhin auf das Gangende.

Geprüfter Stand:
[`4486127`](https://github.com/grisey/HA_Sauna/commit/44861277bc1112561f554cd437a2400b0c237841).
[CI 35477475041](https://github.com/grisey/HA_Sauna/actions/runs/35477475041)
ist vollständig erfolgreich: **175 Kerntests bestanden, 2 private Replay-Skips;
11 HA-API-Smokes; 36 echte HA-Integrationstests; 4 Browserabläufe**. Beide
Gültigkeitsfälle sowie die Sperr- und Verriegelungsprüfungen bestehen im realen
HA-Kern mit kontrollierten Testgeräten. Keine reale Installation oder
Gerätebetätigung durch den Entwicklungszugriff.

## Zustandsabhängige Erkennung vom 20.09.2026

Der Controller gibt Personen- und Aufgussprüfungen nur frei, wenn sie den
aktuellen Ablauf ändern können. Bereits erkannte Gänge benötigen keine weiteren
Personensignale. Aufgüsse bleiben im Gang aktiv; Nachlauf, laufende Kühlung und
Betrieb-Aus sperren neue Gangsignale. Türprüfungen bewerten nur den jeweils
möglichen Übergang. Schwellen und eingefrorener Referenzkandidat bleiben gleich.

Lokal **175 Kerntests bestanden, 2 private Replay-Skips**, 177 gesammelt.
Der separate Vergleich aller Rasterpunkte mit dem eingefrorenen Kandidaten besteht.
Ein weiterer privater Ablaufvergleich erhält sämtliche Tür-, Lüftungs- und
Aufgusszeitpunkte sowie die Gangzuordnung; nur nachträgliche Personensignale
entfallen. Geprüfter Code:
[`3b46a78`](https://github.com/grisey/HA_Sauna/commit/3b46a78387913336495a102ba8cae1987b196869).
[CI 35476362531](https://github.com/grisey/HA_Sauna/actions/runs/35476362531)
ist vollständig erfolgreich: **175 Kerntests bestanden, 2 private Replay-Skips;
11 HA-API-Smokes; 32 echte HA-Integrationstests; 4 Browserabläufe**. Der neue
HA-Test bestätigt, dass der erste Aufguss den Gang anlegt und weitere Aufgüsse
bei ruhender Personensuche demselben Gang zugeordnet bleiben.

Die vorherige Fassung wurde inzwischen vom Nutzer installiert. Ausschließlich
gelesene Archiv- und Zustandsdaten bestätigen Heizschütz-Aus zum Gangende,
Nachlauflicht mit rund 15 % und die Rückkehr zum Betriebslicht. Die physische
Türöffnungsdauer ist weiterhin keine unabhängige Kontaktmessung.

## Ergänzung vom 20.09.2026 – Ofentimer und manuelles Phasenende

Der Ofentimer zählt jetzt nur bei eingeschaltetem Betrieb und bestätigtem
Schütz-Ein. Details bieten die manuelle Beendigung eines laufenden Nachlaufs oder
einer laufenden Zwangskühlung mit dem regulären Folgeablauf. Ausgeführte
Kernprüfungen: **169 bestanden, 2 private Replay-Skips**; 171 gesammelt.
Separater privater fortlaufender Replay-Vergleich: **1 bestanden, Exit 0**.
Python- und JavaScript-Syntax sowie `git diff --check` sind erfolgreich geprüft.
Geprüfter Code: [`1577ce0`](https://github.com/grisey/HA_Sauna/commit/1577ce067e72c0f6fc5b2fe710a0fc1feaa48099).
[CI 35472918986](https://github.com/grisey/HA_Sauna/actions/runs/35472918986)
und der PR-Lauf sind vollständig erfolgreich: **169 Kerntests bestanden, 2 private
Replay-Skips; 11 HA-API-Smokes; 31 echte HA-Integrationstests; 4 Browserabläufe**.
Die neuen Bedienknöpfe wurden im echten HA-Frontend ausgeführt und visuell geprüft.
Keine JavaScript-Ausnahmen oder abgewiesenen WebSocket-Antworten; der absichtlich
geprüfte Start mit veralteten Messwerten liefert weiterhin die erwartete HTTP-409-
Antwort mit verständlicher Fehlermeldung. Keine reale Installation oder Betätigung.

Die Tests prüfen Schütz-Aus/Unbekannt, Wiederaufnahme, fehlenden Schaltvollzug,
Trennung von optionaler Heizleistung, Erinnerung und unveränderte Rücksetzregeln.
Für manuelles Phasenende prüfen sie tatsächliche Endzeit, einmalige Anrechnung,
Restkühlung, Folgebudget, alte Klicks, Authentifizierung, erhaltene Schutzsperren
und die Licht-/Heizfolge. Die Lüftungserkennung wird mit dieser Ergänzung nicht
geändert. Anrechnung eines Nachlaufs auf erst später fällige Kühlung bleibt offen.

## Zuvor vollständig geprüfter Funktionsstand

Geprüfter Stand:
[`0c16486800a7763cbb25c9a97cc37f8f0e920a69`](https://github.com/grisey/HA_Sauna/commit/0c16486800a7763cbb25c9a97cc37f8f0e920a69).
[CI 35469913084](https://github.com/grisey/HA_Sauna/actions/runs/35469913084)
ist vollständig beendet: **core, ha-api, ha-integration und browser erfolgreich**.
Die aktuelle Fassung korrigiert Lichtsteuerung, Temperaturänderungen während der
Sitzung, Phasenanzeige und ergänzende Türerkennung. Sie ist mit Home Assistant
integriert geprüft; die Abnahme dieser Änderungen am echten Ofen steht noch aus.
Installation und Neustart erfolgen durch den Benutzer über HACS.

## Ausgeführte Prüfungen

| Prüfung | Ergebnis | Aussage und Grenze |
|---|---|---|
| Fachkern, Detektor und SQLite | **158 bestanden, 2 private Replays übersprungen**; 160 gesammelt | Öffentliche CI besitzt die privaten Daten nicht. Diese beiden Skips zählen dort nicht als bestanden. |
| HA-API-Smokes | **11 bestanden** | Echte HA-Schemas mit Manager-Testdoubles; getrennt vom vollständigen HA-Lebenszyklus. |
| Echte HA-Integration | **29 bestanden** | Tatsächliche Manager, Registries, Entitäten, Listener, Services, HTTP und Authentifizierung. Externe Geräte und Messreihen sind kontrollierte Testquellen. Enthält HA-Backup und Restore. |
| Browser im echten HA-Frontend | **3 vollständige Abläufe bestanden** | Chromium: Bedienung, laufende Anzeigen, Archiv, Export, Einstellungen, Fehlermeldungen und mobile Breite. Keine erfassten JavaScript-Ausnahmen oder abgewiesenen WebSocket-Antworten. |
| Lokale reine Tests | **158 bestanden, 2 Replay-Skips, Exit 0** | Python mit `-S`; ohne Home Assistant und dessen native Abhängigkeiten. |
| Privater fortlaufender Replay-Vergleich | **1 bestanden, Exit 0** | Alle Rasterpunkte und Ereignisse in drei Sensormodi gegen den eingefrorenen Kandidaten verglichen. |
| Kandidat und vollständiges Referenz-Replay | **3 bestanden, Exit 0** | Integrität und vollständiger Vergleich erfolgreich; eingefrorene Prüfsummen unverändert. |

## Anforderungen, Implementierung und Nachweis

| Funktion | Produktiver Pfad | Ausführbare Prüfung |
|---|---|---|
| Betriebslicht 35 %, Gangnachlauf 15 %, Zwangskühlung 5 %; vorherige Helligkeit anschließend wiederherstellen | `device.py`, zentraler Parameterkatalog | `tests/integration/test_device_path.py`: reale Lichtdienste, manuelle Zwischenänderung, Übergang Nachlauf → Kühlung ohne Aufhellen, sichtbarer Lichtfehler ohne Heizabbruch |
| Lichtnachlauf nach endgültigem Sitzungsende: 10 Minuten bei 50 %, anschließend aus | `core/controller.py`, `device.py`, `core/display.py` | `test_session_light.py`, HA-Gerätepfad und Browser: Ablauf, abweichende Einstellungen, Fehler, erhaltene Frist bei Optionsneuladen, Aufhebung durch neue Sitzung |
| Solltemperatur, Erhöhung je Gang und Endtemperatur während der Sitzung ändern | `settings.py`, Controller, Climate/Number, API, Optionsflow | `test_live_settings.py`, HA-Lebenszyklus, Gerätepfad und Panel-API: neue Solltemperatur sofort, weitere Steigerungen ab dort; kein Reload oder Verlust von Gang, Nachlauf, Kühlung, Türwartefrist, Mindestheizzeit und Schutz |
| Phasenbezogene Zeit in Übersicht; technische Timer und Bereitschaft nur in Details | `core/display.py`, `api.py`, `panel.js` | Kerntest und Browser: passende Phasenzeit einschließlich Lichtnachlauf, beständige Bedienelemente, deutsche Feldhilfen, schmale Ansicht |
| Ergänzende Türregel beim Aufheizen | `core/detector.py`, Heizkontext aus `runtime.py` | `test_detector.py` und realer HA-Messpfad: beidseitiger anhaltender Temperaturabfall trotz eingeschalteter Heizung; Gegenproben mit Aus/Unbekannt, einem Sensor, kurzem Ausreißer und heißem Zustand |
| Fehlende notwendige Einstellungen mit Defaults ergänzen; gespeicherte Werte erhalten | `core/parameters.py`, Setup und zentraler Schreibweg | Grundlagen-, Übersetzungs- und HA-API-Tests; Start mit vormals unvollständiger Konfiguration |
| Session, vorläufiger Gang, Aufhebung ohne Zählung/Nachlauf, Bestätigung und einmaliger Abschluss | `core/controller.py`, `core/timeline.py` | `test_operation.py`, `test_timeline.py`, `test_foundation.py` |
| Heizbudget, Nachlaufanrechnung, Gang- und Kühlsperren, Thermostat und Zusatzkühlung | `core/heating.py`, `core/controller.py`, `core/thermostat.py` | `test_heating.py`, `test_cooling.py`, `test_thermostat.py` und vollständige HA-Messkette |
| Einrichtung, Rollen, entkoppelter Taster und späterer Quellenwechsel | `config_flow.py`, `bindings.py`, `runtime.py`, `device.py` | `tests/integration/test_lifecycle.py`, `test_device_path.py`: reale ConfigEntries, Entitäten, Eingänge, Setup/Unload/Reload |
| Optionale Leistungsmessung, Energieschätzung und rein informativ angehaltener Ofentimer | `core/power.py`, `core/energy.py`, `core/mechanical_timer.py`, HA-Adapter | Kern- und HA-Tests: Schätzung mit 4,5 kW, gemessene Anteile, Ausfälle, erhaltene Energie; Timer erzeugt keine Heizbefehle |
| Archiv in Originalauflösung, Parameterrevisionen, Download, HA-Backup und Restore | `archive.py`, `backup.py`, `api.py` | `test_archive.py`, `tests/integration/test_archive_backup.py`, Browserdownload: tatsächlicher HA-Backup-Writer und Restore in getrennte HA-Instanz |

Bei einer Temperaturänderung genau am Sitzungsende wird die beendete Sitzung mit
ihrem bisherigen Parameterstand archiviert. Die neue Einstellung wird nicht
rückwirkend in diesen Abschluss geschrieben; dafür besteht ein eigener Archivtest.

## Türerkennung: Befund und begrenzte Änderung

Die gelesenen Testdaten enthalten einen Luftfeuchteabfall bei den Türöffnungen.
Bei einer kurzen Öffnung blieb der Temperaturtrend unter der bisherigen
Auslösestärke; bei der anderen erfüllten die beiden Messpositionen die Temperatur-
und Feuchtebedingungen nicht gleichzeitig. Ein pauschal fehlender Feuchteabfall
war deshalb keine zutreffende Beschreibung.

Die zusätzliche Regel gilt standardmäßig nur unter **70 °C**, bei zwei verfügbaren
Messpositionen und durchgehend eingeschalteter Heizung. Beide geglätteten
Temperaturtrends müssen mindestens **5 Sekunden unter −0,8 °C/min** liegen.
Alle drei Werte sind einstellbar. Eine Heizabschaltung verwirft den Nachweis.
Im heißen Bereich bleibt die bisherige Temperatur-/Feuchteregel maßgeblich.

Im zusätzlichen lokalen Vergleich mit Heizkontext bleiben **15 Öffnungen und
15 Schließungen** erhalten. Die heißen Öffnungen behalten ihre Zeitpunkte;
eine Öffnung unterhalb der Temperaturgrenze wird drei Sekunden früher erkannt.
Die beiden zur Anpassung verwendeten aktuellen Testöffnungen werden erkannt.
Die unbeschränkte Ergänzung erzeugte zusätzliche Ereignisse und wurde verworfen.
Dieser Vergleich ist **Kalibrierung, keine unabhängige Feldvalidierung**.
Ein weiterer vom Benutzer ausgeführter Türtest wurde bereits durch die unveränderte
installierte Fassung erkannt und in den ausschließlich gelesenen Logs bestätigt.
Das belegt noch nicht die neue Ergänzung am realen Ofen.

## Umgebung und Reproduktion

- HA-Tests: Linux, Home Assistant **2026.9.2**, Python **3.14.2**.
- Öffentliche Kerntests: Python **3.13**. Lokal: Homebrew-Python **3.14** ohne HA-Import.
- Browser: zugehöriges HA-Frontend, Playwright/Chromium aus `tests/browser/requirements.txt`.
- Versionen und Installationsschritte: [CI-Workflow](../.github/workflows/tests.yml).

In einer isolierten Linux-Umgebung mit den dort angegebenen Abhängigkeiten:

```sh
python -m unittest discover -s tests -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/ha -v
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/integration -v
python -m pip install -r tests/browser/requirements.txt
python -m playwright install --with-deps chromium
HA_TEST_REQUIRED=1 python -m unittest discover -s tests/browser -v
```

Privates Replay mit `requirements-replay.txt` und lokal bereitgestelltem Archiv:

```sh
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder.zip python -m unittest discover -s tests -p test_stream_replay.py -v
SAUNA_RECORDER_ARCHIVE=/pfad/sauna-recorder.zip python -m unittest discover -s tests -p test_candidate.py -v
```

Nach zwei früheren macOS-Abstürzen werden HA-Tests ausschließlich in Linux
ausgeführt. Ein vor einem Absturz ausgegebenes `OK` wurde nicht als Erfolg
gewertet. Die hier genannten lokalen Tests und Replays beendeten sich mit Exit 0.

## Verbleibende Grenzen

Die neue Fassung wurde nicht auf dem Saunasystem installiert, neugestartet oder
zum Schalten des echten Ofens verwendet. SSH blieb ausschließlich lesend.
HACS-Update und betreute Prüfung von Licht, Schütz und Sensoren erfolgen durch
den Benutzer: [Testinstallation](testinstallation.md).

Ein realer optionaler Leistungsmesser fehlt bislang; sein Softwarepfad ist mit
HA-Testentitäten geprüft. Die Erkennung eines internen Ofen-Aus aus Temperatur-
krümmung bleibt auf Nutzerwunsch zurückgestellt. Ohne gültige obere Temperatur
wird kein unterer Ersatzwert angenommen. Bereits gespeicherte Messgültigkeits-
fristen bleiben beim Update erhalten; der neue Default überschreibt sie nicht.

Versionsnummer und eingefrorene Referenzdateien bleiben unverändert. Es wurde
kein Release erzeugt. Rohmessungen, private Entity-IDs und Zugangsdaten werden
nicht veröffentlicht. Ein vollständiger Softwaretest ersetzt keine Hardwareabnahme.
