# HA Sauna

HA Sauna bündelt Ihre Saunasitzung in Home Assistant: Temperatur, Licht,
Saunagänge, Bedienung und Verlauf bleiben an einer Stelle sichtbar.

In der Übersicht schalten Sie den Betrieb ein, wählen die Temperatur direkt am
Anzeigebogen oder über Schnellwahltasten und sehen Temperatur, Luftfeuchte,
Heizzustand und die nächste relevante Zeit. Die Temperatur kann konstant bleiben
oder mit einem Programm von Gang zu Gang steigen. Im Automatikbetrieb bietet
die Übersicht beim Licht **Aus**, **Automatik** und **Hell**. **Gedimmt** und
freie Helligkeit stehen Administratoren in den Details zur Verfügung.

Der bestehende Saunataster bleibt Teil der Bedienung: Ein kurzer Druck startet
mit dem hinterlegten Programm oder übersteuert den Ofen vorübergehend; langes
Drücken beendet die Saunasitzung. Lichttaster schalten und dimmen das Licht
weiter unmittelbar.

Für eine Saunasitzung stehen die Ansichten **Steuerung** sowie **Verlauf und
Archiv** bereit. Administratoren erhalten zusätzlich Details zu Betrieb,
Fristen, Erkennung, Einstellungen und Archivexport.

Nach einem bestätigten Saunagang folgt die [Ofenkühlung](docs/ofenkuehlung.md)
mit der bisher eingestellten Dauer. Die eigenständige Zwangskühlung entfällt.

Der [Folgeauftrag zu Präsenz, Ofen und Phasen](docs/praesenz-ofen-phasen.md)
beschreibt die vorbereitete externe Präsenzquelle, Türanforderung, Gangveto und
korrigierte Historie einschließlich der noch offenen Aktivierungsregeln.

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
