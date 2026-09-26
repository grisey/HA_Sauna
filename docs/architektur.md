# Architektur

Pro konfigurierte Sauna gibt es genau einen führenden Controller. Er besitzt die
laufende Sitzung mit Gängen, Heizzeit, Thermostat, Nachlauf, Kühlung, Licht und
Fristen. Konfiguration, Eingangsmessungen, Schutzgründe und Archiv sind davon
getrennt. Der Controller bleibt frei von Home-Assistant-Abhängigkeiten; nur der
Geräteadapter liest Quellen und führt Aktorbefehle aus.

## Konfiguration und Bedienung

`config_flow.py` und `bindings.py` ordnen die externen Rollen zu und prüfen
deren Metadaten. `core/parameters.py` bildet den unveränderlichen zentralen
Parameterstand. `core/program_catalog.py` verwaltet benannte
Temperaturprogramme getrennt vom Laufzeitablauf. Einstellungen und API schreiben
den Optionsstand; die drei in einer Sitzung erlaubten Temperaturwerte werden
über den Live-Einstellpfad in den bestehenden Controller übernommen. Eine solche
Änderung lädt nicht die gesamte Integration neu.

## Messung, Erkennung und Ablauf

`device.py` sammelt unveränderte Quellmeldungen, hält ihre Empfangszeit fest und
liefert nur diese Werte an den Ablauf. `core/moisture.py` berechnet aus einem
frischen Temperatur-/Feuchte-Paar den absoluten Wassergehalt; `sensor.py`
stellt ihn für beide Messpositionen als Diagnoseentität bereit.

`core/detector.py` arbeitet kausal mit begrenzten Arbeitsfenstern und meldet
Tür-, Personen-, Aufguss- und Lüftungssignale. `core/timeline.py` ordnet diese
Signale Gängen zu, während `core/controller.py` die daraus folgenden Heiz-,
Nachlauf- und Kühlentscheidungen trifft. Die detaillierte fachliche Regel steht
in [Erkennung](erkennung.md), [Gangmodell](gangmodell.md) und
[Betrieb](betrieb.md).

`core/thermostat.py` und `core/heating.py` entscheiden die Temperaturregelung
und zählen bestätigte Heizintervalle. `core/power.py` und `core/energy.py`
trennen gemessene von geschätzter Energie. `core/light.py` berechnet die
temperatur- und tageslichtabhängigen Zielhelligkeiten; `core/light_output.py`
führt Übergänge und vorübergehende manuelle Wahl aus.

## Laufzeit, Archiv und Darstellung

`runtime.py` serialisiert Eingänge, hält den Lebenszyklus zusammen und stößt
Archivierung sowie Entitätsaktualisierung an. `core/warmup.py` berechnet die
voraussichtliche Aufheizzeit. Der Anstieg der letzten abgeschlossenen
Aufheizphase geht schrittweise in den stabilen aktuellen Trend über.
Nur neue Messungen verändern die Schätzung; Statusabfragen lesen das Ergebnis.
Diese Anzeige beeinflusst keine Heizentscheidung.

`archive.py` und `backup.py` speichern Messungen, Merkmale, Ereignisse und
Parameterstände in SQLite, sichern sie konsistent über HA und exportieren sie
authentifiziert. `api.py`, `frontend.py` und `panel.js` zeigen diesen
führenden Zustand und berechnen weder Erkennung noch Heizentscheidung ein
zweites Mal. Nach einem HA-Neustart bleibt das Archiv erhalten; ein Betrieb wird
nicht automatisch fortgesetzt.
