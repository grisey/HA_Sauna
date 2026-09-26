# Entscheidungen

> Ergänzender Vorrang: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> beschreibt den Produktivstand des Folgeauftrags vom 26.09.2026.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

## Geltender Regelstand

Der verbindliche Regelstand steht in [Betrieb](betrieb.md),
[Gangmodell](gangmodell.md) und [Zeitmodell](zeitmodell.md). Diese Seiten
ersetzen frühere Zwischenstände; deren Verlauf bleibt in Git erhalten.

- Eine Sitzung ist der einzige führende Ablauf für Heizung, Gänge, Nachlauf,
  Kühlung und Licht. Konfiguration, Archiv und Schutzgründe bleiben getrennt.
- Bestätigte beendete Gänge zählen genau einmal. Ein vorläufiger Gang während
  eines manuell pausierten Nachlaufs storniert den alten Nachlauf erst nach
  Aufgussbestätigung; Aufhebung oder Fristablauf setzen dessen Rest fort, ohne
  doppelte Kühlanrechnung.
- Temperaturprogramme verteilen nur die Steigerung. Nach der Endtemperatur sind
  weitere Gänge unbeschränkt möglich; direkte Sollwahl bleibt konstant.
- In Betriebsart Manuell entfallen reguläre Thermostat-, Heizbudget- und
  Lichtautomatiken. Technische Sperren und bestätigte Übertemperatur bleiben
  wirksam; deren Kühlung wartet einen aktiven Gang ab.
- Alle Betriebswerte sind einstellbare Defaults, gespeicherte Werte bleiben
  erhalten. Die feste Solltemperatur-Obergrenze beträgt 100 °C.
- Die Bereitschaftsprognose ist Anzeige in Fünf-Minuten-Stufen und hat keine
  Wirkung auf Heizregelung, Schutz oder Fristen.

## Weiterführende Festlegungen

[Bedienung](bedienung.md) beschreibt die Nutzeroberfläche. [Parameter](parameter.md)
ist die Quelle für Eingabewerte, [Darstellung](darstellung.md) für Anzeige,
[Speicherung](speicherung.md) für Archiv und [Abnahmebericht](abnahme.md) für
Prüfstand und Hardwareabnahme.

Die Erkennung bleibt allgemein und ohne Sonderregeln für einzelne Aufzeichnungen.
Messungen werden in voller empfangener Auflösung archiviert. Es gibt keine
automatische Verdichtung oder Löschung, keine erfundenen Sensoroffsets und keine
automatische Betriebsfortsetzung nach HA-Neustart.
