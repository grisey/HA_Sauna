# Präsenz, Ofen und Phasen – Produktivstand vom 26.09.2026

Dieser Folgeauftrag ergänzt [Ofenkühlung](ofenkuehlung.md) und ersetzt ältere
Aussagen zu „Gang fordert EIN“ sowie zur Unterdrückung der Bereitschaftserfassung
während eines vorläufigen Gangs. Die eigenständige Zwangskühlung bleibt entfernt.
Die mathematischen Tür-, Personen-, Feuchte- und Aufgussdetektoren sowie ihre
Schwellen und fachlichen Erkennungszeitpunkte wurden nicht verändert.

## Status und Produktivpfade

| Auftragsabschnitt | Ergebnis und führende Dateien |
| --- | --- |
| 2, 3, 5 | `core/contracts.py`, `core/presence.py`, `presence_adapter.py`: normalisierte Proxy-/HA-Meldungen mit stabiler Identität, Aussageart, Herkunft, fachlicher Zeit, Empfang, Verfügbarkeit und unbekanntem Zustand. Direkte zusammenhängende Beobachtungen bestehen unabhängig von Aufgüssen. |
| 2, 3, 7 | `runtime.py`, `__init__.py`, `bindings.py`, `device.py`: ein eigener Präsenzlistener, Anfangszustand, Zustand/Verfügbarkeit, Reload und Abmeldung. Proxy bleibt die einzige führende Quelle. Ein konfiguriertes externes Entity ist ausschließlich beobachtend; kein ODER und kein Ausfall-Fallback. |
| 4.1 | `core/heater_overrides.py`, `core/controller.py`, `core/parameters.py`: Türschließen verarbeitet eine vorbereitete Anforderung sofort; `door_request_minutes` aktiviert optional eine Öffnungsfrist ab verfügbarer Öffnungsmeldung. Kein Default. Eine verbrauchte/unterdrückte Anforderung wird nicht nachgeholt. Alte Timer, Betrieb-AUS, laufende Heizung und Ofenkühlung sperren Wiederholungen. |
| 4.2 | `core/thermostat.py`, `core/controller.py`: Gang als Abschaltveto. Er verhindert eine reguläre Temperaturabschaltung einer bereits angeforderten Heizung oder einer zuletzt EIN angesteuerten, tatsächlich rückgemeldeten manuellen Heizung. Ein zuvor AUS angesteuerter Ofen startet allein wegen des Gangs nicht. Verhinderte Abschaltungen erzeugen keine Thermostatpause. |
| 4.3, 6 | `core/models.py`, `core/controller.py`, `core/phases.py`, `archive.py`: unabhängige Grundphasenmarken, echte Schützmeldungen, laufende Abschnitte pausierbarer Ofenkühlung, exklusive Phasenprojektion und einzelne Bereitschaftspausen. Die Dauermittlung erhält die korrigierten Pausen, liefert aber weiterhin ausschließlich `after_run_minutes`. |
| 7 | `core/consumer_events.py`, `core/contracts.py`, `runtime.py`, `archive.py`: stabile Verbraucherereignisse, Live- und Korrekturkennzeichnung, Deduplizierung über Reload. `media_player` als optionales Audioziel gespeichert; keinerlei Wiedergabe, Ankündigungsplanung oder Musikregeln. |
| 8 | `config_flow.py`, `api.py`, `panel.js`, `presentation.py`, `core/parameter_text.py`, `strings.json`, `translations/de.json`: Quellen-/Entitätsauswahl, optionale Frist, vorbereiteter Audioausgang, Belegung und Regelursachen in Details; korrigierte Hauptphasen und reale Heizspur getrennt. |

Die Zusatzfelder `presence_source` (Standard `proxy`, optional `ha_presence` als
gewünschte vorbereitete Quelle), Bindings `presence`/`audio_output` und
`door_request_minutes` sind sitzungsgesperrt. Alte Einstellungen bleiben lesbar.
Eine leere Öffnungsfrist entfernt nur den Öffnungstimer; der Schließauslöser bleibt.
Eine Anforderung an/über Solltemperatur plus Zuschlag wird ohne erfundene
Bindungszeit verbraucht und als `door_request_at_limit` bezeichnet.

