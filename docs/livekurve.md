# Daten- und Zeichenvertrag des Sitzungsverlaufs

Die Darstellung bleibt Bestandteil der bestehenden `panel.js`. Home Assistant
liefert damit weiterhin eine einzelne, durch ihren Inhalt fingerprintete Datei
aus. Archiv, Export, Erkennung und Steuerung verwenden unveränderte Verträge.

## Archiv und Abschluss

Der Anzeigecache besitzt zwei getrennte Zustände: `pageRunLoaded` kennzeichnet das
Ende eines Seitenlaufs; `finalSynced` setzt zusätzlich einen endgültigen
Sitzungssnapshot mit `ended_at` voraus. Ein vollständig gelesener offener Cache
wird weiter nachgeladen. Das gilt nach Wegfall der Livesitzung und beim späteren
Auswählen einer alten Sitzung, auch wenn inzwischen eine neue läuft.

Jede vollständig empfangene Seite übernimmt Snapshot, Phasenprojektion und
Cursor. Eine leere Schlussseite kann deshalb Endzeit und Projektion ändern.
Messdatenrevisionen entstehen ausschließlich durch Messnachträge. Ein Fehler
behält den zuletzt übernommenen Cursor; Instanz, Auswahlgeneration und
Sitzungsidentität verhindern die Übernahme ersetzter Antworten.

Alle Archivrecords bleiben erhalten. Deduplizierung erfolgt anhand der
Archivrecord-ID. Gleiche Quellzeitpunkte verschiedener Records bleiben getrennt.
Der chronologische Index enthält vier unabhängige Sensorreihen sowie die
Ereignisgruppen. Ein geordneter Nachtrag erweitert den Index; eine verspätete
Einfügung invalidiert die Aggregation ihrer Reihe.

## Dauerhafte Zeichenflächen

Ein `HistoryChart` gehört zu einer Kombination aus Integrationsinstanz und
Sitzungs-ID. Canvas, SVG-Interaktionsfläche, Tooltip und Minimap-Griffe bleiben bei
Status-, Titel-, Phasen-, Abschluss- und Höhenänderungen erhalten. Entfernen der
Ansicht/Integration oder Wechsel der Sitzung beendet diese Lebensdauer.

Die Messkurven und die Minimap verwenden Canvas2D und numerisch aufgebaute
`Path2D`-Objekte. Die vorhandene Auswahl erster/kleinster/größter/letzter Punkte,
Randnachbarn und monotone kubische Kontrollpunkte bleibt maßgeblich. Explizite
Fehlwerte, TTL-Unterbrechungen und `displayGap` trennen die Segmente. Die
Bildschirmaggregation ersetzt keine Archivdaten und dient niemals der
Tooltip-Suche.

Der sichtbare Datenbereich einschließlich seiner Randnachbarn besitzt einen
Schlüssel aus Zeitfenster, TTL, Aggregationsbreite, Punktanzahl und Identitäten
der Randpunkte. Dadurch bleiben Pfade bei Nachträgen außerhalb dieses Bereichs
gültig. Neue Punkte innerhalb des Bereichs verändern seine Anzahl; neue
Randnachbarn verändern ihre Identität. Ein neues Extremum aktualisiert die
zugehörige Temperatur- oder Feuchteskala und alle davon abhängigen Pfade.

Annotationen und Raster liegen unter den Kurven; Achsen und Cursor liegen
darüber. Tür, Lüftung, Gangbestätigung, Aufguss und tatsächliche Heizzeit behalten
ihre bisherige Bedeutung und Reihenfolge. Autoritative Phasenprojektionen
ersetzen vorläufige Phasen einschließlich ihrer Rücknahmen. Tabellen und
Beschriftungen bleiben als zugängliches DOM bestehen.

## Änderungen und Eingabe

Es gibt eine geplante Historienarbeit pro Bildschirmframe und keine laufende
Animationsschleife. Statusantworten aktualisieren sofort die Bedienanzeige und
Annotationen. Der Archivnachtrag übernimmt gemeinsam Messdaten und den eventuell
nachzuführenden Zeitausschnitt. Dadurch wird derselbe Nachtrag nicht zunächst mit
altem und anschließend mit neuem Messcache vollständig gezeichnet.

Bei festem Ausschnitt und unveränderten Skalen erzeugen Uhr-/Phasenänderungen
keine Messkurven. Hover aktualisiert nur Cursor und Tooltip. Eine tatsächlich
fortschreitende Zeitabbildung im Gesamtverlauf benötigt weiterhin eine neue
Transformation/Zeichnung. Zoom, Pan, Größenänderung und Höhenwahl werden durch
denselben Scheduler verarbeitet. Verborgene Ansichten zeichnen nicht.

Clientrechtecke werden in einer Lesephase gecacht. Scrollende Vorfahren über
Shadow-Roots und Slots, Fenster, VisualViewport, Größenänderungen, Gestenbeginn
und Layoutverschiebungen invalidieren sie. Eine neu gesetzte Resolution-Abfrage
erkennt Pixelratioänderungen. Bitmapgröße und CSS-Größe bleiben getrennt;
Löschen der Pixel setzt nicht jedes Mal `canvas.width` zurück.

Tooltip-Inhalte verwenden dauerhafte Textknoten und eine Position per Transform.
Die binäre Suche läuft auf den Originalreihen. `raw_value`, einschließlich `0`,
bleibt unverändert; ohne Originalstring lautet die Kennzeichnung
„Wert … · kein Originalwert gespeichert“. Empfangs- und Messzeit bewahren
Sekundenbruchteile und lokale Zeitzonenangabe. Nach DOM-Schreibzugriffen liest der
Eingabepfad keine neue Layoutgeometrie.

Disconnect entfernt Listener, ResizeObserver, Resolution-Abfrage und geplante
Framearbeit. Reconnect verwendet die vorhandene Panel-Hülle ohne doppelte
Ereignisbehandlung und erstellt die Zeicheninstanz neu.

## Reproduzierbare Prüfung

`node --test tests/panel_*.test.js` prüft Daten-/Kurven-/Tooltip-Verträge mit
synthetischen Daten. Die Canvas-Doubles dieser Tests belegen Funktionsverhalten;
sie dienen keiner Leistungsbehauptung.

Mit Node und einer installierten Playwright-WebKit-Laufzeit können die echten
Browserpfade ausgeführt werden:

```sh
node tests/browser/live_history/accept.cjs
node tests/browser/live_history/profile.cjs
```

`HISTORY_EVIDENCE` bestimmt das private Ausgabeverzeichnis. `ENGINE=chromium` und
`CHROMIUM_EXECUTABLE` wählen alternativ eine vorhandene Chromium-Laufzeit.
Playwright muss als Entwicklungswerkzeug über die normale Modulauflösung
verfügbar sein. Es ist keine Laufzeitabhängigkeit der Integration.

Die Browserfixture ersetzt ausschließlich den Datentransport durch synthetische
Antworten. Sie verwendet echte Status-, Cache-, Zeichen- und Eingabemethoden,
57.600 Anfangsrecords und getrennte Quellzeitpunkte. Die Vergleichsmessung lädt
die gebundenen Git-Referenzen und den lokalen Produktcode. Kaltstart,
Transport-/Framewartezeit, synchrone Methodenarbeit und beobachtete
Bildschirmframe-Abstände werden getrennt ausgewiesen. Eine solche Probe ersetzt
keine Messung in einer produktiven Companion-App-Sitzung.
