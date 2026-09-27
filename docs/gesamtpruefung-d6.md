# Korrekturen zur Gesamtprüfung von d5b1403

Der unabhängige Bericht nennt 28 Fehlergruppen. Die früheren positiven
Teilbewertungen gelten nicht als Gesamtfreigabe. Die folgende Zuordnung
beschreibt die Korrekturen und ihre maßgeblichen Zustandsquellen. Sie ist
keine Behauptung einer erneut vollständigen Prüfung aller Repositoryfunktionen.
Testläufe ersetzen die fachliche Prüfung der Implementierung nicht.

## Aufteilung und Gegenprüfung

Drei Bearbeiter bearbeiten getrennte Schreibbereiche: Erkennung/Controller/
Runtime, Licht/Geräte und Panel. Der Hauptagent bearbeitet Konfiguration,
Archivschnittstelle und Backup und prüft die Zusammenführung. Änderungen
an einer gemeinsamen Datei werden vorab dem jeweiligen Eigentümer übergeben.
Die Codegegenprüfung betrachtet insbesondere die Schnittstellen zwischen
den Bereichen, die vorher zu wenig geprüft wurden.

## Zeit, Erkennung und Sitzung

| Befund | Korrektur am maßgeblichen Zustand | Zu erhaltende Gegenseite |
| --- | --- | --- |
| DET-01 | Türbestätigungszähler enden bei Wechsel/Ausfall der vollständigen Messpositionen und beim Ende ihrer Episode. | Eine verfügbare vollständige T/RH-Position genügt weiterhin. |
| DET-02 | Tatsächliche Heizintervalle und zeitlich gebuchte logische Freigabekanten werden je Detektorraster zusammengeführt. Verarbeitete Kanten werden bis auf den weiter gültigen Anker entfernt. | Spätes physisches AUS löscht keine frühere Evidenz; logisches AUS/EIN unterbricht den Nachweis auch bei durchgehend physischem EIN. Keine historischen Aktorbefehle. |
| LOGIC-02 | Gleichzeitige Detektorereignisse werden vor dem inklusiven Abschluss der Bestätigungsfrist verarbeitet. | Früher abgelaufene Fristen bleiben wirksam; ohne Aufguss endet ein vorläufiger Gang weiterhin. |
| LOGIC-03 | Das gewichtete Wärmeintegral wird einschließlich seines Divisors numerisch stabil berechnet. | Parameterwerte, fachliche Formel und Obergrenze ändern sich nicht. |
| STORAGE-01 | Die Heizentscheidung trägt ihre Sitzungsreferenz; Archiv und Geräteauftrag verwenden diese Herkunft. | Neue Sitzungen erhalten eigene Entscheidungen. Ein unveränderter Abschlussauftrag verliert beim folgenden sessionlosen Refresh seinen Ursprung nicht. |

## Licht und Geräteausgabe

| Befund | Korrektur am maßgeblichen Zustand | Zu erhaltende Gegenseite |
| --- | --- | --- |
| LOGIC-01 | AUTO und Ablauf einer manuellen Vorgabe geben die weiterhin gültige Phasenkurve frei. | Nachlauf-/Kühlfrist bleibt Eigentum des Controllers; der normale Lichtübergang außerhalb dieser Phasen bleibt erhalten. |
| LOGIC-04 | Wartende Kühlung wird aus dem aktuellen Kühlobjekt abgeleitet, nicht aus dem nicht mehr geschriebenen historischen Pausefeld. | Unknown/EIN verdient keine Kühlzeit; Wiederkehr von AUS setzt dieselbe Kühlung fort. |
| TRANSPORT-01 | Gehaltenes AUS wird nur bei bestätigtem AUS oder noch gültiger eigener Bestätigungserwartung dedupliziert. | Fremdes EIN und fehlende Bestätigung führen zur Wiederholung/Fehleranzeige; Loslassen startet den Nachlauf. |
| TRANSPORT-02 | Die Lichtechofrist beginnt am tatsächlichen Lichtsendepfad. | Ein vorausgehender Heizdienst verkürzt sie nicht; echte spätere Außenbedienung bleibt erkennbar. |
| TRANSPORT-03 | Die alte Leuchtenzuordnung verliert ihr Ausgaberecht vor finalem OFF und behält es während des Neuladens nicht. | Bei abgebrochener Übergabe und unveränderter alter Zuordnung wird das Recht wiederhergestellt. Die Frist wandert nicht zur neuen Leuchte. |
| TRANSPORT-04 | Heizintervalle und Energie wechseln an der Leistungs-TTL zur unabhängigen Rückmeldung beziehungsweise Relaisstellung oder Unknown. | Mechanischer Timer und Kühlzählung behalten ihre eigenen Relaisquellen. |
| INT-03 | Übermäßig große Helligkeits-Ganzzahlen werden vor Float-Endlichkeitsprüfung kontrolliert abgewiesen. | Berechtigungen, bisherige manuelle Vorgabe und Aktorausgabe bleiben bei ungültiger Eingabe unverändert. |

