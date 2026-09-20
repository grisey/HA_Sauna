# HA Sauna

HA Sauna bringt die Steuerung, Temperaturwahl, das Licht und den Verlauf einer
Saunasitzung in Home Assistant zusammen. Sie richtet sich an Menschen, die ihre
Sauna von einer klaren Oberfläche aus bedienen und trotzdem die vorhandenen
Schutz- und Bedienelemente weiter nutzen möchten.

Die Einrichtung beginnt mit dem kurzen [Einrichtungsleitfaden](docs/einrichtung.md).
Er beschreibt, welche Geräte nach ihrer Rolle ausgewählt werden und welche
Standards vor dem ersten Einsatz sinnvoll sind.

## Die Sauna bedienen

In der **Übersicht** starten und beenden Sie den Saunabetrieb, wählen eine
Temperatur und sehen die Messwerte auf einen Blick. Die Zeitanzeige hilft bei
zwei Fragen: Wann kann der nächste Gang beginnen, und wie lange ist ein Start
noch möglich? Auch Normallicht, Raumlicht und die Rückkehr zur automatischen
Lichtsteuerung sind hier direkt erreichbar.

Der physische Bedieneingang kann weiterhin zum Ein- und Ausschalten dienen. Ein
entkoppelter Taster löst dabei den Saunabetrieb aus; der Heizaktor bleibt davon
getrennt und wird von der Regelung geführt. So bleibt eine vorhandene Bedienung
verständlich, ohne den Heizaktor unmittelbar zu schalten.

Unter **Details** finden Sie die weitergehende Bedienung: aktuelle Betriebs- und
Fristinformationen, die manuelle Ofen- und Lichtsteuerung, Einstellungen,
Protokollierung und den Archivexport. Nachlauf und Kühlung lassen sich dort bei
Bedarf einzeln beenden; die verbleibende Regelung bleibt dabei erhalten.

## Temperatur wählen oder steigern

Für eine direkte Sollwahl wählen Sie Temperatur per Taste oder Schieber. Diese
Wahl bleibt konstant. Die Schnellauswahl kann angepasst werden; Temperaturen
sind bis 100 °C begrenzt.

Für eine steigende Temperatur stehen zwei anpassbare Profile bereit:

| Profil | Werkseinstellung |
|---|---|
| 4-Gang-Programm | 80 → 85 → 90 → 95 °C |
| 3-Gang-Programm | 70 → 80 → 90 °C |

Start, Ende und die Verteilung der Steigerung lassen sich anpassen. Die
Verteilungszahl beschreibt nur, wie die Temperatur von Start zu Ende ansteigt.
Sie plant und begrenzt keine Saunagänge. Ist die Endtemperatur erreicht, bleibt
sie auch für beliebig viele weitere Gänge gültig. Eine Änderung während einer
Sitzung wirkt auf die weitere Temperaturfolge, ohne den laufenden Betrieb zu
unterbrechen.

## Licht, das dem Aufheizen folgt

Das Licht orientiert sich während des Aufheizens an der Temperatur. Ab dem
einstellbaren Kaltpunkt von standardmäßig 30 °C steigt es linear vom Grundlicht
von 5 % bis zum normalen Licht. Dieses beträgt tagsüber standardmäßig 40 % und
nachts 25 %; in der Dämmerung geht es gleitend zwischen beiden Werten über.

Im Nachlauf dimmt das Licht standardmäßig auf 15 %, während einer Kühlung auf
5 %. Übergänge dauern standardmäßig 30 Sekunden und lassen sich anpassen.
Anschließend steigt die Helligkeit bis zum Ende der jeweiligen Phase wieder
auf den zur Temperatur passenden Wert. Bei
einem Saunagang bleibt das normale Licht auch dann erhalten, wenn die Temperatur
fällt; während einer Kühlung hat deren Lichtvorgabe Vorrang. Eine manuelle
Lichtwahl gilt bis zum nächsten Phasenwechsel.

Nach dem Ende einer Sitzung leuchtet das Licht standardmäßig noch 10 Minuten mit
50 % und schaltet dann aus. Dauer und Helligkeit sind einstellbar.

## Heizzeit, Kühlung und Energie

Die Heizzeit beginnt mit einem einstellbaren Budget von standardmäßig 90 Minuten
gezählter Heizzeit. Nach der ersten Kühlung derselben Sitzung sinkt dieses Budget
einmalig auf 60 Minuten und bleibt danach gleich. Eine fällige Kühlung pausiert
bei einem laufenden Saunagang; anschließend wird der Nachlauf auf ihre Dauer
angerechnet. Die Kühlvorgabe beträgt standardmäßig 15 Minuten. Alle diese Werte
sind Standards und können angepasst werden.

Bei manueller Ofenübersteuerung bleibt die ausstehende Kühlung erhalten.
Wird während des Nachlaufs manuell geheizt, hält dessen Uhr an und läuft danach
mit der verbliebenen Zeit weiter.

Der mechanische Ofentimer bleibt eine Erinnerung: Die Anzeige schätzt seine
Restzeit, steuert aber weder Ofen noch Schutzfunktionen. Für die Sitzungsenergie
nutzt HA Sauna standardmäßig 4,5 kW und die gezählte Heizzeit. Ist eine passende
Leistungsmessung vorhanden, kann sie diese Schätzung ersetzen.

## Übersicht, Archiv und Verwaltung

Neben der Übersicht gibt es einen Verlauf mit Archivwahl. Dort bleiben
Messwerte und Ereignisse einer Sitzung nachvollziehbar. In den Details können
Sie das Archiv als ZIP herunterladen.

Die Protokollierung ist ebenfalls in den Details wählbar: **INFO** ist der
Standard für Betriebsereignisse und Fehler, **ERROR** beschränkt sich auf Fehler,
und **DEBUG** ergänzt ausführliche Diagnoseinformationen.

## Weiterführende technische Dokumentation

Technische Hintergründe, Parameter und der dokumentierte Nachweis bleiben
separat verfügbar: [Betrieb](docs/betrieb.md), [Parameter](docs/parameter.md),
[Oberfläche](docs/darstellung.md), [Archiv und Backup](docs/speicherung.md),
[Entscheidungen](docs/entscheidungen.md), [Umsetzung](docs/umsetzung.md) und
[Abnahme](docs/abnahme.md).
