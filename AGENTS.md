# Arbeitsregeln

- Maßgeblich sind `docs/entscheidungen.md`, Gang-, Betriebs-, Parameter- und
  Zeitmodell sowie der festgehaltene Kandidat. Spätere Nutzerkorrekturen gehen vor.
- Die Besprechung bleibt schrittweise und jeweils bei einem Thema. Regeln und
  Fristbeziehungen verständlich erklären, statt einzelne Zustände aufzuzählen.
  Bereits geklärte Grundlagen nicht erneut abfragen. Vorschläge und ausdrücklich
  vereinbarte Regeln voneinander unterscheiden; keine plausiblen Defaults erfinden.
  Als nächste Besprechungsblöcke folgen Darstellung und danach Speicherung.
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
- Es gilt eine einstellbare Heizzeitgrenze für sämtliche Heizabschnitte, ohne
  eigene Anheiz- und Folgedauer. Der Heizzeittimer zählt nur tatsächliches Heizen
  und pausiert bei idle. Keine zusätzliche prozentuale Idle-Gutschrift und kein
  durch Idle-Zeiten vergrößertes Heizbudget. Die zuvor vereinbarte lokale
  Rücksetzung nach genügend langer zusammenhängender Auszeit bleibt davon
  getrennt und ist kein Sessionwechsel.
- Eine bereits laufende Zwangskühlung sperrt neue Gangstarts. Ein bereits
  laufender Gang wird dagegen nicht durch Zwangskühlung unterbrochen; eine
  währenddessen fällige Kühlung bleibt bis zu seinem Ende ausstehend. Stark
  gedimmtes Licht kennzeichnet die laufende Zwangskühlung, nicht bloß ihre Fälligkeit.
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
- Erkennung, Einordnung und Aktorsteuerung trennen. Rückwirkende Zeitzuordnung
  erzeugt weder historische Heizbefehle noch umgeschriebene HA-Zustandswechsel.
- Eigener Thermostat: Bereits im vorläufigen Gang werden reguläre Hysterese-
  und Ablaufabschaltungen unterdrückt; Schutzabschaltung und ausdrückliches
  Ausschalten bleiben übergeordnet. Keine Live-Aktorfreigabe, solange Schutz-,
  Wiederanlauf- und Ersatztemperaturregeln offen sind.
- Beide Sensoren im Normalbetrieb; Ein-Sensor-Betrieb mit Fehleranzeige. Keine
  Mittelung, kein erfundener fester Höhenoffset, keine IBS-Sensoren.
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
