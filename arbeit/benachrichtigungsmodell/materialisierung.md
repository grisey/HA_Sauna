# Materialisierte Benachrichtigungsgrundformen

Die semantischen Vorgaben aus [grundformen.md](grundformen.md) liegen als zwei
isolierte Python-Module vor. Zwei getrennte Writer mit dem Modell
`gpt-6.1-sol` haben Empfängerformen beziehungsweise Austauschformen erstellt.
Ein dritter `gpt-6.1-sol` hat die Vorgaben und den resultierenden Code unabhängig
geprüft. Seine drei Erstbefunde wurden durch die jeweiligen Writer korrigiert;
die [Nachprüfung](peer-pruefung.md) bewertet den begrenzten Umfang positiv.

## Implementierte Verträge

| Modul | Verwendbare Grundform |
| --- | --- |
| `core/notification_recipients.py` | `ActorRef`, Entitäts-/Gerätereferenzen und kanonische Transportziele; getrennte Fähigkeitsbelege für Versand und Rückkanal; ausdrückliche Nichtauflösbarkeit; Zuordnungs- und Sitzungsmengen. |
| `core/notification_recipients.py` | `eligible_recipients` und `validate_assignment` verwenden dieselbe Eignungsregel für die Auswahl und die Prüfung vor Speicherung. Die Sitzungsakkumulation vereinigt ausdrücklich übergebene Zuordnungen anhand kanonischer Ziele. |
| `core/notification_exchange.py` | Nachrichten- und Rückmeldungshüllen; Zuordnung nach Instanz, optionaler Sitzung, Ziel, Korrelation und Nachrichten-ID; Erkennung bereits zugeordneter Rückmeldungs-IDs. |
| `core/notification_exchange.py` | Getrennte Formen für Transportübergabe, Endgerätebeobachtung und Antwortzuordnung; ein `Protocol` beschreibt die Grenze zu einem späteren Transportadapter. |

Die Modulpfade beziehen sich auf `custom_components/ha_sauna/`.
Pflichtidentitäten, zusammengesetzte Referenzen und Mengen werden an den
Bausteingrenzen validiert. Unbekannte Benutzer bleiben ausdrücklich unbekannt.
Nutzdaten der Nachrichten bleiben undurchsichtig; das Modell deutet sie nicht
als Saunaaktion. Die Wahl der als Eingriff geltenden Bedienungen, Zuordnung bei
unbekanntem Starter, Änderungen der Konfiguration und Persistenz bleiben bei
den ausdrücklich benannten offenen Entscheidungen.

## Abschluss der Materialisierung

Für diesen Schritt gelten folgende endliche Kriterien:

1. Die beiden Module stellen die vereinbarten Grundformen als aufrufbare
   Python-Verträge bereit, ohne Laufzeitaktivierung oder Produktinteraktionen.
2. Die unabhängige Peerprüfung hat alle erforderlichen Korrekturen geschlossen;
   ihr geprüfter Stand ist durch Dateihashes gebunden.
3. Der vorhandene Fachkernjob auf einem GitHub-gehosteten Linux-Runner nimmt
   beide neuen Testdateien auf und beendet die Prüfprozesse erfolgreich.
   Geprüfter Commit, ausgecheckter Commit beziehungsweise Tree und Ergebnis
   bleiben im konkreten CI-Nachweis getrennt.

Die Peerprüfung ist eine statische Quellprüfung. Die neuen Tests sind
synthetische Vertragsprüfungen; auch ein erfolgreicher CI-Lauf belegt keine
funktionierende Geräteauswahl in einer HA-Oberfläche und keinen tatsächlichen
Companion-Empfang. Die dazu nötigen Adapter und ihre Nachweise sind durch die
Begrenzung auf Grundformen weiterhin außerhalb dieser Materialisierung.

## Arbeitsregeln und Ablage

Die Testregel wurde auf ausdrücklichen Auftrag direkt auf `main` durch Commit
`7bb9e562caca96bfb9debf3d106511c29d03a6d7` geändert und in diesen Branch
übernommen: Tests ausschließlich in GitHub Actions auf GitHub-gehosteten
Runnern; kein SSH, SCP/SFTP oder Zugriff über Remote-Shells.

17 historische Hostangaben wurden aus 16 lokalen Projektdateien entfernt.
Die anschließende Suche im gesamten Projektverzeichnis einschließlich des
benachbarten Worktrees ergab keine verbliebenen Namensverweise außerhalb der
Git-Verwaltungsdaten. Die historischen Aussagen über frühere Ausführungen
wurden nicht in angebliche GitHub-Ausführungen umgeschrieben.

Die Unterlagen der ersten Vorbereitung beschreiben ihren ursprünglichen
Quellstand und bleiben als solcher erkennbar. Der vorliegende Bericht und die
Peerprüfung ergänzen sie um die Materialisierung. Protokolle einzelner
CI-Prozesse bleiben lokal unter `arbeit/benachrichtigungsmodell/`.
