# Entscheidungen und Umsetzungsstand

## Geltender Stand vom 19.09.2026

Die späteren Nutzerkorrekturen sind in [Betrieb](betrieb.md),
[Gangmodell](gangmodell.md), [Zeitmodell](zeitmodell.md),
[Parameter](parameter.md), [Oberfläche](darstellung.md) und
[Speicherung](speicherung.md) konsolidiert. Diese Darstellung ersetzt die zuvor
widersprüchlichen Zwischenstände. Der ursprüngliche Verlauf bleibt in Git erhalten.

- Unbestätigte Erkennung bei Fristablauf oder Durchlüften vollständig aufheben,
  ohne Gangzählung oder Nachlauf. Bestätigte beendete Gänge zählen genau einmal.
- Grundkonfiguration bleibt während der Session gesperrt, auch bei kurzem Aus/Ein.
- Bereitschaftsziel ist Solltemperatur plus einstellbarer Aufschlag, Standard 5 °C;
  einstellbare Hysterese Standard 3 °C. Keine Zielnachführung innerhalb eines Gangs.
- Tatsächliches Heizbudget zunächst 90 Minuten, nach erster abgeschlossener Kühlung
  einmalig um 30 Minuten verringert, danach konstant. Kühlvorgabe 15 Minuten.
  Alle Zahlen sind einstellbare Defaults. Neue Session erhält wieder das Anfangsbudget.
- Thermostat-Cooldown Standard 5 Minuten; Mindestheizzeit Standard 10 Minuten ab
  tatsächlichem Einschalten. Nachlauf, Kühlung, Aus und technische Schutzabschaltung
  haben Vorrang. Nachlauf hält den Ofen aus und sperrt neue Gänge.
- Laufender Gang wird von fälliger Kühlung nicht abgebrochen. Danach Nachlauf und
  nur die verbleibende Kühlung. Nachlauf wird einmal vollständig angerechnet.
- Türöffnung in Bereitschaft/Aufheizen verschiebt fällige Kühlung: standardmäßig
  10 Minuten ab Öffnung, falls offen geblieben; bei rechtzeitiger Schließung
  stattdessen 4 Minuten ab Schließung. Ohne Personensignal beginnt danach die
  fällige Kühlung. Bereits laufende Kühlung wird nicht rückgängig gemacht.
- Mehr als 105 °C länger als 10 Minuten führt zu doppelter Kühlvorgabe ohne
  Sessionabbruch. Grenze, Dauer und Faktor sind einstellbar. Die bestehenden
  Gang-/Nachlaufregeln gelten. Sofortige temperaturbedingte Sessionabschaltung
  ist verworfen. Bestätigte technische Dauerausfälle bleiben ein eigener Schutzgrund.
- Mechanischer Timer trennt physisch den Strom. Die Integration führt eine
  getrennte Schätzung von standardmäßig 4 Stunden, mit einstellbarer Vorwarnung.
  Die tatsächliche Heizrückmeldung bleibt maßgeblich.
  Präzisierung: ausschließlich Anzeige/Erinnerung zum erneuten Einstellen des
  Drehschalters, keinerlei Steuer- oder Schutzwirkung aus der geschätzten Frist.
- Zwei Hauptansichten: Übersicht mit einfacher Steuerung und eigenem Blatt für
  Sessionverlauf samt Archivauswahl; Details mit übersichtlich sortiertem Betrieb,
  Fristen, Fehlern, eigener Erkennungskontrolle und Einstellungen/Export.
- Gestaltung entspricht der gelieferten Diagrammvorlage. Zoom, Achsen, Tooltips
  und Aktualisierung dürfen verbessert werden. Erkennungsdetails stehen separat.

## Präzisierung für die erste Testinstallation

