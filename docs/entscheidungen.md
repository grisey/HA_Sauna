# Entscheidungen und nächste Besprechungspunkte

Grundlage: Nutzerfestlegungen der Besprechung, fortgeschrieben am 18.09.2026.
Der [Messkandidat](kandidat.md) dokumentiert die eingefrorene Kalibrierung.
[Gangmodell](gangmodell.md), [Betrieb](betrieb.md) und
[Parameter](parameter.md) halten die fachlichen Regeln und deren Status fest.
Diese Fortschreibung betrifft die Dokumentation, nicht die Implementierung.

## Vereinbart

**Aufbau:** eigene Home-Assistant-Integration mit eigenem Thermostat und einem
zentralen Python-Ablaufkern. Messung, Erkennung, Gang/Session, Temperaturregelung,
Bedienung und Speicherung haben getrennte Zuständigkeiten.

**Sensoren:** Kanal 3 auf Kopfhöhe der obersten Bank, Kanal 6 etwa 20–30 cm
darunter. Beide sind im Normalbetrieb fest eingebunden. Bei Ausfall eines
Sensors erfolgt Weiterbetrieb mit dem verbleibenden Kanal und Fehlermeldung.
Die Temperaturen werden weder gleichgesetzt noch mit einem erfundenen festen
Höhenoffset umgerechnet. IBS wird nicht verwendet.

**Vorbereitung und Gang:** Durchlüften und anschließende Schließung können die
Personenfrüherkennung stützen. Das Personenmuster legt einen vorläufigen Gang
an, der bereits als Saunagang angezeigt und behandelt wird. Ausschließlich
ein Aufguss bestätigt den Gang. Er kann einen verpassten Start unmittelbar
bestätigt nachholen. Im unveränderten Kandidaten benötigt nur der schwache
Personenpfad vorbereitendes Durchlüften. Die Freigabe eines neuen Gangstarts
bleibt eine Aufgabe der Ablaufsteuerung, nicht des Messdetektors.

**Türereignisse:** schnelle Türmeldungen, Durchlüftungseinordnung und Gangablauf
bleiben getrennt. Kurze Türbetätigung erhält Gang, Zeitbasis und Aufgüsse.
Bestätigtes Durchlüften nach Aufguss ermöglicht den regulären Abschluss.
Ein Türereignis allein belegt keinen Öffnungszweck und keine Personenzahl.

**Zeitbezug und Bestätigungsfrist:** zugeordneter Beginn bei der Türschließung,
erste Erkennung und Aufgussbestätigung bleiben getrennt. Für die vorläufige
Gangerkennung ist eine konfigurierbare Bestätigungsfrist vorgesehen. Als
angemessen wurden 12 oder 13 Minuten ab der zugehörigen Türschließung benannt;
ein fester Ausgangswert ist noch nicht ausgewählt. Die ungefähre Gangdauer von
15 Minuten ist kein automatisches Gangende. Der besprochene Fristablauf und
sein bisheriger Umsetzungsstand stehen im Gangmodell; daraus wird keine neue
allgemeine Diskussion bereits geklärter Gangregeln eröffnet.

**Session und Sessiongrenze:** Die Session ist das übergeordnete Objekt für alle
sessionbezogenen Laufzeitobjekte. Nach Ablauf der konfigurierten Frist seit dem
Ausschalten des Saunabetriebs beginnt beim nächsten Einschalten eine vollständig
neue Session mit neu initialisierten Unterobjekten. Bei früherem Einschalten
bleibt dieselbe Session bestehen. Konfiguration, archivierte Daten und
übergeordnete Schutzfunktionen bleiben vom Sessionwechsel getrennt. Normale
Heizpausen und Zwangskühlung lösen keinen Sessionwechsel aus.

