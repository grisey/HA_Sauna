# Arbeitsregeln

## Arbeitsgrundlage

Vor Änderungen den aktuellen Repository- und Dateistand lesen. Die jüngste
Nutzerfestlegung ist maßgeblich. Die geltenden Fachbeschreibungen stehen unter
[Betrieb](docs/betrieb.md), [Gangmodell](docs/gangmodell.md),
[Zeitmodell](docs/zeitmodell.md) und [Ofenkühlung](docs/ofenkuehlung.md).
[Architektur](docs/architektur.md) erklärt die Zuständigkeiten der Software.

Besprechungen behandeln jeweils einen zusammenhängenden Punkt. Vereinbarte
Regeln werden übernommen; offene Entscheidungen werden mit ihrer konkreten
Auswirkung erklärt. Berichte benennen den Sachverhalt in verständlichen Worten
und verbinden ihn bei Bedarf mit seinem Befundkürzel. Anforderungen,
vorhandene Implementierung und tatsächlich erhobene Nachweise werden jeweils
als solche beschrieben.

## Projektbestand und Arbeitsunterlagen

Der versionierte Projektbestand umfasst Programmcode, Tests und ihre benötigten
Referenzdaten, die dauerhafte Dokumentation sowie die aktuellen Arbeitsvorgaben.
Eine Fachregel hat eine führende Beschreibung; weitere Seiten verweisen darauf.

Befundberichte, Umsetzungspläne und Protokolle einzelner Prüfrunden werden lokal
unter `arbeit/` aufbewahrt. Diese Ablage ist in `.gitignore` eingetragen.
Gemischte Dateien werden nach ihrem Inhalt aufgeteilt: Die Programmerklärung
gehört zur Dokumentation, der konkrete Arbeitsnachweis zur Arbeitsablage.
Bei einer solchen Ausgliederung werden Ablage und Git-Verfolgung gemeinsam
angepasst. Historische Nachweise behalten ihren ursprünglichen Stand.

## Implementierung

Jede Zustandsregel hat eine maßgebliche Implementierung. Fachliche Korrekturen
setzen dort an. Gemeinsam verwendete Bedienelemente und Zustände werden an
einer Stelle geführt. Ein zusätzlicher Ablauf braucht einen konkreten
fachlichen Anwendungsfall und eine eindeutige Zuständigkeit.

Erkennung, Gangzuordnung und Geräteausgabe haben getrennte Verantwortlichkeiten.
Der Controller führt den aktuellen Betrieb; die Geräteanbindung setzt seine
Ausgaben um. Rückwirkende Zuordnungen ergänzen den historischen Verlauf.
Die [Zeitbezüge](docs/zeitmodell.md) bestimmen, wie fachlicher Zeitpunkt,
Buchungszeit und tatsächliche Verarbeitung zusammenhängen.

Produktive Erkennung verwendet allgemeine Messregeln. Referenzaufzeichnungen
dienen der Prüfung dieser Regeln. Die Bezeichnungen der Sensorrollen sind
fachlich fest; ihre konkreten Home-Assistant-Entitäten werden konfiguriert.
Parameter besitzen eine gemeinsame Definition mit Standardwert, Einheit und
zulässigem Bereich. Gespeicherte gültige Werte bleiben bei einer Aktualisierung
erhalten.

Python folgt Ruff gemäß `pyproject.toml`. Die Oberfläche folgt der in
`.prettierrc.json` festgelegten Formatierung mit Prettier 3.6.2. Exporte und
reproduzierbare Prüfartefakte verwenden eine festgelegte Reihenfolge und
deterministische Serialisierung.

## Fachliche Grundlagen

Eine Messposition umfasst Temperatur und Luftfeuchte und ermöglicht den
vollständigen Betrieb einschließlich thermischer Türerkennung. Sind beide
Positionen eingerichtet, führt der gültige obere Temperaturwert. Bei dessen
Ausfall übernimmt der gültige untere Wert; bei Rückkehr führt wieder oben.
Der Ausfall einer eingerichteten Position wird sichtbar angezeigt.

Die bestätigte Stellung des zugeordneten Ofenschalters ist eine ausreichende
Schaltrückmeldung. Dies gilt insbesondere für das Shelly-Relais. Eine zusätzliche
Leistungsmessung oder unabhängige Heizrückmeldung kann die Zeiterfassung
ergänzen. Anforderung, bestätigte Schalterstellung und gemessene Leistung
werden mit ihrer jeweiligen Aussagekraft geführt.

