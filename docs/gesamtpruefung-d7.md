# Korrekturen zur Codeanalyse von 091a2a3

Die unabhängige Analyse vom 28.09.2026 nennt 16 neue beziehungsweise verbliebene
Fehlerursachen. Von den zuvor gemeldeten 28 Gruppen bestätigt sie 24 nur im
jeweils beschriebenen Umfang; vier sind teilweise offen. Dieser Nachtrag ersetzt
keine Gesamtfreigabe des Repositorys und nimmt diese Einschränkung ausdrücklich
auf. Grüne Tests belegen ihre konkreten Fälle, keine allgemeine Fehlerfreiheit.

## Verantwortungen und Erhaltung

Drei neue Sol-Bearbeiter übernehmen getrennt Erkennung/Controller/Runtime,
Geräte/Licht/Prognose und Panel. Der Hauptagent übernimmt Konfigurationsfluss und
Reload sowie die Zusammenführung. Die Gegenprüfung verfolgt Schreiber,
Zustandsübergang und tatsächlichen Verbraucher auch über diese Grenzen hinweg.
Gemeinsame Dateien haben jeweils nur einen Bearbeiter.

Unverändert bindend: oben führt die Temperaturregelung, unten ist Ersatz; eine
vollständige Temperatur-/Feuchteposition ermöglicht den gesamten Betrieb. Der
Ausfall einer zweiten konfigurierten Position bleibt sichtbar. Die bestätigte
Relaisstellung genügt. Ofenkühlung und Schutz behalten ihren Vorrang; die
manuelle Betriebsart wird nicht in eine zusätzliche Automatik umgewandelt.
Historische Darstellung erzeugt keine historischen Aktorbefehle.

## Ursachen, Korrekturen und Gegenfälle

