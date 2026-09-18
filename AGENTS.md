# Arbeitsregeln

- Massgeblich sind `docs/entscheidungen.md`, das Zeitmodell und der festgehaltene
  Kandidat. Spaetere ausdrueckliche Nutzerkorrekturen gehen aelteren Annahmen vor.
- Die Besprechung erfolgt schrittweise: Gang/Session, Heizregelung, Bedienung,
  Datenerfassung/Speicherung. Offene Regeln nicht durch plausible Defaults ersetzen.
- Erkennung, fachliche Einordnung und Aktorsteuerung trennen. Rueckdatierte
  fachliche Zeitraeume duerfen keine historischen Heizbefehle oder gefaelschte
  HA-Zustandswechsel erzeugen.
- Ein eigener Thermostat ist vereinbart. Waehrend eines erkannten Gangs werden
  regulaere Hysterese-/Ablaufabschaltungen unterdrueckt; Sicherheitsabschaltungen
  und explizites Ausschalten bleiben uebergeordnet. Keine Live-Aktorfreigabe,
  solange Schutz-, Wiederanlauf- und Ersatztemperaturregeln nicht festgelegt sind.
- Normalbetrieb mit K3 und K6; Ein-Sensor-Betrieb mit Fehleranzeige. Keine
  Mittelung und kein erfundener fester Hoehenoffset. IBS nicht verwenden.
- Alle produktiven Einstellungswerte haben eine einzige Quelle in den eigenen
  Parameterentitaeten. Schnappschuesse im Replay oder Archiv sind keine konkurrierenden
  Laufzeitwerte. Keine unbemerkten Aenderungen am eingefrorenen Kandidaten.
- Code, Modellentscheidungen und Messbefunde getrennt kennzeichnen. Auf derselben
  Session kalibrierte Ergebnisse nicht als unabhaengige Validierung ausgeben.
- Keine Roh-Recorderdaten, Zugangsdaten, HA-Konfigurationen oder persoenlichen
  Nutzungsdetails veroeffentlichen. Beispieldaten nur gezielt freigegeben oder synthetisch.
- Keine Versionen anheben, keine Lizenz auswaehlen, keine Releases oder Deployments
  ohne Auftrag. Bestehende Dateien und Repo-Stand vor Aenderungen lesen.
- Tests mit synthetischen Ereignissen und optionalem lokalem Recorder-Replay
  ausfuehren. Keine mehrfach implementierten produktiven Detektoren pflegen.
