# Oberfläche

> Ergänzender Vorrang: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> beschreibt den Produktivstand des Folgeauftrags vom 26.09.2026.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

Die Topnavigation neben der Sauna enthält **Steuerung**, **Verlauf**,
**Details** und **Einstellungen**. **Steuerung** enthält die alltägliche
Bedienung mit den normalen Home-Assistant-Bedienrechten. **Verlauf** zeigt die
Sitzungen als eigenes Blatt. Details und
technische Einstellbereiche stehen Home-Assistant-Administratoren zur Verfügung.
Es gibt keine zusätzlichen Benutzerrollen der Integration.

## Steuerung

Die Steuerung zeigt Zustand und passende Zeit direkt nebeneinander; die
Saunagangzahl steht rechts. Die aktive Sitzungsangabe erscheint unter
**Ausschalten**. Der Betriebsmodus hat eine eigene Kachel. Eine Saunaauswahl
erscheint erst bei mehreren eingerichteten Saunen.

Temperaturwahl und Temperaturautomatik stehen zusammen in der Bedienkachel.
Zwischen Sitzungen ist die Auswahl offen sichtbar. Während einer bestehenden
Sitzung, einschließlich der Wiederaufnahmefrist, öffnet **Programm ändern**
die Auswahl. Daneben steht immer das bestätigte, aktuell wirksame Programm als
Name, **Konstant** oder **Individuell**; ein vorgemerkter Entwurf ändert diese
Angabe erst nach dem Übernehmen. Ein begonnener Entwurf bleibt bis **Programm übernehmen** oder
**Abbrechen** auch während Statusaktualisierungen geöffnet. Die
Administrator-Übersteuerungen im Automatikbetrieb bleiben davon unabhängig
sichtbar. Die tatsächliche Ofenrückmeldung steht unmittelbar beim Phasenstatus
kompakt als **Ofen an** oder **Ofen aus**; fehlt sie, erscheint **Ofen unbekannt**.
Die Steuerung zeigt keinen Türstatus; er bleibt in den Details ablesbar.
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
Während einer Sitzung zeigt eine geänderte Auswahl einen vorgemerkten Entwurf,
während die aktive Wahl markiert bleibt. **Abbrechen** verwirft den Entwurf.
**Übernehmen** ist bei Änderungen aktiv; während der Anfrage zeigt es
**Wird übernommen …**, nach Erfolg kurz **✓ Übernommen**. In einer laufenden
Sitzung steht **Programm übernehmen** als Hauptaktion beim Betrieb. Zwischen
Sitzungen werden benannte und konstante Wahlen unmittelbar übernommen;
individuelle Eingaben benötigen weiterhin **Übernehmen**.

Die Helligkeitsauswahl zeigt **Gedimmt** und **Hell** mit kleineren Prozentangaben.
Im Automatikbetrieb stehen auf **Steuerung** für Administratoren die
vorübergehenden Ofen- und Lichtübersteuerungen einschließlich freier Helligkeit.
Normale Benutzer sehen für das Licht **Aus**, **Automatik** und **Hell**.
Die Betriebsart **Manuell** blendet die Temperaturautomatik aus
und zeigt **Ofen EIN/AUS** und die Lichtstufen **Aus**, **Gedimmt**, **Hell** auch für
normale Benutzer. Der Betriebsartwechsel ist nur
zwischen abgeschlossenen Sitzungen möglich. Vorübergehende Übersteuerungen
während des Automatikbetriebs sind davon unabhängig.

Direkt neben dem Zustand steht eine schlichte Zeitzeile. Beim Aufheizen lautet
sie beispielsweise **Heizen – noch 10 Minuten bis bereit**, bei Bereitschaft
**Bereit – noch 20 Minuten**. Geschätzte Aufheizzeiten zeigen keine Sekunden und
verwenden unter fünf Minuten den Text **noch unter 5 Minuten bis bereit**. Bei
Bereit zeigt die Zeit, wie lange noch mindestens ein Gang begonnen werden kann;
Heizpausen können diesen Zeitraum verlängern. Gang, Ofenkühlung und
Sperren zeigen ihre eigene kompakte Zeit statt einer Bereitschaft.
Nach Sitzungsende wird der verbleibende
Lichtnachlauf angezeigt. Technische Fristen und die Schätzung des mechanischen
Ofentimers stehen kompakt in den Details.

