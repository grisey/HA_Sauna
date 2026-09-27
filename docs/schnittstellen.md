# Verträge des Folgeauftrags vom 26.09.2026

Verbindliche Datentypen liegen in `core/contracts.py`.

- `PresenceReport`: quellenunabhängige Belegung mit Aussageart, Belegreferenz,
  fachlichem Zeitpunkt, Empfang und Verfügbarkeit. `proxy_retraction` bedeutet
  unbekannte Belegung, keine beobachtete Abwesenheit. Direkte Präsenz wird vorerst
  nur beobachtet; die führende Quelle bleibt Proxy, ohne ODER-Verknüpfung.
- `ControlInputs`: einmalige Türanforderung, laufende Heizanforderung des Gangs und
  bestehende Ofenkühlung. Schutz und Betrieb-AUS bleiben übergeordnet.
- `PhaseProjection`: genau eine Hauptphase je Intervall, Korrekturhinweise und
  einzelne Bereitschaftspausen. Grundlage sind `Session.base_phases` und
  `Session.contactor_history` plus Gang-/Ofenkühlungsintervalle. Die tatsächliche
  Schützspur wird niemals durch die Projektion verändert.
- `ConsumerEvent`: stabile Identität und beide Zeitbezüge für Belegung,
  Verfügbarkeit, Gangbeginn/-bestätigung/-rücknahme/-ende und Aufgüsse.
  `archive_correction` ist keine Liveauslösung. Keine Wiedergabe implementiert.

## Temperaturänderungen und vollständige Einstellungen

Das vollständige Parameterformular erhält das laufende Temperaturprogramm,
wenn sein mitgesendeter Sollwert unverändert ist. Eine direkte Sollwertwahl
über die partielle Temperaturschnittstelle bleibt eine ausdrückliche Wahl,
auch wenn die gewählte Zahl der bisher gespeicherten entspricht. Änderungen
technischer Grenzen werden vor jeder Übernahme gegen den vollständigen
Konfigurationskandidaten geprüft, einschließlich der Innenstufen eines freien
Programms. Ein ungültiger Kandidat verändert weder Livezustand noch Optionen.

Diese Unterscheidung liegt im gemeinsamen Einstellungspfad. Das Panel lässt
den Sollwert im Vollformular stehen und führt dafür keine zweite Regel ein.

## Historischer Arbeitsplan vom 26.09.2026

Die folgende Aufteilung dokumentiert den damaligen Arbeitsplan, nicht die
aktuellen Dateinamen oder dauerhafte Schreibzuständigkeiten. Die Türhilfe liegt
in `core/temporary_door_heat.py`; die damals vorgesehenen Dateien
`core/heater_overrides.py` und `tests/test_heater_overrides.py` wurden nicht angelegt.

1. Präsenz: neue `core/presence.py`, `presence_adapter.py`,
   `tests/test_presence.py`, `tests/integration/test_presence_adapter.py`.
2. Ofen: `core/thermostat.py`, neue `core/heater_overrides.py`,
   `tests/test_thermostat.py`, `tests/test_heater_overrides.py`.
3. Phasen: `core/timeline.py`, neue `core/phases.py`, `archive.py`,
   `tests/test_timeline.py`, `tests/test_phases.py`, `tests/test_archive.py`.
4. Oberfläche: `config_flow.py`, `panel.js`, `presentation.py`, `strings.json`,
   `translations/de.json`, `core/parameter_text.py`,
   neue `tests/panel_presence.test.js`, `tests/ha/test_presence_config.py`.

Alle übrigen Dateien, insbesondere Controller, gemeinsame Modelle/Verträge,
Parameterdefinitionen, Bindings, Runtime, Geräteadapter, API, Einstellungen und
Dokumentation gehören ausschließlich dem Koordinator. Teilagenten liefern
Integrationsanforderungen statt in diese Dateien zu schreiben.
