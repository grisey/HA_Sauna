# Datenerfassung und Speicherung – eigener Besprechungsblock

## Bereits vereinbart

Sessiondaten sollen unabhaengig vom Recorder gespeichert werden. Fachlicher
Beginn und spaetere Erkennung bleiben getrennte Zeiten. Die GUI soll dadurch
nachtraeglich erkannte Gangintervalle zutreffend darstellen koennen.
Der Speicher soll keine zweite Quelle produktiver Parameter oder Aktorbefehle sein.

## Zwei getrennte Aufgaben

**Betriebszustand fuer Wiederaufnahme:** Session-/Gangidentitaet, letzter
bekannter Tuerzustand, Bezug der Startanker, Aufgussbestaetigung, Aufheizmerker,
Fristen und offene Entscheidungen. Die technische Ablage allein legt noch
nicht fest, ob nach einem Neustart weitergeheizt werden darf.

**Sessionarchiv:** Messreihen beider Hoehen, erkannte Ereignisse, zugeordnete
Intervalle, Entscheidungsgruende, Sensorqualitaet und dokumentierte Ausfaelle.
Fachliche Rueckzuordnung soll nachvollziehbar bleiben; ein urspruenglicher
Erkennungszeitpunkt darf nicht durch die spaetere Interpretation verschwinden.

Das konkrete Datenschema wird hier nur fachlich vorbereitet. Dateiformat,
Datenbank, Schreibverfahren und Aufbewahrungsregeln sind noch nicht ausgewaehlt.

## In diesem Block zu entscheiden

- Zu speichernde Roh-/gefilterte Messwerte und Merkmale, Messaufloesung und Umgang
  mit Ereignisspitzen sowie Messluecken.
- Ereignisjournal und daraus abgeleitete Gang-/Sessionansicht; Korrekturen und
  eindeutige Referenzen zwischen Messung, Erkennung und Handlung.
- Backend, atomare Schreibvorgaenge, Absturzverhalten, Migration und Groessenlimit.
- Aufbewahrung, Loeschen, Exportformat, Sicherung und Wiederherstellung.
- Historische GUI-Abfrage und Aktualisierung nach spaeterer Gangzuordnung.
- Parameter-Schnappschuss pro Mess-/Entscheidungsabschnitt als Auditinformation,
  nicht als alternative aktuell konsumierte Konfiguration.

## Vorbereitung im Repository

Der Fachkern enthaelt explizite Ereignis- und Zeitreferenzen. Sein unveraenderlicher
In-Memory-Zustand und die Testausgaben sind **kein implementiertes Sessionarchiv**.
Die `processed`-Liste ist eine Test-/Referenzprojektion innerhalb einer Session;
ihre Persistenz, Indexierung und Begrenzung gehoeren zur Speicherumsetzung.

Das Repository ist oeffentlich. Der private Recorderexport, seine Zugangsdaten-
und Kontextfelder sowie das komplette HA-Systeminventar werden nicht hochgeladen.
Reproduktion erfolgt mit lokal angegebener Originaldatei und geprueftem Hash.