Die Aufheizprognose beginnt mit dem geeigneten Verlauf der letzten Sitzung
und übernimmt den aktuellen Temperaturtrend schrittweise. Kurze Schwankungen
führen nicht zu einem erneuten Anstieg der Restzeit. Eine Verlängerung kommt
bei dauerhaft langsamerem Aufheizen oder Wärmeverlust durch eine erkannte
Türöffnung infrage.

Orange (`#e58a55`) kennzeichnet die wirksame Auswahl in der Navigation und bei
Einstellungen. Auslösende Befehle wie **Starten**, **Übernehmen** und
**Speichern** verwenden die neutrale Aktionsfarbe des Home-Assistant-Themas;
gedecktes Rot kennzeichnet Ausschalten und Beenden.
Auch die wirksame Ofen- und Lichtauswahl erscheint orange. Nicht ausgewählte
Optionen und Nebenaktionen haben neutrale Flächen mit einer einheitlichen,
dezenten Kontur; Löschen erhält rote Schrift. Zusätzliche farbige Rahmen und
Innenstriche entfallen. Nur der Tastaturfokus wird deutlich umrandet;
Einfügemarkierungen beim Sortieren bleiben erhalten.

Ein Entwurf bleibt neutral und trägt den Zusatz **Vorgemerkt**. Die Zusammenfassung
**Noch nicht übernommen** steht auf einer dezenten Fläche ohne Sonderrahmen.
Während der Anfrage bleibt die Schaltfläche mit **Wird übernommen …**
gesperrt. **✓ Übernommen** bestätigt den Erfolg zwei Sekunden auf derselben
Fläche; danach kehrt das Bedienelement in seine normale Darstellung zurück.
Fehler erscheinen als Text und erhalten den Entwurf. Farbe allein vermittelt
keinen Bedienzustand. Statusplaketten bleiben zurückhaltend. Temperaturanzeige
und Temperaturkurve verwenden dieselbe Messfarbe (Standard `#ff6b4a`);
Luftfeuchteanzeige und Feuchtekurve verwenden eine zweite gemeinsame Messfarbe
(Standard `#42a5ff`). Die bisherigen Phasenfarben des Verlaufs bleiben die
Vorgaben für jede Phase. Nur die große Bedienkachel erhält bei einer bekannten,
automatischen Phase eine Tönung von 8 % der Phasenfarbe auf dem Kartenhintergrund.
Aus, Manuell, unbekannte und veraltete Zustände bleiben neutral. Es gibt dafür
keine Animation und keinen zusätzlichen Rahmen. Ofen-Ein und Ofen-Aus sind
zusätzlich farblich erkennbar.

Administratoren können unter **Einstellungen → Darstellung** die Farben der
Bedienoberfläche, Phasen, Messungen und Ereignisse je Sauna anpassen. Die
Einstellungen sind in **Bedienung**, **Grunddarstellung**,
**Zustände und Phasen** und **Messungen und Ereignisse** gegliedert. Farbfeld und
Hexwert zeigen denselben Entwurf. Die Vorschau wirkt sofort in Anzeigen,
Verlauf, SVG, Canvas, Legenden und Hinweisen. **Darstellung speichern** übernimmt
sie für die ausgewählte Sauna. **Änderungen verwerfen** stellt die gespeicherten
Werte wieder her; **Standarddarstellung wiederherstellen** setzt nur den Entwurf
auf die Vorgaben. Lesbarer Text und
sichtbarer Tastaturfokus bleiben auch bei angepassten Farben erhalten.

Die Anzeigeskalen beginnen standardmäßig bei **40–110 °C** für Temperatur und
**0–60 %** für Luftfeuchte. Administratoren können beide Grenzen ebenfalls je
Sauna anpassen. Luftfeuchtegrenzen liegen zwischen 0 und 100 %, jede Skala
braucht eine kleinere Unter- als Obergrenze. Die Skalen betreffen allein die
Anzeigen, nicht die Verlaufsachsen, empfangene Messwerte, zulässige
Solltemperaturen oder laufende Regelung. Außerhalb der Skala bleibt der
tatsächliche Zahlenwert lesbar; der Marker steht am Skalenende und weist auf
die Überschreitung hin. Der Sollschieber nutzt nur den Schnittbereich aus
sichtbarer Skala und zulässiger Solltemperatur. Ohne Schnittbereich ist er
deaktiviert. Vorschau und Speichern laden die Integration nicht neu und
verändern keine laufende Sitzung.

