# Architektur

Pro Sauna führt ein Controller die Sitzung und ihre Regelentscheidungen.
Die Runtime ordnet Meldungen und Bedienungen zeitlich ein; der Geräteadapter
liest Home-Assistant-Quellen, sendet Gerätebefehle und verarbeitet Rückmeldungen.

[Designentscheidungen](entscheidungen.md) · [Betrieb](betrieb.md) ·
[Gangmodell](gangmodell.md) · [Ofenkühlung](ofenkuehlung.md)

## Konfiguration und Bedienung

`bindings.py` beschreibt die Rollen externer Entitäten. `config_flow.py` ordnet
ihnen konkrete Quellen und Geräte zu und prüft ihre Metadaten. `Configuration`
in `runtime.py` fasst diese Zuordnung mit den Einstellungen zusammen;
`defaults.json` führt die einstellbaren Vorgaben und Metadaten für Parameter,
Programme, Instanzen und Darstellung. `core/defaults.py` validiert den Katalog;
`core/parameters.py` bildet daraus den unveränderlichen Parameterstand.
`core/program_catalog.py` validiert benannte Temperaturprogramme.
Die historischen Altformatadapter bleiben von den aktuellen Vorgaben getrennt.
API, Formulare und HA-Entitäten konsumieren die aufgelösten Werte und Metadaten.

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

Der Geräteadapter behält tatsächlich laufende Ofen- und Lichtdienste auch nach
Ablauf ihrer Wartefrist oder Abbruch des aufrufenden Befehls. Ein noch laufendes
Ofen-EIN wird nicht wiederholt; angefordertes AUS folgt nach dessen tatsächlichem
Abschluss. Entladen und Gerätewechsel benötigen den erfolgreichen Abschluss des
abschließenden Ofen-AUS. Andernfalls bleiben Laufzeit, Archiv und Listener für
die ausstehende Ausgabe und einen erneuten Abschluss erhalten.
Auch ein abgelehntes Entladen lässt den Saunabetrieb ausgeschaltet.

Eine wegen eines laufenden Lichtdiensts übersprungene Ausgabe erhält nach dessen
tatsächlichem Abschluss genau einen Folgezyklus. Ein zuvor gemeldetes Licht-AUS
ersetzt keinen abschließenden AUS-Dienst nach einem eigenen neueren EIN-Auftrag.
Die Lichtübergabe unterscheidet einen noch offenen oder fehlgeschlagenen Dienst
von einem erfolgreich abgeschlossenen AUS mit fehlender Rückmeldung.

Periodische Aufrufe werden vor der Laufzeitsperre zusammengefasst; höchstens ein
Timerzyklus läuft oder wartet. Folgezyklen prüfen bekannte laufende Lichtdienste
ohne erneute Wartefrist. Die eigenen HA-Entitäten schreiben nur geänderte Zustände,
Attribute oder Verfügbarkeit; zeitabhängige Attribute bleiben darin enthalten.

Geräteereignisse bleiben vollständig in Empfangsreihenfolge gepuffert. Ein
einziger laufender oder wartender Aufruf verarbeitet diese Eingänge. Während
eines Geräteaufrufs neu eingegangene Ereignisse erhalten anschließend einen
neuen Eintritt in die Laufzeitsperre hinter bereits wartenden Bedienhandlungen.

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
