# Archiv und Export

## Welche Sitzungen erhalten bleiben

Sitzungen mit mindestens einem bestätigten Gang bleiben mit ihren Originaldaten
ohne Altersbegrenzung erhalten, ebenso Sitzungen mit einem
[Kalibrierprotokoll](kalibrierung.md#protokoll-und-aufbewahrung). Die übrigen
Sitzungen ohne bestätigten Gang werden nach dem Abschluss verworfen; dieselbe
Bereinigung erfolgt beim Integrationsstart für ältere Sitzungen. Das gilt auch
für Aufzeichnungen im manuellen Modus.

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
Ausgaben und Fehlerzustände erhalten, ebenso
[Umgebung und Erkennungsverlauf](#umgebung-und-erkennungsverlauf). Die
vollständigen internen Prüfverläufe der Erkennung (`detector_trace`, die
Zustandswahrscheinlichkeiten jedes Messschritts) werden nur bei ausdrücklich
gewählter Analyseaufzeichnung zusätzlich gespeichert. Die aktuelle
Live-Diagnose bleibt in beiden Profilen verfügbar; Erkennung und Regelung
verwenden dieselben Eingänge und Regeln. Bereits gespeicherte Prüfverläufe
bleiben lesbar und im Export enthalten.

### Umgebung und Erkennungsverlauf

Diese Einträge gehören zur Sitzung und werden in beiden Profilen gespeichert.
Im Export stehen sie wie alle Datensätze in `records.jsonl`. Ältere Sitzungen
enthalten sie nicht.

| Eintragsart | Gespeichert | Inhalt |
| --- | --- | --- |
| `environment` | Bei Sitzungsbeginn, danach bei jeder neuen Messzeit der gebundenen [Umgebungsquelle](parameter.md#umgebungsdaten); liefert die Quelle keine Messzeit, bei jedem geänderten Wert | Anlass (`session_start`, `source_changed`), Wetterlage, Station, je Größe Zustand, Wert, Einheit, Verfügbarkeit, HA-Aktualisierung und liefernde Entität, Mess- und Vorhersagezeitpunkt, gebundene Wetter- und Zeitentitäten, Datenart der Quelle und letzte Aktualisierung. Es gibt keinen eigenen Takt; eine bloße Aktualisierung in Home Assistant oder interpolierte Zwischenwerte derselben Messung ergeben keinen Eintrag. Ohne gebundene Quelle entfällt er. |
| `recognition_state` | Bei jedem Wechsel des wahrscheinlichsten [Erkennungszustands](erkennung.md#zustandsmodell-der-kabine) | Messschritt, neuer Zustand (`closed`, `open`, `infusion`, `decay`, `recovery`, `shift`) und seine Wahrscheinlichkeit. Bei gleicher Wahrscheinlichkeit bleibt der bisherige Zustand. |
| `recognition_rejected` | Wenn die Öffnungsform einen Öffnungskandidaten verwirft | Art (`door_open`), Zeitpunkt, Entscheidungszeit, Grund (`opening_form`) sowie Zustand, nächstbeste Erklärung und deren Verhältnis bei der Bestätigung des Kandidaten. |
| `recognition_episode` | Wenn die Erkennung eine Episode verworfener Öffnungskandidaten meldet | Öffnen, Schließen, Schlusskandidat, Ende `E` und Meldung der Episode, ihre Kandidaten und das Ergebnis: `rejected` oder `door`, wenn das [Gangmodell](gangmodell.md#verworfene-öffnung-mit-präsenz) sie über die Anwesenheit als Türvorgang übernimmt. Der Türvorgang selbst steht als Erkennungsmeldung mit Herkunft `presence` im Archiv. |

Daraus stammen die Außentemperatur (K-S20) und die verworfenen Kandidaten
(K-E2) der [Kennzahlen](kennzahlen.md).

## Sitzungen exportieren

**Archiv als ZIP herunterladen** unter **Einstellungen → Daten und Wartung**
erfordert Administratorrechte.
Er enthält einen konsistenten Stand, auch während einer laufenden Aufzeichnung:
den Stand bei Beginn des Exports. Später eingehende Daten werden weiter
gespeichert und sind im nächsten Export enthalten.

Der Export liest diesen Stand in einer Lesetransaktion direkt aus der
Archivdatei und überträgt das ZIP schon während der Erzeugung. Er legt weder
eine Kopie noch eine temporäre Datei an. Vor dem Download prüft das Panel, ob
der Export beginnen kann; ein Fehler bis dahin erscheint als Meldung im Panel.
Bricht der Export während der Übertragung ab, endet der Download unvollständig,
und das Home-Assistant-Protokoll enthält einen Fehlereintrag.

Je Sauna läuft höchstens ein Export; ein weiterer wird mit einer Meldung
abgelehnt. Ein beginnendes Home-Assistant-Backup hat Vorrang: Es beendet einen
laufenden Export, der Export endet mit Fehler.

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | Schema, Instanz, Auflösung und Zeitformat |
| `sessions.jsonl` | Neuester Stand jeder Sitzung mit Sitzungs-ID (`session_id`) einschließlich ihrer Konfiguration |
| `records.jsonl` | Alle Datensätze und gespeicherten Revisionen in Aufzeichnungsreihenfolge |
| `measurements.csv` | Originalmessungen mit Datensatz-/Sitzungs-ID, Empfangs-/Messzeit, Messort, Messgröße, Quelle, Wert und Rohwert |

## Archiv sichern und wiederherstellen

Je Sauna liegt eine SQLite-Datei unter
`<HA-Konfigurationsverzeichnis>/ha_sauna/<entry-id>.sqlite`.
Sie wird im Home-Assistant-Backup konsistent gesichert; ein laufender
[Export](#sitzungen-exportieren) wird dafür beendet.

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

### Archiv bereinigen

Ältere Archive enthalten viele Sitzungsstände, die sich nur in Zählern
unterscheiden (vor AL3 je Sekunde der Ofenkühlung), und das Erkennungsprotokoll
des Analyseprofils. Das Werkzeug
[`tools/archive_cleanup.py`](../tools/archive_cleanup.py) schreibt eine
bereinigte Kopie: Es behält je Sitzung den ersten, den letzten und jeden
inhaltlich geänderten Stand nach derselben Regel wie der laufende Betrieb, legt
das Erkennungsprotokoll mit `--trace` in eine eigene Datei, erhöht die
Archivrevision und verkleinert die Datei. Sitzungen, Messwerte und alle übrigen
Einträge bleiben unverändert; die Quelle wird nicht verändert. Ausgetauscht wird
die Datei bei angehaltenem Home Assistant Core.

### Archiv neu aufbauen

Das Werkzeug [`tools/archive_rebuild.py`](../tools/archive_rebuild.py) rechnet
alle Sitzungen einer Archivkopie mit dem aktuellen Stand nach und schreibt ein
neues Archiv; die Quelle bleibt unverändert. Je Sitzung läuft die Laufzeit auf
den aufgezeichneten Eingaben: Messwerte oben und unten, Präsenz,
Schützrückmeldung, Betrieb an und aus, manuelle Ofenwahl und Solländerungen.
Neu entstehen Erkennung, Gänge, Phasen, Entscheidungen, Sitzungsstände,
Zustandsverlauf und verworfene Kandidaten; Heizzeit und Energie folgen der
aufgezeichneten Schützrückmeldung. Messwerte, Quellzustände,
Heizbeobachtungen, Schaltbefehle, Umgebung und die Kennzeichnung Testbetrieb
werden unverändert übernommen. Dass die aufgezeichnete Schützstellung nicht zur
neu gerechneten Steuerung passt, ist dabei hinzunehmen.

Nachträge des Nutzers (`--nachtraege`, je Sitzung etwa
`{"name": "end_gang", "at": "…"}` für einen Gang, der von Hand hätte beendet
werden sollen) gehen wie Bedienungen ein. Jede Sitzung behält ihre Kennung und
erhält einen internen Herkunftseintrag `rebuild` mit dem Stand; das Panel zeigt
ihn nicht. Die neue Datei ersetzt das Archiv; einen Umschalter auf das Original
gibt es nicht, die alte Datei bleibt nur als Sicherung. Die Archivrevision liegt
über der Quelle, damit geöffnete Ansichten neu laden. Ausgetauscht wird die
Datei wie beim Bereinigen bei angehaltenem Home Assistant Core.

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

Der Verlauf hat zwei Unteransichten. **Sitzung** zeigt oben das Sitzungsbild
(Saunazeit, Aufgüsse, Höchsttemperatur, Ruhe zwischen den Gängen, Sitzung),
darunter das Diagramm mit Temperatur und Feuchte und unten die Gänge im Vergleich
(Soll und mittlere Abweichung davon, Eintritt, Austritt, Höchste, Feuchteanstieg
und Wasser je Aufguss, Ruhe)
neben der Auswahl. **Anlage** zeigt Aufheizen, Regelung im Bereit, Heizen und
Energie sowie den Wärmeverlust durch die Tür, darunter die Temperatur gegen die
gestrichelte Sollinie mit Ofenbahn und Türflächen. Alle Zahlen stammen aus den
[Kennzahlen](kennzahlen.md) der beendeten Sitzung; das Panel rechnet keine
Kennzahl. Sollbezogene Werte erscheinen nur, wenn die Sitzung einen Sollwert hat.
Für eine laufende Sitzung folgen die Kennzahlen nach ihrem Ende.

Beide Achsen des Diagramms sind glatt geteilt (Schritte 1, 2, 2,5 und 5 × 10ⁿ)
und teilen sich die Querlinien; die Zahl der Teilungen gibt der schlechter
genutzten Kurve die meiste Höhe. Phasen stehen im Band über dem Diagramm,
Ereignisse in eigenen Bahnen darunter: Aufgüsse als weiße Raute und dünne Linie,
Türvorgänge als hellbrauner Balken und Fläche, der Ofen als Balken. Die Schalter
über dem Diagramm blenden Aufgüsse (Standard), Türvorgänge und Ofen ein und
wechseln zwischen relativer und absoluter Feuchte.

Ein Klick auf einen Zeitpunkt markiert dessen Messwerte, ein zweiter einen
Zeitraum. Ein weiterer Klick oder **Auswahl aufheben** hebt die Auswahl auf; mit
dem Finger gilt dasselbe für Tippen. Die **Auswahl** zeigt am Zeitpunkt
Temperatur und Feuchte in beiden Einheiten und im Gang die Aufgüsse bis dahin,
für einen Zeitraum die Werte an Anfang und Ende und die Ereignisse darin. Für
einen gewählten Saunagang wird seine Karte hervorgehoben; die Auswahl nennt die
Gangzeiten, die Zeiten der Aufgüsse, Eintritt, kurze Öffnungen und Austritt sowie
die höchste Feuchte. In einer laufenden Sitzung nennt sie zusätzlich den
Bestätigungsstand **Vorläufig** oder **Bestätigt** und den belegten Türschluss
beim Eintritt. Die Gangdauer verwendet die zugeordneten Grenzen; die Türöffnung
allein beendet den Gang nicht, fehlende Grenzen bleiben unbelegt. Ein
[von Hand beendeter Gang](gangmodell.md#gang-von-hand-beenden) endet zum
Tastendruck; der Tastendruck steht als Bedienhandlung `manual_gang_end` mit Gang,
Zeitpunkt, vorherigem Bestätigungsstand und Bediener im Archiv. Ein gewähltes
Ereignis zeigt Zeit, Art und beim Aufguss sein Ende; Administratoren springen von
dort zum Erkennungsnachweis. Fehler im Sitzungsverlauf, aufgehobene vorläufige
Erkennungen und gebuchte Gang- und Bestätigungszeiten stehen in der Detailansicht.
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

**Details** (Administratoren) hat vier Unteransichten. **Zustand** zeigt in einer
Statuszeile, ob alle Messwerte gültig sind, die Zahl aktueller Fehler und den
Stand. Darunter stehen die Werte je Messposition (Temperatur, relative und
absolute Feuchte, Gültigkeit und Abstand der letzten beiden Messungen,
Regelposition) mit der Differenz oben − unten und ihrem zeitgewichteten Mittel
über die laufende Sitzung und ab dem ersten Bereit; dann Geräte und
Rückmeldungen (Ofen mit Rückmeldequelle, Heizzeit, gemessene Leistung nur mit
Leistungssensor, Licht mit Modus, Präsenz, Tür, laufende Fristen), die Umgebung,
Archiv und Start (Sitzungen und Datensätze im Archiv, Dateigröße, Startdauer des
Archivs beim Laden der Integration) und die aktuellen Fehler. Die Rohdaten sind
eingeklappt und werden erst beim Öffnen aufbereitet. Archivumfang und Mittelwerte
liefert `GET …/archive/overview` (Administratoren) im Takt der
Verlaufsaktualisierung. **Erkennung** ist die [Erkennungskontrolle](erkennung.md#erkennungskontrolle), **Steuerung** der Verlauf der
Sitzung mit Regelentscheidungen, Fristen und Protokoll, **Kalibrierung** Start,
Lauf und Raster der [Kalibrierung](kalibrierung.md#start-und-abbruch).

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
| `trace_at` (optional) | Messschritt, mit dem die Erkennung das Ereignis bestätigt hat. |
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
| `GET /api/ha_sauna/{entry_id}/export` | Administrator; ZIP-Export als fortlaufende Übertragung. Mit `check=1` nur die Prüfung bis zum festgelegten Stand, Antwort `{"success":true}`. Fehler vor Beginn der Übertragung als JSON `{"error":"…"}`; ein bereits laufender Export oder ein Abbruch durch ein Backup ergibt 409. |
| `POST /api/ha_sauna/{entry_id}/archive/erase` | Administrator; `{"session_id":"…"}` löscht eine Sitzung, `{"reset":true}` das gesamte Archiv. |
| `POST /api/ha_sauna/{entry_id}/archive/label` | Administrator; `{"session_id":"…","test":true}` kennzeichnet eine abgeschlossene Sitzung als [Testbetrieb](kennzahlen.md#testbetrieb), `false` hebt das auf. Unbekannte Sitzung 404, laufende 409. |
| `GET /api/ha_sauna/{entry_id}/key-figures?session_id=…` | Leserechte; [Kennzahlen](kennzahlen.md) einer abgeschlossenen Sitzung, sonst 404. |
| `GET /api/ha_sauna/{entry_id}/archive/recognition?session_id=…` | Administrator; [Erkennungskontrolle](erkennung.md#erkennungskontrolle) einer laufenden oder abgeschlossenen Sitzung: Zusammenfassung, Zustandsspur, Ereignisse mit Bedeutung und Entscheidungszeit, verworfene Kandidaten, aufgehobene vorläufige Gänge. Ohne `session_id` 400, unbekannte Sitzung 404. |
| `GET /api/ha_sauna/{entry_id}/statistics` | Leserechte; [Statistik](kennzahlen.md#statistik) über alle abgeschlossenen Sitzungen, mit `season` (Anfangsjahr) über eine Saison, mit `season` und `part` (`autumn`, `winter`, `spring`) über eine Jahreszeit, mit `summer` über einen Sommer. `periods` nennt alle Saisons mit ihren Jahreszeiten und alle Sommer mit ausgewerteten Sitzungen. |

Archivseiten werden mit `after` abgefragt. Maßgeblich ist der zurückgegebene
Zeiger `next_after`, auch wenn eine Seite durch Rechtefilterung leer ist.
`projection=history` beschränkt die Antwort auf Verlaufsdaten; die vollständige
Abfrage und der Export behalten alle Originaldatensätze.

Normale Benutzer erhalten Messwerte, Quellenzustände und Phasen.
Quellen-IDs, Konfiguration und Diagnosedaten sind Administratoren vorbehalten.
