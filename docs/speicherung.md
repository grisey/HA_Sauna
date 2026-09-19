# Archiv, Backup und Export

Das produktive Archiv liegt pro Instanz unter
`<HA-Konfigurationsverzeichnis>/ha_sauna/<entry-id>.sqlite`, außerhalb von `www`.
`archive.py` serialisiert alle Schreibvorgänge. Originalmessungen bleiben in
voller empfangener Auflösung erhalten, einschließlich Rohwert, Messrolle,
Quelle, Empfangszeit und einem nur tatsächlich bekannten Gerätezeitstempel.
Es gibt keine automatische Verdichtung oder altersabhängige Löschung.

Die append-only Tabelle `records` hält Messungen, Quellenzustände, Erkennungen,
Detektormerkmale, Betriebsphasen, Diagnosen, Gerätebefehle, Heizentscheidungen,
Benachrichtigungen und sämtliche Sessionrevisionen. `sessions` hält zusätzlich
den neuesten Stand pro Session, mit Konfiguration und Ereignisreferenzen.
Gangbestätigung ersetzt nicht die ursprünglich archivierte vorläufige Zuordnung.
Quellen-Schnappschüsse und Rasterwerte sind getrennt von empfangenen Originalen.

## HA-Backup

Die offiziellen HA-Pre-/Post-Backup-Hooks pausieren den Archivschreiber nach
Abarbeitung aller bisherigen Aufträge. Neue Eingänge werden weiter gepuffert.
HA sichert so eine abgeschlossene SQLite-Datei ohne offene Schreibtransaktion.
Nach dem Backup wird die Warteschlange fortgesetzt; Fehler werden sichtbar.

`tests/integration/test_archive_backup.py` erzeugt ein tatsächliches HA-Core-
Backup, ohne Recorderdaten einzuschließen. Die offizielle Restore-Routine liest
es in ein getrenntes Konfigurationsverzeichnis zurück. Eine neue HA-Instanz
prüft gespeicherte Optionen, Originaldaten und alle Sessionzuordnungen auf
Gleichheit. Das ist von einem bloßen SQLite-Backup getrennt nachgewiesen.
Nach Neustart werden Archive geladen, aber kein Betrieb automatisch fortgesetzt.

## Authentifizierter Export

Der Download in **Details → Einstellungen & Export** nutzt HAs kurzlebigen
signierten Abrufpfad. Direkter Abruf ohne HA-Anmeldung oder gültige Signatur
wird abgelehnt. Eine temporäre SQLite-Kopie stellt einen konsistenten Stand her,
während neue Eingänge weiter gespeichert werden. Die ZIP-Datei wird gestreamt
und anschließend gelöscht, auch bei abgebrochenem Abruf.

- `manifest.json`: Formatversion und Bedeutung der Zeit-/Auflösungsangaben.
- `sessions.jsonl`: neuester Stand jeder Session samt damaliger Konfiguration.
- `records.jsonl`: vollständige Aufzeichnung mit allen Zuordnungsrevisionen.
- `measurements.csv`: Originalmessungen mit Referenz auf ihren Archivdatensatz.

Das Archiv-API liefert Sessionlisten und Datensatzseiten. UI-Caches und
Darstellungsreduktion sind keine weitere Datenhaltung oder Regelungsquelle.
Tests prüfen Subsekundenauflösung, Referenzerhalt, Schreibpuffer beim Backup,
HTTP-Authentifizierung, Export während Erfassung und Browserdownload.
Tatsächliche Testabschlüsse: [Abnahme](abnahme.md).
