# Arbeitsregeln

## Vorrang des Folgeauftrags vom 26.09.2026

[Präsenz, Ofen und Phasen](docs/praesenz-ofen-phasen.md) und
[Ofenkühlung](docs/ofenkuehlung.md) ersetzen entgegenstehende ältere Kühl-,
Gang-Heiz- und Phasenregeln. Externe Präsenz bleibt bis zur Entscheidung der
offenen Gangregeln beobachtend; die Proxyquelle führt weiterhin.

Der abschließend vereinbarte Stand desselben Tages fordert im aktiven Gang
durchgehend Heizen an. Die austauschbare Türhilfe wirkt ausschließlich nach
einem geeigneten Türschluss mit der vorhandenen tatsächlichen Mindestheizzeit;
eine Öffnungsfrist entfällt. Ofenkühlung bleibt übergeordnet und wird nicht
durch Tür, Präsenz oder manuelles Heizen unterbrochen. Ihre einstellbaren
Standardwerte sind 5 bis 15 Minuten, 15 Minuten Halbwertszeit und zwei
gewichtete Heizminuten je gewichteter Bereitschaftspause von einer Minute.
Gespeicherte Werte bleiben erhalten. Die nachfolgenden historischen Regeln
zu pausierbarer Kühlung, Gang-Abschaltveto und Heizbudgets gelten dafür nicht.

## Einheitlicher Codestil

Python folgt Ruff mit der Konfiguration aus `pyproject.toml`. Die Oberfläche
folgt Prettier 3.6.2 mit `.prettierrc.json`. Gemeinsame Zustandsregeln und
Bedienelemente werden an einer Stelle gehalten; neue Ausnahmen brauchen einen
konkreten, nachvollziehbaren Anwendungsfall.

## Vorrang des Entscheidungsstands vom 20.09.2026

Die konsolidierten Fachregeln in `docs/betrieb.md`, `docs/gangmodell.md` und
`docs/zeitmodell.md` sowie spätere Nutzerkorrekturen gehen den nachstehenden
Ablaufbeschreibungen vom 19.09. vor. `docs/entscheidungen.md` verweist auf diesen
geltenden Stand. Die Umsetzung ist beauftragt und
erfolgt in kleinen, abgegrenzten Terra-Aufgaben mit Prüfung durch den Hauptagenten.
Insbesondere sind Lichtkurve, Temperaturprogramme, pausierbare Kühlung und die
Anrechnung jedes Nachlaufs auf die nächste Kühlung inzwischen entschieden.
Die Verteilungszahl der Temperatursteigerung begrenzt keine tatsächlichen Gänge.
Produktive Erkennung darf keine Sonderregeln für IDs, Zeitpunkte oder einzelne
Ereignisse der Testaufzeichnungen enthalten.

## Frühere fachliche Festlegungen und fortgeltende Arbeitsregeln vom 19.09.2026

Die folgenden Zahlen und Ablaufbeschreibungen dokumentieren einen früheren
Entwicklungsstand. Insbesondere feste Temperatursteigerung, 35-%-Betriebslicht
und die als offen bezeichnete adaptive Lüftung wurden inzwischen fortgeschrieben;
hierfür gelten die oben verlinkten Fachregeln. Sämtliche nachstehenden Regeln zu
Arbeitsweise, Zugriffsrechten, Datenschutz, Freigaben und Prüfungen bleiben erhalten.

