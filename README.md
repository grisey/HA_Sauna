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
noch möglich? Das Licht lässt sich ausschalten, auf **Hell** stellen oder wieder
der **Automatik** übergeben. Bei **Hell** steht die zugehörige Helligkeit als
kleine Prozentangabe; **Automatik** bleibt ohne Prozentangabe.

Der Saunataster startet immer im Automatikbetrieb mit dem hinterlegten
Standardprogramm. Während der Sitzung übersteuert ein kurzer Druck den Ofen;
der nächste kurze Druck gibt ihn
wieder an die Automatik zurück. Zum Beenden halten Sie den Taster gedrückt, bis
das Licht den Abschluss mit starkem Dimmen bestätigt. Nach dem Loslassen beginnt
der Lichtnachlauf. Eine dauerhafte Schalterstellung kann alternativ als
Bedieneingang eingerichtet werden.

Die Lichttaster bedienen das Licht weiterhin direkt: kurz drücken zum
Ausschalten, gedrückt halten zum Heller- oder Dunklerstellen. Die gewählte
Helligkeit bleibt im Automatikbetrieb bis zum nächsten Phasenwechsel erhalten,
längstens für die eingestellte Übersteuerungsdauer.

Zwischen den Sitzungen können Sie zur Betriebsart **Manuell** wechseln. Dann
bedienen Sie Ofen und Licht selbst; die Temperaturautomatik wird ausgeblendet.
Messung und Aufzeichnung laufen weiter. Technischer Schutz und die Reaktion auf
bestätigte Übertemperatur bleiben aktiv. Für eine einzelne Korrektur im
Automatikbetrieb stehen in den Details weiterhin vorübergehende Übersteuerungen
bereit. Diese enden spätestens nach einer einstellbaren Dauer von standardmäßig
zehn Minuten. So übernimmt die Automatik auch dann wieder, wenn Sie den Ofen
während des Aufheizens vorübergehend ausgeschaltet haben.

Unter **Details** finden Sie die weitergehende Bedienung: aktuelle Betriebs- und
Fristinformationen, die manuelle Ofen- und Lichtsteuerung, Einstellungen,
Protokollierung und den Archivexport. Nachlauf und Kühlung lassen sich dort bei
Bedarf einzeln beenden; die verbleibende Regelung bleibt dabei erhalten.

## Temperatur wählen oder steigern

Für eine direkte Sollwahl wählen Sie die Temperatur per Taste oder direkt auf
dem Bogen der Temperaturanzeige. Diese Wahl bleibt konstant. Der Einstellbereich
reicht standardmäßig von 60 bis 100 °C; seine Untergrenze und die Schnellauswahl
sind anpassbar.

Die **Temperaturautomatik** verteilt eine gewünschte Steigerung gleichmäßig von
der Start- bis zur Endtemperatur. Benannte Programme machen wiederkehrende
Auswahlen schnell erreichbar. Name und Temperaturfolge stehen zusammen:

| Programm | Werkseinstellung |
|---|---|
| Genusszeit | 80 → 85 → 90 °C |
| Gipfelstürmer | 84 → 92 → 100 °C |
| Ewigkeit | 80 → 84 → 88 → 92 → 96 °C |
| Liegewiese | 75 → 80 → 85 °C |
| Höhenwanderung | 90 → 95 → 100 °C |
| Schnellstarter | 70 → 90 °C |

In den Einstellungen können Sie diese Programme ändern und eigene hinzufügen.
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
Lichtwahl gilt bis zum nächsten Phasenwechsel, höchstens jedoch bis zum Ende
der eingestellten Übersteuerungsdauer.

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
Wird während des Nachlaufs manuell geheizt, hält dessen Uhr an. Ohne neuen
Saunagang läuft anschließend die verbliebene Zeit weiter. Beginnt ein neuer
Gang, entfällt der alte Restnachlauf; nach Gangende beginnt ein vollständiger
neuer Nachlauf. Bereits erbrachte Nachlaufzeit wird einmalig auf die nächste
Kühlung angerechnet.

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
