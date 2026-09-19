# Entscheidungen und Umsetzungsstand

## Ergänzung vom 19.09.2026

Der Nutzer hat die bisher offenen Aufhebungsfolgen präzisiert: Ohne Aufguss
bis zum Ende der Bestätigungsfrist wird der vorläufige Gang vollständig
aufgehoben, als sei kein Gang gestartet worden. Keine Gangzählung, kein
Nachlauf und keine besondere Heizbehandlung bleiben zurück. Dasselbe gilt
für bestätigtes Durchlüften während eines noch unbestätigten Gangs.
Diagnoseereignisse dürfen die verworfene Erkennung nachvollziehbar erhalten;
sie stellen keinen tatsächlich gezählten oder abgeschlossenen Gang dar.
Weitere Personensignale derselben Episode gewähren keine neue volle Frist;
ein späterer eindeutiger Aufguss kann die erhaltene Schließung verwenden.

Die anschließende Klarstellung ist maßgeblich: Während einer Session sind
Änderungen der Grundkonfiguration nicht vorgesehen. Konfigurationsdialog und
Parameterentitäten sperren diese Änderungen zentral. Dadurch gibt es keine
Neuberechnung laufender Fristen durch geänderte Grundparameter. Eine kurze
Betriebsunterbrechung beendet diese Sperre nicht; die Sessionfrist muss ablaufen.

Grundlage: Nutzerfestlegungen der Besprechung, fortgeschrieben am 18.09.2026.
Der [Messkandidat](kandidat.md) dokumentiert die eingefrorene Kalibrierung.
[Gangmodell](gangmodell.md), [Betrieb](betrieb.md), [Parameter](parameter.md),
[Darstellung](darstellung.md) und [Speicherung](speicherung.md) halten die
fachlichen Regeln und deren Status fest. Diese Fortschreibung betrifft die
Dokumentation, nicht die Implementierung.

## Vereinbart

**Aufbau:** eigene Home-Assistant-Integration mit eigenem Thermostat und einem
zentralen Python-Ablaufkern. Messung, Erkennung, Gang/Session, Temperaturregelung,
Bedienung und Speicherung haben getrennte Zuständigkeiten.

**Sensoren:** Kanal 3 auf Kopfhöhe der obersten Bank, Kanal 6 etwa 20–30 cm
darunter. Beide sind im Normalbetrieb fest eingebunden. Bei Ausfall eines
Sensors erfolgt Weiterbetrieb mit dem verbleibenden Kanal und Fehlermeldung.
Die Temperaturen werden weder gleichgesetzt noch mit einem erfundenen festen
Höhenoffset umgerechnet. IBS wird nicht verwendet.