Maßgeblich sind die konsolidierten Regeln in `docs/entscheidungen.md` und
`docs/betrieb.md`. Die frühere einheitliche Heizzeit wurde ausdrücklich durch
90 Minuten Anfangsbudget, einmalig 30 Minuten Verringerung nach erster Kühlung
und danach konstanten Wert ersetzt. Alle Zahlen sind einstellbare Defaults.
Temperaturüberschreitung erzeugt nach anhaltendem Nachweis Zusatzkühlung ohne
Sessionabbruch. Die jüngste Türwartefrist beträgt standardmäßig 4 Minuten nach
Schließung beziehungsweise 10 Minuten bei offen bleibender Tür.
Für die erste Testinstallation zählt der Heizzähler ohne optionalen Leistungsmesser
bei Schütz EIN. Die Temperaturkrümmungs-Erkennung ist ausdrücklich zurückgestellt.
Die Messwert-Gültigkeit muss die normalen Meldeabstände abdecken. Eine zu kurze
Frist verursacht reale Heizunterbrechungen und danach eine neue Mindestheizzeit.
Keine zusätzliche Überbrückung nach Ablauf der Gültigkeit: Der Nutzer hat diesen
Ansatz nach Klärung der zu kurzen lokalen Frist verworfen. Gespeicherte Werte
nicht stillschweigend überschreiben. Bereits gestartete Kühlung und Nachlauf
gehen auch bei widersprüchlichem Gangsignal vor.
Leistungsmessung bleibt optional; der mechanische Timer ist reine Anzeige.
Die Timeranzeige zählt nur bei Betrieb-Ein und bestätigtem Schütz-Ein. Bei
Schütz-Aus oder unbekannter Schützstellung hält sie auch während Heizpause,
Nachlauf und Zwangskühlung an. Die optionale Heizleistungsmessung ist davon
getrennt. Sie hält bei Betrieb-Aus an und behält die Restzeit, auch nach einer
beendeten Sitzung ohne gezählte Gänge. Erst nach einer beendeten Sitzung mit
gezählten Gängen startet sie beim nächsten Einschalten neu.
Die Temperatursteigerung folgt gezählten Gängen: Startwert plus Gangzahl mal
Schrittweite, begrenzt durch eine optionale Endtemperatur. Ohne Endwert konstant.
Solltemperatur, Erhöhung je Gang und Endtemperatur sind während der Sitzung änderbar. Eine neue Solltemperatur gilt sofort als Ausgangspunkt weiterer Steigerungen; keine Änderung darf Gang, Fristen, Heizsperren oder Schutz zurücksetzen. Andere Einstellungen und Geräte bleiben während einer Sitzung gesperrt.
Standardlicht: Betrieb 35 %, Nachlauf 15 %, Zwangskühlung 5 %, jeweils einstellbar. In der Übersicht steht nur die jeweils relevante Phasenzeit; Heizsumme und mechanischer Timer stehen in den Details.
Nach endgültigem Sitzungsende: eigener einstellbarer Lichtnachlauf, Standard 10 Minuten bei 50 %, danach Licht aus. Eine neue Sitzung verwirft die alte Lichtfrist; keine Wirkung auf Heizregelung.
In Details können laufender Nachlauf und laufende Zwangskühlung einzeln manuell
wie bei Fristablauf beendet werden. Tatsächlich verstrichener Nachlauf wird
angerechnet; eine folgende Kühlung, Betrieb-Aus und technische Schutzsperren
bleiben wirksam. Bedienung protokollieren, veraltete Phasenaufrufe abweisen.
Der entkoppelte Taster steuert den Saunabetrieb, nie unmittelbar den Schütz.
Erkennungen nur prüfen, wenn sie den aktuellen Ablauf noch ändern können.
Türöffnung ausschließlich bei geschlossener, Türschließung ausschließlich bei
offener Tür prüfen. Personensuche ruht bereits im vorläufigen Gang und nach
Aufgussbestätigung; weitere Aufgüsse bleiben aktiv. Nachlauf, laufende Kühlung
und Betrieb-Aus sperren neue Gangsignale. Erkennungsfreigaben aus dem führenden
Controller ableiten, auch zwischen Signalen desselben Messzyklus. Keine Änderung
der Tür-/Lüftungsschwellen für den angekündigten Vergleich längerer Öffnungen.
Tasterimpuls und dauerhafter Betriebsschalter sind getrennt konfigurierbar.
Übersicht, separates Verlaufsblatt und sortierte Detailansicht folgen der
Nutzervorlage; Erkennungskontrolle gehört ausschließlich in die Details.

- Maßgeblich sind `docs/entscheidungen.md`, Gang-, Betriebs-, Parameter- und
  Zeitmodell sowie der festgehaltene Kandidat. Spätere Nutzerkorrekturen gehen vor.
