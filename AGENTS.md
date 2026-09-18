# Arbeitsregeln

- Maßgeblich sind `docs/entscheidungen.md`, `docs/gangmodell.md`, das Zeitmodell
  und der festgehaltene Kandidat. Ausdrückliche spätere Nutzerkorrekturen gehen vor.
- Die Besprechung bleibt schrittweise: Gang/Session, Heizregelung, Bedienung,
  Datenerfassung/Speicherung. Offene Regeln nicht durch plausible Defaults ersetzen.
- Durchlüften ist Vorbereitung; Personenfrüherkennung erzeugt einen vorläufigen
  Gang. Nur ein Aufguss bestätigt ihn. Beide Stände zeigen dieselbe Phase
  Saunagang und verwenden dieselbe ID und Startzeit. Der Bestätigungsstand wird
  aus den Aufgüssen abgeleitet, nicht parallel als schreibbarer Merker geführt.
- Erkennung, Einordnung und Aktorsteuerung trennen. Rückwirkende Zeitzuordnung
  erzeugt weder historische Heizbefehle noch umgeschriebene HA-Zustandswechsel.
- Eigener Thermostat: Bereits im vorläufigen Gang werden reguläre Hysterese-
  und Ablaufabschaltungen unterdrückt; Schutzabschaltung und ausdrückliches
  Ausschalten bleiben übergeordnet. Keine Live-Aktorfreigabe, solange Schutz-,
  Wiederanlauf- und Ersatztemperaturregeln offen sind.
- Beide Sensoren im Normalbetrieb; Ein-Sensor-Betrieb mit Fehleranzeige. Keine
  Mittelung, kein erfundener fester Höhenoffset, keine IBS-Sensoren.
- Eine Quelle je produktivem Parameter in den eigenen Parameterentitäten.
  Replay- und Archiv-Schnappschüsse sind keine konkurrierenden Laufzeitwerte.
  Der eingefrorene Kandidat wird nicht beiläufig geändert.
- Anforderungen, Implementierungsstand und Messbefunde unterscheiden. Ergebnisse
  derselben Kalibrierungssession sind keine unabhängige Validierung.
- Keine Roh-Recorderdaten, Zugangsdaten, HA-Konfigurationen oder persönlichen
  Nutzungsdetails veröffentlichen. Beispiele nur synthetisch oder freigegeben.
- Keine Versionen erhöhen, keine Lizenz wählen, keine Releases oder Deployments
  ohne Auftrag. Vor Änderungen den aktuellen Repo- und Dateistand lesen.
- Synthetische Tests und optionales lokales Replay ausführen. Keine parallelen
  produktiven Detektorimplementierungen. Beschreibung und Code müssen dieselben
  Begriffe und Zustandsübergänge verwenden.
