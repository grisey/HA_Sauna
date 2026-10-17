# Archiv und Export

## Welche Sitzungen erhalten bleiben

Sitzungen mit mindestens einem bestätigten Gang bleiben mit ihren Originaldaten
ohne Altersbegrenzung erhalten. Sitzungen ohne bestätigten Gang werden nach dem
Abschluss verworfen; dieselbe Bereinigung erfolgt beim Integrationsstart für
ältere Sitzungen ohne bestätigten Gang. Das gilt auch für Aufzeichnungen im
manuellen Modus.

AUS unterbricht zunächst nur den Betrieb. Erneutes EIN vor Ende der
Wiederaufnahmefrist gehört unabhängig von der Betriebsart zur gleichen
Aufzeichnung. Auch manuelles Vorheizen vor dem Wechsel auf Automatik bleibt
Teil dieser Sitzung. Erst der Ablauf dieser Frist entscheidet über den
Abschluss und die Speicherung; die Lichtbedienung verkürzt sie nicht.

Das Archiv umfasst Messungen vor und nach der Sitzung. Die Zeiträume sind
konfigurierbar; der Standard beträgt jeweils **15 Minuten**. Maßgeblich ist der
bei der Sitzung gespeicherte [Messkontext](parameter.md#daten-und-laufzeit).
Außerhalb dieser Fenster werden keine sitzungslosen Messreihen dauerhaft
aufbewahrt. Der Nachlauf kann eine bereits beendete Sitzung noch ergänzen.

Nach einem Neustart bleibt eine unterbrochene Sitzung historisch offen; ein
Endzeitpunkt wird nicht erfunden. Sie setzt den Ofenbetrieb nicht fort.

Im normalen Betrieb bleiben Originalmessungen, erkannte Ereignisse und ihre
Bestätigungs- und Zuordnungsbelege sowie Phasen, Kühlung, Schaltentscheidungen,
Ausgaben und Fehlerzustände erhalten. Die vollständigen internen Prüfverläufe
der Erkennung werden nur bei ausdrücklich gewählter Analyseaufzeichnung
zusätzlich gespeichert. Die aktuelle Live-Diagnose bleibt in beiden Profilen
verfügbar; Erkennung und Regelung verwenden dieselben Eingänge und Regeln.
Bereits gespeicherte Prüfverläufe bleiben lesbar und im Export enthalten.

## Sitzungen exportieren

**Archiv als ZIP herunterladen** unter **Einstellungen → Daten und Wartung**
erfordert Administratorrechte.
Er enthält einen konsistenten Stand, auch während einer laufenden Aufzeichnung.

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | Schema, Instanz, Auflösung und Zeitformat |
| `sessions.jsonl` | Neuester Stand jeder Sitzung einschließlich ihrer Konfiguration |
| `records.jsonl` | Alle Datensätze und gespeicherten Revisionen in Aufzeichnungsreihenfolge |
| `measurements.csv` | Originalmessungen mit Datensatz-/Sitzungs-ID, Empfangs-/Messzeit, Messort, Messgröße, Quelle, Wert und Rohwert |

## Archiv sichern und wiederherstellen

Je Sauna liegt eine SQLite-Datei unter
`<HA-Konfigurationsverzeichnis>/ha_sauna/<entry-id>.sqlite`.
Sie wird im Home-Assistant-Backup konsistent gesichert.

Die Datenbank speichert vollständige Konfigurationen in einer gemeinsamen
Tabelle unveränderlicher Versionen. Jeder Sitzungsschnappschuss verweist auf
die damals geltende Version. Eine Änderung während einer Sitzung erzeugt
eine weitere Version; unveränderte Werte werden auch über mehrere Sitzungen
hinweg wiederverwendet. Parameterwerte, Gerätezuordnungen und weitere
Konfigurationsfelder bleiben vollständig erhalten. Die Version enthält keine
Verweise auf später veränderbare Standardeinstellungen.

Das interne SQLite-Schema 2 ergänzt bestehende Archive, ohne alte Datensätze,
IDs oder eingebettete Konfigurationen umzuschreiben. Neue Version,
Sitzungsrevision und aktueller Sitzungsstand werden gemeinsam in einer
Transaktion gespeichert. Alte Archive im Schema 1 bleiben auch ohne vorherige
Migration lesbar. Die öffentlichen Archivantworten und der ZIP-Export behalten
Schema 1 mit vollständig eingebetteter Konfiguration; interne Versions-IDs
werden dort nicht ausgegeben. Das Backup enthält die Versionstabelle mit allen
Referenzen. Erst wenn nach einer Sitzungs- oder Archivlöschung keine erhaltene
Revision mehr auf eine Version verweist, wird diese ebenfalls entfernt.

## Sitzungen löschen

Administratoren können einzelne abgeschlossene oder durch Neustart unterbrochene
Sitzungen sowie das gesamte Archiv löschen. Eine laufende Sitzung einschließlich
Wiederaufnahmepause sperrt beide Aktionen. Einstellungen und Gerätezuordnungen
bleiben erhalten.

## Messungen und Verlauf nachvollziehen

Der Normalverlauf verwendet die führende Messposition der Regelung.
Diagnoseansichten sind Administratoren vorbehalten und verwenden die zur Sitzung
gespeicherten Einstellungen.
Verfügbare Messpositionen, Präsenzquelle und Rückmeldequellen folgen dem gemeinsamen
Anlagenmodell. Die Liveansicht verwendet die aktuelle Zuordnung, eine archivierte
Sitzung ihren gespeicherten Aufbau. Fehlt dieser in älteren Daten, bleibt die
Konfiguration unbekannt; vorhandene Messungen werden trotzdem angezeigt.

Phasen und Ereignisse erscheinen in eigenen Bändern am Diagramm. Ein Klick auf
einen Zeitpunkt markiert dessen Messwerte, ein zweiter einen Zeitraum. Ein
weiterer Klick oder **Auswahl aufheben** stellt die Sitzungsübersicht wieder her.
Für einen ausgewählten Saunagang enthält **Weitere Daten** den belegten
Türschluss beim Eintritt, den bestätigten Abschluss des zugehörigen
Austrittsvorgangs und den Bestätigungsstand **Vorläufig** oder **Bestätigt**.
Die Gangdauer verwendet diese zugeordneten Grenzen. Die Türöffnung allein
beendet den Gang nicht. Fehlende oder unvollständige Grenzen bleiben unbelegt;
gebuchte Steuerungszeiten füllen sie nicht auf. Bei einem laufenden Gang mit
belegtem Eintritt wird die vom Backend ermittelte bisherige Dauer angezeigt.
Kennzahlen einer unvollständigen Gangphase beziehen sich ausdrücklich auf den
ausgewählten Zeitraum. Gebuchte Gang- und Bestätigungszeiten bleiben separat
in den Sitzungsdaten der Detailansicht erhalten.
Für Zeiträume stehen Temperaturänderung und Änderung je Minute an der ausgewerteten
Messposition zur Verfügung. **Weitere Daten** enthält die zeitgewichtete
Temperaturdifferenz oben minus unten, die mittlere absolute Abweichung vom
damals gebuchten Sollwert sowie größte Über- und Unterschreitung. Dabei zählt
jeder Wert für seine ursprüngliche Gültigkeitsdauer; der Endpunkt gehört nur
zur Nettoänderung. Fehlende Werte, Quellenwechsel und Gültigkeitslücken liefern
einen konkreten Fehlhinweis statt eines Teilmittelwerts für den ganzen Zeitraum.

Die Feuchteabnahme je °C verwendet die serverseitig aus archivierten
Originalmessungen projizierte absolute Feuchte und setzt
eine gemeinsame Nettoabnahme von Temperatur und Feuchte voraus. Ein Türschluss
oder bestätigtes Durchlüften allein belegt kein physikalisches Feuchteplateau;
ein durch eine Türöffnung begrenzter Aufguss belegt allein kein natürliches
Abklingende. Daraus werden keine zusätzlichen Erholungszeiten abgeleitet.
Phasen und Ereignisse wählen ihre belegten Zeitgrenzen aus. Die Markierung in
der Zeitachse wechselt zwischen dem gewählten Ausschnitt und der gesamten Sitzung;
ihre Griffe ändern die Zeitgrenzen.

Strg-/Cmd-Mausrad oder eine Vergrößerungsgeste ändern den Maßstab. Ein vergrößerter
Ausschnitt lässt sich horizontal im Diagramm verschieben. Bei fokussiertem
Diagramm vergrößern **+** und **−** den Maßstab beziehungsweise den sichtbaren
Zeitraum; die Pfeiltasten verschieben ihn, **Pos1** zeigt die gesamte Sitzung.
Umschalt und Pfeiltasten bewegen die Zeitmarkierung, **Eingabe** übernimmt ihre
Grenzen und **Escape** hebt die Auswahl auf.

Jeder Aufguss verwendet seinen eigenen gespeicherten Endbeleg. Die zugeordnete
Endzeit und die spätere Bestätigung bleiben getrennt sichtbar. Ohne Endbeleg
erscheint der Startpunkt mit **Ende nicht belegt**; Gangende und spätere Aufgüsse
ersetzen keinen fehlenden Beleg. Nachgelieferte oder korrigierte Endbelege
aktualisieren eine ausdrücklich gewählte Ereignisauswahl. Freie Zeitbereiche
und der sichtbare Ausschnitt bleiben erhalten.

Die Auswahl bezeichnet Aufzeichnungen mit Datum und Zeitspanne. Der Betriebsverlauf
ordnet Messungen, Regelentscheidungen, Bedienhandlungen und Geräteausgaben nach
ihrer Herkunft. Heizanforderung, Schützrückmeldung und beobachtete Heizung bleiben
unterscheidbar; fehlende Rückmeldungen gelten nicht als ausgeschalteter Zustand.

Erfolgreiche Lichtstellfolgen erscheinen zusammengefasst mit Beginn und letztem
belegtem Wert. Phasenwechsel, Bedienhandlungen, Ausschalten und Fehler begrenzen
die Abschnitte. Ein letzter Stellbefehl belegt allein kein abgeschlossenes
Lichtprogramm. Einzelbefehle bleiben zugänglich; Fehler werden einzeln angezeigt.
Der Fehlerzähler zählt aufgetretene Störungen, keine fehlerfreien Anfangszustände
oder Entwarnungen.

Laufende Aufzeichnungen werden im Hintergrund in größeren Abständen ergänzt.
Zeitfenster, Zoom, Markierungen und offene Abschnitte bleiben dabei erhalten.
**Pos1** im Diagramm übernimmt den inzwischen erweiterten gesamten Zeitraum.
**Wird geladen** kennzeichnet den ersten Datenabruf. Bei einer fehlgeschlagenen
Aktualisierung bleibt der vorhandene Stand mit einem Fehlerhinweis sichtbar.

Die Details zeigen gespeicherte Werte mit ihrer Herkunft. Nicht aufgezeichnete
Messverläufe lassen sich rückwirkend nicht ergänzen; fehlende Angaben erscheinen
entsprechend gekennzeichnet. Eine Heizbeobachtung bei einem Zustandswechsel ist
keine fortlaufende Leistungsmessung.

### Daten und Zeitangaben

Originalwerte behalten Messrolle, Quelle, Empfangszeit und verfügbare Genauigkeit.
Ein Gerätezeitstempel wird nur übernommen, wenn die Quelle ihn liefert.
Zeitstempel tragen Zeitzone und vorhandene Sekundenbruchteile; die Oberfläche
zeigt die lokale Browserzeit.

| Zeitfeld bei Erkennungen | Bedeutung |
| --- | --- |
| `effective_at` | Zugeordneter Ereignisbeginn, gegebenenfalls rückblickend. |
| `trace_at` (optional) | Ursprünglicher Diagnose-/Bestätigungszeitpunkt im Auswertungsraster. |
| `booking_at` | Zeitpunkt der Einordnung in den Betriebsablauf. |
| `detected_at` | Zeitpunkt, an dem die Erkennung das Ereignis feststellt. |

Bei Ereignissen ohne `trace_at` bleibt `effective_at` der Bezug für die
bisherigen Zulassungen und Fristen.

Nachträgliche Gangbestätigungen und Korrekturen ergänzen den Verlauf.
Frühere Zustände und die tatsächlichen Schalterrückmeldungen bleiben erhalten.
Jede Sitzungsrevision enthält den damaligen Konfigurationsstand. Ein späterer
Gerätewechsel schreibt die Herkunft alter Messungen nicht um.
Manuelle Ofenwahlen erhalten ihr Ende nur aus einem eigenen Endbeleg derselben
Auswahl und ursprünglichen Sitzung. Eine spätere Heizentscheidung ersetzt diesen
Beleg nicht. Nachgeladene Endbelege ergänzen die Anzeige; fehlende oder
widersprüchliche Belege lassen das Gültigkeitsende offen.

Verlaufskurven verbinden vorhandene Messwerte auch über Messlücken hinweg.
Die Zeichnung wird an die Bildschirmauflösung angepasst; Wertanzeige und
Export verwenden weiterhin Originalmessungen.

### Nachträgliche Zuordnung im Verlauf

Empfangene Messungen werden vor späteren Bedienhandlungen mit ihren damaligen
Freigaben ausgewertet. Auch ein nur zwischenzeitlich erreichter Sollwert zählt
für die Bereitschaft. Am Ende einer Bestätigungsfrist werden bereits empfangene
Messungen dieses Zeitpunkts noch berücksichtigt.

Ereignisbeginn, Buchung und Erkennung können auseinanderliegen. Späte Erkennung
kann Gang- und Phasenzuordnung im Verlauf berichtigen; Gerätebefehle gelten erst
zur tatsächlichen Ausgabezeit. Messzeiten und bestätigte Schalterrückmeldungen
bleiben erhalten.

## Technische Referenz: API-Zugriff

Alle Zugriffe benötigen eine Home-Assistant-Anmeldung und die Berechtigung
für die jeweilige Sauna (`entry_id`).

| Endpunkt | Zugriff und Bedeutung |
| --- | --- |
| `GET /api/ha_sauna/{entry_id}/archive` | Leserechte; ohne `session_id` die Sitzungsliste, mit `session_id` Sitzungsstand und Datensätze. |
| `GET /api/ha_sauna/{entry_id}/export` | Administrator; ZIP-Export. |
| `POST /api/ha_sauna/{entry_id}/archive/erase` | Administrator; `{"session_id":"…"}` löscht eine Sitzung, `{"reset":true}` das gesamte Archiv. |

Archivseiten werden mit `after` abgefragt. Maßgeblich ist der zurückgegebene
Zeiger `next_after`, auch wenn eine Seite durch Rechtefilterung leer ist.
`projection=history` beschränkt die Antwort auf Verlaufsdaten; die vollständige
Abfrage und der Export behalten alle Originaldatensätze.

Normale Benutzer erhalten Messwerte, Quellenzustände und Phasen.
Quellen-IDs, Konfiguration und Diagnosedaten sind Administratoren vorbehalten.