Die technische Fehlerverriegelung und ausdrückliches Betrieb-AUS behalten ihren
Vorrang. Sollwertprogramme, Zuschlag, Hysterese, reale Mindestheizzeit, reguläre
Thermostatpause, Heizzeitbuchhaltung, Energie, mechanischer Timer und Lichtkurve
bleiben erhalten. Eine Türanforderung unterhalb der oberen Regelgrenze darf die
reguläre Thermostatpause übergehen. Die Mindestheizzeit beginnt erst mit der
bestehenden tatsächlichen Heizrückmeldung. Es gibt keine zusätzliche Haltezeit.

## Historie und Auswertung

`Session.base_phases` läuft während vorläufiger Gänge weiter. Wird ein Kandidat
zurückgenommen, verschwindet nur sein Gangintervall; der gesamte belegte Verlauf
Aufheizen/Bereit wird sichtbar. Ein rückdatierter Gang verdrängt den Hintergrund
ab seinem fachlichen Beginn. Ofenkühlung schließt am gespeicherten Beginn an.
Betrieb-AUS hat Vorrang. Pausierte Ofenkühlung verbraucht keine Kühlzeit.

`Session.contactor_history` enthält ausschließlich tatsächliche Rückmeldungen,
einschließlich unbekannter Stellung. `readiness_pauses` liefert Beginn und Ende
jedes zusammenhängenden Schnittintervalls aus wirksamer Bereitschaft, Betrieb-EIN
und bestätigt AUS gemeldetem Schütz. Seine Dauer ist Ende minus Beginn. Gänge,
Ofenkühlung, Betrieb-AUS und unbekannte Schützstellung zählen nicht dazu.
Bereitschaft kann sowohl EIN- als auch AUS-Intervalle enthalten.

Die Archivantwort ergänzt `phase_projection`; originale Sessionrevisionen,
Messungen, Aktormeldungen und Befehle bleiben unverändert. Alte Archive werden
lesend rekonstruiert, soweit die gespeicherten Phasen, Bereitschaftszeitpunkte
und Schützmeldungen es belegen. Nicht belegbare Hintergrundabschnitte werden
`unknown` und `complete=false`, nicht erfunden. Historische Zwangskühlzyklen
bleiben lesbar, einschließlich sichtbarer Unsicherheit über alte Pausen.
Liveansicht und Archiv verwenden dieselbe Projektion, nicht überlagerte
Hintergrund- und Gangrechtecke. Die tatsächliche Heizspur bleibt separat.

## Ereignisse für spätere Verbraucher

`ConsumerEvent` enthält `event_id`, Art, Session-/Gangreferenz, Quellbeleg sowie
fachliche und empfangene Zeit. Belegung einschließlich Verfügbarkeit benötigt
keinen Aufguss. Gangbeginn, Bestätigung, Rücknahme, Ende und bereits erkannter
Aufguss werden getrennt geliefert. Ein Gang bestätigt niemals rückwärts Präsenz.
Proxy-Rücknahme ist unbekannt, keine gemessene Abwesenheit. Quellenverlust erzeugt
keinen Austritt und keinen Kühlbeginn.

`delivery=archive_correction` ist ausschließlich eine historische Korrektur;
`delivery=live` bezeichnet eine jetzt verfügbare Meldung, deren fachlicher Zeitpunkt
früher liegen kann. Archiv-Neuberechnung publiziert keine Verbraucherereignisse.
Wiederholte HA-Zustände besitzen dieselbe Meldungs-ID. Beim Reload liest die
Runtime bereits archivierte Verbraucher-IDs; eine unveränderte Anfangsmeldung
aktualisiert den beobachteten Zustand, ohne erneut ausgeliefert zu werden.
Ein späterer Ausgabeadapter muss diese Identität ebenfalls idempotent verarbeiten.
Die jetzige Schnittstelle ruft keinerlei `media_player`-Dienst auf. Ein erkannter
Feuchteanstieg bleibt ein erfolgter Aufguss, keine vorausgehende Ankündigung.

## Weiterhin ausdrücklich offen

1. Konkrete Öffnungsfrist und eine zusätzliche Bindung der Türanforderung bei
   bereits erreichter Abschaltgrenze. Bis dahin kein Default und keine Bindung.
2. Automatische Unterbrechung der Ofenkühlung durch Tür oder neue Belegung sowie
   ein Nachholen unterdrückter Anforderungen. Bis dahin werden sie verworfen;
   bestehende manuelle Ausnahmen bleiben erhalten.
3. Bezugshorizont, erforderliche Dauer/Verteilung der Bereitschaftspausen,
   Umrechnung in Kühldauer und Festlegungs-/Aktualisierungszeitpunkt. Bis dahin
   gespeicherte feste Dauer, keine Heizbudget- oder Rücksetzformel.