Am realen Ofen sind derzeit Schützstellung und Temperaturen verfügbar. Ohne
Leistungsmessung läuft der Heizzähler bei Schütz EIN. Eine Leistungsmessung bleibt
optional verfügbar und hat bei gültigem Messwert Vorrang. Die Schätzung des
mechanischen Timers löst weiterhin keinerlei Steuerung aus. Eine Erkennung
anhand nachlassenden Temperaturanstiegs soll einen tatsächlichen Übergang
erkennen; die feste 0,5-Grad-/5-Minuten-Regel wurde verworfen. Dieser seltene
Grenzfall ist auf ausdrücklichen Nutzerwunsch für die erste Testinstallation
zurückgestellt, damit die Prüfung der Grundfunktion Vorrang erhält.

Sessionenergie wird ohne Leistungsmesser aus Heizzeit und konfigurierbarer
Ofenleistung geschätzt (Standard 4,5 kW). Mit Messgerät haben gemessene
Leistungswerte Vorrang. Messlücken und geschätzte Anteile bleiben erkennbar.
Installation erfolgt ausschließlich durch den Nutzer über HACS. SSH darf nur
lesend für Status und Logs verwendet werden; Schreiben darüber ist verboten.

Der Nutzer beauftragt die Vorbereitung einer ersten Testinstallation auf seinem
Home-Assistant-System. Ofenaktivierung und Änderungen an der bestehenden realen
Steuerung werden dadurch nicht stillschweigend durchgeführt.

## Unveränderte Architekturentscheidungen

Ein führender Session-/Ablaufkern; gleiche Bedienung durch physischen Eingang,
HA-Entitäten und Panel. Eine neue Session initialisiert ihre Unterobjekte gemeinsam.
Keine Rückdatierung realer Schaltbefehle. Konfiguration, Schutzgründe und Archiv
überleben den Sessionwechsel. Kein automatischer Betriebsstart nach HA-Neustart.

Erkennung verwendet beide Messhöhen getrennt. Ein-Sensor-Erkennung bleibt möglich
mit Fehleranzeige. Keine Temperaturmittelung oder erfundener Höhenoffset, keine
IBS-Sensoren. Die Heizregelung benötigt einen gültigen oberen Wert; eine sichere
untere Ersatztemperatur wurde nicht festgelegt und wird nicht angenommen.
Die normale Messwertgültigkeit muss zum Meldeabstand passen. Nach Klärung einer
zu kurzen lokalen Gültigkeitsfrist wurde die zusätzliche Überbrückung nach
Gültigkeitsende verworfen. Die Fehler-Bestätigungsfrist betrifft ausschließlich
die spätere Verriegelung. Bei fehlender gültiger Regeltemperatur pausiert die
Heizung sofort. Bereits gestartete Kühlung und Nachlauf haben auch bei einem
widersprüchlichen Gangsignal Vorrang.

Vollauflösung und Ereignisrevisionen in SQLite unter HA-Konfiguration. Echte
HA-Backup-Einbindung mit Restore-Prüfung. Authentifizierter ZIP-Download in den
Einstellungen; keine Archive unter www. Keine automatische Löschung oder Verdichtung.

## Grenzen und verworfene Varianten

Keine zusätzliche prozentuale Idle-Gutschrift, keine dynamische Vergrößerung des
Heizbudgets, kein automatisches Gangende nach ungefähr 15 Minuten. Keine separate
Schreibquelle für Bestätigung oder Zählbarkeit. Keine adaptive Erkennung als
unbesprochener Ersatz des eingefrorenen Kandidaten. Keine voreilige rückwirkende
Zuordnung des Gangendes zur Türöffnung.

Auf spätere Nutzeranweisung erhalten alle notwendigen Einstellungen Standardwerte. Bereits gespeicherte örtliche Einstellungen bleiben erhalten. Prognosen der Aufheizzeit sind nicht aus Softwaretests als zuverlässige Regel abzuleiten.

Der Auftrag autorisiert Repositoryarbeit und isolierte Tests, keine reale
Ofenaktivierung, keine Produktionsinstallation, keine Versionserhöhung, kein
Release und keine Lizenzwahl. Privater Recorderexport und Nutzerkonfiguration
bleiben lokal. Ausgeführte Tests und noch fehlende Hardwareabnahme werden
getrennt im [Abnahmebericht](abnahme.md) benannt.


## Korrekturen nach der ersten Testinstallation

