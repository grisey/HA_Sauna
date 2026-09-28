# Referenz-Replay „Tür–Gang“

Das eingefrorene Programm [candidate/replay.py](../candidate/replay.py) erhält
einen historischen Kalibrierstand der Erkennung als ausführbare Referenz. Es
wertet einen Recorderexport aus und schreibt ein JSON-Ergebnis mit den daraus
abgeleiteten Tür-, Lüftungs-, Personen- und Aufgusssignalen. Seine Eingaben und
Ausgaben sind lokale Dateien.

Die laufende Integration verwendet den
[produktiven Detector](../custom_components/ha_sauna/core/detector.py).
Dessen aktuelle Regeln beschreibt [Erkennung](erkennung.md); die Verarbeitung
seiner Signale steht im [Gangmodell](gangmodell.md). Der Referenzkandidat macht
den früheren Rechenweg mit seinem damaligen Parametersatz weiterhin
nachvollziehbar. Die Kanalbezeichnungen K3 und K6 gehören zu diesem festgehaltenen
Export. Die Integration ordnet ihre Quellen über die konfigurierten Rollen
oben und unten zu.

## Eingabeformat

Das Programm erwartet ein ZIP-Archiv mit `manifest.json` und `states.jsonl`.
Im Manifest geben `start_inclusive` und `end_exclusive` den Zeitraum als
Zeitstempel mit Zeitzone an. Daraus entsteht ein Ein-Sekunden-Raster, das den
Anfang einschließt und vor der Endzeit endet. Raster und Ereigniszeiten verwenden `Europe/Berlin`.

`states.jsonl` enthält je Zeile ein JSON-Objekt. Das Replay liest daraus
`entity_id`, `state`, `last_updated_ts` und `state_id`.
`last_updated_ts` ist die Unixzeit in Sekunden; `state_id` identifiziert den
Originaldatensatz. Temperaturwerte werden als Grad Celsius und Feuchtewerte
als relative Feuchte in Prozent ausgewertet.

Die Zuordnung in `load` verwendet das Namensmuster
`sensor.yellow_j11_sht35_pca_sht35_kanal_{c}_{s}` mit `c` gleich `3` oder `6`
und `s` gleich `temperatur` oder `luftfeuchte`. Hinzu kommen
`input_boolean.saunatur_offen` und `input_boolean.sauna_session_offen`.
Der Export enthält für jede dieser Quellen Einträge. Die Sitzungsquelle mit
Zustand `on` gibt die Personen- und Aufgussprüfung frei; der Türzustand dieser
Prüfung entsteht im Replay aus den Messungen.

`load` sortiert die Werte einer Quelle nach Zeit und State-ID. Bei gleicher
Zeit übernimmt es den letzten Datensatz dieser Sortierung. Jeder Rasterpunkt
verwendet den jüngsten bis dahin eingegangenen Wert. Der Abschnitt vor dem
ersten Wert bleibt als fehlend gekennzeichnet.

## Berechnung und Parameter

[candidate/parameter.json](../candidate/parameter.json) enthält den eingefrorenen
Parametersatz des Replays. `raster_s` legt das unterstützte Ein-Sekunden-Raster
fest, `median_s` das rückwärtsgerichtete Medianfenster. Die Bereiche `tuer`,
`lueftung`, `person` und `aufguss` enthalten die jeweiligen Fenster, Schwellen
und Bestätigungsdauern. Die Personentrendfenster sind durch
`person.pruefraster_s` teilbar.

`prepare` bildet geglättete Messreihen und robuste Trends als Median der
paarweisen Steigungen. `door` führt daraus eine abwechselnde Folge von
Öffnungs- und Schließsignalen; der Anfangszustand ist geschlossen.
`ventilation` bewertet jede Öffnung anhand des vorherigen Temperaturmaximums,
des Temperaturverlusts und der verstrichenen Zeit. Eine bestätigte Lüftung
liefert nach dem Schließen den Kontext für den schwachen Personenpfad.
`signals` prüft Personen anhand der Feuchte- und Temperaturtrends und Aufgüsse
anhand der Rohwertdifferenzen. Fenster und Bestätigungsdauer bestimmen jeweils,
welche Messstrecke ein Signal belegt.

