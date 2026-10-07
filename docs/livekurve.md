# Daten- und Zeichenvertrag des Sitzungsverlaufs

Der Sitzungsverlauf wird in `panel.js` aus Status- und Archivantworten aufgebaut.
Er verbindet fortlaufende Messkurven mit der zeitlichen Einordnung einer
Saunasitzung.

[Darstellungsreferenz](darstellung.md) · [Bedienungsanleitung](bedienung.md) ·
[Archivvertrag](speicherung.md)

## Datenübernahme und Sitzungsabschluss

Der Verlauf lädt Archivdaten seitenweise. Jede vollständig empfangene Seite
übernimmt den Sitzungssnapshot, die Phasenprojektion und den Seitenzeiger.
Auch eine Seite mit leerer Recordliste kann dadurch einen aktualisierten
Abschlussstand liefern. Bei einem Abruffehler setzt der nächste Versuch am
zuletzt übernommenen Seitenzeiger an.

| Cachefeld | Bedeutung |
|---|---|
| `after` | Übernommener Seitenzeiger für den nächsten Abruf |
| `pageRunLoaded` | Der aktuelle Seitenlauf ist vollständig gelesen. |
| `finalSynced` | Der vollständige Seitenlauf enthält einen endgültigen Sitzungssnapshot mit `ended_at`. |

Ein Cache mit offenem Sitzungssnapshot wird weiter nachgeladen, auch beim
späteren Auswählen dieser Sitzung. Laufende Abrufe gehören zur Instanz,
Auswahlgeneration und Sitzungsidentität ihres Auftrags. Antworten werden
übernommen, solange dieser Bezug zur aktuellen Auswahl passt.

Die Panelabfrage verwendet `projection=history` und bis zu 5000 Records pro
Seite. Sie überträgt Messungen, Quellen-Schnappschüsse, Phasen und für
Administratoren zusätzlich Erkennungen und Diagnosen. Frühere vollständige
Sitzungsrevisionen und übrige interne Records bleiben im Archiv und Export;
für den Verlauf genügt der aktuelle Sitzungssnapshot jeder Antwort. Die
Filterung erfolgt vor der Seiteneinteilung. Alle zugehörigen Originalmesspunkte
bleiben unverändert erhalten.

Die Record-ID bestimmt die Deduplizierung. Verschiedene Records mit gleichem
Quellzeitpunkt behalten ihre eigene Identität. Der chronologische Index führt
Temperatur und Feuchte für jede Messposition als eigene Reihe und gruppiert
Ereignisse nach Art. Ein geordneter Nachtrag erweitert den Index. Bei einer
Einfügung in einen früheren Reihenabschnitt wird dessen Darstellungshierarchie
neu aufgebaut.

## Originalpunkt und sichtbare Kurve

Die Kurvenaufbereitung arbeitet mit den indexierten Messpunkten. Fehlende Werte,
abgelaufene Messwertgültigkeit und ein gesetztes `displayGap` bilden
Segmentgrenzen. Innerhalb eines Bildschirmintervalls bleiben erster, kleinster,
größter und letzter Punkt sowie die erforderlichen Randnachbarn für die
Zeichnung verfügbar. Monotone kubische Kontrollpunkte bilden den Kurvenverlauf.

Die Aufbereitung wird je Reihe zwischengespeichert. Ihr Schlüssel beschreibt
den sichtbaren Zeitraum samt Messwertgültigkeit und Aggregationsbreite. Anzahl
und Identität der Randpunkte binden die Auswahl an ihren Datenstand. Nachträge
innerhalb dieses Bereichs erneuern die betroffene Auswahl; zusätzliche
Extremwerte führen zur Anpassung der Skala und der davon abhängigen Pfade.

Zoom und Verschieben verwenden die vorhandenen inneren Aggregationsgruppen
weiter. Fensterränder werden anhand ihrer tatsächlichen Nachbarpunkte bestimmt.
Die gespeicherten Originalrecords bleiben die Quelle für Tooltip und
Erkennungskontrolle.

## Zeichenflächen und Geometrie

Ein `HistoryChart` gehört zu einer Instanz und einer Sitzungs-ID. Seine
Zeichenflächen und Eingabeelemente bleiben während der Aktualisierung derselben
Sitzung erhalten. Beim Wechsel der Sitzung oder Instanz entsteht die passende
neue Zeicheninstanz.