Die Timeranzeige hält bei Betrieb-Aus an. Erst nach abgeschlossener Sitzung mit
gezählten Saunagängen beginnt sie beim nächsten Einschalten neu. Leere Testsitzungen
behalten ihre Restzeit. Bereitschaftswerte gehören nur in Details; Übersicht und
Temperaturtasten bleiben auch während gesperrter Einstellungen sichtbar.

Die vorherige Temperatursteigerung wird wieder aufgenommen: einstellbare Schrittweite
je gezähltem Gang (Default 5 °C), frei wählbare Endtemperatur, Startwert aus der
Solltemperatur. Ohne Endtemperatur bleibt der Betrieb konstant.

Die Protokollierung ist in den Einstellungen als ERROR, INFO (Standard) oder DEBUG
wählbar und wirkt sofort. Gerätezuordnungen sind nachträglich änderbar, sobald keine
Sitzung offen ist. Entkoppelter Taster und Schütz sind getrennte Rollen; Loslassen
eines Tasters darf nicht ausschalten.

## Weitere Korrekturen im laufenden Test

Solltemperatur, Steigerungsrate und Endtemperatur sind live änderbar. Eine manuelle Solltemperatur ersetzt sofort das aktuelle Ziel; weitere gezählte Gänge erhöhen ab diesem neuen Ausgangspunkt. Historische Gangzählungen werden nicht erneut addiert. Gang-, Nachlauf-, Kühl- und Heizsperren behalten immer Vorrang; keine Änderung lädt die Integration neu.

Die Übersicht zeigt nur eine zur Phase passende Zeit. Die Lichtvorgaben sind einstellbare 35 % beim Start, 15 % im Nachlauf und 5 % bei Zwangskühlung. Alle erforderlichen Parameter erhalten Standardwerte.

Nach dem endgültigen Sitzungsende folgt auf Nutzeranweisung ein eigener Lichtnachlauf,
standardmäßig 10 Minuten bei 50 %, anschließend Licht aus. Neue Sitzung verwirft
die alte Lichtfrist. Dies hat keine Wirkung auf die Heizregelung.

Nach zwei nicht erkannten Testöffnungen erlaubt der Nutzer die Prüfung einer zusätzlichen Türregel ohne Feuchteabfall und betont die Priorität des heißen Betriebs. Die unbeschränkte Zusatzregel erzeugte im privaten Referenzreplay zusätzliche Ereignisse und wird deshalb nicht verwendet. Die Ergänzung wird auf niedrigere Temperaturen und durchgehend eingeschaltete Heizung mit zwei verfügbaren Messpositionen begrenzt; die Erkennung im heißen Zustand bleibt vorerst unverändert.

## Präzisierungen vom 20.09.2026

Der mechanische Timerantrieb erhält über den Schütz Strom. Seine Anzeige zählt
deshalb nur bei eingeschaltetem Betrieb und bestätigtem Schütz-Ein; ausgeschalteter
oder unbekannter Schütz hält sie an. Eine optionale Heizleistungsmessung beeinflusst
weiter den Heizzähler, nicht die Schätzung des Timerantriebs.

Nachlauf und bereits laufende Zwangskühlung sind in Details einzeln manuell
beendbar, mit demselben Folgeablauf wie bei Fristende. Verstrichener Nachlauf wird
einmal angerechnet, folgende Kühlung bleibt bestehen; Schutzsperren und Betrieb-Aus
werden nicht aufgehoben. Endzeitpunkt und manuelle Bedienung bleiben nachvollziehbar.

Die Lüftungserkennung wird weiter geprüft: Ein kurzer Austritt unter 15 Sekunden
muss denselben Gang samt anschließendem Aufguss erhalten. Eine pauschal kürzere
Zeitgrenze oder getrennte Erkennungsregeln für Gangstart und Gangende sind noch
nicht beschlossen. Zu untersuchen sind Stärke und Verlauf von Temperatur- und
Feuchteabfall sowie Erholung; die Sensorreaktion ist keine Messung der physischen
Türöffnungsdauer. Der bestehende Kandidat bleibt bis zum Nachweis unverändert.

