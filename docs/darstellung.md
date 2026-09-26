# Oberfläche

> Ergänzender Vorrang: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> beschreibt den Produktivstand des Folgeauftrags vom 26.09.2026.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

Die Hauptansichten heißen **Übersicht** und **Details**. Die Übersicht enthält
die alltägliche Bedienung sowie **Verlauf und Archiv**. Details stehen
Home-Assistant-Administratoren zur Verfügung. Es gibt keine zusätzlichen
Benutzerrollen der Integration.

## Übersicht

Die Steuerung zeigt Zustand, Ofenaktivität, Türstatus, Temperatur und Luftfeuchte.
Bei der Temperaturwahl liegt der Schieber direkt auf der Skala. Klick, Ziehen
und Pfeiltasten ändern das Soll; der einstellbare Regelbereich beginnt
standardmäßig bei 60 °C und endet bei 100 °C. Eine direkte Wahl bedeutet
konstantes Heizen. Messwerte werden durch diesen Einstellbereich nicht begrenzt.
Messposition und internes Bereitschaftsziel erscheinen erst in den Details.

Benannte Programme werden kompakt mit Namen und Temperaturfolge angezeigt.
Unter **Temperaturautomatik** lassen sich Startwert, Endwert und die Verteilung
frei wählen. Die Anzahl der Temperaturstufen ist keine Begrenzung der
tatsächlichen Saunagänge. Nach Erreichen der Endtemperatur bleibt diese erhalten.

Die Helligkeitsauswahl zeigt **Gedimmt** und **Hell** mit kleineren Prozentangaben.
Im Automatikbetrieb stehen auf der Übersicht **Aus**, **Automatik** und **Hell**
zur Verfügung. Die Betriebsart **Manuell** blendet die Temperaturautomatik aus
und zeigt die manuelle Ofen- und Lichtbedienung. Der Betriebsartwechsel ist nur
zwischen abgeschlossenen Sitzungen möglich. Vorübergehende Übersteuerungen
während des Automatikbetriebs sind davon unabhängig.

Direkt neben der Phase stehen die Bereitschaft und das verbleibende Startfenster.
Geschätzte Zeiten erscheinen in Fünf-Minuten-Schritten ohne Sekunden. Solange
die aktuelle Aufheizphase noch keinen stabilen Anstieg liefert, verwendet die
Prognose den durchschnittlichen Anstieg der letzten Sitzung. Fehlt auch dafür
ein brauchbarer Verlauf, bleibt die Startzeit offen. Die Prognose ersetzt keine
Heizsperre oder laufende Kühlzeit. Während eines Gangs steht dessen Dauer im
Vordergrund. Nach Sitzungsende wird der
verbleibende Lichtnachlauf angezeigt. Technische Fristen und die Schätzung des
mechanischen Ofentimers stehen kompakt in den Details.

Die Temperaturanzeige übernimmt die Farbe der aktuellen Phase. Die
Luftfeuchteanzeige ist bis einschließlich 20 % grün, bis einschließlich 30 %
gelb und darüber rot. Ofen-Ein und Ofen-Aus sind zusätzlich farblich erkennbar.

## Verlauf und Archiv

Das eigene Verlaufsblatt zeigt die aktuelle oder letzte Sitzung; ältere
Sitzungen sind über die Archivauswahl erreichbar. Die Standardansicht enthält
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
Ränder lassen sich verschieben. **Gesamte Saunasitzung** stellt den vollständigen
Verlauf wieder her.

## Details

Die Betriebsdetails sind nach **Heizung**, **Licht**, **Messwerten** und
**Zeiten** geordnet. Soll- und obere Regeltemperatur, Heizentscheidung,
Rückmeldungsquelle, Energieverbrauch und manuelle Bedienungen sind ihrem
Steuerbereich zugeordnet. Alle laufenden Fristen stehen gemeinsam und kompakt
an einem Ort.

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

Die Einstellungen sind nach Temperatur, Betrieb, Licht, Überwachung, Timer,
Energie und Darstellung geordnet. Die Programmbibliothek lässt Namen,
Start- und Endtemperaturen sowie die Verteilung ändern und neue Programme
hinzufügen. Die beim Start am Saunataster verwendete Wahl wird separat
festgelegt. Erkennungsparameter stehen in einem eigenen Expertenbereich.

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
