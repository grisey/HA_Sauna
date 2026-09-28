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
Heizentscheidungen tragen die Sitzung, in der sie entstanden sind. Daraus
gesendete Gerätebefehle übernehmen diese Zuordnung auch dann, wenn die Sitzung
inzwischen beendet ist oder bereits eine neue begonnen hat.
Auch die beim Entladen erzeugte letzte AUS-Entscheidung wird als eigener
Entscheidungsrecord über denselben Cursor wie im Normalbetrieb geschrieben.
Ein Archivfehler darf den physischen AUS-Versuch beim Entladen nicht verhindern.

Ein Lichtbefehl wird genau einmal beim Abschluss der tatsächlichen Dienstaufgabe
archiviert, auch nach Timeout oder Abbruch ihres wartenden Aufrufers. Seine
ursprüngliche Sitzung, Phase, Herleitung und Frist bleiben erhalten. `planned_at`
bezeichnet die Planung, `sent_at` den tatsächlichen Dienstbeginn und
`completed_at` den Abschluss. Der Record verwendet den Abschluss als
`received_at` und meldet einen tatsächlichen Dienstfehler in `service_error`.
Ein Warte-Timeout ist kein abgeschlossener Dienstfehler. Ein gesonderter
Archivfehler verändert das Ergebnis des Aktoraufrufs nicht.

## HA-Backup

Die offiziellen HA-Pre-/Post-Backup-Hooks pausieren den Archivschreiber nach
Abarbeitung aller bisherigen Aufträge. Neue Eingänge werden weiter gepuffert.
HA sichert so eine abgeschlossene SQLite-Datei ohne offene Schreibtransaktion.
Nach dem Backup wird die Warteschlange fortgesetzt; Fehler werden sichtbar.
Die Registrierung umfasst jeden Archivschreiber bis zum tatsächlichen Ende
seines Workers, auch während des Entladens einer Instanz. Ein bereits
schließender Schreiber wird vor der Kopie vollständig beendet. Das Öffnen
eines neuen Archivs einschließlich der Schemaanlage wartet während der
Sicherung; die Vorbereitung und die Archivinitialisierung sind gegenseitig
gesperrt. So kann keine neu hinzukommende Instanz an der Schreibpause
vorbeischreiben.

`tests/integration/test_archive_backup.py` erzeugt ein tatsächliches HA-Core-
Backup, ohne Recorderdaten einzuschließen. Die offizielle Restore-Routine liest
es in ein getrenntes Konfigurationsverzeichnis zurück. Eine neue HA-Instanz
prüft gespeicherte Optionen, Originaldaten und alle Sessionzuordnungen auf
Gleichheit. Das ist von einem bloßen SQLite-Backup getrennt nachgewiesen.
Nach Neustart werden Archive geladen, aber kein Betrieb automatisch fortgesetzt.

## Authentifizierter Export

Der Download unter **Einstellungen** nutzt HAs kurzlebigen
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
Archivcache und Seitenzeiger gehören auch zur Rechteprojektion. Beim Wechsel
zwischen öffentlicher und administrativer Ansicht beginnt das Panel bei null;
Antworten aus der alten Projektion werden verworfen. Bereits geladene
Diagnoseansichten werden beim Rechteentzug sofort geleert. Der Zeitbereich einer
weiter ausgewählten Sitzung bleibt erhalten.
Der Seitenzeiger `after` akzeptiert höchstens `2**63 - 1`, passend zur
SQLite-Datensatz-ID. Größere Werte werden mit HTTP 400 abgewiesen; negative
Werte werden weiterhin auf null begrenzt.
Tests prüfen Subsekundenauflösung, Referenzerhalt, Schreibpuffer beim Backup,
HTTP-Authentifizierung, Export während Erfassung und Browserdownload.
Tatsächliche Testabschlüsse: [Abnahme](abnahme.md).