| Befund | Maßgeblicher Korrekturweg | Zu erhaltender Gegenfall |
| --- | --- | --- |
| F01: Nachgeholte gesperrte Messungen eröffnen einen Gang | Der Controller liefert seine gebuchten zeitlichen Betriebs-/Kühlfreigaben für jedes Detektorraster. Heutige Freigabe ersetzt nicht die damalige; auch ein damals erlaubter, inzwischen durch AUS/Kühlung abgeschlossener Abschnitt darf keinen heutigen Gang eröffnen. | Türbeobachtung bleibt möglich; tatsächliche Heizintervalle bleiben die Grundlage des thermischen Nachweises. Aktorausgaben erfolgen nur zur aktuellen Zeit. |
| F02: Messungen nach Sitzungsstart gehen am Detektor vorbei | Die Runtime synchronisiert die Detektorzugehörigkeit beim Sitzungswechsel vor den folgenden Eingängen. | Originalmessungen bleiben vollständig und unverändert im Archiv. |
| F03: Fristgleicher Aufguss erhält je Callbackreihenfolge eine andere Gang-ID | Ein Eingangszyklus hält den inklusiven Bestätigungsabschluss auch durch indirekte Refresh-/Feedbackaufrufe offen und schließt ihn nach der Erkennung. | Tatsächlich früher abgelaufene Fristen bleiben abgelaufen; ohne Aufguss wird der vorläufige Gang zurückgenommen. |
| F04: Lange zulässige Türbestätigung wird vorzeitig verworfen | Ein weiterhin vollständig belegter Öffnungsweg darf seine konfigurierte Haltezeit erreichen. | Fehlende, veraltete oder gewechselte Quellen setzen den Nachweis weiterhin zurück. |
| F05: Alter Licht-EIN-Aufruf überholt Übergabe-AUS | Lichtdienstaufrufe und Übergabe teilen eine geordnete Transportzuständigkeit; finales bestätigtes AUS folgt dem tatsächlichen Ende vorheriger Aufrufe. | Wartefristen bleiben begrenzt; ein Timeout oder abgebrochener Aufrufer darf keinen noch laufenden Dienst unsichtbar machen. |
| F06: Reloadfehler sperrt fortbestehende Runtime | False und Ausnahme stellen nur die noch aktuelle, offene alte Runtime samt gespeicherter Konfiguration und Lichtzuständigkeit wieder her. | Neuere Optionen und neue/geschlossene Runtimes werden nicht überschrieben; Timer werden ausschließlich an eine tatsächlich neue passende Runtime übergeben. |
| F07: Tasterunterbrechung wird als Langdruck ausgelegt | Nichtverfügbarkeit erreicht den Gestenautomaten, ungültiger unbestätigter Druck endet; bestätigtes Loslassen bleibt verarbeitbar. | Ein bereits ausgeführter Langdruck bleibt während HOLD aus und beginnt erst beim bestätigten Loslassen den Nachlauf. |
| F08: Manuelle Lichtwahl während HOLD ersetzt Nachlaufhelligkeit | Der Lichtplaner bindet die manuelle Wahl an die eingehende Controllerphase. | Die aktuelle Controllerfrist und konfigurierte Nachlaufhelligkeit bleiben maßgeblich, unabhängig vom zuletzt ausgegebenen Planerstand. |
| F09: Alte Saunawerte bleiben nach Instanzwechsel bedienbar | Der Wechsel entzieht den alten Status und dessen Bedienelemente sofort; passende Antwortgeneration gibt die neue Ansicht frei. | Fehler/alte Antworten geben keine alte Bedienung frei; Navigation und serverseitige Rechte bleiben erhalten. |
| F10: Älterer Sollwertfehler verhindert gültigen Nachfolger | Die Warteschlange wartet das Ende des Vorgängers ab, übernimmt aber nicht dessen Ablehnung als eigenen Fehler. | Eigene Fehler bleiben sichtbar; Reihenfolge, Instanzbindung und bestätigte Ausgangswerte bleiben erhalten. |
| F11: Offlineformular erhält unpassende freie Stufen | Geladener Writer und Optionsformular verwenden denselben vollständigen validierten Parameterkandidaten. | Unverändertes Vollformular erhält Programm und Verteilungsanker; ausdrückliche Sollwahl bleibt eine wirksame Auswahl. |
| F12: Schlussentscheidung fehlt als eigener Archivrecord | Normaler Zyklus und Close verwenden denselben Entscheidungscursor. | Archivfehler verhindern nicht den physischen AUS-Versuch; Herkunft bleibt die beendete Sitzung. |
| F13: Verteilungshilfe zeigt alte statt sichtbarer Werte | Eingabefelder und Hilfe lesen denselben validierten Programmentwurf. | Ungültige Rohwerte bleiben bearbeitbar; keine zweite Verteilungsformel. |
| F14: Fokuskontrast des ausgewählten Ereignisbuttons zu gering | Der innere Button verwendet die bestehende Fokusrolle seiner tatsächlichen Warnfläche. | Zentrale Palette und gewählte Diagrammfarben bleiben erhalten. |
| F15: Alte Verlangsamungsfrist lässt neue kurze ETA-Sprünge zu | Akzeptierte langsamere Rate wird neue Referenz; eine weitere Verlangsamung muss sich erneut bestätigen. | Glatte Anpassung und erkannter Türverlust bleiben möglich; die Prognose beeinflusst keine Heizentscheidung. |
| F16: Zeittexte behalten alte Zone | Zone gehört auch zu Tabellen-, Annotations- und Diagnosecacheidentitäten; Sitzungsauswahl aktualisiert gehaltene Metadaten. | Archivzeitstempel, Datenrevisionen und Steuerungszeiten bleiben unverändert. |

## Bereinigung und Dokumentation

Entfernt werden ausschließlich der ungenutzte Runtime-Fristwrapper mit seinem
isolierten Test, der vom Backend nicht mehr belieferte Startfenster-Ansichtsast
und verwaiste CSSregeln ohne Markuperzeuger. Der heutige Aufheizhinweis bleibt.
Ungeklärte Cachekompatibilität, historische Archivfelder, Migration und der
eingefrorene Kandidat werden erhalten.

Formulartexte beschreiben nun Verteilung statt fester Erhöhung je Gang und die
Ersatzfunktion des unteren Sensors. Betriebsdokumente grenzen den automatischen
Gang-Heizbedarf von Manuell ab und erklären vorläufige Bestätigung gegenüber
einem späteren eigenständigen Aufgussgang.

## Nachweisgrenzen

Ausführung ausschließlich in Linux-CI: Kern, JavaScript, HA-Adapter,
HA-Integration und Chromium. Lokale Mac-Tests, auch Einzelproben, sind untersagt.
Konkrete Läufe, Exitcodes, Fehlläufe und der tatsächlich geprüfte Commit gehören
in den Abschlussbericht. Die hier beschriebenen Änderungen sind keine Aussage,
dass jede mögliche Eingangsfolge praktisch ausgeführt wurde.

Keine reale Sauna wurde bedient. Reale Hardware, Safari/assistive Technologien,
privater Recorderexport und die drei im Analysebericht ausdrücklich unbestätigten
Prüfannahmen werden nicht durch diese Korrekturen pauschal abgenommen. Kein Merge,
Release oder Deployment gehört zu diesem Auftrag.
