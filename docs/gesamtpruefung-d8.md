# Korrekturen zur Codeanalyse von 27f0d5a

Die unabhängige Analyse vom 28.09.2026 bestätigt 15 der früheren 16 Ursachen
innerhalb ihrer Original- und Erhaltungsfälle. Die Lichtübergabe F05 bleibt beim
Reload derselben Leuchte teilweise offen. Insgesamt nennt sie sieben aktuelle
Ursachen: zwei P2 und fünf P3. Diese Korrekturrunde behandelt diese sieben
Ursachen, einschließlich des verbliebenen Umfangs von F05/TRANSPORT-03. Sie ist
keine erneute vollständige Abnahme des Repositorys.

## Koordination und Maßstab

Drei neu eingesetzte Sol-Bearbeiter haben getrennte Schreibbereiche:
Controller/Runtime, Geräte/Lifecycle und Panel. Die gemeinsame Runtime wird nur
vom ersten Bearbeiter verändert. Der Hauptagent übernimmt API/Settings,
Referenzbereinigung, Dokumentation und Gegenprüfung. Die Aufträge benennen
sowohl die erforderliche Änderung als auch die zu erhaltenden Regeln.

Die Prüfung verfolgt Eingang, führenden Schreiber, Übergang und tatsächlichen
Verbraucher. Ein erfolgreicher Test belegt ausschließlich seine Assertions.
Die Ergänzungen verwenden bestehende Fixtures und Produktionspfade; sie führen
keinen zweiten Controller, Detektor oder fachlichen Ergebnisrechner ein.
Ausführung ausschließlich in Linux-CI, keine lokalen Tests auf dem Mac.

## Ursachen und Erhaltung

| Befund | Änderung am maßgeblichen Pfad | Erhaltener Gegenfall |
| --- | --- | --- |
| N01: Lichtabschluss fehlt nach Caller-Abbruch | Die tatsächliche Dienstaufgabe archiviert ihren Abschluss einmal mit ursprünglicher Sitzung und Herleitung, tatsächlichem Beginn/Ende und tatsächlichem Fehler. Abschlussfolgen hängen nicht mehr am wartenden Aufrufer. | Timeout beendet den realen Dienst nicht; Fehler werden nicht als Erfolg archiviert. Archivfehler verändern keine Aktorantwort. |
| N02: Kurzes Erreichen der Solltemperatur geht verloren | Die Runtime reicht jede empfangene Regeltemperatur geordnet an `Controller.set_temperature`. Der vorhandene Bereitschaftsübergang beendet die Übersteuerung nach seiner bestehenden Regel. | Gleiche Zeitstempel und wiederholte Rollen bleiben in FIFO-Reihenfolge; alle bereitstehenden Kanäle bilden weiter ein Detektorraster. Kein historisches Aktor-I/O, kein zweiter Bereitschaftsmerker. |
| N03: API bestätigt alte Runtimewerte | Betriebsart und Tasterprogramm antworten aus dem Ergebnis ihres tatsächlich ausgeführten gemeinsamen Writers. | Rechte, Eingabeprüfung, Sitzungssperren und tatsächliche Persistenz bleiben unverändert. |
| N04: Gültige Katalog-ID wird abgeschnitten | Der Editor zerlegt ausschließlich das feste Aktionspräfix und die Art; die verbleibende ID bleibt vollständig. | Vorhandene IDs einschließlich Doppelpunkten bleiben zulässig; keine Migration oder Einschränkung des API-Vertrags. |
| N05: Alter Lichtdienst überholt neuen Runtimewert | Jede Runtimeübergabe wartet begrenzt auf die tatsächliche alte Lichtaufgabe. Fehlgeschlagene Übergabe erhält den bisherigen Eigentümer und das Archiv; neue Runtime erst nach dessen Abschluss. | Unterschiedliche Leuchten, Optionsrücknahme, echtes externes Licht, Ofen-AUS und Archivabschluss bleiben getrennt. Keine zusätzliche Dienstqueue oder globale Ersatz-Zustandsmaschine. |
| N06: Öffentlicher Finalcache verdeckt spätere Admin-Diagnose | Cache, Cursor, Loader und abgeleitete Ansichten berücksichtigen die Rechteprojektion. Wechsel lädt ab null und entzieht alten Antworten ihre Schreibberechtigung, auch bei Rückkehr zur vorigen Rolle. | Gleichbleibende Rolle verwendet weiter den Finalcache. Diagramminstanz und gewählter Zeitbereich bleiben erhalten; Rechteentzug leert Diagnoseinhalte sofort. |
| N07: Historische Entscheidung verliert tatsächliche Erzeugungszeit | `Decision.created_at` entsteht im Controller; Archiv-`received_at` übernimmt diese Zeit unabhängig von späterem Persistieren. | `Decision.at` bleibt logische Buchungszeit. Echte Erkennungszeit, historische Gangzählung und ausschließlich aktuelle Aktorausgaben bleiben erhalten. |