## Verlauf und Archiv

Das eigene Verlaufsblatt heißt bei eingeschaltetem Betrieb **Laufende Sitzung**,
sonst **Letzte Sitzung**. Ältere Sitzungen sind über die Archivauswahl erreichbar.
Die Standardansicht enthält
eine Temperatur- und eine Feuchtekurve. Der Detailverlauf ergänzt beide
Messpositionen. Die Gestaltung folgt der ursprünglichen Saunaansicht:

| Element | Darstellung |
|---|---|
| Temperatur | Gemeinsame Messfarbe von Anzeige und Kurve, standardmäßig `#ff6b4a`, linke °C-Achse |
| Luftfeuchte | Gemeinsame Messfarbe von Anzeige und Kurve, standardmäßig `#42a5ff`, rechte Prozentachse |
| Messposition im Detailverlauf | Oben durchgezogen, unten gestrichelt |
| Tür offen | Gelbe Fläche |
| Saunagang | Magentafarbene Fläche; vorläufig gestrichelt umrandet |
| Aufguss | Weiße Zeitmarke |
| Phasen | Je Phase einstellbare Farbe; die bisherigen Verlaufsfarben bleiben die Vorgaben |
| Gezählt als Heizaktivität | Schmaler Streifen unter dem Verlauf |

Leichte Glättung dient nur der Darstellung. Ein Tooltip nennt den empfangenen
Originalwert und seinen Zeitpunkt. Messlücken bleiben sichtbar; gespeicherte
Rohwerte werden weder geglättet noch verdichtet.

Plus/Minus, eine echte Vergrößerungsgeste oder Strg beziehungsweise Cmd mit dem
Mausrad verändern den Ausschnitt. Normales Scrollen zoomt nicht. Eine kleine
Übersicht über die gesamte Sitzung zeigt das ausgewählte Fenster; dessen
Ränder lassen sich verschieben. **Gesamt** stellt den vollständigen
Verlauf wieder her.

Der dauerhafte Daten-, Canvas- und Eingabevertrag ist in
[Livekurve](livekurve.md) dokumentiert.

## Details

Die Betriebsdetails beginnen mit Zustand und Fristen. Danach folgen **Ofen**,
**Licht**, **Messung** und weitere Statusangaben. Soll- und obere
Regeltemperatur, Heizentscheidung, Rückmeldungsquelle, Energieverbrauch sowie
automatisch geplanter Lichtwert, manuelle Vorgabe und Übersteuerungsfrist sind
dort ablesbar. Die
Bedienelemente bleiben auf **Steuerung**. Alle laufenden Fristen stehen
gemeinsam und kompakt an einem Ort.

Die markierten Bedienungen auf **Steuerung** zeigen die aktive Auswahl.
**Automatik** bleibt markiert, wenn der Ofen automatisch heizt; **Ofen an/aus**
beschreibt getrennt die tatsächliche Rückmeldung. Offene Auswahlfelder und
Eingaben bleiben bei laufender Aktualisierung erhalten.

Eine laufende Ofenkühlung kann von Administratoren auf **Steuerung**
ausdrücklich vorzeitig beendet werden.
Laufende Schutzsperren bleiben wirksam. Die mechanische
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
Bedienrechten verwalten dort die Programmbibliothek und die Tasterwahl.
Programme erscheinen als kompakte Zeilen; ein Editor öffnet nur den gewählten
Eintrag. Per Ziehen am Griff oder mit Pfeiltasten wird die Reihenfolge im
Entwurf geändert. **Programme speichern** übernimmt sie dauerhaft,
**Änderungen verwerfen** setzt sie zurück. Programm-IDs bleiben beim Sortieren
erhalten. Der
Taster startet mit einem benannten Programm oder einer unabhängig gespeicherten
konstanten Temperatur. Technische Konfiguration, Erkennungsparameter,
Protokollierung und Export stehen nur Administratoren zur Verfügung.

Während einer Sitzung sind die dafür vorgesehenen Temperaturwerte sowie für
Administratoren die Protokollstufe und Darstellung änderbar. Grundwerte, Programmeinträge, Betriebsart und
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