## Konfiguration und Speicherung

| Befund | Korrektur am maßgeblichen Zustand | Zu erhaltende Gegenseite |
| --- | --- | --- |
| STORAGE-02 | Eine Writerregistrierung besteht bis zum tatsächlichen Ende des Archivworkers. Neue Archivinitialisierung und Backupvorbereitung teilen eine Sperre; neue Archive warten während der Kopie. | Laufende Eingänge werden weiter gepuffert. Bereits begonnener Runtime-Close darf keine Abschlussrecords an der Pause vorbeischreiben. |
| INT-02 | Der vollständige Konfigurationskandidat einschließlich freier Innenstufen wird vor Live- oder Optionsänderung validiert. | Gültige neue Grenzen sind speicherbar; abgewiesene Kandidaten verändern keine Optionen und erzeugen keine falsche Erfolgszusage. |
| INT-04 | Ein Vollformular mit unverändertem Ziel erhält das Programm. Eine gezielte Sollwahl über den Teiländerungsweg bleibt explizit, auch bei gleichem Wert. | Freie Stufen, ausgewählte ID und laufender Verteilungsanker bleiben bei allgemeinem Speichern erhalten. |
| INT-05 | Der Archivcursor wird vor SQLite auf den positiven int64-Bereich geprüft. | Der bisherige negative Cursor wird weiterhin auf null begrenzt; Leseberechtigungen bleiben vorgeschaltet. |

## Oberfläche und Diagnose

| Befund | Korrektur am Verbraucher | Zu erhaltende Gegenseite |
| --- | --- | --- |
| FE-01 | Popup, Diagrammbedienung, ausgewählte Ereigniszeile und leere Karte verwenden Kontrastrollen ihrer tatsächlich gemalten Flächen. | Zentrale Palette und bestehende Diagrammfarben bleiben erhalten. |
| FE-02 | Der finale Übersichtscache enthält Messposition und Datenrevision. | Seitenweises Laden, Hauptkurve und Zoom behalten ihre Datenquellen. |
| FE-03 | Der Abschluss einer Temperaturgeste entfernt nur die eigene Interaktion. | Eine inzwischen begonnene zweite Geste bleibt ausführbar. |
| FE-04 | Eine bestätigte Katalogspeicherung beendet ihren Entwurf direkt. | Tatsächlich ungespeicherte Bearbeitungen werden weiterhin vor Poll-Antworten geschützt. |
| FE-05 | Die lokale Zeitzone gehört zur Cacheidentität der Zeitachse. | Archivzeitstempel bleiben unverändert. |
| FE-06 | Leer-/Instanzwechselpfade leeren auch Übersicht und Zeitbereich. | Keine Kurve einer vorherigen Sauna bleibt sichtbar. |
| FE-07 | Navigation verwendet einen zur Buttonrolle passenden aktuellen Seitenzustand; die Minimap erhält einen numerischen Sliderwert. | Tastatur- und Zeitausschnittbedienung bleiben erhalten. |
| FE-08 | Haltezähler werden als erfüllte Prüfpunkte bezeichnet. | Kein neuer Zeitzähler und keine Änderung der Erkennung. |
| FE-09 | Lichtantworten schließen nur ihren eigenen Entwurfsstand ab; Saunawechsel verwirft den lokalen Lichtentwurf. | Neue Eingaben während einer alten Anfrage bleiben erhalten. |
| FE-10 | Abschluss, Bereinigung und Fehlermeldung sind an Instanz und Generation gebunden. | Requests werden weiter an ihre ursprüngliche Instanz gesendet. |
| FE-11 | Fokussierte Eingaben behalten ihren Wert, übernehmen aber aktuelle Sperrattribute. | Der bestehende serverseitige Aktionsschutz bleibt erhalten. |
| FE-12 | Neue Detectionrecords tragen den exakten auslösenden Rasterzeitpunkt `trace_at`; Diagnosemarker verwenden diese Referenz. | Fachlicher Beginn und spätere Erkennungszeit bleiben getrennt. Fehlende Altmetadaten werden nicht durch erfundene Zeitzuordnung ersetzt. |

## Grenzen und Rückprüfung

Prüfungen laufen ausschließlich in Linux-CI, nicht lokal auf macOS. Gezielte
Regressionen verwenden die bestehenden Produktionspfade und Fixtures; sie
enthalten keine zweite Fachimplementierung. Die Ergebnisse sind im zugehörigen
Abschlussbericht mit Commit und CI-Lauf festzuhalten, einschließlich Fehlläufen.

Die obere Temperatur bleibt führend, die untere ist ihr Ersatz. Die bestätigte
Shelly-Relaisstellung genügt als vorgesehene Rückmeldung; zusätzliche Sensorik
ist keine Abnahmebedingung. Schutzprioritäten, Betriebsarten, Rechte und
Diagrammstandards sind weiterhin bindend. Kein Merge, Release, Deployment
oder Zugriff auf reale Aktoren gehört zu dieser Korrektur.