- Die Besprechung bleibt schrittweise und jeweils bei einem Thema. Regeln und
  Fristbeziehungen verständlich erklären, statt einzelne Zustände aufzuzählen.
  Bereits geklärte Grundlagen nicht erneut abfragen. Vorschläge und ausdrücklich
  vereinbarte Regeln voneinander unterscheiden; Die jüngste Nutzeranweisung verlangt Standardwerte für alle notwendigen Einstellungen; vorhandene gespeicherte Werte bleiben erhalten.
  Die Sessionansicht ist in `docs/darstellung.md` vereinbart. Die Anforderungen
  an Vollauflösung, HA-Backup und Export stehen in `docs/speicherung.md`.
- Die Session ist das übergeordnete Laufzeitobjekt. Nach Ablauf der Frist seit
  Betrieb-Aus wird beim nächsten Einschalten eine neue Session mit sämtlichen
  neu initialisierten sessionbezogenen Unterobjekten angelegt. Dies nicht auf
  die Rücksetzung einer Heizzeitsumme verengen. Übergreifende Konfiguration,
  historische Daten und Schutzfunktionen sind vom Sessionwechsel getrennt.
- Personenfrüherkennung erzeugt einen vorläufigen Gang, Aufguss bestätigt ihn.
  Beide zeigen Saunagang mit derselben ID und Startzeit. Bestätigungsstand aus
  Aufgüssen ableiten, nicht parallel als schreibbaren Merker führen.
- Kurze Türbetätigung erhält den Gang. Ausdrückliches Ausschalten beendet ihn
  hingegen sofort. Rechtzeitiges Wiedereinschalten kann nur die Session fortsetzen,
  niemals den dadurch beendeten Gang. Normale Heizpausen sind kein Betrieb aus.
- Jeder beendete Gang mit zugeordnetem Aufguss zählt genau einmal, unabhängig
  vom Beendigungsgrund. Die Zählberechtigung folgt aus den Gang-/Aufgussobjekten;
  keine Sonderregel nur für ausgeschaltete Gänge und kein paralleler Merker.
- Das Heizbudget folgt Anfangswert und einmaliger Verringerung nach erster
  abgeschlossener Kühlung. Der Heizzeittimer verwendet die gültige optionale
  Leistungsmessung, sonst eine unabhängige Heizrückmeldung oder ersatzweise die
  Schützstellung. Ohne unabhängige Messung ist die Zählung eine Schätzung.
  Keine zusätzliche prozentuale Idle-Gutschrift und kein
  durch Idle-Zeiten vergrößertes Heizbudget. Die zuvor vereinbarte lokale
  Rücksetzung nach genügend langer zusammenhängender Auszeit bleibt davon
  getrennt und ist kein Sessionwechsel.
- Eine bereits laufende Zwangskühlung sperrt neue Gangstarts. Ein bereits
  laufender Gang wird dagegen nicht durch Zwangskühlung unterbrochen. Wird die
  Heizzeitgrenze dabei erreicht, folgt nach Gangende zuerst der Nachlauf und
  anschließend nur die um diesen Nachlauf reduzierte Restkühlzeit. Bei Restzeit
  null entfällt der zusätzliche Kühlabschnitt. Dafür keinen parallelen Sonderablauf
  anlegen. Stark gedimmtes Licht kennzeichnet die laufende Zwangskühlung.
- Ein laufender Nachlauf behält bei Betriebsunterbrechung seinen Endzeitpunkt.
  Seine verstrichene Dauer wird vollständig und ohne Doppelzählung auf die
  zugehörige Zwangskühlungsdauer angerechnet. Nachlauf und Zwangskühlung bleiben
  verschiedene Abläufe; eine neue Session übernimmt keine alten Laufzeitobjekte.
- Session-Unterbrechungsfrist, Thermostat-Cooldown und laufzeitbedingte
  Zwangskühlung haben unterschiedliche Bedeutung und eigene Parameter. Keine
  Zusammenlegung oder unbegründete Ableitung aus gleichen Zeiteinheiten; auch
  die Heizzeit-Rücksetz-Auszeit ist keine gemeinsame Dauer der Zwangskühlung.
