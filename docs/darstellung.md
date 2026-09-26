# Oberfläche

> Ergänzender Vorrang: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> beschreibt den Produktivstand des Folgeauftrags vom 26.09.2026.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

Die Topnavigation neben der Sauna enthält **Übersicht**, **Details** und **Einstellungen**.
Die Übersicht enthält die alltägliche Bedienung mit den normalen
Home-Assistant-Bedienrechten sowie **Verlauf und Archiv**. Details und
technische Einstellbereiche stehen Home-Assistant-Administratoren zur Verfügung.
Es gibt keine zusätzlichen Benutzerrollen der Integration.

## Übersicht

Die Steuerung zeigt Zustand und passende Zeit direkt nebeneinander; die
Saunagangzahl steht rechts. Die aktive Sitzungsangabe erscheint unter
**Ausschalten**. Der Betriebsmodus hat eine eigene Kachel. Eine Saunaauswahl
erscheint erst bei mehreren eingerichteten Saunen.

Temperaturwahl und Temperaturautomatik stehen zusammen in der Bedienkachel.
Im Automatikbetrieb zeigt der Umgebungsbereich unter Temperatur und Luftfeuchte
Türstatus und Ofenstatus. Im manuellen Betrieb entfällt der Türhinweis; eine
zweite Ofenstatuskachel wird nicht gezeigt.
Bei der Temperaturwahl liegt der Schieber direkt auf der Skala. Klick, Ziehen
und Pfeiltasten ändern das Soll; der einstellbare Regelbereich beginnt
standardmäßig bei 60 °C und endet bei 100 °C. Eine direkte Wahl bedeutet
konstantes Heizen. Messwerte werden durch diesen Einstellbereich nicht begrenzt.
Messposition und internes Bereitschaftsziel erscheinen erst in den Details.

Die Auswahlleiste enthält **Programm**, **Individuell** und **Konstant**.
Benannte Programme stehen untereinander als kompakte Auswahlbuttons mit Namen
und Temperaturfolge. **Konstant** zeigt die festen Temperaturtasten.
Die Wahl **Individuell** blendet die **Temperaturautomatik** ein. Dort stehen
gleichmäßige Verteilung mit Start, Ende und Stufenzahl sowie die Einzelwahl jeder
Temperatur zur Verfügung. Das Infosymbol bei **Verteilung** öffnet eine kurze
Hilfe am Feld. Nach Erreichen der letzten Stufe bleibt deren Temperatur erhalten.
Eine eigene Aktionszeile grenzt **Übernehmen** von den Eingaben ab. Der Button
ist bei Änderungen aktiv und hervorgehoben; nach dem Speichern zeigt er
**Übernommen**.

Die Helligkeitsauswahl zeigt **Gedimmt** und **Hell** mit kleineren Prozentangaben.
Im Automatikbetrieb stehen auf der Übersicht **Aus**, **Automatik** und **Hell**
zur Verfügung. Die Betriebsart **Manuell** blendet die Temperaturautomatik aus
und zeigt **Ofen EIN/AUS** und die Lichtstufen **Aus**, **Gedimmt**, **Hell** auch für
normale Benutzer. Der Betriebsartwechsel ist nur
zwischen abgeschlossenen Sitzungen möglich. Vorübergehende Übersteuerungen
während des Automatikbetriebs sind davon unabhängig.

Direkt neben dem Zustand steht eine schlichte Zeitzeile. Beim Aufheizen lautet
sie beispielsweise **Heizen – noch 10 Minuten bis bereit**, bei Bereitschaft
**Bereit – noch 20 Minuten**. Geschätzte Aufheizzeiten zeigen keine Sekunden und
verwenden unter fünf Minuten den Text **noch unter 5 Minuten bis bereit**. Bei
Bereit zeigt die Zeit, wie lange noch mindestens ein Gang begonnen werden kann;
Heizpausen können diesen Zeitraum verlängern. Gang, Nachlauf, Kühlung und
Sperren zeigen ihre eigene kompakte Zeit statt einer Bereitschaft.
Nach Sitzungsende wird der verbleibende
Lichtnachlauf angezeigt. Technische Fristen und die Schätzung des mechanischen
Ofentimers stehen kompakt in den Details.

Die Aufheizprognose beginnt mit dem geeigneten Verlauf der letzten Sitzung
und übernimmt den aktuellen Temperaturtrend schrittweise. Kurze Schwankungen
führen nicht zu einem erneuten Anstieg der Restzeit. Eine Verlängerung kommt
bei dauerhaft langsamerem Aufheizen oder Wärmeverlust durch eine erkannte
Türöffnung infrage.

Die Temperaturanzeige übernimmt die Farbe der aktuellen Phase. Die
Luftfeuchteanzeige ist bis einschließlich 20 % grün, bis einschließlich 30 %
gelb und darüber rot. Ofen-Ein und Ofen-Aus sind zusätzlich farblich erkennbar.

## Verlauf und Archiv

