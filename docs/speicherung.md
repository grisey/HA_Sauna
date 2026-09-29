# Archiv, Backup und Export

Jede Instanz besitzt ein SQLite-Archiv unter
`<HA-Konfigurationsverzeichnis>/ha_sauna/<entry-id>.sqlite`. Das Verzeichnis liegt
im privaten Home-Assistant-Konfigurationsbereich. Zugriff und Download erfolgen
über die berechtigten Schnittstellen der Integration.

Originalmessungen bleiben altersunabhängig in voller empfangener Auflösung
erhalten. Ein Messdatensatz enthält Rohwert, Messrolle, Quelle und Empfangszeit.
Ein Messzeitstempel wird übernommen, wenn die Quelle ihn tatsächlich liefert.
ISO-8601-Zeitangaben erhalten Zeitzone und vorhandene Sekundenbruchteile.
Quellen-Schnappschüsse und abgeleitete Rasterwerte besitzen eigene Datensatzarten.

## Schema und Schreibweg

`archive.py` verwendet Schema `1`, das in `metadata` gespeichert ist. Das Öffnen
prüft diese Versionskennung; eine andere Kennung führt zu einem Versionsfehler.

| Tabelle | Aufgabe |
| --- | --- |
| `metadata` | Schlüssel und Werte für die Archivversion. |
| `records` | Fortlaufende Datensätze mit `id`, `entry_id`, optionaler `session_id`, `kind`, `received_at` und JSON-`payload`. Neue Revisionen werden angehängt. |
| `sessions` | Neuester Stand jeder Sitzung mit Instanz, Beginn, Aktualisierungszeit, optionalem Ende und vollständigem JSON-Snapshot. |

Die Aufzeichnung verbindet Mess- und Quellenmeldungen mit Erkennungen,
Entscheidungen und Geräteaufträgen. Jede gespeicherte Sitzungsrevision enthält
ihren damaligen Konfigurationsstand und ihre Ereignisreferenzen. Eine spätere
Gangbestätigung ergänzt dadurch die Historie; die zuvor aufgezeichnete
vorläufige Zuordnung bleibt erhalten.

Ein Append friert seine Daten beim Einreihen als JSON ein. Ein einzelner
Archivschreiber arbeitet die Aufträge in Reihenfolge ab. Die Sitzungsrevision
in `records` und ihr neuester Stand in `sessions` werden gemeinsam in einer
SQLite-Transaktion geschrieben. Ein wartender Abschlussaufruf `flush()` erfasst
alle vor ihm eingereihten Aufträge. Bei einem Schreibfehler behält der Schreiber
die ausstehenden Datensätze zur erneuten Verarbeitung in Reihenfolge; ein
anhaltender Fehler wird an den wartenden Aufrufer weitergegeben.

Heizentscheidungen tragen die Sitzung, in der sie entstanden sind. Zugehörige
Geräteaufträge übernehmen diese Herkunft auch nach deren Ende oder dem Beginn
einer neuen Sitzung. Der normale Zyklus und das Entladen speichern Entscheidungen
über denselben Fortschrittszeiger. Zum Entladen gehört eine eigene abschließende
AUS-Entscheidung. Ein eigenständiger Ofen-AUS-Versuch erfolgt auch dann, wenn die
Archivierung beim Abschluss fehlschlägt.

Ein Lichtbefehl wird genau einmal beim Abschluss seiner tatsächlichen
Dienstaufgabe archiviert. Der Abschluss gehört weiterhin zur ursprünglichen
Sitzung, Phase, Herleitung und Frist, auch wenn das Warten zuvor abgebrochen
wurde oder seine Zeitgrenze erreicht hatte. `planned_at` bezeichnet die Planung,
`sent_at` den tatsächlichen Dienstbeginn und `completed_at` den Abschluss.
Der Archivrecord verwendet den Abschluss als `received_at`. `service_error`
beschreibt den tatsächlichen Dienstausgang.

Die bei der Planung bekannte Phasenfrist wird als `ends_at` an den
Dienstauftrag übergeben. Sein Abschlussrecord erhält diese ursprüngliche Frist,
auch nach einer späteren Zustandsänderung oder einem Runtimeabschluss.
SQLite-Leser und ZIP-Export übernehmen denselben gespeicherten Wert. Phasen
ohne Frist führen `ends_at` mit dem Wert `null`.

## Home-Assistant-Backup und Wiederherstellung

Die offiziellen Pre-/Post-Backup-Hooks koordinieren die Schreibpause. Vor der
Kopie arbeitet jeder erfasste Archivschreiber seine bisherigen Aufträge ab.
Weitere Eingänge bleiben während der Pause in der Warteschlange. Die
Home-Assistant-Sicherung übernimmt so einen abgeschlossenen SQLite-Stand;
anschließend werden die wartenden Aufträge weiterverarbeitet.