**Ausschalten im Gang:** Ein ausdrücklicher Ausschaltbefehl beendet den Gang
sofort, auch wenn er bereits bestätigt ist. Ende und Grund „ausgeschaltet“
werden festgehalten. Bei rechtzeitigem Wiedereinschalten wird ausschließlich
die Session fortgesetzt, nicht der ausgeschaltete Gang.

**Gangzählung:** Jeder beendete Gang mit mindestens einem zugeordneten Aufguss
zählt genau einmal, unabhängig vom Beendigungsgrund. Die Berechtigung folgt
aus der Bestätigung durch das Aufgussobjekt, nicht aus einem separaten Zählmerker
oder einer Sonderregel für ausgeschaltete Gänge.

**Physischer Schalter:** Der Shelly-Schalter soll sich wie ein einfacher
An-/Ausschalter für den Saunabetrieb anfühlen. Maßgeblich ist die Betriebsfreigabe,
nicht der momentane Heizrelaiszustand oder die noch fortsetzbare Session.

**Heizung:** engere Hysterese im eigenen Thermostat. Bereits im vorläufigen
Gang werden reguläre Hysterese- und betriebliche Ablaufabschaltungen unterdrückt.
Sicherheitsabschaltung und ausdrückliches Ausschalten bleiben übergeordnet.
Dynamische Solltemperaturnachführung innerhalb eines Gangs ist verworfen.
Hysterese und normaler Thermostat-Cooldown sind einfache Konfigurationsvariablen.

**Einheitliche Heizdauer:** Es gilt dieselbe einstellbare Heizzeitgrenze beim
Anheizen und bei späteren Heizabschnitten. Die Unterscheidung zwischen Anheiz-
und kürzerer Folgedauer entfällt. Ein Aufheizmerker wird für diese Zeitwahl
nicht mehr benötigt; eine Darstellung des erstmaligen Aufheizens ist davon
getrennt. Ein neuer numerischer Ausgangswert wird hier nicht festgelegt.

**Heizlaufzeit:** Der Heizzeittimer läuft nur während tatsächlichen Heizens
und pausiert bei idle. Damit ist die Berücksichtigung normaler Idle-Zeiten
vollständig beschrieben. Keine zusätzliche prozentuale Gutschrift auf die
Kühldauer und keine zusätzliche Vergrößerung des Heizbudgets. Die zuvor
vereinbarte Rücksetzung der Heizzeitsumme nach genügend langer zusammenhängender
Ofen-Auszeit bleibt eine lokale Regel innerhalb der Session und ist kein
Sessionwechsel.

**Zwangskühlung:** Eine bereits laufende Zwangskühlung sperrt neue Gangstarts.
Ein bereits laufender Gang wird nicht unterbrochen; eine währenddessen fällige
Zwangskühlung bleibt bis zu seinem Ende ausstehend. Stark gedimmtes Licht zeigt
die laufende Zwangskühlung an. Die zwischenzeitliche Interpretation, Zwangskühlung
würde einen laufenden Gang unterbrechen, ist ausdrücklich verworfen.

**Nachlauf:** Betriebsunterbrechungen verändern einen laufenden Nachlauf nicht.
Er behält seinen ursprünglichen Endzeitpunkt. Seine verstrichene Dauer wird
vollständig und ohne Doppelzählung auf die zugehörige Zwangskühlungsdauer
angerechnet. Eine noch nicht verstrichene Nachlaufzeit wird nicht vorweg
angerechnet. Bei einer neuen Session gelten die gemeinsamen Initialisierungsregeln.

**Getrennte Zeitmechanismen:** Sessionfrist, Thermostat-Cooldown, Nachlauf,
Zwangskühlung und lokale Rücksetz-Auszeit behalten ihre jeweilige Bedeutung
und eigene Parameter. Die vereinbarte Nachlaufanrechnung ist eine Beziehung
zwischen Abläufen, keine Gleichsetzung ihrer Fristen.