Das eigene Verlaufsblatt heißt bei eingeschaltetem Betrieb **Laufende Sitzung**,
sonst **Letzte Sitzung**. Ältere Sitzungen sind über die Archivauswahl erreichbar.
Die Standardansicht enthält
eine Temperatur- und eine Feuchtekurve. Der Detailverlauf ergänzt beide
Messpositionen. Die Gestaltung folgt der ursprünglichen Saunaansicht:

| Element | Darstellung |
|---|---|
| Temperatur | Orange, linke °C-Achse |
| Luftfeuchte | Blau, rechte Prozentachse |
| Messposition im Detailverlauf | Oben durchgezogen, unten gestrichelt |
| Tür offen | Gelbe Fläche |
| Saunagang | Magentafarbene Fläche; vorläufig gestrichelt umrandet |
| Aufguss | Weiße Zeitmarke |
| Heizen / bereit / lüften / Zwangskühlung | Orange / grüne / blaugraue / graublaue Fläche |
| Gezählt als Heizaktivität | Schmaler Streifen unter dem Verlauf |

Leichte Glättung dient nur der Darstellung. Ein Tooltip nennt den empfangenen
Originalwert und seinen Zeitpunkt. Messlücken bleiben sichtbar; gespeicherte
Rohwerte werden weder geglättet noch verdichtet.

Plus/Minus, eine echte Vergrößerungsgeste oder Strg beziehungsweise Cmd mit dem
Mausrad verändern den Ausschnitt. Normales Scrollen zoomt nicht. Eine kleine
Übersicht über die gesamte Sitzung zeigt das ausgewählte Fenster; dessen
Ränder lassen sich verschieben. **Gesamt** stellt den vollständigen
Verlauf wieder her.

## Details

Die Betriebsdetails sind nach **Ofen**, **Licht**, **Messung** und
**Zeiten** geordnet. Soll- und obere Regeltemperatur, Heizentscheidung,
Rückmeldungsquelle, Energieverbrauch und manuelle Bedienungen sind ihrem
Steuerbereich zugeordnet. Alle laufenden Fristen stehen gemeinsam und kompakt
an einem Ort.

Die markierten Bedienungen zeigen die aktive Auswahl. **Automatik** bleibt
markiert, wenn der Ofen automatisch heizt; **Ofen an/aus** beschreibt getrennt
die tatsächliche Rückmeldung. Offene Auswahlfelder und Eingaben bleiben bei
laufender Aktualisierung erhalten. Die Oberfläche ändert gezielt die
betroffenen Werte und Zustände.

Nachlauf und laufende Zwangskühlung können dort wie bei regulärem Zeitablauf
beendet werden. Laufende Schutzsperren bleiben wirksam. Die mechanische
Timeranzeige nennt ihren Pausengrund und bleibt eine Schätzung; sie steuert
keinen Aktor.

Die **Erkennungskontrolle** zeigt die gespeicherten Merkmale für Tür, Personen,
Aufgüsse und Lüftung mit den zur Sitzung gehörenden Schwellen. Ereignismarker
liegen in den zugehörigen Graphen. Marker und Ereigniszeile führen beim
Anklicken zueinander. Inaktive Bestätigungszeiten mit Wert null und leere
Merkmalskurven werden nicht als vermeintliche Information aufgelistet.
Im Browser wird keine zweite Erkennung berechnet.

## Einstellungen

Ganz unten setzt **Als Startseite festlegen** die Saunaübersicht als persönliche
Home-Assistant-Startseite. Die Auswahl gehört zum angemeldeten Benutzerprofil.

Die Einstellungen sind nach Temperatur, Betrieb, Licht, Überwachung, Timer,
Energie und Darstellung geordnet. Nutzer mit normalen Home-Assistant-
Bedienrechten verwalten dort die Programmbibliothek und die Tasterwahl. Der
Taster startet mit einem benannten Programm oder einer unabhängig gespeicherten
konstanten Temperatur. Technische Konfiguration, Erkennungsparameter,
Protokollierung und Export stehen nur Administratoren zur Verfügung.

Während einer Sitzung sind nur die dafür vorgesehenen Temperaturwerte und die
Protokollstufe änderbar. Grundwerte, Programmeinträge, Betriebsart und
Gerätezuordnungen bleiben gesperrt. Diese Grenzen gelten auch bei direkten
API-Aufrufen. Die Bedienrechte stammen aus Home Assistant.

**INFO** protokolliert Betriebsereignisse und Fehler, **ERROR** nur Fehler,
**DEBUG** zusätzlich Messwerte und Erkennungsprüfungen. Die Auswahl wirkt ohne
Neustart und verändert das vollständige Sitzungsarchiv nicht. Das Archiv kann
als authentifizierter ZIP-Download exportiert werden.

**Standardwerte wiederherstellen** setzt nach Sitzungsende die
Softwareeinstellungen einschließlich Programmbibliothek zurück. Sensoren,
Geräte und die Zuordnung des Bedieneingangs bleiben erhalten.

Die ausgeführten Oberflächenprüfungen und ihre Grenzen stehen im
[Abnahmebericht](abnahme.md).
