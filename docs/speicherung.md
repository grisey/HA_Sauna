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
Aufzeichnung, solange Home Assistant und die Integration dazwischen nicht neu
starten. Auch manuelles Vorheizen vor dem Wechsel auf Automatik bleibt
Teil dieser Sitzung. Erst der Ablauf dieser Frist entscheidet über den
Abschluss und die Speicherung; die Lichtbedienung verkürzt sie nicht.

Das Archiv umfasst Messungen vor und nach der Sitzung. Die Zeiträume sind
konfigurierbar; der Standard beträgt jeweils **15 Minuten**. Maßgeblich ist der
bei der Sitzung gespeicherte [Messkontext](parameter.md#daten-und-laufzeit).
Außerhalb dieser Fenster werden keine sitzungslosen Messreihen dauerhaft
aufbewahrt. Der Nachlauf kann eine bereits beendete Sitzung noch ergänzen.

Nach einem Neustart von Home Assistant oder der Integration bleibt eine
unterbrochene Sitzung historisch offen; ein Endzeitpunkt wird nicht erfunden.
Sie setzt den Ofenbetrieb nicht fort, und erneutes Einschalten beginnt eine neue
Sitzung, auch innerhalb der Wiederaufnahmefrist.

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
| `recognition_decay` | Wenn die Erkennung nach einem Aufguss entscheidet, dass das Abklingen abgeflacht ist | Zeitpunkt, Entscheidungszeit und Zeitpunkt der steilsten Abnahme. Begrenzt den [Feuchtezuwachs je Aufguss](kennzahlen.md). |

Daraus stammen die Außentemperatur (K-S20) und die verworfenen Kandidaten
(K-E2) der [Kennzahlen](kennzahlen.md).

Erkennungsmeldungen (`detection`, `detection_rejected`) zu Tür auf, Tür zu und
Aufguss tragen neben der Wahrscheinlichkeit (`probability`) wie
`recognition_rejected` den Zustand, die nächstbeste Erklärung und deren
Verhältnis bei der Entscheidung (`state`, `against`, `ratio`), entnommen dem
[Entscheidungsprotokoll](erkennung.md#entscheidung) der Erkennung. Ältere
Sitzungen enthalten nur die Wahrscheinlichkeit.

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
den aufgezeichneten Eingaben: Messwerte oben und unten, Präsenz, Heizzustand,
Betrieb an und aus, manuelle Ofenwahl, **Ofenkühlung beenden**, Solländerungen
und Wechsel der Programmart.

Die Steuerung erhält als Schützrückmeldung ihren eigenen Schaltbefehl, wie im
Automatikbetrieb. Ofenkühlung, Schützgeschichte und Phasen verlaufen damit wie
mit dem aktuellen Stand; die Kühlung endet spätestens nach ihrer berechneten
Dauer. Der aufgezeichnete Heizzustand bestimmt Heizzeit und Energie und geht wie
im Betrieb an die Erkennung. Er stammt aus der Schützgeschichte der Sitzung;
ältere Sitzungen ohne diese Geschichte verwenden die aufgezeichneten Zustände
des Ofenschalters. Dass der aufgezeichnete Heizzustand nicht zur neu gerechneten
Steuerung passt, etwa Heizen in einer nachgerechneten Kühlung, ist hinzunehmen.
Daraus entstehen keine Fehlermeldungen, weil im Nachbau keine Geräte überwacht
werden.

Solländerungen umfassen Soll-, Endtemperatur und Steigerungsverteilung. Ein
gespeicherter Wechsel der Programmart (konstant oder steigend) geht mit ein;
beginnt die Sitzung vor dem ersten Wechsel mit einer anderen Programmart als
der am Ende gespeicherten, gilt bis dahin die Programmart vor diesem Wechsel.
Ein beendetes Kühlen von Hand gilt der Kühlung, die im nachgerechneten Ablauf zu
dieser Zeit läuft.

Neu entstehen Erkennung, Gänge, Phasen, Entscheidungen, Sitzungsstände,
Zustandsverlauf und verworfene Kandidaten. Messwerte, Quellzustände,
Heizbeobachtungen, Schaltbefehle, Umgebung und die Kennzeichnung Testbetrieb
werden unverändert übernommen.

Nachträge des Nutzers (`--nachtraege`, je Sitzung etwa
`{"name": "end_gang", "at": "…"}` für einen Gang, der von Hand hätte beendet
werden sollen) gehen wie Bedienungen ein. Jede Sitzung behält ihre Kennung und
erhält einen internen Herkunftseintrag `rebuild` mit dem Stand; das Panel zeigt
ihn nicht.

Endet eine Sitzung mit einer Unterbrechung durch Entladen oder Neustart der
Integration und beginnt die nächste Sitzung innerhalb der Wiederaufnahmefrist
nach Betrieb AUS, führt das Werkzeug beide zusammen. Im Betrieb bleiben solche
Sitzungen getrennt ([Neustart](#welche-sitzungen-erhalten-bleiben)). Maßgeblich ist die
gespeicherte Frist der ersten Sitzung, ohne sie die Werkseinstellung. Die
Eingaben beider Teile laufen als eine Sitzung mit der Kennung der ersten; die
Lücke gilt wie Betrieb AUS und erneutes EIN innerhalb der Frist. Übernommene
Einträge beider Teile und sitzungslose Messwerte in der Lücke gehören zur
zusammengeführten Sitzung, ebenso Kennzeichnung Testbetrieb und Nachträge. Der
Herkunftseintrag nennt die zusammengeführten Kennungen.

Die neue Datei ersetzt das Archiv; einen Umschalter auf das Original
gibt es nicht, die alte Datei bleibt nur als Sicherung. Die Archivrevision liegt
über der Quelle und mindestens bei `--mindestrevision`, damit geöffnete
Ansichten neu laden, auch wenn das ersetzte Archiv seit der Kopie weitergezählt
hat. Ausgetauscht wird die Datei wie beim Bereinigen bei angehaltenem Home
Assistant Core.

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
(Soll und mittlere Abweichung davon, Eintritt, Austritt, Höchste, Feuchtezuwachs
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
**Wird geladen** kennzeichnet den ersten Datenabruf. Eine laufende Sitzung wird in
Seiten geladen; das Diagramm erscheint nach der ersten Seite und vollständig nach der
letzten. Eine abgeschlossene Sitzung kommt als [Übersicht](#verlaufsansicht-abgeschlossener-sitzungen)
in einer Antwort. Nach dem Zeichnen lädt das Panel im Hintergrund die vollen
Sekundenwerte der Sitzung, Abschnitt für Abschnitt der gespeicherten Ansicht. Die
Kurve bleibt dabei unverändert. Sobald alle Abschnitte da sind, gelten die Werte
beim Zeigen mit der Maus, an Auswahlgrenzen, „–“ bei Ausfällen und die absolute
Feuchte sekundengenau; vorher stammen sie aus der Übersicht. Beim Zoomen kommen die
Abschnitte des Ausschnitts zuerst und bereits geladene ohne neuen Abruf zur Kurve.
Wechselt die Sitzung oder ist der Verlauf ausgeblendet, ruht das Nachladen; geladene
Abschnitte bleiben für die Rückkehr. **Details · Erkennung** und **Steuerung** zeigen
alle Werte der Sitzung, aus den nachgeladenen Abschnitten oder mit einem eigenen
Abruf; bis dahin zeichnet der Verlauf mit der Übersicht. Bei einer fehlgeschlagenen
Aktualisierung bleibt der vorhandene Stand mit einem Fehlerhinweis sichtbar; ein
Fehler beim Nachladen im Hintergrund lässt die Werte der Übersicht stehen.

### Verlaufsansicht abgeschlossener Sitzungen

Für jede abgeschlossene Sitzung speichert das Archiv die Anzeigedaten des Verlaufs
in der Tabelle `history_views`, sobald ihr Messfenster vollständig ist (Ende plus
`history_context_seconds`): Sitzungsstand, Messfenster, Phasen, die übrigen
Datensätze der Verlaufsprojektion mit ihrer Anzeige und alle Messwerte mit
absoluter Feuchte und Gültigkeit in Zeitabschnitten von rund 17,5 Minuten
(2²⁰ ms). Gerechnet wird nach dem Start von Home Assistant im Hintergrund (nach den
Kennzahlen, neueste Sitzung zuerst), nach dem Ende einer Sitzung, sobald ihr
Messfenster voll ist, und sonst beim ersten Abruf. Die Vorbereitung endet vor der
nächsten Sitzung, wenn das Archiv schließt oder für ein Backup anhält.

Ein Eintrag gilt nur zu seiner Kennung aus Rechenversion, Archivrevision, letzter
Änderung der Sitzung und letzter Datensatz-ID der Sitzung. Löschen, Zurücksetzen,
Kennzeichnen als Testbetrieb, die Bereinigung beim Start und ein Neuaufbau ändern
die Archivrevision; Löschen und Zurücksetzen entfernen die Einträge. Laufende
Sitzungen und Sitzungen mit offenem Messfenster werden wie bisher seitenweise
gelesen.

Die Übersicht enthält je Messreihe (Temperatur, relative und absolute Feuchte je
Position) und Zeitabschnitt den ersten, letzten, kleinsten und größten Wert (M4).
Die Abschnitte sind Zweierpotenzen in Millisekunden; gespeichert werden 2048
Abschnitte über das Messfenster und sechs gröbere Stufen. Das Panel fasst seine
Kurven ebenso in Zweierpotenz-Abschnitten je CSS-Pixel zusammen; liefert der Server
die gröbste Stufe, die für die Breite der Kurvenfläche noch aus ganzen Abschnitten
des Panels besteht, zeichnet das Panel dieselben Punkte wie mit allen Werten.
Spitzen bleiben erhalten; alle Werte sind Originalwerte vorhandener Messungen. Die
Abschnitte an Anfang und Ende des Messfensters kommen vollständig.

**Details** (Administratoren) hat vier Unteransichten. **Zustand** zeigt in einer
Statuszeile, ob alle Messwerte gültig sind, die Zahl aktueller Fehler und den
Stand. Darunter stehen die Werte je Messposition (Temperatur, relative und
absolute Feuchte, Gültigkeit und Abstand der letzten beiden Messungen,
Regelposition) mit der Differenz oben − unten und ihrem zeitgewichteten Mittel
über die laufende Sitzung und ab dem ersten Bereit, dazu die von Regelung und
Erkennung genutzten Positionen; ohne aktuelle Regeltemperatur steht nur der Stand
der Erkennung. Dann folgen Geräte und
Rückmeldungen (Ofen mit Rückmeldequelle, Heizzeit, gemessene Leistung nur mit
Leistungssensor, Licht mit Modus, Präsenz, Tür, laufende Fristen), die Umgebung,
Archiv und Start (Sitzungen und Datensätze im Archiv, Dateigröße, Startdauer des
Archivs beim Laden der Integration) und die aktuellen Fehler. Die Rohdaten sind
eingeklappt und werden erst beim Öffnen aufbereitet. Archivumfang und Mittelwerte
liefert `GET …/archive/overview` (Administratoren) im Takt der
Verlaufsaktualisierung. **Erkennung** ist die [Erkennungskontrolle](erkennung.md#erkennungskontrolle), **Kalibrierung** Start,
laufenden Lauf, gespeicherte Läufe mit Ergebnis je Schritt und Raster der
[Kalibrierung](kalibrierung.md#start-und-abbruch).

**Steuerung** zeigt die gewählte Sitzung aus dem Archiv. Der Betriebsverlauf hat
eine Spur je Frage: Phase, Temperatur gegen Soll, Heizanforderung (gefärbt nach dem
gespeicherten Grund, kräftig für Heizen, blass für Nicht-Heizen), Ofenrückmeldung
aus den Heizintervallen, Ofenkühlung, Tür, Präsenz, Licht und manuelle Eingriffe.
Für den gewählten Zeitpunkt stehen der Grund der letzten Heizentscheidung, Phase
und Beginn, Temperatur und Soll, Ofenbefehl und Rückmeldung, Tür und Präsenz, ein
laufender Gang sowie unter **Danach** die nächsten Wechsel der Heizanforderung.
**Fristen und Regeln** stehen einmal: Ofenkühlungen mit Beginn, Ende, Dauer und
auslösendem Gang, Dauerrahmen der Ofenkühlung, Heizpause, Mindestheizzeit,
Regelband, Mindesttemperatur und Sollfolge mit Programm, gerechnet aus dem
gespeicherten Sitzungsstand und der gespeicherten Konfiguration der Sitzung
(`control_rules` in der Archivantwort für Administratoren); die heutige Einstellung
gilt nicht rückwirkend. Das **Protokoll** ist eingeklappt und je Typ filterbar
(Heizentscheidungen, Ofenbefehle, Lichtbefehle, Präsenzmeldungen, Bedienungen,
gebuchte Gänge mit Beginn, Bestätigung und Ende, Erkennungen, Schaltausgang,
Heizrückmeldungen, Fehler im Sitzungsverlauf, Temperaturvorgaben, Betrieb und
Hinweise); ein Klick auf die Uhrzeit setzt den Zeitpunkt. Kalibrierzeilen stehen
unter **Kalibrierung**. Die Rohdaten der Sitzung (Heizzeit und Energie,
Ofenkühlung und Fristen, Temperaturprogramm, Gänge und Präsenz, gespeicherte
Konfiguration, Messwerte und Lichtverlauf, Einzelbelege) werden erst beim Öffnen
aufbereitet. Die Originalfelder der Messungen (Originalwert, Messzeit,
Sensorkennung) lädt **Einzelbelege** für die gezeigte Seite nach.

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
| `GET /api/ha_sauna/{entry_id}/archive/measurements?session_id=…` | Leserechte; alle Messwerte einer abgeschlossenen Sitzung aus der gespeicherten [Verlaufsansicht](#verlaufsansicht-abgeschlossener-sitzungen) im Format von `measurement_columns`, mit `from` und `to` (Millisekunden seit 1970, Ende ausgeschlossen) nur die Messungen mit Empfangszeit darin. Ohne `session_id` oder mit anderen Parametern 400; unbekannte Sitzung oder offenes Messfenster 404. |
| `GET /api/ha_sauna/{entry_id}/export` | Administrator; ZIP-Export als fortlaufende Übertragung. Mit `check=1` nur die Prüfung bis zum festgelegten Stand, Antwort `{"success":true}`. Fehler vor Beginn der Übertragung als JSON `{"error":"…"}`; ein bereits laufender Export oder ein Abbruch durch ein Backup ergibt 409. |
| `POST /api/ha_sauna/{entry_id}/archive/erase` | Administrator; `{"session_id":"…"}` löscht eine Sitzung, `{"reset":true}` das gesamte Archiv. |
| `POST /api/ha_sauna/{entry_id}/archive/label` | Administrator; `{"session_id":"…","test":true}` kennzeichnet eine abgeschlossene Sitzung als [Testbetrieb](kennzahlen.md#testbetrieb), `false` hebt das auf. Unbekannte Sitzung 404, laufende 409. |
| `GET /api/ha_sauna/{entry_id}/key-figures?session_id=…` | Leserechte; [Kennzahlen](kennzahlen.md) einer abgeschlossenen Sitzung, sonst 404. |
| `GET /api/ha_sauna/{entry_id}/archive/recognition?session_id=…` | Administrator; [Erkennungskontrolle](erkennung.md#erkennungskontrolle) einer laufenden oder abgeschlossenen Sitzung: Zusammenfassung, Zustandsspur, Ereignisse mit Bedeutung und Entscheidungszeit, verworfene Kandidaten, aufgehobene vorläufige Gänge. Ohne `session_id` 400, unbekannte Sitzung 404. |
| `GET /api/ha_sauna/{entry_id}/archive/calibration` | Administrator; gespeicherte [Kalibrierläufe](kalibrierung.md#start-und-abbruch) ohne den laufenden, der zuletzt begonnene zuerst (`runs`), und das Ergebnis je Schritt des gewählten (`run`, ohne `run_id` des ersten). Andere Abfrageparameter 400, unbekannte `run_id` 404. |
| `GET /api/ha_sauna/{entry_id}/statistics` | Leserechte; [Statistik](kennzahlen.md#statistik) über alle abgeschlossenen Sitzungen, mit `season` (Anfangsjahr) über eine Saison, mit `season` und `part` (`autumn`, `winter`, `spring`) über eine Jahreszeit, mit `summer` über einen Sommer. `periods` nennt alle Saisons mit ihren Jahreszeiten und alle Sommer mit ausgewerteten Sitzungen. |

Archivseiten werden mit `after` abgefragt. Maßgeblich ist der zurückgegebene
Zeiger `next_after`, auch wenn eine Seite durch Rechtefilterung leer ist.
`projection=history` beschränkt die Antwort auf Verlaufsdaten; die vollständige
Abfrage und der Export behalten alle Originaldatensätze.

In der Verlaufsprojektion stehen Messungen und Anfangsmessungen spaltenweise unter
`measurement_columns`, alle übrigen Datensätze unter `records`:

| Feld | Inhalt |
| --- | --- |
| `roles` | Art, Position und Größe, dazu die Quelle (Administratoren) oder `source_matches_binding` (sonst). |
| `role`, `id`, `received_at`, `value` | Je Messung in Archivreihenfolge: Rolle, Datensatz-ID, Empfangszeit und Wert. |
| `absolute_humidity`, `absolute_humidity_valid_until` | Je Messung die absolute Feuchte und ihre Gültigkeit, sonst `null`. |
| `record_received_at` | Paare aus Datensatz-ID und Archivzeit, wo diese von der Empfangszeit abweicht (Anfangsmessungen). |

Originalwert, Messzeit und Sensorkennung liefert die Abfrage ohne Projektion.
Messungen anderer Form bleiben einzelne Datensätze unter `records`.

Mit `resolution=overview` (nur mit `projection=history`, `after=0` und ohne
`energy_after`) liefert das Archiv eine abgeschlossene Sitzung mit vollständigem
Messfenster aus der gespeicherten Verlaufsansicht: alle übrigen Datensätze in einer
Antwort (`next_after: null`) und unter `measurement_columns` die Übersicht. `columns`
nennt die Pixel der Kurvenfläche (1 bis 100.000); ohne `columns` gilt die feinste
gespeicherte Stufe. `measurement_resolution` beschreibt die Antwort:

| Feld | Inhalt |
| --- | --- |
| `overview` | `true`, wenn nicht alle Messungen geliefert sind. |
| `bin_ms` | Abschnittsbreite der gelieferten Stufe. Das Panel lädt nach, sobald seine eigene Breite kleiner ist. |
| `columns`, `chunk_ms` | Gespeicherte Abschnitte über das Messfenster, Zeitabschnitt der vollen Werte. |
| `rows`, `total_rows` | Gelieferte und alle Messungen der Sitzung. |

Für andere Sitzungen gilt die Seitenabfrage ohne `measurement_resolution`.
Antworten aus der gespeicherten Ansicht tragen ein `ETag` aus Sitzungs- und
Archivrevision, Rechenversion, Berechtigung und Stufe und `Cache-Control: private,
no-cache`; der Browser fragt jedes Mal nach und erhält bei unveränderter Ansicht 304.

Normale Benutzer erhalten Messwerte, Quellenzustände und Phasen.
Quellen-IDs, Konfiguration und Diagnosedaten sind Administratoren vorbehalten.
