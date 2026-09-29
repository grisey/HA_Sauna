# Präsenz, Ofen und Phasen

Der Controller führt den Gangzustand und bestimmt daraus die Heizanforderung.
Die Erkennung liefert Beobachtungen; Geräterückmeldungen belegen die
bestätigte Schalterstellung des Ofens.

## Heizpriorität

Im Automatikbetrieb gilt folgende Reihenfolge:

1. Technischer Schutz, fehlende Heizfreigabe oder Betrieb-AUS fordern Ofen-AUS an.
2. Angeforderte oder laufende Ofenkühlung fordert Ofen-AUS an.
3. Ein aktuell aktiver Gang fordert durchgehend Heizen an.
4. Eine geeignete Türschließung kann die Mindestheizhilfe auslösen.
5. Der Thermostat regelt anhand von Temperatur, Mindestheizzeit und Heizpause.

Die Ganganforderung gilt ab der vorläufigen Erkennung und bleibt nach dem ersten
Aufguss bestehen.

[Kühlablauf](ofenkuehlung.md)

Eine ausdrückliche vorübergehende Ofenwahl ist eine Bedienhandlung innerhalb
der Automatik. Gewähltes Ofen-AUS hält den Ofen auch während eines aktiven Gangs
aus. Die Wahl endet bei Rückgabe, Phasenwechsel oder einem Wechsel der
automatischen EIN-/AUS-Anforderung, spätestens nach 10 Minuten. Ofen-EIN setzt
die gültige Heizfreigabe voraus; Schutz, Betrieb-AUS und Ofenkühlung behalten
Vorrang.

[Bedienrechte](bedienung.md)

In der Betriebsart Manuell folgt die Heizanforderung der ausdrücklichen
Ofenwahl. Gang- und Präsenzereignisse begleiten dort den Verlauf. Die
Betriebsart bleibt bis zur nächsten ausdrücklichen Wahl bestehen; technischer
Schutz und Betrieb-AUS behalten Vorrang.

## Türschluss als Mindestheizhilfe

Die Türhilfe bewertet jede Türöffnung als eigenen Vorgang. Eine geeignete
Öffnung erfolgt bei eingeschaltetem Automatikbetrieb in der Phase Aufheizen oder
Bereit. Der zugehörige Türschluss kann einmalig Heizen anfordern. Ein Wechsel
in einen Saunagang oder eine angeforderte beziehungsweise laufende Ofenkühlung
verwirft die Eignung dieses Vorgangs. Eine spätere geeignete Türöffnung beginnt
einen neuen Vorgang.

Mit der tatsächlichen Heizbestätigung geht die Anforderung in die laufende
Mindestheizzeit über, standardmäßig 10 Minuten. Bereits laufendes Heizen behält
seinen ursprünglichen Beginn. Bis zur Bestätigung bleibt die Türanforderung
eine widerrufbare Anforderung; die Heizzeiterfassung folgt den tatsächlichen
Rückmeldungen. Betrieb-AUS und eine ausdrückliche Ofen-AUS-Wahl verwerfen die
Türanforderung.

## Tatsächlicher Zustand und Phasenansicht

Die aktuelle Heizanforderung beschreibt den jetzt gewünschten Ofenzustand.
Bestätigte Schalterrückmeldungen dokumentieren den tatsächlichen Verlauf der
Schalterstellung in `contactor_history`. Eine zusätzlich eingerichtete Leistungs-
oder Heizrückmeldung liefert die entsprechende Messgrundlage für Heizzeit und
Verbrauch; die [Rückmeldequellen](betrieb.md#heizzeit-timer-und-energie) sind
nach ihrer Verfügbarkeit geordnet. Die historische Phasenansicht fasst den Verlauf in
zusammenhängende, überlappungsfreie Abschnitte zusammen. Später erkannte Ereignisse
können diese Zuordnung ergänzen.

[Zeitpunkte von Beobachtung, Buchung und Erkennung](zeitmodell.md)

Die Grundphasen `base_phases` bilden den zeitlichen Betriebsablauf. Für die
Ofenkühlung verwendet die Steuerung daraus die Bereitschaftszeiten
`readiness_pauses`: die Schnittmenge aus zugeordneter Bereitschaft, Betrieb-EIN
und bestätigtem Schütz-AUS. Jede Zeitspanne zählt einmal. Die daraus berechnete
Kühldauer wird beim tatsächlichen Kühlbeginn gespeichert.

## Präsenzquellen im aktuellen Programm

Die aus Temperatur und Feuchte abgeleiteten Personensignale führen die
Gangzuordnung. Eine zusätzlich eingerichtete direkte Präsenzquelle dient
ausschließlich der Beobachtung. Der Controller bleibt für Gangführung und
Aktorbefehle zuständig. Die Gerätezuordnung bietet außerdem ein
Audioziel als vorbereitete Bindung für eine spätere Ausgabe.

[Beobachtungsverträge](schnittstellen.md#präsenz-und-verbraucherereignisse)

### Erweiterungsvertrag für direkte Präsenzführung

Die konkrete Gangführung aus direkter Präsenz entsteht erst nach Prüfung der
Sensorposition und des realen Sensorverhaltens. Eine einzelne Erkennung löst
nicht unmittelbar einen Saunagang aus. Beginn, Unterbrechung, Ende und
Quellausfall sind für eine spätere Aktivierung gemeinsam festzulegen;
der heutige Pfad bleibt beobachtend.

Die spätere direkte Quelle soll die Gangzuordnung allein führen und die Türhilfe
vollständig ersetzen. Das ist ein Erweiterungsziel; die konkrete Start- und
Endauswertung ist noch offen. Die zwölfminütige Aufgussbestätigungsfrist gehört
zum aktuellen Proxyverfahren und wird bei direkter Präsenzführung nicht benötigt.
Die spätere Präsenzkopplung der Lüftungsbestätigung bleibt bis zu den Sensortests
unkonkretisiert. Der Controller bleibt für Aktorbefehle zuständig.

[Gangführung und Lüftungsbestätigung](gangmodell.md#beginn-und-bestätigung)