## Zusätzliche Gegenprüfung

Die Bereitschaftskorrektur wurde bis zur Rückgabe einer manuellen
Heizübersteuerung und zur aktuellen Heizanforderung verfolgt. Physische
Bedienkanten im selben Messpaket dürfen den letzten vorab aufgenommenen
Temperaturwert nicht vor seinem Eingang verwenden. Bereitschaft,
Bestätigungsfristen und Detektorlieferung bleiben im vorhandenen Controller.

Beim Licht wurden Caller-Timeout, Caller-Abbruch, tatsächlicher Diensterfolg/-fehler,
parallele und serielle Plattformgrenzen, erfolgreicher und abgebrochener Reload,
direktes Entladen und wiederholter Runtimeabschluss unterschieden. Das Archiv
bleibt für den tatsächlichen Abschluss geöffnet. Der Ofen-AUS-Versuch hängt
nicht am erfolgreichen Schreiben des Archivs oder am Abschluss des Lichtdiensts.
Die unabhängige Gegenlektüre fand zusätzlich einen bereits am Ausgabelock
wartenden Leuchtenwechsel: Er konnte erst nach dem Runtimeabschluss eintreten.
Auch diese Übergabeausgabe wird deshalb an der zentralen Dienststartgrenze
durch den abgeschlossenen Runtimezustand gesperrt.
Der echte HA-Lauf zeigte außerdem, dass eine verweigerte Integrationentladung
den Eintrag in HAs nicht wiederholbaren Zustand `FAILED_UNLOAD` versetzt.
Technische Optionsänderungen prüfen deshalb schon vor diesem HA-Aufruf die
Lichtübergabe. Bei Rücknahme bleibt die Ausgabe bis zum Abschluss der
Optionswiederherstellung entzogen, damit wartende Regelzyklen nicht erneut
den Runtime-Lock mit derselben ausstehenden Lichtaufgabe belegen.

Beim Panel umfasst der Rechtewechsel sowohl abgeschlossene Archive als auch
bereits laufende Seitenabrufe und verborgene Diagnoseansichten. Die neue
Browserprüfung verwendet einen regulären HA-Gruppenwechsel hinter dem
bestehenden Token, ohne das Panel neu zu laden.
Die Linux-Browserprüfung deckte zusätzlich eine intrinsisch zu breite
Sitzungsauswahl im schmalen Verlauf auf: 387 Pixel Feldbreite bei 362 Pixel
verfügbarer Inhaltsbreite. Die Begrenzung betrifft dieses Auswahlfeld;
Sitzungsnamen, Auswahllogik und horizontal scrollbare Tabellen bleiben erhalten.

## Bereinigung und Grenzen

Nach Referenzprüfung wurden ausschließlich die fünf verwaisten Texte zu
`below_start_temperature` aus Präsentation und beiden Sprachressourcen entfernt.
Freie und absteigende Programme bleiben zulässig. Cachealias `loaded`,
Archivkompatibilität, Altdaten und eingefrorener Kandidat bleiben erhalten.

Oben bleibt Hauptsensor, unten Ersatz; eine vollständige Temperatur-/Feuchteposition
ermöglicht den gesamten Betrieb. Ausfall einer zweiten konfigurierten Position
bleibt sichtbar. Bestätigte Relaisstellung, Schutzvorrang, Ofenkühlung, HOLD-AUS
und Nachlaufbeginn beim Loslassen bleiben verbindlich.

Der gesonderte Abschlussbericht nennt den endgültigen Commit, jeden Linuxprozess
und dessen Ergebnis sowie vorausgehende Fehlläufe. Die separaten Probes des
externen Berichts waren nicht beigefügt und wurden hier nicht ausgeführt.
Die erneute unabhängige Prüfung erhält den vollständigen Quellbestand,
einschließlich unveränderter Dateien. PR 14 bleibt vor dem Merge angehalten.