**Parameter und Anzeige:** notwendige Werte direkt in der Integration
konfigurierbar machen. Genau eine konsumierte Quelle je Einstellung, gemeinsame
Validierung und Speicherung. Sinnvolle Relationen statt unnötiger unabhängiger
Absolutwerte verwenden; fachlich verschiedene Fristen nicht zusammenlegen.
Abgeleitete Werte und Restzeiten nur lesbar anzeigen. Phase und Bestätigungsstand
stammen aus dem Ablaufkern. Die automatische relative Erkennungsanpassung ist
noch kein freigegebener Ersatzdetektor.

**Daten:** gesicherter Betriebszustand und Sessionarchiv unabhängig vom Recorder.
Speicherverfahren und Datenumfang werden gesondert besprochen.

## Korrekturen früherer Annahmen

Die Gefäßentnahme um 22:39:40 ist das zusätzlich bestätigte 15. Türereignis der
Referenzsession. Nicht jede Öffnung nach Aufguss beendet einen Gang. Manuelle
Eingriffe waren einmalige Reparaturversuche, keine konkurrierenden Sollvorgaben
für die Automatik. Normale Hystereseabschaltungen im Gang wurden durch die
vereinbarte zustandsabhängige Heizbehandlung ersetzt.

Ein durch Ausschalten beendeter Gang wird nicht wiederhergestellt, zählt aber
bei zugeordnetem Aufguss wie jeder andere beendete bestätigte Gang. Eine
Betriebsunterbrechung pausiert oder erneuert einen laufenden Nachlauf nicht.
Die frühere Frage nach einer solchen Wiederaufnahme ist erledigt.

Die vorgeschlagene Kopplung von Zwangskühlungsdauer und Rücksetz-Auszeit bleibt
verworfen. Ebenso entfallen separate erste und spätere Heizzeitgrenzen sowie
die zusätzliche Idle-Anrechnung zu beispielsweise 50 Prozent. Keine dieser
verworfenen Varianten wird über eine Parametrierung wieder eingeführt.

Der Vorrang laufender Zwangskühlung betrifft neue Gangstarts. Er bedeutet nicht,
dass eine erst während eines Gangs fällige Kühlung diesen Gang beenden oder
seine Heizbehandlung unterbrechen darf.

## Nächste Besprechungsblöcke

1. **Darstellung:** aktuelle Sessionansicht, sichtbare Phase und Bestätigungsstand,
   zugeordnete Gangdauer und Anzahl, getrennte Heizaktivität und Restzeiten,
   Messkurven beider Höhen mit Tür-, Aufguss- und Gangereignissen sowie
   Fehlerhinweise. Die konkrete Anordnung und Benennung werden besprochen;
   hier wird noch kein neues GUI-Konzept als beschlossen festgelegt.
2. **Datenerfassung und Speicherung:** Messauflösung und Datenumfang,
   Ereignis- und Entscheidungsprotokoll, Sessionarchiv und historische Ansicht,
   gesicherter Betriebszustand und Wiederanlauf nach HA-Neustart,
   Aufbewahrung, Export und Sicherung; siehe [Speicherblock](speicherung.md).

Konkrete Einstellungswerte werden über die Parameterverwaltung festgelegt.
Die technische Umsetzung muss weiterhin ihre Voraussetzungen, insbesondere
Schutzgrenzen, Aktorrückmeldungen und Ersatztemperatur bei Ausfall von Kanal 3,
explizit behandeln; der Besprechungsfortschritt ist keine Live-Betriebsfreigabe.
Offene technische Entscheidungen oder bislang nur vorgeschlagene Detektoränderungen
werden nicht stillschweigend durch angenommene Standardwerte ersetzt.

Die Besprechung bleibt bei einem Thema. Bereits geklärte Gang-, Heizzeit- und
Sessionregeln werden nicht erneut als Varianten angeboten. Dokumentation,
Implementierung und Messbefund bleiben getrennt gekennzeichnet.