- Parameter in der Integration konfigurierbar machen; genau eine Quelle je Wert.
  Sinnvolle Beziehungen im Code, veränderliche Grundwerte/Faktoren in der
  Parameterverwaltung. Ergebnisse und Restzeiten nicht unabhängig einstellen.
- Sämtliche extern verknüpften Entitäten werden im Konfigurationsbereich nach
  ihrer Funktion ausgewählt und können dort später neu zugeordnet werden.
  Keine konkreten Entity-IDs, Gerätenamen oder Kanalnummern im produktiven
  Ablaufkern hinterlegen. Obere und untere Messposition bleiben feste Rollen;
  deren reale Temperatur-/Feuchtequellen, Heizaktor, Bedienquelle und Licht sind
  Konfiguration. Die Referenzbezeichnungen K3/K6 im eingefrorenen Replay bleiben
  unverändert. Einzelheiten und Auswahlprüfung: `docs/parameter.md`.
- Erkennung, Einordnung und Aktorsteuerung trennen. Rückwirkende Zeitzuordnung
  erzeugt weder historische Heizbefehle noch umgeschriebene HA-Zustandswechsel.
- Eigener Thermostat: Bereits im vorläufigen Gang werden reguläre Hysterese-
  und Ablaufabschaltungen unterdrückt; Schutzabschaltung und ausdrückliches
  Ausschalten bleiben übergeordnet. Keine Live-Aktorfreigabe, solange Schutz-,
  sichere Start- und Ersatztemperaturregeln offen sind.
- Beide Sensoren im Normalbetrieb; Ein-Sensor-Betrieb mit Fehleranzeige. Keine
  Mittelung, kein erfundener fester Höhenoffset, keine IBS-Sensoren.
- Sessiondaten langfristig in voller empfangener Auflösung archivieren. Keine
  automatische altersbedingte Verdichtung oder Löschung der Originalmessungen.
  Ereignisse, Zuordnungen und Parameterstände nachvollziehbar erhalten.
- Das Archiv muss im HA-Backup konsistent enthalten und daraus wiederherstellbar
  sein. Dateipfad allein ist kein Konsistenznachweis; Backup und Restore testen.
  Historienpersistenz ist erforderlich, automatische Betriebsfortsetzung nach
  HA-Neustart nicht. Letztere nur bei sehr einfacher Umsetzung, nicht als
  zusätzliche zwingende Zustands-/Wiederanlaufmaschine einführen.
- SQLite unter dem HA-Konfigurationsverzeichnis und ZIP-Export mit Messreihen
  sowie maschinenlesbaren Session-/Ereignisdaten sind als technische Richtung
  angenommen. Export als authentifizierter Download im Einstellungsbereich;
  Archiv und Exporte nicht ungeschützt unter `www` veröffentlichen. Zustimmung
  zu diesem Aufbau ist kein Nachweis eines implementierten oder getesteten Backups.
- Replay- und Archiv-Schnappschüsse sind keine konkurrierenden Laufzeitwerte.
  Den eingefrorenen Kandidaten nicht beiläufig ändern. Eine adaptive Erkennung
  ist noch ein Prüfvorschlag, keine bereits freigegebene Änderung der Messlogik.
- Anforderungen, Implementierungsstand und Messbefunde unterscheiden. Eine
  Dokumentationsfortschreibung behauptet keine zugehörige Codeumsetzung.
  Ergebnisse derselben Kalibrierungssession sind keine unabhängige Validierung.
- Keine Roh-Recorderdaten, Zugangsdaten, HA-Konfigurationen oder persönlichen
  Nutzungsdetails veröffentlichen. Beispiele nur synthetisch oder freigegeben.
- Keine Versionen erhöhen, keine Lizenz wählen, keine Releases oder Deployments
  ohne Auftrag. Vor Änderungen den aktuellen Repo- und Dateistand lesen.
- Bei Codeänderungen synthetische Tests und optionales lokales Replay ausführen.
  Bei reiner Dokumentation Links, Entscheidungsstatus und Unverändertheit der
  Code-/Kandidatenartefakte prüfen. Keine parallelen produktiven Detektoren.
