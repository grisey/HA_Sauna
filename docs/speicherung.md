# Archiv und Export

## Aufbewahrung

Sitzungen mit mindestens einem bestätigten Gang bleiben mit ihren Originaldaten
ohne Altersbegrenzung erhalten. Sitzungen ohne bestätigten Gang werden nach dem
Abschluss verworfen; dieselbe Bereinigung erfolgt beim Integrationsstart für
ältere Sitzungen ohne bestätigten Gang.

Das Archiv umfasst Messungen **15 Minuten vor und nach der Sitzung**.
Außerhalb dieser Fenster werden keine sitzungslosen Messreihen dauerhaft
aufbewahrt. Der Nachlauf kann eine bereits beendete Sitzung noch ergänzen.

Nach einem Neustart bleibt eine unterbrochene Sitzung historisch offen; ein
Endzeitpunkt wird nicht erfunden. Sie setzt den Ofenbetrieb nicht fort.

## Daten und Zeitangaben

Originalwerte behalten Messrolle, Quelle, Empfangszeit und verfügbare Genauigkeit.
Ein Gerätezeitstempel wird nur übernommen, wenn die Quelle ihn liefert.
Zeitstempel tragen Zeitzone und vorhandene Sekundenbruchteile; die Oberfläche
zeigt die lokale Browserzeit.

| Zeitfeld bei Erkennungen | Bedeutung |
| --- | --- |
| `effective_at` | Zugeordneter Ereignisbeginn, gegebenenfalls rückblickend. |
| `booking_at` | Zeitpunkt der Einordnung in den Betriebsablauf. |
| `detected_at` | Zeitpunkt, an dem die Erkennung das Ereignis feststellt. |

Nachträgliche Gangbestätigungen und Korrekturen ergänzen den Verlauf.
Frühere Zustände und die tatsächlichen Schalterrückmeldungen bleiben erhalten.
Jede Sitzungsrevision enthält den damaligen Konfigurationsstand. Ein späterer
Gerätewechsel schreibt die Herkunft alter Messungen nicht um.

Fehlende oder veraltete Messungen bleiben Datenlücken. Für die Zeichnung
verdichtete Kurven verändern die Originalwerte im Export nicht.

## Backup und Wiederherstellung

Je Sauna liegt eine SQLite-Datei unter
`<HA-Konfigurationsverzeichnis>/ha_sauna/<entry-id>.sqlite`.
Sie wird im Home-Assistant-Backup konsistent gesichert.

Nach einer Wiederherstellung steht der gespeicherte Verlauf bereit.
Der Saunabetrieb erfordert erneutes Einschalten.

## Löschen

Administratoren können einzelne abgeschlossene oder durch Neustart unterbrochene
Sitzungen sowie das gesamte Archiv löschen. Eine laufende Sitzung einschließlich
Wiederaufnahmepause sperrt beide Aktionen. Einstellungen und Gerätezuordnungen
bleiben erhalten.

## Export

Der ZIP-Download unter **Einstellungen** erfordert Administratorrechte.
Er enthält einen konsistenten Stand, auch während einer laufenden Aufzeichnung.

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | Schema, Instanz, Auflösung und Zeitformat |
| `sessions.jsonl` | Neuester Stand jeder Sitzung einschließlich ihrer Konfiguration |
| `records.jsonl` | Alle Datensätze und gespeicherten Revisionen in Aufzeichnungsreihenfolge |
| `measurements.csv` | Originalmessungen mit Datensatz-/Sitzungs-ID, Empfangs-/Messzeit, Messort, Messgröße, Quelle, Wert und Rohwert |

## API-Zugriff

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
