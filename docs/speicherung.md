# Datenerfassung und Speicherung

Eigener Besprechungsblock; Speicherverfahren und Datenumfang sind noch offen.

## Vereinbartes Ziel

Sessiondaten werden unabhängig vom Recorder geführt. Die Darstellung soll einen
zunächst vorläufig erkannten und später durch Aufguss bestätigten Gang demselben
Zeitintervall zuordnen können. Der jeweilige Erkenntnisstand bleibt nachvollziehbar.

Dabei sind drei Zeiten getrennt zu erhalten: zugeordneter Beginn, erste Erkennung
und erste Aufgussbestätigung. Vorbereitung, Schließung und bestätigender Aufguss
werden über ihre Ereignisreferenzen verbunden. Ein später bestätigtes Intervall
löscht nicht die Information, dass es zuvor nur vorläufig erkannt war.

## Zwei Speicheraufgaben

**Betriebszustand für Wiederaufnahme:** Session und Gang mit ihren Identitäten,
Türzustand und Episodenbezügen, Vorbereitung, Aufgussereignissen, Aufheizmerker,
Fristen und offenen Entscheidungen. Der Gang-Bestätigungsstand ist aus den
Aufgüssen ableitbar und wird nicht als unabhängige zweite Wahrheit gepflegt.
Eine technische Wiederherstellung entscheidet noch nicht über eine Heizfreigabe.

**Sessionarchiv:** Messreihen beider Höhen, erkannte Ereignisse, zugeordnete
Intervalle, Entscheidungsgründe, Sensorqualität und Ausfälle. Vorläufige Erkennung,
Bestätigung und regulärer Abschluss bleiben unterscheidbar. Die spätere Anzeige
kann das Intervall neu einordnen, ohne ursprüngliche Ereigniszeiten umzuschreiben.

## In diesem Block festzulegen

- Messwerte und Merkmale, Auflösung, Ereignisspitzen und Messlücken.
- Ereignisjournal und abgeleitete Sessionansicht, Korrekturen und Referenzen.
- Speicherverfahren, atomare Schreibvorgänge, Absturzverhalten und Migration.
- Aufbewahrung, Größenlimit, Löschen, Export, Sicherung und Wiederherstellung.
- Historische GUI-Abfrage und Aktualisierung nach späterer Bestätigung.
- Parameterschnappschüsse als Entscheidungsbeleg, nicht als zweite aktuelle
  Konfiguration.

## Stand der Vorbereitung

Das Gangmodell enthält Ereignis- und Zeitreferenzen. Sein unveränderlicher
In-Memory-Zustand ist noch kein dauerhaftes Archiv. Die `processed`-Liste dient
dem Offline-Modell; Speicherung, Indexierung und Begrenzung werden hier geklärt.

Roh-Recorderdaten, Zugangsdaten und das vollständige HA-Inventar bleiben außerhalb
des öffentlichen Repositorys. Das Replay verwendet eine lokal bereitgestellte
Originaldatei mit geprüfter Prüfsumme.
