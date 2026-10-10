# Erstaufbau eines Sitzungsverlaufs

Die Prüfung läuft ausschließlich unter Linux. Die CI-Ausgabe enthält zwei
JSON-Zeilen mit getrennten Messbereichen:

- `ARCHIVE_VOLUME_BENCHMARK`: Der produktive Archivlesepfad lädt alle Seiten
  einer sechsstündigen Sitzung mit vier Messquellen und 46.804 Originalpunkten.
  Zusätzlich enthält die Datenbank 250.000 sitzungsfremde Messungen. Der eingefrorene
  vorherige SQL-Ausdruck und der aktuelle Ausdruck arbeiten auf derselben
  Datenbank. Sämtliche Antworten einschließlich Messwerten, Kontext, Cursor und
  Phasenprojektion müssen gleich sein. Erfasst werden SQLite-VM-Schritte,
  JSON-Decodierungen, Decodierzeit, Antwortbytes und Gesamtdauer. Die feste
  Regressionsgrenze verlangt mindestens den Faktor drei weniger VM-Schritte;
  Laufzeiten werden ohne maschinenabhängige Zeitgrenze ausgegeben.
- `HISTORY_VOLUME_BENCHMARK`: Der produktive Panel-Lader durchläuft zehn
  JSON-Seiten derselben synthetischen Mengengröße. Indexierung und
  Kurvenvorbereitung laufen nach jeder Seite. Jeder Originalpunkt darf genau
  einmal indexiert und einmal in die Darstellungsbins aufgenommen werden.
  Ein erneuter Aufruf verwendet dieselben vorbereiteten Serien ohne neue
  Anfrage oder erneute Indexierung. Netzübertragung, DOM und Browserzeichnung
  sind nicht Teil dieser Messung.

Die Szenarien sind vollständig synthetisch. Die Größenordnung des breiten
Datenbestands ist an einem lokalen Recorderexport orientiert; dieser ist kein
Sitzungsarchiv. Sitzungsdauer, Werte, Messabstände und Quellen stammen aus dem
synthetischen Szenario und den vorhandenen Testfixtures. Eine Aussage über die
Laufzeit eines konkreten privaten Archivs folgt daraus nicht.

Gezielter Aufruf im Repository auf Linux:

```sh
python -m unittest discover -s tests -p test_archive_read_performance.py -v
node --test tests/panel_history_loading.test.js
```

Für die zusätzliche Prüfung im echten Browser wird das Panel neu geladen und
unter „Verlauf“ erstmals eine abgeschlossene Sitzung ausgewählt. Ein vorheriger
Panelbesuch kann den Sitzungscache bereits gefüllt haben. Im Netzwerkprotokoll
beginnt ein kalter Ladevorgang mit
`/api/ha_sauna/<entry_id>/archive?session_id=<session_id>&projection=history&after=0`.
Die folgenden Seiten verwenden den jeweils gelieferten Cursor. Der letzte
Antwortwert `next_after: null` markiert das Ende des Seitenlaufs. Die
Berechtigungsrolle beeinflusst den Umfang der übertragenen Datensatzarten und
muss beim Vergleich gleich bleiben.

Die Browser-Aufzeichnung umfasst den Beginn dieser ersten Anfrage bis zur
vollständigen Kurvenzeichnung nach der letzten Seite. Antwortwartezeit,
Antwortbytes, JSON-Verarbeitung und lange Hauptthread-Aufgaben werden getrennt
betrachtet. Danach wird dieselbe Sitzung erneut ausgewählt; hierbei dürfen keine
Archivseiten erneut angefordert werden. Ein zweiter kalter Vergleich erfordert
einen erneuten Panelstart. Private Antworten, HAR-Dateien und Screenshots bleiben
in der lokalen Arbeitsablage; für CI werden ausschließlich die synthetischen
Szenarien verwendet.

`tests/browser/test_history_performance.py` ergänzt die Messung um Chromium
in der echten Home-Assistant-Oberfläche und den authentifizierten HTTP-Pfad.
Das Browserfixture verwendet denselben synthetischen Datenbankgenerator und
dieselbe eingefrorene SQL-Vergleichsabfrage. Vor jedem Lauf wird das Dokument
neu geladen, damit Sitzungsdaten und Kurvenpfade kalt sind. Die Ausgabe
`CHROMIUM_HISTORY_COLD_BENCHMARK` enthält die Zeit vom ersten Seitenrequest bis
zur vollständigen Canvaszeichnung sowie zur nächsten Zeichenmöglichkeit nach
zwei Animationsframes. Die zweite Zeit ist keine Messung des Bildschirmscans.

Zusätzlich erfasst der Browser die CPU-Zeit der Kurvenaktualisierung und des
gesamten Verlaufsrenders, lange Hauptthread-Aufgaben, Seitenzahl sowie
komprimierte und unkomprimierte Antwortbytes. Die Prüfung verlangt zehn Seiten,
46.804 indexierte Originaldatensätze, tatsächlich gezeichnete Canvaspixel und
denselben Datensatzhash vor und nach der Optimierung. Laufzeiten werden als
CI-Messwerte ausgegeben; ein physisches HA-Gerät und dessen Netzwerk sind nicht
Teil dieses Vergleichs. Das Fixture übernimmt nur Setup/Cleanup der vorhandenen
Browserprüfung und sammelt deren Tests nicht erneut.

Der zusätzliche Fall `ARCHIVE_LEGACY_VOLUME_BENCHMARK` entfernt die nativen
Phasenlisten des synthetischen Sitzungsmodells und ergänzt sechs Stunden
historische Belege: zwölf Gänge, Phasenwechsel, regelmäßige Heizschützmeldungen,
Sitzungsrevisionen und dichte Meldungen anderer tatsächlich archivierter
Quellenrollen. Die Meldeabstände sind ausdrücklich synthetische Lastannahmen,
keine gemessene private Archivdichte oder duplizierte Konfigurationsvorgaben.
Alle vorhandenen Messwertdatensätze bleiben unverändert.

Gemessen werden erste Seite, sämtliche Folgeseiten, vollständiger kalter und
warmer Abruf sowie Zeit und Aufrufzahl der Legacy-Projektion einschließlich
Belegabruf und Decodierung. Auf derselben Datenbank werden die eingefrorene
frühere Belegabfrage und die produktive gefilterte Abfrage jeweils kalt und warm
ausgeführt. Vor jedem kalten Lauf wird der Projektionscache geleert. Sämtliche
Antworten aller vier Läufe müssen einschließlich Originalmesswerten und
Phasenprojektion identisch sein.

Die produktive Abfrage behält alle `phase`- und `session`-Belege sowie
`source_state`-Belege mit Rolle `heater`. Andere Quellenrollen werden von der
Legacy-Projektion nicht ausgewertet und daher nicht mehr als Pythonobjekte
decodiert. Im synthetischen Szenario sinken die kalt decodierten Belege von
44.312 auf 1.108; warm erfolgt in beiden Varianten kein erneuter Belegabruf.
Der Test prüft diese Decodiermengen anhand der tatsächlich erzeugten Belegarten.
Der konservative Cache-Identitätscheck und das Datenbankschema bleiben erhalten.
