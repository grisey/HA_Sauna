# Architektur

Jede konfigurierte Sauna besitzt einen führenden Controller. Er verwaltet die
laufende Sitzung und trifft ihre Regelentscheidungen. Die Runtime ordnet die
eingehenden Meldungen und Bedienungen zeitlich ein. Der Geräteadapter verbindet
diesen Ablauf mit Home Assistant: Er liest die konfigurierten Quellen, sendet
Gerätebefehle und verarbeitet deren Rückmeldungen.

[Designentscheidungen](entscheidungen.md) · [Betrieb](betrieb.md) ·
[Gangmodell](gangmodell.md) · [Ofenkühlung](ofenkuehlung.md)

## Konfiguration und Bedienung

`bindings.py` beschreibt die Rollen externer Entitäten. `config_flow.py` ordnet
ihnen konkrete Quellen und Geräte zu und prüft ihre Metadaten. `Configuration`
in `runtime.py` fasst diese Zuordnung mit den Einstellungen zusammen;
`core/parameters.py` definiert Standardwert, Einheit und zulässigen Bereich der
Parameter und bildet ihren unveränderlichen Stand.
`core/program_catalog.py` beschreibt benannte Temperaturprogramme.

`settings.py` ist der gemeinsame Schreibweg für Einstellungen. Er prüft den
vollständigen Änderungskandidaten und übernimmt gültige Live-Temperaturänderungen
in den bestehenden Controller. Dessen Sitzung und Fristen bleiben dabei erhalten.
Technische Einstellungen und Gerätezuordnungen werden nach Sitzungsende
geändert.

[Änderbarkeit der Einstellungen](parameter.md) · [Bedienrechte](bedienung.md)

Darstellung und Protokollstufe besitzen eigene Änderungswege. Ihre Wirkung bleibt
auf Anzeige beziehungsweise Protokollierung beschränkt. Die Konfiguration wird
in den Optionen des Home-Assistant-Eintrags gespeichert.

## Quellen und Erkennung

`device.py` übernimmt Quellwert, Messrolle, Quellenidentität und Empfangszeit.
Ein Gerätezeitstempel wird übernommen, wenn ihn die Quelle tatsächlich liefert.
`runtime.py` verarbeitet empfangene Meldungen in ihrer Warteschlange und stellt
gleichzeitige Messkanäle gemeinsam für das Erkennungsraster bereit.

`core/moisture.py` berechnet den absoluten Wassergehalt aus einem gültigen
Temperatur-/Feuchtepaar derselben Messposition. `sensor.py` stellt diese
abgeleiteten Werte als Diagnoseentitäten bereit. `core/detector.py` wertet
bereits empfangene Messungen in begrenzten Zeitfenstern aus und meldet
Erkennungsereignisse an den Ablaufkern. Der Controller liefert den
Erkennungskontext nach jedem verarbeiteten Signal neu.

[Erkennung](erkennung.md) · [Zeitmodell](zeitmodell.md)

`core/timeline.py` ordnet die Ereignisse Gängen zu. Die Proxy-Präsenz folgt diesen
Zuordnungen. Direkte Präsenzmeldungen werden mit ihrer eigenen Quelle als
zusätzliche Beobachtungen geführt.

[Datenverträge](schnittstellen.md)

## Regelung und Geräteausgabe

`core/controller.py` verbindet die Gangzuordnung mit dem Betriebszustand und der
Ofenkühlung. `core/thermostat.py` wertet die Heizanforderung unter den geltenden
Schutzbedingungen aus. Die vorübergehende Türhilfe liegt in
`core/temporary_door_heat.py`. Der Geräteadapter setzt die Entscheidung um und
führt die beobachteten Zustände an den Controller zurück.

[Manuelle Ofenwahl und Rückkehrpunkte](betrieb.md#bedienhandlungen-und-betriebsart)

`core/heating.py` zählt Heizintervalle anhand der verfügbaren Rückmeldung.
`core/power.py` und `core/energy.py` kennzeichnen die Mess- oder Schätzgrundlage
der Energieangabe. Die mechanische Timeranzeige verwendet ihre eigene
Schützrückmeldung.

[Heizzeit, Timer und Energie](betrieb.md#heizzeit-timer-und-energie)

`core/light.py` berechnet Zielhelligkeiten aus Betriebsphase, Temperatur und
Tageslicht. `core/light_output.py` plant Übergänge und manuelle Lichtwahlen.
Der Geräteadapter führt die zugehörigen Dienste aus und unterscheidet eigene
Rückmeldungen von externer Lichtbedienung. Der Lichtnachlauf besitzt seine
eigene Frist und ist von der Ofenkühlung getrennt.

## Lebenszyklus, Archiv und Anzeige

`runtime.py` koordiniert Start, laufende Verarbeitung und Abschluss. Beim
Entladen werden Sitzung und Archiv abgeschlossen; ein eigenständiger
Ofen-AUS-Versuch gehört auch bei einem Archivfehler zum Abschluss. Ein erneuter
Start von Home Assistant stellt die archivierte Historie bereit. Der Saunabetrieb
beginnt mit einem erneuten Einschaltauftrag.

`archive.py` schreibt Originaldaten und Zustandsrevisionen nach SQLite.
`backup.py` koordiniert die Schreibpause für das Home-Assistant-Backup.
`api.py` stellt den berechtigten Zugriff auf Status, Archiv und Export bereit.

[Archivschema, Backup und Zugriff](speicherung.md)

`core/phases.py` leitet die Phasenansicht aus den fachlichen Objekten und der
Schützspur ab. `presentation.py`, `frontend.py` und `panel.js` bereiten den
führenden Zustand für die Oberfläche auf. `core/warmup.py` berechnet eine
Anzeigeprognose aus der letzten abgeschlossenen Aufheizphase und dem aktuellen
Messverlauf. Neue Messungen verändern die Schätzung; Statusabfragen lesen das
vorbereitete Ergebnis. Die Regelung verwendet ihre eigenen Messwerte und
Freigaben.

[Datenladen und Zeichnung](livekurve.md)