4. Verhalten eines aktiven Vetos bei unbekannter direkter Präsenz.
5. Zusammenfassung kurzer direkter Belegung oder Unterbrechungen sowie Vorrang
   direkter Präsenz gegenüber bisherigem Tür-/Gangende. Punkte 4 und 5 begrenzen
   die Aktivierung: externe Belegung ist beobachtend, Proxy bleibt führend.
6. Inhalte und zeitlicher Ablauf von Musik und geplanter Aufgussankündigung.

## Geräte- und HA-Quellen

Für den FP300 ist die Präsenzentität auszuwählen, nicht der separate PIR-Ausgang.
Die Integration übernimmt keine Zigbee-/Matter-Kommunikation oder Geräteverzüge.
[Zigbee2MQTT PS-S04D/FP300](https://www.zigbee2mqtt.io/devices/PS-S04D.html)
führt `presence`, `pir_detection` und `absence_delay_timer` getrennt.
[HA Binary Sensor](https://www.home-assistant.io/integrations/binary_sensor/)
verwendet `on`/`off` sowie unbekannte/nicht verfügbare Zustände. Ein unverändertes
gültiges `on` verfällt hier nicht durch sein altes `last_changed`.
Die [HA-Listener](https://developers.home-assistant.io/docs/integration_listen_events/)
werden beim Entladen abgemeldet.

## Prüfprotokoll und Übergabe

Ausgangscommit: `323c57e648f41622025330bfc7ecdfcad8c9c5ec`, Branch
`codex/rc2-ready-state`, Remote `https://github.com/grisey/HA_Sauna.git`.
Die vorhandenen lokalen Ofenkühlungsänderungen wurden geprüft und als
`86f11e644fa62fb785945f26edb695cd742b6d99` gemeinsam mit den Schnittstellen
übernommen. Vier getrennte Teil-Worktrees starteten von diesem Stand; wegen
höchstens drei gleichzeitig verfügbaren Teilagenten startete die Oberfläche
nach Abschluss der ersten Teilaufgaben. Der Koordinator hat die Teilcommits
anschließend verbunden und die tatsächlichen Produktivpfade geprüft.

Tatsächlich ausgeführt am 26.09.2026:

- `python3 -m unittest discover -s tests`: 361 Tests, davon 359 bestanden und
  zwei private Recorder-Replays übersprungen.
- `python -m unittest discover -s tests/ha` mit isoliertem HA 2026.9.2:
  14 Tests bestanden, Prozessabschluss 0.
- `python -m unittest discover -s tests/integration` mit isoliertem HA 2026.9.2
  und lokalem Loopback-Testserver: 61 Tests bestanden, Prozessabschluss 0.
  Enthalten sind reale Entitätslistener, Auswahl/Persistenz, Reload,
  Quellwechsel/Sitzungssperre, fehlende Wirkung direkter Präsenz auf Gänge und
  Ofen, Audio ohne Serviceaufrufe sowie über Reload eindeutige Verbraucher-IDs.
- `node --test tests/panel_*.test.js`: neun Testdateien bestanden. Der neue Test
  prüft ausdrücklich Refresh → API-/Archivcache → exklusive Chartprojektion.
- Python-Kompilierung und `git diff --check` ohne Fehler.

Ein vorheriger isolierter Lauf der drei Präsenz-Runtime-Tests meldete alle
Assertions erfolgreich, stürzte aber danach bei Python 3.14.7 `_Py_Finalize`
mit Exit 139 ab. Er wird nicht als erfolgreicher Prozesslauf gewertet; die
Ursache ist nicht geklärt. Der anschließende vollständige Lauf einschließlich
dieser drei Tests beendete sich regulär mit Exit 0. Es wurde keine
Produktivanforderung zur Umgehung des Absturzes verändert.

Nicht ausgeführt: private Replays vom 22.09. (Belegpaket unter dem genannten
Linux-Pfad hier nicht vorhanden), Browser-End-to-End-Suite mit Playwright und
Tests auf realer Sauna-/FP300-Hardware. Die neue UI wurde über ihre produktiven
JavaScript-Render-/Refreshpfade geprüft. Keine Neukalibrierung, keine privaten
Rohdaten im Repository. Detektor, mathematische Schwellen, Timeline-Zuordnung,
Heizzeit-/Energie-/mechanische Timer-Kerne und Manifestversion sind gegenüber
der Ausgangsbasis unverändert. Kein Release und kein HA-Deployment.