Ein aktiver Gang fordert im Automatikbetrieb ab seiner vorläufigen Erkennung
Heizen an. Die Wirkung ausdrücklicher Bedienhandlungen sowie die Vorränge von
Ausschalten, Schutz und Ofenkühlung beschreibt [Betrieb](docs/betrieb.md).
Die bestätigten Aufgüsse bestimmen die Gangbestätigung und die einmalige
Zählung beim Gangende. Der Controller liefert den Erkennungskontext nach
jedem verarbeiteten Signal neu.

Der Saunataster startet bei Betrieb-AUS die gespeicherte Tastervorgabe.
Die Startgeste bleibt verbraucht. Eine neue lange Geste bei laufendem Betrieb
beendet die Sitzung. Eigenständige Langmeldungen verwenden jeweils den aktuellen
Betriebskontext. Beim gehaltenen Enddruck bleibt das Licht ausgeschaltet;
bestätigtes Loslassen beginnt den eingestellten Lichtnachlauf. Die
[Bedienhandlungen](docs/betrieb.md#bedienhandlungen-und-betriebsart) und
[Ereignisverträge](docs/schnittstellen.md#tasterereignisse) erklären diese Wege.

Das Archiv erhält Messwerte dauerhaft in der empfangenen Auflösung sowie die
zugehörigen Ereignisse und Parameterstände. Backup und Wiederherstellung
folgen [Speicherung](docs/speicherung.md). Nach einem Home-Assistant-Neustart
beginnt der Saunabetrieb mit ausdrücklichem Einschalten.

Das Referenzprogramm und seine Parameter unter `candidate/` bilden einen
eingefrorenen Reproduktionsstand. Änderungen daran erfolgen in einem
ausdrücklich darauf bezogenen Auftrag. Die begleitende
[Programmerklärung](docs/kandidat.md) wird als Dokumentation gepflegt;
gebundene Dokumentprüfsummen werden dabei nachvollziehbar aktualisiert.

## Prüfung

Die Analyse verfolgt jede betroffene Anforderung vom ausführbaren Eingang über
die Zustandsübergänge bis zu sämtlichen Verbrauchern der gemeinsamen Daten.
Ein Testnachweis gilt für seine angegebenen Eingaben und beobachteten Wirkungen.
Codeänderungen werden mit gezielten synthetischen Tests der betroffenen Pfade
geprüft. Tests verwenden vorhandene Fixtures und konkrete fachliche
Gegenbeispiele. Erwartete Wirkungen werden aus der Anforderung abgeleitet.

Tests laufen ausschließlich auf dem Linux-Zielsystem oder in Linux-CI.
Für jeden Prozess werden der tatsächlich geprüfte Stand, sein eigener Exitcode
und der vollständige Prozessabschluss erfasst. Der Prüfumfang richtet sich
nach der konkreten Änderung. PR-Head, tatsächlich ausgecheckter Commit und
zugehörige Trees werden getrennt erfasst. Ein Treevergleich belegt Gleichheit
oder die tatsächliche Abweichung. Die [Prüfanleitung](docs/abnahme.md) erklärt die vorhandenen
Verfahren und Umgebungen.

Bei reinen Dokumentationsänderungen werden Fachkonsistenz, Entscheidungsstatus
und Verweise geprüft. Ein Dateivergleich bestätigt den erhaltenen Programmstand.
Gebundene Dokumentprüfsummen werden separat auf den ausgelieferten Text geprüft.
Der Referenz-Replay verwendet ausdrücklich bereitgestellte Quelldaten.

## Dokumentationsstil

Die Dokumentation beschreibt Funktion, Voraussetzungen und Wirkung direkt und
positiv. Anwendertexte verwenden verständliche deutsche Begriffe und die
sichtbaren Bezeichnungen der Oberfläche. Technische Referenzen erklären
Feldnamen bei ihrer ersten Verwendung. Jeder Absatz vermittelt einen
Zusammenhang. Aufzählungen dienen konkreten Handlungsfolgen, Rangfolgen oder
Vergleichen; Tabellen ordnen nachzuschlagende Werte eindeutig zu.

## Datenschutz und Veröffentlichungen

Private Recorderdaten, Zugangsdaten, Home-Assistant-Konfigurationen und
persönliche Nutzungsdetails bleiben im geschützten Arbeitsbereich.
Veröffentlichte Beispiele verwenden synthetische oder ausdrücklich dafür
freigegebene Daten. Archiv und Export werden über authentifizierte Zugriffe
bereitgestellt.

Versionsänderungen, Lizenzwahl, Releases und Deployments erfolgen auf
ausdrücklichen Auftrag.