Noch zu klären: Soll abgeschlossener Nachlauf auch nach erneutem Heizen einmalig
auf eine erst später fällige Zwangskühlung angerechnet werden? Der bestehende
Ablauf rechnet ihn nur auf eine bereits angeforderte Kühlung an.

Erkennungen werden auf ihre Wirkung im bestehenden Ablauf begrenzt. Eine
geschlossene Tür benötigt keine Schließungsprüfung, eine offene Tür keine
weitere Öffnungsprüfung. Personensuche soll einen neuen Gang anlegen; ein bereits
vorläufiger oder durch Aufguss bestätigter Gang benötigt sie nicht mehr. Weitere
Aufgüsse werden weiter zugeordnet. Während Nachlauf, laufender Zwangskühlung und
Betrieb-Aus bleiben neue Gangsignale gesperrt. Die Freigaben folgen ausschließlich
dem Controller; der Detektor führt keinen zweiten Bestätigungsstand.

Der Nutzer akzeptiert die nicht erkannte kurze Testöffnung. Tür- und Lüftungs-
schwellen bleiben für den angekündigten Versuch mit längerer Öffnung unverändert.

## Verbindlicher jüngster Entscheidungsstand vom 20.09.2026

Dieser Abschnitt geht allen früheren Aussagen in diesem Dokument vor. Soweit sie
abweichen, sind insbesondere die bisherigen Lichtwerte (35 % Betrieb und 5 %
Zwangskühlung), die daraus abgeleiteten Lichtphasen sowie die noch offene Frage
zur Anrechnung eines bereits abgeschlossenen Nachlaufs überholt.

- Heizbereich und Lichtbereich teilen sich dieselbe führende Sitzung und deren
  Phasen; sie dürfen keine konkurrierenden Sitzungs- oder Phasenmodelle führen.
  Die Lichthelligkeit verläuft temperaturbezogen linear. Der Kaltpunkt ist
  einstellbar (Standard 30 °C), die Solltemperatur ist höchstens 100 °C.
- Temperaturprogramme haben immer editierbaren Start- und Endwert. Standard ist
  eine gleichmäßige Steigerung in vier Gängen. Die festen, konfigurierbaren
  Vorlagen sind 80/85/90/95 °C und 70/80/90 °C. Wird beim bekannten
  80–85–90–95-Programm der Endwert nach zwei Gängen auf 100 °C geändert,
  bleibt das nächste Ziel 90 °C und anschließend gilt 100 °C als Endziel.
  Eine direkte Sollwahl per Schieber oder Taste bedeutet dagegen konstantes
  Heizen ohne weitere Steigerung. Die beim Start am Außentaster verwendete
  Programmwahl ist konfigurierbar.
  Die Zahl für die Temperaturverteilung ist ausschließlich eine Einstellung
  der Steigerung, keine geplante oder maximale Zahl tatsächlicher Saunagänge.
  Nach Erreichen der Endtemperatur gilt diese für beliebig viele weitere
  Gänge. Der Parameter erscheint nur bei den Steigerungseinstellungen.
- Zwangskühlung pausiert bei manueller Ofenübersteuerung und bei einem laufenden
  Gang. Nach dessen Ende wird nur die um den Nachlauf verkürzte Restkühlung
  angehängt; bei einem Phasenwechsel ohne Gang setzt die Kühlung fort. Jeder
  aktuell abgelaufene oder manuell beendete Nachlauf wird genau einmal der
  nächsten Kühlung gutgeschrieben, auch wenn diese erst später fällig wird.
  Damit ist die zuvor offen gelassene Nachlaufanrechnung entschieden.
  Bei manuellem Heizen pausiert auch die Nachlaufuhr. Nach Rückkehr zur Automatik
  läuft ihre Restzeit weiter; die Pause zählt weder als Nachlauf noch als Kühlung.
  Ein manuell beendeter Nachlauf schreibt nur seine bis dahin gezählte Dauer gut.
