# Verträge des Folgeauftrags vom 26.09.2026

Verbindliche Datentypen liegen in `core/contracts.py`.

- `PresenceReport`: quellenunabhängige Belegung mit Aussageart, Belegreferenz,
  fachlichem Zeitpunkt, Empfang und Verfügbarkeit. `proxy_retraction` bedeutet
  unbekannte Belegung, keine beobachtete Abwesenheit. Direkte Präsenz wird vorerst
  nur beobachtet; die führende Quelle bleibt Proxy, ohne ODER-Verknüpfung.
- `ControlInputs`: einmalige Türanforderung, aktuelles Gang-Abschaltveto und
  bestehende Ofenkühlung. Schutz und Betrieb-AUS bleiben übergeordnet.
- `PhaseProjection`: genau eine Hauptphase je Intervall, Korrekturhinweise und
  einzelne Bereitschaftspausen. Grundlage sind `Session.base_phases` und
  `Session.contactor_history` plus Gang-/Ofenkühlungsintervalle. Die tatsächliche
  Schützspur wird niemals durch die Projektion verändert.
- `ConsumerEvent`: stabile Identität und beide Zeitbezüge für Belegung,
  Verfügbarkeit, Gangbeginn/-bestätigung/-rücknahme/-ende und Aufgüsse.
  `archive_correction` ist keine Liveauslösung. Keine Wiedergabe implementiert.

## Schreibzuständigkeiten während der Teilaufgaben

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
