# HA Sauna

HA Sauna bündelt Ihre Saunasitzung in Home Assistant: Temperatur, Licht,
Saunagänge, Bedienung und Verlauf bleiben an einer Stelle sichtbar.

Oben neben der Sauna stehen **Übersicht**, **Details** und **Einstellungen** bereit. In der
Übersicht schalten Sie den Betrieb ein, wählen die Temperatur direkt am
Anzeigebogen oder über Schnellwahltasten und sehen Temperatur, Luftfeuchte,
Heizzustand und die nächste relevante Zeit. Die Temperatur kann konstant bleiben
oder einem Programm mit gleichmäßig verteilten oder einzeln gewählten Stufen
folgen. Im Automatikbetrieb bietet
die Übersicht beim Licht **Aus**, **Automatik** und **Hell**. **Gedimmt** und
freie Helligkeit stehen Administratoren in den Details zur Verfügung.

Der bestehende Saunataster bleibt Teil der Bedienung: Ein kurzer Druck startet
mit dem hinterlegten Programm oder übersteuert den Ofen vorübergehend; langes
Drücken beendet die Saunasitzung. Lichttaster schalten und dimmen das Licht
weiter unmittelbar.

Normale Home-Assistant-Bedienrechte reichen für die reguläre Bedienung:
Betriebsartwechsel zwischen abgeschlossenen Sitzungen, manuelles Ofen-
Ein-/Ausschalten, Lichtstufen sowie Programme und Tasterwahl in den
**Einstellungen**. Der Taster nutzt ein benanntes Programm oder eine eigene
konstante Temperatur. Technische Konfiguration, Details, Ofenübersteuerungen im
Automatikbetrieb, freie Helligkeitswahl und Archivexport bleiben Administratoren
vorbehalten.

## Start

Für die Installation und Gerätezuordnung lesen Sie das kurze
[Einrichtungsrunbook](docs/einrichtung.md). Es beschreibt HACS, die Rollen der
Sensoren und Aktoren sowie den getrennten Anschluss von Taster und Heizschütz.

Die ausführliche [Bedienungsanleitung](docs/bedienung.md) erklärt die tägliche
Nutzung, Temperaturprogramme, Licht, Taster, Übersteuerungen und den Verlauf.

## Weiterführendes

Die technischen Regeln und sämtliche einstellbaren Werte stehen in
[Betrieb](docs/betrieb.md) und [Parameter](docs/parameter.md). Hinweise zu
Archiv, Datenhaltung und Prüfung finden Sie in [Speicherung](docs/speicherung.md)
und [Abnahme](docs/abnahme.md).