Die Registrierung hält jeden Schreiber bis zum Ende seines Workers erreichbar,
auch während die zugehörige Runtime entladen wird. Hat das Archiv selbst seinen
Abschluss bereits begonnen, wartet die Backupvorbereitung dessen Ende ab.
Ein neu beginnendes Archiv wartet mit dem Öffnen und der Schemaanlage bis zur
Freigabe der Sicherung. Nach der Kopie werden alle Schreibpausen freigegeben,
bevor auf die weiteren Schreibabschlüsse gewartet wird. Beobachtete Fehler werden
an Home Assistant zurückgegeben.

Das Archiv gehört zur Sicherung des Home-Assistant-Konfigurationsverzeichnisses.
Der Integrationstest `tests/integration/test_archive_backup.py` verwendet dazu
die offizielle HA-Core-Backup- und Restore-Routine und startet eine getrennte
Instanz mit den wiederhergestellten Optionen und Originaldaten. Der Vergleich
umfasst die Sitzungszuordnungen.

[Prüfanleitung](abnahme.md)

Nach einem Neustart steht die archivierte Historie zur Verfügung. Der
Saunabetrieb beginnt mit einem erneuten Einschaltauftrag.

## Archivzugriff

`GET /api/ha_sauna/{entry_id}/archive` setzt eine HA-Anmeldung und die
Leseberechtigung für die Betriebsentität dieser Instanz voraus. Die Grundabfrage
liefert eine Sitzungsliste. Mit `session_id` enthält
sie den Sitzungssnapshot, die Phasenprojektion und eine Seite von Archivrecords.
Diese Teile stammen aus derselben SQLite-Lesetransaktion. Für ältere Snapshots
wird die Projektion aus den vollständigen zugehörigen Belegen berechnet,
unabhängig von der aktuellen Datensatzseite.

Administratoren erhalten die vollständigen Datensätze. Für andere
leseberechtigte Benutzer stellt die API Messungen, Quellen-Schnappschüsse und
Phasen mit Werten und Zeiten bereit. Ihre Sitzungsantwort beschreibt den
Verlauf. Konkrete Quellen-IDs und der Konfigurationsblock sind ausschließlich
für Administratoren freigegeben.

`after` bezeichnet die zuletzt gelesene Datensatz-ID. Gültige positive Werte
reichen bis `2**63 - 1`; negative Werte werden auf null begrenzt. Eine ungültige
Ganzzahl oder ein größerer Wert erhält HTTP 400. `next_after` ist der vom Server
ermittelte Fortsetzungszeiger. Die Rechtefilterung kann eine sichtbare Seite
leeren, obwohl dieser Zeiger eine weitere Seite bezeichnet. Der Verbraucher
übernimmt deshalb den Serverzeiger.

Archivcache, Seitenzeiger und abgeleitete Ansichten gehören zur jeweiligen
Rechteprojektion. Jede Antwort bleibt an die Projektion und Abrufgeneration
ihres Auftrags gebunden. Veraltete Antworten werden verworfen; bei fehlenden
Administratorrechten werden bereits geladene Diagnosedaten und ihre abgeleiteten
Ansichten entfernt.

## Export herunterladen

`GET /api/ha_sauna/{entry_id}/export` erfordert einen HA-Administrator. Der
Download unter **Einstellungen** verwendet einen von Home Assistant signierten,
kurzlebigen Abrufpfad. Auch für diesen Abruf gelten die Rechte des zugehörigen
Benutzers. Ein authentifizierter direkter API-Abruf verwendet dieselbe
Administratorprüfung.

Vor dem Export wird eine laufende Sitzung als aktuelle Revision eingereiht.
Eine temporäre SQLite-Kopie liefert den konsistenten Exportstand, während die
weitere Erfassung fortläuft. Die ZIP-Datei enthält:

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | Schema, Instanz-ID sowie Beschreibung von Auflösung und Zeitformat. |
| `sessions.jsonl` | Neuester Stand jeder Sitzung samt damaliger Konfiguration. |
| `records.jsonl` | Vollständige Aufzeichnung in Datensatzreihenfolge einschließlich aller gespeicherten Revisionen. |
| `measurements.csv` | Originalmessungen mit `record_id`, `session_id`, Empfangs- und Messzeit, Position, Messgröße, Quelle, Wert und Rohwert. |

Die Exporterzeugung besitzt ihre temporäre Datei bis zum tatsächlichen Ende des
Schreibvorgangs. Ein abgebrochener Auftrag gibt diese Datei zur Bereinigung frei.
Der HTTP-Download streamt die fertige ZIP-Datei und entfernt sie beim Abschluss
oder Abbruch. Der Server kennzeichnet die Antwort mit `Cache-Control: no-store`.

[Verlaufsvertrag](livekurve.md)
