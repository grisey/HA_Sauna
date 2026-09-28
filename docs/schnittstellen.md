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
Auch bei nicht geladener Integration verwendet der Optionsflow denselben
vollständigen Kandidaten. Eine wirksame Änderung des freien Programms darf
keine ältere Stufenliste zurücklassen, die nach dem nächsten Laden die neuen
Start-/End-/Verteilungswerte überstimmt.

Scheitert ein technisches Neuladen, werden Optionen, Startfreigabe und
Lichtzuständigkeit nur für die noch aktuelle, offene alte Runtime
wiederhergestellt. Neuere Optionsänderungen und bereits geschlossene oder
ersetzte Runtimes bleiben unberührt. HAs eigener Entladefehler bleibt sichtbar.
Der Wechsel auf eine andere Leuchte wartet begrenzt auf bereits laufende alte
Ausgaben und bestätigt erst danach das abschließende AUS der bisherigen Leuchte.
Auch ein Runtimewechsel mit unveränderter Leuchtenbindung wartet vor dem
Plattformentladen auf den tatsächlichen Abschluss alter Lichtdienste. Ist das
innerhalb des Dienstbudgets nicht möglich, bleibt die bisherige Runtime samt
Archiv zuständig; der Reloadversuch wird zurückgenommen. Bei technischen
Optionsänderungen erfolgt diese Vorprüfung bereits vor dem HA-Reload, damit
ein noch laufender Lichtdienst keinen nicht wiederholbaren HA-Entladefehler
erzeugt. Während der Rücknahme bleibt die Lichtausgabe entzogen; erst nach
Wiederherstellung der Optionen wird sie freigegeben. Ein direkter
Runtimeabschluss versucht zuerst Ofen-AUS und lässt bei einer noch laufenden
Lichtaufgabe das Archiv für deren Abschluss offen. Der Abschluss kann erneut
versucht werden; eine neue Runtime darf den offenen Vorgänger nicht ersetzen.

Die Antworten für Betriebsart und Tasterprogramm stammen aus dem unveränderlichen
Ergebnis des gemeinsamen Einstellungsschreibers. Ein Runtimewechsel während des
Einlesens des HTTP-Bodys kann dadurch keinen früheren Konfigurationsstand als
erfolgreich übernommen bestätigen.

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