- Reicht beim Beginn eines Nachlaufs das verbleibende Heizbudget nicht für die
  Ofen-Mindestheizdauer, wird die Kühlung bereits an diesen Nachlauf angehängt
  und dessen Restzeit gutgeschrieben; ein Zwischenheizen findet nicht statt.
  Budgetablauf ist ein Fälligkeitsmerker: Seine Wirkung tritt bei der nächsten
  passenden Gelegenheit ein und beachtet stets die Türwartephase. Die bisherigen
  Defaults bleiben bis zu einer neuen Festlegung 4 Minuten nach Türschluss und
  10 Minuten bei offen bleibender Tür. Ein laufender Gang wird dadurch nie
  abgeschaltet.
- Manuelle Lichtübersteuerung gilt bis zum Phasenwechsel, manuelle
  Ofenübersteuerung bis zum nächsten fachlichen automatischen Eingriff.
  Statusabfragen und Resends sind keine solchen Eingriffe. Schutzfunktionen
  behalten Vorrang.
- Lichtstandards sind Grundlicht 5 %, Nachlauf 15 %, tagsüber 40 %, nachts
  25 % und außerhalb einer Sitzung 50 %, jeweils konfigurierbar; Übergänge
  dauern standardmäßig 30 Sekunden und sind ebenfalls konfigurierbar. Nach dem
  Sessionende leuchtet das Licht 10 Minuten mit 50 % und geht danach sofort aus.
  Ein Gang hält das Normallicht auch bei fallender Temperatur, außer während
  einer Kühlung.
- Die Erkennung wird anhand von drei echten Sitzungen weiter untersucht. Kurze
  Austritte sollen einen Gang erhalten, die Lüftung soll von starren Zeiten
  entkoppelt werden, und Handtuchwedeln nach einem Aufguss kann Temperaturstürze
  auslösen. Daraus ist noch keine fertige Detektionsregel abzuleiten oder zu
  behaupten; der bestehende Kandidat bleibt bis belastbarer Auswertung bestehen.
  Produktive Regeln dürfen keine Sitzungs-IDs, Testzeitpunkte, Aufgussnummern
  oder Sonderfälle einzelner Referenzereignisse enthalten. Die Aufzeichnungen
  dienen der Prüfung allgemeiner Merkmale und ihrer Übertragbarkeit.
- In den Einstellungen stellt ein eigener Button sämtliche Einstellungswerte
  einschließlich Temperaturprogrammen und Protokollstufe auf ihre zentralen
  Standards zurück. Entitätszuordnungen und die dazugehörige Taster- oder
  Schalterkonfiguration bleiben erhalten. Die Rücksetzung ist für HA-Admins
  nach Sitzungsende verfügbar; laufende Sitzungen und Fristen können damit
  nicht zurückgesetzt werden.

## Bedienungsüberarbeitung nach 1.0.0-rc1

Dieser jüngste Abschnitt ersetzt abweichende frühere Vorgaben zur Bedienung.

- Die Temperaturwahl liegt direkt auf dem Bogen der Temperaturanzeige. Klick,
  Ziehen und Tastaturbedienung verwenden dieselben Koordinaten und denselben
  übernommenen Sollwert. Der Regelbereich reicht standardmäßig von einstellbaren
  60 °C bis zur vorhandenen Obergrenze 100 °C. Der Lichtbezugspunkt und gemessene
  Isttemperaturen sind davon unabhängig. Die Zusatzregel für Türöffnungen bei
  niedrigen Temperaturen verwendet die gemeinsame Untergrenze als ihre
  Obergrenze; normale Türerkennung und Türschließung bleiben davon unabhängig.
- Die freie Steigerung heißt **Temperaturautomatik**. Benannte Programme sind
  in den Einstellungen mit Name, Start, Ende und Verteilung änderbar und
  erweiterbar. Die Verteilung begrenzt weiterhin keine tatsächlichen Gänge.
  Standards: Genusszeit 80/85/90; Gipfelstürmer 84/92/100; Ewigkeit
  80/84/88/92/96; Liegewiese 75/80/85; Höhenwanderung 90/95/100;
  Schnellstarter 70/90 °C. Die Auswahl zeigt den Namen und darunter die
  berechneten Temperaturstufen als Nebeninformation.
