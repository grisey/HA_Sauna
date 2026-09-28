# Präsenz, Ofen und Phasen

Der Controller führt den Gangzustand und bestimmt daraus die Heizanforderung.
Die Erkennung liefert Beobachtungen; Geräterückmeldungen belegen die
bestätigte Schalterstellung des Ofens. Diese Seite erklärt ihr Zusammenwirken im
[Saunabetrieb](betrieb.md).

## Heizpriorität

Im Automatikbetrieb gilt folgende Reihenfolge:

1. Technischer Schutz, fehlende Heizfreigabe oder Betrieb-AUS fordern Ofen-AUS an.
2. Angeforderte oder laufende Ofenkühlung fordert Ofen-AUS an.
3. Ein aktuell aktiver Gang fordert durchgehend Heizen an.
4. Eine geeignete Türschließung kann die Mindestheizhilfe auslösen.
5. Der Thermostat regelt anhand von Temperatur, Mindestheizzeit und Heizpause.

Die Ganganforderung gilt ab der vorläufigen Erkennung und bleibt nach dem ersten
Aufguss bestehen. Der Controller leitet daraus die jetzt gültige Anforderung ab.
Den Kühlablauf nach einem Gang beschreibt [Ofenkühlung](ofenkuehlung.md).

Eine ausdrückliche vorübergehende Ofenwahl ist eine Bedienhandlung innerhalb
der Automatik. Gewähltes Ofen-AUS hält den Ofen auch während eines aktiven Gangs
aus. Die Wahl endet bei Rückgabe, Phasenwechsel oder einem Wechsel der
automatischen EIN-/AUS-Anforderung, spätestens nach 10 Minuten. Ofen-EIN setzt
die gültige Heizfreigabe voraus; Schutz, Betrieb-AUS und Ofenkühlung behalten
Vorrang. Die [Bedienung](bedienung.md) legt die verfügbaren Zugänge fest.

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
können diese Zuordnung ergänzen. Das [Zeitmodell](zeitmodell.md) erklärt die
Zeitpunkte von Beobachtung, Buchung und tatsächlicher Erkennung.

Die Grundphasen `base_phases` bilden den zeitlichen Betriebsablauf. Für die
Ofenkühlung verwendet die Steuerung daraus die Bereitschaftszeiten
`readiness_pauses`: die Schnittmenge aus zugeordneter Bereitschaft, Betrieb-EIN
und bestätigtem Schütz-AUS. Jede Zeitspanne zählt einmal. Die daraus berechnete
Kühldauer wird beim tatsächlichen Kühlbeginn gespeichert.

## Präsenzquellen im aktuellen Programm

Die aus Temperatur und Feuchte abgeleiteten Personensignale führen die
Gangzuordnung. Eine zusätzlich eingerichtete direkte Präsenzquelle dient
ausschließlich der Beobachtung. Der Controller bleibt für Gangführung und
Aktorbefehle zuständig. Die technischen Beobachtungsverträge erläutern
[Schnittstellen](schnittstellen.md). Die Gerätezuordnung bietet außerdem ein
Audioziel als vorbereitete Bindung für eine spätere Ausgabe.

### Erweiterungsvertrag für direkte Präsenzführung

Die Erweiterung zur direkten Präsenzführung setzt gemeinsam festgelegte Regeln
für Beginn, Unterbrechung, Ende und Quellausfall voraus. Bei einer solchen
Aktivierung führt die direkte Quelle die Gangzuordnung allein und ersetzt die
Türhilfe vollständig. Ein Quellausfall folgt den dafür festgelegten Regeln.
Dieser Schnittstellenvertrag erhält die alleinige Zuständigkeit des Controllers
für Aktorbefehle.
