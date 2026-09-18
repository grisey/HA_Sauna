# Entscheidungen und Bearbeitungsstand

Quelle: ausdrueckliche Nutzerfestlegungen dieser Besprechung, Stand 18.09.2026.
Der eingefrorene Messkandidat steht separat in `kandidat.md`.

## Vereinbart

**Aufbau:** eigene Home-Assistant-Integration mit eigenem Thermostat. Ein
zusammenhaengender Python-Fachkern fuehrt die Ablaufentscheidungen. Erkennung,
Gang/Session, Temperaturregelung, Entitaeten und Speicherung sind getrennte Aufgaben.

**Sensoren:** K3 auf Kopfhoehe der obersten Bank; K6 etwa 20–30 cm darunter.
Beide im Normalbetrieb fest einbinden. Faellt einer aus, arbeitet die Steuerung
mit dem anderen weiter und meldet den betroffenen Kanal. Hoehentemperaturen
werden nicht gleichgesetzt. IBS wird nicht verwendet.

**Erkennung:** schnelle Tuerereignisse sind von Durchlueftungseinordnung und
Gangentscheidung getrennt. Personenfrueherkennung ist weniger spezifisch als
Aufguss. Aufguss kann einen verpassten Gangstart nachtraeglich herstellen und
zugleich bestaetigen. Jeder tatsaechliche Gang beinhaltet nach Nutzerangabe Aufguss.

**Tuergebrauch:** kurze Oeffnung, beispielsweise Austritt einzelner Personen,
erhaelt den laufenden Gang, seine Aufgussbestaetigung und Heizbehandlung. Erst
bestaetigtes Durchlueften nach Aufguss qualifiziert einen Gangabschluss.
Vor jedem der vier Referenzgaenge wurde ausgiebig gelueftet; dieser Verlauf
stuetzt im Kandidaten den schwachen Personenpfad. Tuerereignisse allein starten
keinen Gang. Aus Messkurven werden keine Personenzahl und kein Oeffnungszweck abgeleitet.

**Zeitliche Zuordnung:** Nach Erkennung beginnt die fachliche Gangzeit bei der
zugeordneten Tuerschliessung. Der Erkennungszeitpunkt bleibt separat. Die reale
Steuerung kann erst bei Erkennung reagieren. Eine spaetere kurze Oeffnung
verschiebt den bereits bekannten Gangbeginn nicht. Details: `zeitmodell.md`.

**Heizung:** eigener Thermostat ermoeglicht engere Hysterese; bei erkanntem Gang
wird auch der regulaere Wechsel von `heating` nach `idle` zustandsbedingt
verhindert. Dynamische Solltemperaturnachfuehrung innerhalb eines Gangs ist verworfen.
Uebergeordnete Sicherheitsabschaltung und ausdrueckliches Ausschalten bleiben wirksam.

**Anheizen/Laufzeit:** Sessionmerker `aufgeheizt` beim temperaturbedingten Uebergang
zu `idle`. Dieser Merker wird innerhalb derselben Session nicht zurueckgenommen.
Heizdauer aus Anheizdauer (Ausgangspunkt 100 Minuten) und spaeterer kuerzerer
Dauer waehlen. Eine ausreichend lange Auszeit setzt den Heizzeitabschnitt zurueck;
kurze Unterbrechungen schenken keine neue volle Heizdauer. Zwangskuehlung aus
Heizlaufzeit, nicht nach jedem regulaeren Thermostatstopp. Betriebsbedingte
Abschaltungen duerfen einen laufenden Gang nicht unterbrechen.

**Session:** kurzes Aus-/Wiedereinschalten erhaelt dieselbe Session. Ausschalten
wirkt sofort auf den Betrieb; Sessionabschluss ist eine gesonderte Entscheidung.
Normale Hysteresepausen sind kein Session-Aus-/Wiedereinschalten.

**Parameter/GUI:** eigene einheitliche Parameterentitaeten, genau eine konsumierte
Quelle je Einstellungswert. Abgeleitete Werte und Restzeiten sind lesbare Zustaende.
GUI-Phase aus dem Fachzustand ableiten; Bedienaktionen durch denselben Kern fuehren.

**Daten:** Betriebszustand und Sessionarchiv unabhaengig vom Recorder vorsehen.
Speicherverfahren, Aufloesung und Wiederaufnahme sind ein eigener Besprechungsblock.

## Ueberholte Annahmen

- Nicht jede Tueröffnung nach Aufguss beendet einen Gang.
- Nicht alle urspruenglichen Tuerereignisse waren erfasst: die kurze Gefaessentnahme
  um 22:39:40 ist das bestaetigte 15. Referenzereignis.
- Die frueher erlaubte normale Hystereseabschaltung waehrend des Gangs wurde durch
  die eigene zustandsabhaengige Thermostatregelung ersetzt.
- Manuelle Eingriffe in der Referenzsession waren einmalige Reparaturversuche,
  keine konkurrierenden Sollvorgaben fuer die neue Automatik.
- Ein-Sensor-Tests des aelteren konservativen Detektors belegen nicht das Verhalten
  dynamischer Sensorausfaelle des neuen Kandidaten.

## Noch zu klaeren, in Besprechungsreihenfolge

1. **Gang/Session:** Verarbeitung eines erkannten Gangs, dem noch kein Aufguss
   folgt, bei laengerem Durchlueften oder Fristablauf; Bestand der alten
   Zwoelf-Minuten-Frist; exakte Session-Unterbrechungsfrist und Wiederaufnahme
   von Gang/Nachlauf bei Betriebsunterbrechung beziehungsweise HA-Neustart.
2. **Heizregelung:** Hysteresewerte, kuerzere Heizdauer, Auszeit fuer Ruecksetzung,
   Zwangskuehlungsdauer, Behandlung kurzer Auszeiten in der Zeitrechnung,
   Umschaltpunkt des Dauerlimits, Gangbeginn bei bereits laufender Kuehlsperre,
   Nachlauf-/Lueftungsheizverhalten, Sicherheitsgrenzen und Rueckmeldungsdiagnose.
   Ersatztemperatur bei Ausfall von K3 gesondert bestimmen; kein fixer Offset.
3. **Bedienung:** Bedeutung von `bereit`, endgueltige Parametergrenzen/Einheiten,
   Verhalten einer Parameteraenderung bei laufender Frist, Start-/Endstufen,
   Taster-/Licht-/Musik-/Benachrichtigungsfunktionen.
4. **Speicherung:** siehe `speicherung.md`; Messaufloesung, Backend, Aufbewahrung,
   Export, Sicherung und Wiederanlauf. Keine Auswahl allein aus technischer Bequemlichkeit.

Die Vorbereitung implementiert nur den geklaerten Teil. Insbesondere sind alte
Bestandswerte (z.B. Nachlauf 10 Minuten, Lueftungssoll 60 °C) keine automatisch
beschlossenen neuen Produktivwerte.