**Entitätszuordnung:** sämtliche verknüpften externen Entitäten sind im
Konfigurationsbereich auswählbar und später neu zuordenbar. Obere und untere
Messposition, Heizaktor, physische Bedienquelle und Saunalicht werden als Rollen
mit geeigneten Entitäten verbunden; konkret installierte Entity-IDs gehören
nicht in den Ablaufcode. Gleiches gilt für ergänzende Status-, Medien- oder
Benachrichtigungsanbindungen, soweit sie verwendet werden. Die Zuordnung ist
zentral gespeichert; Auswahleignung wird geprüft. K3/K6 bleiben Referenznamen
des eingefrorenen Kandidaten, keine fest vorgegebenen produktiven Quellnamen.
Einzelheiten: [Entitätszuordnung](parameter.md#verknüpfte-entitäten-auswählen).

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
Ein bereits laufender Gang wird nicht unterbrochen. Wird seine Heizzeitgrenze
erreicht, folgt nach Gangende zuerst der Nachlauf und danach nur noch die um
diesen Nachlauf reduzierte Restkühlzeit. Bei Restzeit null entfällt der zusätzliche
Kühlabschnitt. Dies verwendet die allgemeine Nachlaufanrechnung, keine weitere
Sonderlogik. Stark gedimmtes Licht zeigt die laufende Zwangskühlung an.

**Nachlauf:** Betriebsunterbrechungen verändern einen laufenden Nachlauf nicht.
Er behält seinen ursprünglichen Endzeitpunkt. Seine verstrichene Dauer wird
vollständig und ohne Doppelzählung auf die zugehörige Zwangskühlungsdauer
angerechnet. Eine noch nicht verstrichene Nachlaufzeit wird nicht vorweg
angerechnet. Bei einer neuen Session gelten die gemeinsamen Initialisierungsregeln.

**Getrennte Zeitmechanismen:** Sessionfrist, Thermostat-Cooldown, Nachlauf,
Zwangskühlung und lokale Rücksetz-Auszeit behalten ihre jeweilige Bedeutung
und eigene Parameter. Die vereinbarte Nachlaufanrechnung ist eine Beziehung
zwischen Abläufen, keine Gleichsetzung ihrer Fristen.

**Parameter:** notwendige Werte direkt in der Integration konfigurierbar machen.
Genau eine konsumierte Quelle je Einstellung, gemeinsame Validierung und
Speicherung. Sinnvolle Relationen statt unnötiger unabhängiger Absolutwerte;
fachlich verschiedene Fristen nicht zusammenlegen. Abgeleitete Werte und
Restzeiten nur lesbar anzeigen. Die automatische relative Erkennungsanpassung
ist noch kein freigegebener Ersatzdetektor.

**Darstellung:** Der vorgeschlagenen Sessionansicht wurde zugestimmt. Sie
verbindet aktuellen Ablauf und Zeitverlauf, trennt Heizaktivität von der Phase
und zeigt Messwerte beider Höhen, Türereignisse, Aufgüsse, Gangintervalle sowie
Fehlerhinweise. Vorläufiger und bestätigter Gang sind Kennzeichnungen desselben
Intervalls. Einstellungen und Kalibrierung sind getrennt zugänglich.
Einzelheiten: [Darstellung](darstellung.md).

**Archiv und Export:** Sessiondaten werden langfristig in voller empfangener
Auflösung unabhängig vom Recorder gespeichert. Keine automatische Verdichtung
oder altersbedingte Löschung der Originalmessungen. Der technische Vorschlag
ist angenommen: eigene SQLite-Datenbank unter dem HA-Konfigurationsverzeichnis,
konsistente Einbeziehung ins HA-Backup und ZIP-Export mit Messreihen sowie
maschinenlesbaren Session-/Ereignisdaten über den Downloadbutton im
Einstellungsbereich. Die historische Entitätszuordnung bleibt nachvollziehbar.
Konkretes Schema, Archivschreiber und Backup-/Restore-Abnahme folgen in der
Umsetzung; die Zustimmung ist kein bereits bestandener Funktionstest.

**HA-Neustart:** Automatische Wiederaufnahme des laufenden Betriebs ist nicht
notwendig; sie soll nur bei sehr einfacher Umsetzung ergänzt werden. Die
Erstfassung kommt ohne zusätzliche automatische Betriebsfortsetzung aus.
Dauerhaft lesbare Sessiondaten bleiben verpflichtend. Historienwiederherstellung
und Fortsetzung einer Heizregelung sind unterschiedliche Aufgaben. Ein
zusätzlicher komplexer Wiederanlaufautomat wird nicht zur Pflicht gemacht.

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
seine Heizbehandlung unterbrechen darf. Nach dem Gang wird zuerst der Nachlauf
verarbeitet; dessen Anrechnung verhindert eine doppelte volle Kühlpause.

## Verbleibende Umsetzung

Darstellungsaufbau, auswählbare Entitätsverknüpfungen, langfristige Vollauflösung,
SQLite-Ablage, konsistentes HA-Backup, ZIP-Download und der Verzicht auf eine
zwingende Neustartfortsetzung sind festgelegt. Sie werden nicht erneut als
offene Varianten angeboten. Details zur vorgesehenen Ablage stehen im
[Speicherblock](speicherung.md); ihre Dokumentation behauptet keine bereits
implementierte Datenbank, Backup-Prüfung oder Benutzeroberfläche.

Konkrete Einstellungswerte werden über die Parameterverwaltung festgelegt.
Die technische Umsetzung muss weiterhin ihre Voraussetzungen, insbesondere
Schutzgrenzen, Aktorrückmeldungen und Ersatztemperatur bei Ausfall von Kanal 3,
explizit behandeln; der Besprechungsfortschritt ist keine Live-Betriebsfreigabe.
Offene technische Entscheidungen oder bislang nur vorgeschlagene Detektoränderungen
werden nicht stillschweigend durch angenommene Standardwerte ersetzt.

Die Besprechung bleibt bei einem Thema. Bereits geklärte Gang-, Heizzeit- und
Sessionregeln werden nicht erneut als Varianten angeboten. Dokumentation,
Implementierung und Messbefund bleiben getrennt gekennzeichnet.