| Ebene | Technische Aufgabe |
|---|---|
| Canvas der Hauptansicht | Messkurven als numerisch aufgebaute `Path2D`-Objekte |
| Canvas der Übersicht | Übersichtskurve über die gesamte Sitzungsdomäne |
| SVG-Hintergrund | Phasen, Ereignisflächen und Raster unter den Kurven |
| SVG-Vordergrund | Achsen, Zeiger und Interaktionsfläche |
| DOM | Tooltiptexte, Tabellen, Beschriftungen und Ausschnittgriffe |

Die Phasenprojektion liefert die zeitlichen Abschnitte einschließlich
nachträglicher Zuordnungskorrekturen. Darstellung und Archivansicht übernehmen
diesen Stand. Die laufenden Aktorvorgaben entstehen im Controller; der
Geräteadapter führt sie aus.

Gespeicherte Pfade und fertige Zeichnungen berücksichtigen Datenquelle,
Messposition, Abbildung und Stil. Canvasgröße und Pixeldichte bestimmen die
Bitmapauflösung. Die CSS-Geometrie bildet Browserkoordinaten auf den sichtbaren
Verlauf ab. Eine Lesephase erfasst die dafür benötigten Clientrechtecke.
Scrollen entlang der übergeordneten Elemente, einschließlich Shadow-Roots und
Slots, sowie Größen- und Layoutänderungen erneuern diese Geometrie. Die
Resolution-Abfrage erkennt Änderungen der Pixeldichte.

## Ausschnitt, Zoom und Tooltip

Das sichtbare Zeitfenster liegt innerhalb der gesamten Sitzungsdomäne. Die
Übersicht ordnet es darin ein; ihre Griffe verändern die Fenstergrenzen.
Maus, Berührung und Tastatur verwenden denselben begrenzten Fensterzustand.
Zoomtasten und Vergrößerungsgesten ändern die Zeitabbildung. Strg/Cmd mit dem
Mausrad vergrößert am Zeiger, das gewöhnliche Mausrad bewegt die Seite.