- Lichtstufen heißen **Gedimmt** und **Hell**. Prozentwerte werden nachgeordnet
  angezeigt. Im Automatikbetrieb stehen auf der Übersicht nur Aus, Automatik
  und Hell als manuelle Lichtübersteuerung bereit.
- Zwischen abgeschlossenen Sitzungen kann die Betriebsart Automatik/Manuell
  gewechselt werden. Im manuellen Betrieb schaltet der Nutzer Ofen und Licht;
  normale Temperaturregelung, Heizzeit-Kühlpausen und automatische Lichtwechsel
  entfallen. Messung, Archivierung, technische Schutzabschaltung und die
  Reaktion auf bestätigte Übertemperatur bleiben aktiv. Diese Betriebsart ist
  von vorübergehenden Übersteuerungen während der Automatik getrennt.
- Details werden nach Heizungs- und Lichtsteuerung gegliedert. Alle Zeitwerte
  stehen kompakt an einem Ort. Standard- und Detailverlauf sind ausdrücklich
  erreichbar. Erkennungsmarker liegen in den zugehörigen Graphen und sind
  mit der Ereignisliste verknüpft; markierte Zeilen bleiben kontrastreich lesbar.
  Inaktive Bestätigungszeiten mit Wert null werden weggelassen.
- Tooltip und Zoom müssen auch bei vollständigen Rohmessreihen flüssig bleiben.
  Normales Scrollen zoomt nicht. Ein Überblick über die ganze Sitzung mit
  markiertem Ausschnitt ersetzt den bisherigen Zeitausschnitt-Schieber.
- Der Saunataster startet aus dem ausgeschalteten Zustand mit dem hinterlegten
  Standardprogramm im Automatikbetrieb, auch wenn zuvor die Betriebsart Manuell
  gewählt war. Während der Sitzung wechselt ein kurzer Druck zwischen
  direkter Ofenübersteuerung und Rückkehr zur Automatik. Nur langes Drücken
  beendet die Sitzung. Die Erkennung des langen Drucks schaltet den Ofen aus
  und signalisiert den Abschluss mit 1 % Licht; nach dem Loslassen beginnt
  der normale Lichtnachlauf. Beginn, Langdruck und Loslassen derselben
  Betätigung dürfen nicht als mehrere unabhängige Bedienungen wirken.
  Änderungen über Lichttaster gelten ebenfalls als Übersteuerung bis zum
  nächsten vereinbarten Rückkehrpunkt.
- Vorübergehende Ofen- und Lichtübersteuerungen enden spätestens nach einer
  einstellbaren Höchstdauer, standardmäßig zehn Minuten. Ein früherer
  Phasenwechsel beziehungsweise automatischer Heiz-Schaltwechsel oder die
  ausdrückliche Rückkehr zur Automatik kann die jeweilige Übersteuerung zuvor
  beenden. Messwerte und Statusabfragen verlängern diese Frist nicht. Die
  eigenständige Betriebsart Manuell unterliegt dieser Frist nicht.
- Ein kurzer Saunatasterdruck ermöglicht auch während Nachlauf und
  Zwangskühlung den manuellen Heizstart und den Beginn eines neuen Saunagangs.
  Technische Schutzsperren bleiben wirksam. Beginnt kein Gang, laufen pausierte
  Nachlauf- und Kühlzeiten nach Rückkehr zur Automatik mit ihrem Rest weiter.
  Beginnt tatsächlich ein neuer Gang, wird der pausierte Nachlauf storniert;
  seine Restzeit wird nicht wieder aufgenommen. Nach dem neuen Gang beginnt
  ein neuer vollständiger Nachlauf. Die bis zur Pause gezählte Nachlaufzeit
  wird einmalig der nächsten Kühlung gutgeschrieben. Die manuelle Heizzeit ist keine
  Kühlzeit. Ein weiterer kurzer Druck bei manuellem Ofen-Ein gibt wie bisher
  an die Automatik zurück.
