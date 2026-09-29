# Arbeitsregeln

## Arbeitsgrundlage

Änderungen beruhen auf dem zuvor gelesenen Repository- und Dateistand.
Anforderungen, bestehende Implementierung und erhobene Nachweise sind getrennt
gekennzeichnet. Offene Entscheidungen sind mit ihrer konkreten Auswirkung
beschrieben.

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

[Architektur](docs/architektur.md) · [Zeitbezüge](docs/zeitmodell.md) ·
[Erkennung](docs/erkennung.md) · [Parameter und Entitätsrollen](docs/parameter.md)

Python folgt Ruff gemäß `pyproject.toml`. Die Oberfläche folgt der in
`.prettierrc.json` festgelegten Formatierung mit Prettier 3.6.2. Exporte und
reproduzierbare Prüfartefakte verwenden eine festgelegte Reihenfolge und
deterministische Serialisierung.

## Fachliche Grundlagen

[Messpositionen und Sensorvorrang](docs/betrieb.md#temperatur-und-bereitschaft) ·
[Heizzeit und Rückmeldequellen](docs/betrieb.md#heizzeit-timer-und-energie) ·
[Heizpriorität](docs/praesenz-ofen-phasen.md#heizpriorität) ·
[Gangbestätigung und Zählung](docs/gangmodell.md) ·
[Ofenkühlung](docs/ofenkuehlung.md)

[Bedienhandlungen und Betriebsart](docs/betrieb.md#bedienhandlungen-und-betriebsart) ·
[Tasterereignisse](docs/schnittstellen.md#tasterereignisse) ·
[Archiv, Backup und Wiederherstellung](docs/speicherung.md) ·
[Sitzung und Neustart](docs/betrieb.md#sitzung)

Das Referenzprogramm und seine Parameter unter `candidate/` bilden einen
eingefrorenen Reproduktionsstand. Änderungen daran erfolgen in einem
ausdrücklich darauf bezogenen Auftrag. Die begleitende
[Programmerklärung](docs/kandidat.md) wird als Dokumentation gepflegt;
gebundene Dokumentprüfsummen werden dabei aktualisiert.

## Prüfung

Die Analyse verfolgt jede betroffene Anforderung vom ausführbaren Eingang über
die Zustandsübergänge bis zu sämtlichen Verbrauchern der gemeinsamen Daten.

Ein Fehlernachweis enthält den erreichbaren Eingang, seine Vorbedingungen,
die verletzte Fachregel oder den bestehenden Schnittstellenvertrag und die
beobachtete Wirkung. Synthetische Tests bilden diesen Weg mit den produktiven
Methoden nach. Robustheitsfälle und neue Funktionswünsche sind entsprechend
gekennzeichnet.

Tests verwenden vorhandene Fixtures.

Tests laufen ausschließlich auf dem Linux-Zielsystem oder in Linux-CI.
Für jeden Prozess werden der tatsächlich geprüfte Stand, sein eigener Exitcode
und der vollständige Prozessabschluss erfasst. Der Prüfumfang richtet sich
nach der konkreten Änderung. PR-Head, tatsächlich ausgecheckter Commit und
zugehörige Trees werden getrennt erfasst. Ein Treevergleich belegt Gleichheit
oder die tatsächliche Abweichung.

[Prüfanleitung](docs/abnahme.md)

Bei reinen Dokumentationsänderungen werden Fachkonsistenz, Entscheidungsstatus
und Verweise geprüft. Ein Dateivergleich bestätigt den erhaltenen Programmstand.
Gebundene Dokumentprüfsummen werden separat auf den ausgelieferten Text geprüft.
Der Referenz-Replay verwendet ausdrücklich bereitgestellte Quelldaten.

## Dokumentationsstil

Die Dokumentation verwendet neutrale Sachbeschreibungen ohne persönliche
Ansprache oder Handlungsaufforderungen. Überflüssige Sätze und Dopplungen
entfallen. Bedienbezeichnungen entsprechen der Oberfläche; technische Begriffe
sind bei Bedarf erläutert. Aufzählungen dienen Abläufen, Rangfolgen oder Vergleichen.

## Datenschutz und Veröffentlichungen

Private Recorderdaten, Zugangsdaten, Home-Assistant-Konfigurationen und
persönliche Nutzungsdetails bleiben im geschützten Arbeitsbereich.
Veröffentlichte Beispiele verwenden synthetische oder ausdrücklich dafür
freigegebene Daten. Archiv und Export werden über authentifizierte Zugriffe
bereitgestellt.

Versionsänderungen, Lizenzwahl, Releases und Deployments erfolgen auf
ausdrücklichen Auftrag.