[Rechtefilterung und Antwortzuordnung](speicherung.md#archivzugriff)

Der Tooltip sucht mit einer binären Suche in den Originalreihen nach dem
passenden Messpunkt. `raw_value` liefert den empfangenen Originalwert,
einschließlich des Werts `0`. Liegt ausschließlich der numerische Messwert vor,
kennzeichnet der Tooltip dessen Quellenqualität. Empfangszeit und vorhandener
Gerätezeitpunkt behalten ihre Sekundenbruchteile und erscheinen mit lokaler
Zeitzonenangabe.

Der Textcache gehört zum Originalpunkt und zur tatsächlich aufgelösten
Zeitzone. Geänderte Originalwerte oder Zeitangaben erneuern ihn. Die gemeinsame
Zeitformatierung versorgt Achsen, Tabellen und Annotationen. Dauerhafte
Textknoten und eine Positionierung per CSS-Transformation tragen den Tooltip;
die Eingabeverarbeitung verwendet dabei die zuvor gelesene Geometrie.

## Aktualisierung und Lebensdauer

Ein gemeinsamer Frameplaner bündelt anstehende Darstellungsarbeit für sichtbare
Ansichten. Statusänderungen aktualisieren die Bedienanzeige und die zugehörigen
Annotationen. Ein Archivnachtrag übernimmt Messdaten und den nachzuführenden
Zeitausschnitt gemeinsam.

Statusdaten werden während eines laufenden Betriebs, einer bestehenden Sitzung
oder eines Lichtnachlaufs im Zwei-Sekunden-Takt abgeglichen. Im ruhenden Betrieb
beträgt der Abstand zehn Sekunden. Bei verborgenem oder getrenntem Panel ruht
dieser Ablauf; beim erneuten Anzeigen folgt sofort ein Abgleich.
Allgemeine Home-Assistant-Zustandsweitergaben verkürzen den geplanten Abstand
nicht; gezielte Bedienhandlungen lösen bei Bedarf sofort einen Abgleich aus.

Sitzungsliste und Datenseiten werden getrennt vom Status geladen. Eine laufende
Sitzung ergänzt Seiten über ihren Cursor, ohne zuvor wiederholt die Liste zu
laden. Die Liste wird bei einem Sitzungsstart, Sitzungsabschluss oder einer
geänderten zuletzt beendeten Sitzung erneuert. Nach einer Unterbrechung des
Panels erfolgt einmalig ein Listenabgleich, sobald der Verlauf sichtbar ist.
Das erfasst auch eine Sitzung, die vollständig im Hintergrund stattgefunden hat.
Danach bleibt auch eine bestätigte leere Liste ohne weitere Listenabrufe gültig.
Vollständige Archivcaches und eine ausdrücklich ausgewählte ältere Sitzung
bleiben dabei erhalten. Die automatische Auswahl „Letzte Sitzung“ folgt dem
neuesten Eintrag. Regelmäßige Aktualisierungen fügen oberhalb vorhandener
Diagramme keinen Ladehinweis ein und ersetzen deren Zeichenflächen nicht.

Bei festem Ausschnitt und unveränderten Skalen bleiben Messkurven über reine
Uhr- und Phasenänderungen hinweg verwendbar. Die Zeigerbewegung aktualisiert
Cursor und Tooltip. Ein fortschreitender Gesamtverlauf benötigt eine neue
Zeittransformation und Zeichnung. Größenänderung, Zoom und Auswahl der
Messposition werden ebenfalls über den gemeinsamen Frameplaner verarbeitet.

Beim Trennen des Panels werden Ereignislistener, Beobachter und geplante
Framearbeit aufgeräumt. Die erneute Verbindung verwendet die vorhandene
Panelhülle und richtet die Zeicheninstanz für die aktuelle Auswahl ein.

## Reproduzierbare Prüfmethode

Die Prüfungen laufen ausschließlich auf Linux. Die Methodenprüfung verwendet
die Original-Panelmethoden mit synthetischen Daten und kontrollierten Canvasantworten:

```sh
node --test tests/panel_*.test.js
```

Für Browserprüfungen werden Node, Playwright als Entwicklungswerkzeug und eine
installierte Browserlaufzeit benötigt. Die folgenden Aufrufe erfolgen aus dem
Repositoryverzeichnis.

```sh
node tests/browser/live_history/accept.cjs
VARIANTS=fix node tests/browser/live_history/profile.cjs
VARIANTS=candidate node tests/browser/live_history/interaction.cjs
```

| Skript | Gegenstand und Ausgabe |
|---|---|
| `accept.cjs` | Prüft Datenübernahme, dauerhafte Knoten und Browserinteraktionen anhand fester Erwartungen. |
| `profile.cjs` | Erfasst Kaltstart, Aktualisierung, Geometriezugriffe und beobachtete Bildschirmframe-Abstände. |
| `interaction.cjs` | Erfasst Zeigerbewegung, Zoom und Verschieben während eingehender Nachträge; trennt die Zeit bis zum Rendercallback-Ende von dessen synchroner Rechenzeit. |

Die Fixture liefert synthetische Status- und Archivantworten an das
Originalpanel. Die Browserläufe verwenden dessen tatsächliche Zeichen- und
Eingabemethoden. Im WebKit-Interaktionsvergleich werden Zoomimpulse als
Browser-`WheelEvent` mit gesetztem Strg-Modifikator eingespeist. Die Ergebnisse
beschreiben die im Skript zugestellten Eingaben bis zur gemessenen
Browserverarbeitung. Eine Messung auf einem verwendeten Endgerät erfasst
zusätzlich dessen Eingabeweg und sichtbare Bildschirmausgabe.

`HISTORY_EVIDENCE` legt das Ausgabeverzeichnis von `accept.cjs` und
`profile.cjs` fest. `INTERACTION_EVIDENCE` benennt die Ergebnisdatei der
Interaktionsprobe. Standardbrowser ist WebKit; `ENGINE=chromium` und
`CHROMIUM_EXECUTABLE` wählen eine vorhandene Chromium-Laufzeit.

Die oben gesetzten Varianten untersuchen den aktuellen Arbeitsstand.
Vergleichsläufe können zusätzlich die in den Skripten angegebenen Gitstände
laden; dafür müssen diese Revisionen im lokalen Repository vorhanden sein.
`BASELINE_REVISION` bestimmt die Vergleichsrevision der Interaktionsprobe.
Messberichte halten die tatsächlich verwendeten Revisionen und
Umgebungsbedingungen fest.

[Prüfanleitung](abnahme.md)
