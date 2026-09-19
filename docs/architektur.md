# Architektur

Es gibt einen führenden Controller pro konfigurierter Sauna. Die Session besitzt
Gangzuordnung, Heizzeit, Bereitschaft, Thermostatstatus, Nachlauf, Kühlung und
Fristen. Dauerhafte Konfiguration, reale Eingangslage, technische Schutzgründe
und historische Archive liegen außerhalb des Sessionwechsels.

| Baustein | Aufgabe |
|---|---|
| `config_flow.py`, `bindings.py`, `core/parameters.py` | Rollen, Metadatenprüfung, zentrale unveränderliche Parameterstände. |
| `device.py` | HA-Listener einschließlich unverändert neu berichteter Messungen; tatsächliche Services und Rückmeldung. |
| `core/detector.py` | Kausale, begrenzte Arbeitsfenster je Messhöhe; eingefrorene Erkennungslogik mit einstellbaren Expertenparametern. |
| `core/timeline.py` | Ereignisreferenzen, Gangbeginn, Aufgussbestätigung, Aufhebung und Abschluss. |
| `core/controller.py` | Sessionlebenszyklus, Fristfolgen, Kühlreihenfolge und Heizentscheidung. |
| `core/heating.py`, `core/thermostat.py` | Tatsächliche Heizintervalle und eigene Temperaturregelung. |
| `runtime.py` | Serialisierte Ereignisverarbeitung, Lebenszyklus, Archivübergabe und Entitätsaktualisierung. |
| `archive.py`, `backup.py` | SQLite, Schreibpuffer, konsistente HA-Sicherung und Export. |
| `api.py`, `frontend.py`, `panel.js` | Authentifizierte Abfragen/Bedienung und zwei Hauptansichten ohne zweiten Ablaufkern. |

`number`, `sensor`, `switch`, `climate` und `button` verwenden dieselbe Laufzeit.
Parameteränderungen schreiben ausschließlich Entry-Optionen; ein Update-Listener
lädt sie neu. Eine Session sperrt Änderungen an Parametern und Bindungen. Der
Controller hat keine HA-Abhängigkeit. Gerätebefehle sind auf `device.py` begrenzt.

Originalmessungen, berechnete Merkmale und Ereignisse haben getrennte Archivtypen.
Der Detektor veröffentlicht seine tatsächlich berechneten Merkmale zur Diagnose;
die Oberfläche implementiert seine Entscheidung nicht erneut. Archivrevisionen
und Anzeige-Caches sind keine weiteren führenden Regelungswerte.

Setup beginnt mit Ofen-Aus und ohne Session. Unload entfernt Listener, beendet
Betrieb, sendet Aus, stellt das Licht wieder her und schließt das Archiv.
Backup pausiert nur den Schreiber. Kein automatischer Wiederanlauf nach Neustart.
Der private Referenzexport und konkrete Nutzergeräte gehören nicht zum Code.