Das Ergebnis enthält die Varianten `beide`, `nur_k3` und `nur_k6`.
`assess` ordnet die berechneten Signale den im Programm festgehaltenen
Bewertungsannotationen zu. `GANG_WINDOWS` und `REFERENCE_ANCHORS` gehören zu
dieser Auswertung der Kalibrieraufzeichnung. Die Erkennungsfunktionen erhalten
ihre Kriterien aus dem Parametersatz.

Die produktive Integration verwaltet ihre aktuellen Werte über
`core/parameters.py` und `Parameters.values`. Der Adapter `candidate_values`
in `core/detection_parameters.py` überträgt die entsprechenden Werte für den
Vergleich mit dem eingefrorenen JSON-Format.

## Reproduktion

Der Aufruf erfolgt unter Linux mit Python sowie den Bibliotheken `numpy` und
`pandas`. Vom Repositoryverzeichnis aus lautet er beispielsweise:

```sh
python3 candidate/replay.py /privater/pfad/sauna-recorder-2026-09-17.zip \
  --parameters candidate/parameter.json \
  --out /privater/arbeitsordner/ergebnis.json \
  --tests
```

Der Zielordner besteht bereits. `--parameters` wählt die Parameterdatei;
standardmäßig liest das Programm `parameter.json` neben dem Skript. `--out`
legt die JSON-Ausgabedatei fest; der Standardname ist `ergebnis.json` im
aktuellen Arbeitsverzeichnis. `--tests` ergänzt die Ausgabe um
Einzelfaktorvarianten und Verschiebungen des Personenprüfrasters. Der Aufruf
mit diesen Optionen stellt den in der Provenienz erfassten vollständigen
Rechenumfang her.

Das JSON enthält die Archivkennung mit SHA-256, den Zeitraum und den verwendeten
Parametersatz. Unter `modes` stehen Auswertung und Öffnungsepisoden je
Kanalvariante. `proof_open` verweist mit `state_id` und `states_jsonl_line` auf
die zugrunde liegenden Originalzeilen. Temperaturminima und deren Abstand zum
Schließsignal sind nachträgliche Kurvenmerkmale in der Ergebnisbeschreibung.
Das Prüfverfahren und die Voraussetzungen für optionale private Replays stehen
in [Prüfanleitung](abnahme.md).

## Provenienz und Datenhaltung

[candidate/provenienz.json](../candidate/provenienz.json) beschreibt die Herkunft
der Referenz. `source_archive` und `source_sha256` kennzeichnen den verwendeten
Recorderexport. `candidate_files_sha256` bindet die dort genannten Dateien
an ihre Prüfsummen.
Programm und Parametersatz bilden die eingefrorene Referenz. Die Erklärung auf
dieser Seite wird separat fortgeschrieben; ihre gebundene Prüfsumme bezeichnet
den jeweils zugehörigen Dokumentstand. `full_replay_result_sha256` bezeichnet
die vorhandene vollständige Referenzausgabe einschließlich der
Zusatzberechnungen. Quellenkennung, Ergebnisprüfsumme und gespeicherte
Referenzwerte behalten bei einer Überarbeitung der Erklärung ihren Bezug zur
ursprünglichen Berechnung.

Der im Ergebnis enthaltene Eingabedateiname gehört zum byteweisen Vergleich; die optionale
Reproduktionsprüfung gleicht ihn an `source_archive` an. Die gespeicherten
Bewertungen in `modes` beziehen sich auf dieselbe Kalibrieraufzeichnung.

Recorderexport und erzeugtes Ergebnis bleiben in einem privaten Arbeitsordner.
Das Ergebnis enthält Nutzungszeitpunkte und Verweise auf Originalmessungen.
Das Repository enthält das Referenzprogramm, seinen Parametersatz und die
Provenienz; `raw_archive_committed` hält den Ablagezustand der Rohdaten fest.
Für veröffentlichte Beispiele eignen sich synthetische oder ausdrücklich
freigegebene Daten.
