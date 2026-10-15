# Modellübergaben

Die Steuerung erhält ausgewählte fachliche Eingaben und ursprüngliche
Fachereignisse. Quellenwahl und Quellenzulassung liegen beim Geräte-, Runtime-
oder Konfigurationsowner. Gangaufnahme, Türzuordnung, Temperaturfreigabe,
Heiz- und Energiebuchung, Kühlung und Fristen bleiben beim jeweiligen
Fachowner.

Es bestehen drei Arten von Übergaben:

- Bereits geschlossene Fachwerte wie `Session`, `Decision`, `ControlInputs`,
  `ConfigurationModel`, `PhaseProjection` und `LightPlan` werden direkt von
  vorhandenen Verbrauchern verwendet.
- Normalisierte Eingänge aus `core/input_handoff.py` verbinden ausgewählte
  Temperatur, Heizrückmeldung, Schütz, Leistung, Anwesenheit, Betrieb,
  Regelmodus und Heizoverride mit den bestehenden Controllerfunktionen.
- Zusätzliche Beobachtungsformen beschreiben vollständig übergebbare Werte.
  Ohne einen tatsächlichen Verbraucher werden sie nicht zur Laufzeit erzeugt.
  Dazu gehören insbesondere die vollständigen Controller-, Türvorgangs-,
  Aufguss- und Benachrichtigungsbeobachtungen.

Ein neuer Name oder eine zusätzliche Form begründet keine weitere Auswertung,
Historie oder Zustellung. Bereits verwendete Fachwerte benötigen keine
parallelen Wrapper. Eine fertige API-Ausgabe wird nicht allein zur Formbildung
in eine zweite Darstellung und zurück umgewandelt.

## Normalisierter Steuerungseingang

Der normalisierte Eingang enthält das bereits ausgewählte fachliche Ergebnis.
Gerätekennung, Sensor- oder Proxybetrieb, Entity und mathematisches
Erkennungsverfahren gehören zur vorgelagerten Quellenverarbeitung und ihren
Beobachtungen. Eine neue Erkennung muss keine alten Proxyheuristiken
nachbilden, um denselben fachlichen Eingang zu liefern.

Gleicher Ausgangszustand, gleiche fachliche Konfiguration und gleiche Folge
normalisierter Eingaben einschließlich relevanter Zeiten ergeben dieselben
Steuerungsübergänge und Ausgänge. Fachliche Unterschiede bleiben erhalten:
unbekannte Heizrückmeldung ist nicht AUS, unbekannte Anwesenheit ist nicht
Abwesenheit, und zwei Aufgüsse bleiben zwei unterscheidbare Vorkommnisse.

Die konkreten `consume_*_input`-Methoden delegieren an die bisherigen
Controllerfunktionen. Sie verwenden den normalisierten Wert und
`timing.evaluated_at` als deren bisherigen Zeitparameter; Temperatur und
Leistung erhalten zusätzlich ihre bestehende Gültigkeitsfrist. Die bisherigen
Fachfunktionen bleiben die maßgebliche Implementierung. Es gibt keinen
zentralen Dispatcher, keine neue Queue und keinen generischen
`ModelHandoff` vor jedem Controlleraufruf.

Beim Heizoverride bleiben `True`, `False` und `None` unterschiedliche
Bedienhandlungen; `None` gibt an die Automatik zurück. Die vorhandenen internen
Aufhebungen durch Betriebs- oder Moduswechsel bleiben beim jeweiligen Fachowner
und erzeugen keinen zusätzlichen Overridebefehl. Rückgaben der bestehenden
Fachfunktionen werden unverändert weitergegeben.

| Eingang | Tatsächlicher Verbraucher | Bestehender Fachowner |
| --- | --- | --- |
| `TemperatureInput` | `consume_temperature_input` | `set_temperature` |
| `HeatingFeedbackInput` | `consume_heating_feedback_input` | `report_heating` |
| `ContactorFeedbackInput` | `consume_contactor_feedback_input` | `report_contactor` |
| `PowerFeedbackInput` | `consume_power_feedback_input` | `report_power` |
| `OccupancyInput` | `consume_occupancy_input` | bestehende direkte Anwesenheitsabstimmung im Controller |
| `OperationInput` | `consume_operation_input` | `set_operation` |
| `ControlModeInput` | `consume_control_mode_input` | `set_control_mode` |
| `HeaterOverrideInput` | `consume_heater_override_input` | `set_heater_override` |

Der Heizfallback ist bereits ein quellenfreier `bool | None` aus dem
Geräteadapter. `report_fallback_heating` liefert ihn weiterhin an
`_account_heat`; eine zusätzliche Form ist dafür nicht erforderlich. An der
Leistungsfrist bucht der bestehende Owner zunächst gemessene Energie und
anschließend die vorhandene Fallbackaussage als Schätzung oder unbekannten
Anteil. Schütz-EIN ist kein Beleg für physisches Heizen. Kühlung beginnt
weiterhin erst mit tatsächlicher Schütz-AUS-Bestätigung.

### Zeitbezüge

`InputTiming` trennt fachlichen Beginn (`effective_at`), tatsächlichen Eingang
(`received_at`), erstmalige fachliche Nutzbarkeit (`first_usable_at`) und
kausale Auswertung (`evaluated_at`). Die ersten drei Werte sind optional;
fehlender Beleg bleibt `None`. `evaluated_at` ist der konkrete Zeitpunkt des
bestehenden monotonen Steuerungsschritts.

`effective_at` erhält eine vorhandene fachliche Zeit, sonst einen belegten
Empfang. `received_at` gehört genau zum ausgewählten Eingang. Ein Refresh,
ein fremder Callback, ein Timer oder ein Ablauf erzeugt keinen neuen
Quellempfang. Ein vorhandener Empfang darf auch einen unbekannten Wert
belegen; unbekannter Wert und fehlender Empfang sind verschiedene Aussagen.
Bei fehlender oder abgelaufener Temperaturauswahl fehlen dagegen sowohl Wert
und Frist als auch die Zeiten einer ausgewählten Messung. Beim Leistungseingang
bleiben ein vorhandener ursprünglicher Empfang und seine Frist auch bei einem
inzwischen unbekannten Messwert erhalten.

`first_usable_at` wird nur bei einem bereits vorhandenen eigenständigen
vorgelagerten Ownerdatum gesetzt. Bei erneuter Auswahl einer dauerhaften
Präsenzmeldung liefert die Runtime deren tatsächliche erste Nutzbarkeit im
neuen Annahmeintervall; ursprüngliche Fach- und Empfangszeit bleiben erhalten.
Andere angeschlossene Producer liefern ohne solchen Beleg `None`. Der
Controllerannahmezeitpunkt wird nicht als frühere Nutzbarkeit vorgegeben. Bei
UI-Befehlen bleibt der tatsächliche Eintritt vor dem Warten auf den Runtime-Lock
erhalten. Timerabgeleitete Handlungen ohne Empfangsbeleg
behalten unbekannte Herkunftszeiten.

Auswertung, Heiz- und Aktorwirkung folgen der bestehenden kausalen
Buchungsgrenze. Ein später bestätigter früherer Beginn stellt die Controlleruhr
nicht zurück. Originalereignisse bewahren zusätzlich ihre Fach-, Erkennungs-
und Buchungszeiten. `Event.trace_at` bewahrt das originale Diagnose- und
Bestätigungsraster; die reale Erkennungszeit bleibt `detected_at`.
`confirmation_at` bleibt daraus mit Rückfall auf `effective_at` abgeleitet und
erzeugt keine `first_usable_at`-Angabe. Kandidatenzeiten und mathematische
Belegzeiten bleiben in der Diagnose; eine Beobachtungszeit ersetzt keinen dieser Bezüge.

### Anwesenheit und ursprüngliche Ereignisse

`OccupancyInput` enthält ausschließlich `occupancy`, `available`,
`qualified_presence`, `report_id` und `timing`. `qualified_presence` bezeichnet
den qualifizierten Beleg für den übergebenen Belegungszustand, auch für
Abwesenheit. Die Form enthält weder Ereignisse noch Quellenkennung, Entity
oder eine vorweggenommene Gangentscheidung.

Die Runtime prüft Quellenwahl, gebundene Entity und Assertion vor dieser Grenze.
Alle ausgewählten Meldungen passieren sie. Abgelehnte Meldungen erzeugen keine
Abwesenheit.
Verfügbare direkte EIN-/AUS-Belege sind qualifiziert; unknown und unavailable
bleiben unbekannt beziehungsweise unverfügbar. Der Consumer lehnt doppelte
Berichtsidentitäten und ältere effektive Meldungen weiterhin ab. Der
angenommene ursprüngliche `PresenceReport` bleibt bei der Runtime für Anzeige
und Archiv; der Controller hält den normalisierten Eingang. Proxyreports bleiben
unqualifiziert und bewahren Originalidentität, Zeiten und echte Verfügbarkeit.
Sie erzeugen weder dauerhafte Belegungs- oder Abwesenheitshistorie noch eine
erneute Ereigniswirkung. Unbekannt bedeutet keine beobachtete Abwesenheit.

Die Runtime führt die bestehende `PresenceSourceSelection` vor der Annahme.
Ein Auswahlwechsel allein erzeugt weder Gangende noch Kühlwirkung oder
Türwärmeinvalidierung. Neu angenommene Zustandsbelege müssen zeitlich nutzbar
sein; bereits abgeschlossene Eintrittsfolgen werden nicht rückwirkend bestätigt.

Nach der Annahme entscheiden Qualität, Verfügbarkeit, fachliche Zeiten und
der vorhandene Tür-, Gang-, Temperatur- und Kühlbestand. Türfolge,
Temperaturzulassung, Gangbestätigung und Gangende bleiben zentral beim
bestehenden Gangowner. Die Bestätigungsart unterscheidet einmalige Belege von
qualifizierter dauerhafter Belegung; zusätzliche Quellenfreigaben gibt es nicht.
Die [Heizregeln](betrieb.md#heizpriorität) und
[Kühlwirkungen](ofenkuehlung.md#kühlung-abwarten-oder-vorzeitig-beenden) beschreiben
die daraus folgenden Anforderungen.

`regulation_causes` projiziert in Automatik die möglichen Ursachen
`gang_heat`, `provisional_door_heat` und `cooling`, in Manuell keine.
`cooling_presence_policy` beschreibt diesen gemeinsamen Belegvertrag mit
`evidence_quality`, in Manuell mit `none`. Beide Projektionen sind passiv;
sie enthalten weder die aktuelle Belegqualität noch zusätzliche Regelowner.

Proxy-Personenbelege, Tür, Lüftung und Aufguss erreichen `Controller.process`
weiterhin als originale `Event`-Objekte. Ihre Identitäten, Zeiten und
FIFO-Reihenfolge bleiben erhalten, auch bei gleichen Zeiten und quer über
Modelle. Die vorgelagerte Zulassung wird pro Event als
`person_evidence_qualified` an `process`, `recognition_allowed` und
`recognition_allowed_at` übergeben. Sie wird nicht als Quellart im Controller
geführt. Unqualifizierte PERSON-Ereignisse bleiben gebucht und blockiert;
unqualifiziertes INFUSION ohne aktiven Gang bleibt ebenfalls blockiert. Ein
Aufguss darf einen bestehenden Gang weiterhin als Originalereignis annotieren.
Eine vorgelagerte harte Filterung ersetzt diese fachliche Verarbeitung nicht.

### Vorbereitete Tür- und Aufgussbeobachtungen

`DoorInput`, `DoorOperation` und `InfusionInput` bleiben vorbereitete
Zustandsformen ohne Laufzeitproduktion. Die bereits verbundenen
Originalereignisse werden vor `Controller.process` und `timeline.apply` nicht
zu einem zweiten Tür- oder Aufgusszustand rekonstruiert.

`DoorInput` kann Öffnung, Lüftungsbefund und Schluss als gemeinsame geordnete
Folge bewahren. `DoorOperation` übernimmt eine bereits zugeordnete Öffnung mit
optionalem Schluss und Lüftungsbefund. Die Öffnungs-ID identifiziert den
Vorgang. `ventilation_known=False` bezeichnet fehlendes Wissen; `True` ohne
Lüftungsbefund bezeichnet einen tatsächlich ausgewerteten, nicht bestätigten
Befund. Ein leerer Ereignisbestand begründet diesen Wert nicht. Die Timeline
bleibt der einzige Zuordnungsowner.

`InfusionInput` kann bestätigte Aufgussereignisse mit ihren bestehenden
Identitäten bewahren. `InfusionEpisodeDiagnostics` übernimmt getrennt davon
den veröffentlichten Episodendiagnosebestand: Phase, Beginn,
Bestätigungszeit, Ende, Endbeleg, Unterbrechungsstatus sowie Wasser-, Abfall-
und Niveauwerte. Ein fehlendes Ende ist kein belegter Abschluss. Fortsetzung
oder Ende erzeugen keinen zusätzlichen Aufguss. Der Episodenoutput wird beim
Erkennungsowner gelesen und nicht aus bestätigten Ereignissen zurückgerechnet.
Der bisherige Owner besitzt keine stabile Episoden-ID; eine spätere
Ereignisbindung darf nicht aus Kandidaten-ID oder Beginn geraten werden.

## Explizite Beobachtungsformen

`ModelHandoff[T]` ist eine vorbereitete vollständige Beobachtung eines
konkreten unveränderlichen Modelltyps. Sie enthält `HandoffContext` und den
Wert vor und nach einer ausdrücklich gewählten Auswertungsgrenze. `None`
bezeichnet fehlenden Bestand; mindestens ein Wert ist vorhanden. Gleiche Werte
sind zulässig. Eine ausgewertete Eingabe kann einen unveränderten Zustand
ergeben.

Der Kontext enthält Übergabeidentität, Beobachtungszeit sowie optional eine
gemeinsame Anlagenreferenz, den bereits bekannten Bedienenden und eine
Sitzungsreferenz. Sitzungs- und anlagenübergreifende Beobachtungen behalten die
jeweiligen Identitäten in ihren Fachwerten und verwenden im Kontext `None`.
Widersprechende Identitäten werden abgewiesen. Die Herkunft des Bedienenden
wird über `ActorRef` getragen und nicht aus Anwesenheit oder Empfänger abgeleitet.

Bereits vollständige unveränderliche Fachwerte sind direkt verwendbar;
verschachtelte Modelle bleiben typisierte Werte. Konstruktion prüft Feldtypen
und rekursiv die Unveränderlichkeit, führt aber keine Fachregel erneut aus.
Offene Diagnose- und Quellattribute können vollständig als `FrozenMap`
übernommen werden. Lebende HA-Objekte, Zyklen und nicht endliche Zahlen gehören
nicht zu dieser Form.

`ConfigurationOutput` kann den vollständigen `Configuration.as_options()`-Wert
mit dem bestehenden `ConfigurationModel` verbinden. Dazu gehören auch die aus
Geräteeigenschaften ergänzten Fähigkeiten; die Form legt keine eigene Auswahl
zulässiger Optionen fest. Thermische Beobachtungen übernehmen bereits
berechnete Fortschritts- und Schätzergebnisse samt Belegen und lösen keine
erneute Auswertung aus.

`ControllerOutput.capture` kann aktuelle und abgeschlossene Sitzungen, äußere
Steuerdaten sowie automatische und ausgegebene Entscheidungen als explizite
Beobachtung schließen. Es ist kein Wiederherstellungsformat interner Caches
und wird nicht produktiv als Vollcapture angeschlossen. Die State-API liest
bereits unter dem Runtime-Lock konsistent aus den vorhandenen Ownerwerten.
`ConsumerEventOutput` kann ein referenzgebundenes Fachereignis mit seiner
damaligen vollständigen Sitzung beziehungsweise Präsenzmeldung verbinden.
Der bestehende Archivverbrauch des Originalereignisses benötigt keine solche
Sitzungsvollkopie pro Event.

`DetectionEvaluation` gehört zu genau einem Detektorraster; Rasterzeit,
Signalfolge und Erkennungen müssen zusammengehören. Die gesamte Rückgabe eines
mehrere Raster verarbeitenden `Detector.advance()` ist nicht die Diagnose des
letzten Rasters. `MethodEvaluation` kann den tatsächlichen Diagnosebestand
anderer Verfahren als `FrozenMap` samt Auswertungszeit und Erkennungen
übernehmen, ohne Alt-Heuristikfelder vorauszusetzen.

Ein expliziter Verbraucher kann eine solche Beobachtung über
`ModelConsumer.consume` erhalten. Die Form besitzt keine Warteschlange,
Abonnenten, Wiederholungserkennung oder Zustellbestätigung. Ein pauschaler
Capture am Ende von `notify()` ersetzt keine benötigten Zwischenzustände.
Die vorbereiteten Formen führen weder Dienstaufrufe noch Benachrichtigungen
oder Companion-Transport aus. Ihre Vertragsprüfungen sind in der
[Prüfanleitung](../tests/README.md) beschrieben.

## Modelle und tatsächliche Verbraucher

Die Tabelle trennt den direkten Bestand, normalisierte Laufzeiteingänge und
vorbereitete Beobachtungen. Der vollständige Feldvertrag steht im jeweiligen
Datentyp. Eine vorbereitete Form verlangt ohne konkreten Verbraucher keine
Umschreibung des vorhandenen Anschlusses.

| Nr. | Modell und Form | Erzeuger | Tatsächlicher Verbrauch und Status |
| --- | --- | --- | --- |
| 1 | Konfiguration: `ConfigurationModel`, `ButtonCapabilities`; Beobachtung: `ConfigurationOutput` | `Configuration.from_options/as_options`, `Runtime.configuration_model` | Bereits direkt: Setup, Optionslistener, Settings, ConfigFlow, API und Entitäten verwenden die wirksame Konfiguration und Fähigkeiten. `ConfigurationOutput` bleibt vorbereitet; keine zweite Optionsauswahl. |
| 2 | Messung: `Measurement`; Beobachtungen: `ObservedSource`, `DeviceObservation` | `HADevice.ingest` | Bereits direkt: Messwertauswahl, Erkennung und Archiv verwenden ursprüngliche Messwerte und Zeiten. Die Steuerung erhält ausgewählte Eingänge; zusätzliche Quellenbeobachtungsformen bleiben vorbereitet. |
| 3 | Regeltemperatur: `TemperatureInput` | `HADevice.refresh`, `SaunaRuntime._drain_device_inputs` nach einmaliger `regulation_measurement`-Auswahl | Neu normalisiert: `consume_temperature_input` übergibt Wert, Frist und kausale Zeit an `set_temperature`. Keine Entitäts- oder Messhöhenauswahl im Controller. |
| 4 | Heizrückmeldung: `HeatingFeedbackInput`; Beobachtung: `HeatingObservation` | `HADevice.report_received_feedback` nach einmaliger `observe_heating`-Auswahl | Neu normalisiert: `consume_heating_feedback_input` erreicht `report_heating`. Der quellenfreie Fallback bleibt direkt an `report_fallback_heating` und `_account_heat` verbunden. Ausgewählte Quellenbelege bleiben beim Adapter und Archiv; die zusätzliche Beobachtungsform bleibt vorbereitet. |
| 5 | Schütz und Leistung: `ContactorFeedbackInput`, `PowerFeedbackInput`, `ContactorMark` | `HADevice.report_received_feedback` | Neu normalisiert: `consume_contactor_feedback_input` und `consume_power_feedback_input` erreichen `report_contactor` und `report_power`. Marken, Kühlbeginn und Energie werden weiterhin dort geführt. Fehlende Leistung ist nicht null Watt. |
| 6 | Anwesenheit: `OccupancyInput`; Belege: `PresenceReport`, `PresenceObservation`, `PresenceOutput`, `PresenceSourceSelectionSnapshot` | `Runtime.accept_presence` nach Quellen-, Entity- und Assertionzulassung | Neu normalisiert: `consume_occupancy_input` verarbeitet Belegung, Verfügbarkeit, Qualifikation, Berichtsidentität und Zeiten. Originalreports bleiben für API/Archiv bei Runtime; Originalevents mit einzelner Belegqualifikation bleiben direkt verbunden. Zusätzliche Beobachtungs- und Quellenwahlsnapshots bleiben vorbereitet. |
| 7 | Türvorgang: `Door`, Originalevents; Beobachtungen: `DoorInput`, `DoorOperation` | Erkennung und vorhandene Timeline-Zuordnung | Bereits direkt: `Controller.process` und `timeline.apply` konsumieren originale Öffnungs-/Schlussereignisse in Reihenfolge. `DoorInput` und `DoorOperation` bleiben vorbereitet; keine Zustandsrekonstruktion vor der Timeline. |
| 8 | Aufguss: Originalevents; Beobachtungen: `InfusionInput`, `InfusionEpisodeDiagnostics` | Bestätigende Erkennung und Episodenowner | Bereits direkt: Controller und Timeline konsumieren bestätigte Originalvorkommnisse samt Identitäten und Zeiten. Input- und Diagnoseformen bleiben vorbereitet; Fortsetzung und Ende werden nicht als neue Bestätigung ausgegeben. |
| 9 | Lüftung: Originalevent; Beobachtung: `DoorOperation.ventilation`, `ventilation_known` | Bestehender Tür-/Timelineowner | Bereits direkt: Controller, Timeline und Türwärmeverarbeitung verwenden das ursprüngliche Lüftungsereignis. Die vorbereitete Vorgangsbeobachtung schafft keinen unabhängigen Lüftungszustand oder zweiten Zuordnungsowner. |
| 10 | Bedienzustand: `OperationInput`, `ControlModeInput`, `HeaterOverrideInput` | Bestehende Runtime-Bedienverarbeitung | Neu normalisiert: `consume_operation_input`, `consume_control_mode_input` und `consume_heater_override_input` delegieren an die bestehenden Betriebs-, Modus- und Overrideowner. Initiale Controllerkonfiguration bleibt direkt; sie ist kein nachträglicher Modusbefehl. |
| 11 | Taster: Ergebnis von `ButtonGestures`; Beobachtungen: `ButtonGestureSnapshot`, `ButtonEvaluation` | `ButtonGestures`, `Runtime._handle_button_event` | Bereits direkt: `_apply_button_action` verwendet das berechnete Gestenergebnis. Die zusätzlichen Beobachtungen bleiben vorbereitet; keine erneute Gestenauswertung. |
| 12 | Erkennung: `Detection`; Beobachtungen: `DetectionEvaluation`, `MethodEvaluation` | Erkennungsowner je Raster und Auswertungsgrenze | Bereits direkt: Runtime und Archiv verwenden Erkennungen und Originaldiagnosen. Zusätzliche Auswertungsformen bleiben vorbereitet; sie müssen beim expliziten Gebrauch genau zugehörige Raster und Erkennungen verbinden. |
| 13 | Sitzung, Gang, Verlauf: `Session`, `Gang`, `Timeline`, `Event` | Controller und `timeline.apply` | Bereits direkt: Archiv, API und Darstellung verwenden die vollständigen Ownerwerte. Originalereignisse bleiben mit Identität und Zeitbezügen erhalten; keine parallelen Sitzungs- oder Gangmodelle. |
| 14 | Steuerungsausgabe: `ControllerOutput` | Explizites `ControllerOutput.capture` | Vorbereitete Beobachtung ohne Laufzeitproduktion. Die State-API liest die vorhandenen Controller-, Sitzungs- und Gerätewerte bereits unter Runtime-Lock konsistent. Kein pauschaler Vollcapture. |
| 15 | Thermostat: `ControlInputs`, `ThermostatState`, `Decision` | Bedarfsbildung, `thermostat.evaluate`, abschließende Controllerauswertung | Bereits direkt: Controller konsumiert quellenfreie Bedarfspegel, `HADevice.send` die endgültige Entscheidung; Sensoren und API lesen Zustand und Entscheidung. Keine zusätzliche Thermostat-Ausgabeform. |
| 16 | Temperaturprogramm: `TemperatureProgram`, `TemperatureControl` | Temperaturprogramm und `temperature_control` | Bereits direkt: Thermostat, API, Climate- und Number-Entitäten verwenden berechnete Ziele und Bedienprojektion. Kein zweites Programmobjekt oder erneute Sollwertberechnung für eine Übergabe. |
| 17 | Heizzeit und Energie: `HeatingTime`, `Energy` | `heating.report/advance`, `energy.advance` | Bereits direkt: `Session.heating` und `Session.energy` gehen an Sensoren, API und Archiv. Mess-, Schätz- und unbekannte Anteile bleiben erhalten; kein zusätzlicher Einzelbeleg oder paralleler Intervallbestand ohne Verbraucher. |
| 18 | Kühlung und zeitgebundene Phasen: `OvenCoolingResult`, `TimedPhase`, `Deadline` | Kühlungsberechnung und Controller | Bereits direkt: Phasenverarbeitung, Session, API und Darstellung verwenden Berechnungsbeleg, Pausen und Fristen. Deadline-Token behalten ihre bestehende Eigentums- und Ersetzungswirkung. |
| 19 | Temporäre Türwärme: `TemporaryDoorHeatState`, `DoorHeatTransition` | Bestehende Türwärmeauswertung | Bereits direkt: Controller übernimmt den einmal berechneten Zustand und die Anforderung. Für den flüchtigen Übergang wird kein zusätzlicher Snapshotbestand geführt. |
| 20 | Heizfortschritt: vorhandene Evidence und Episode; Beobachtungen in `thermal_handoff.py` | `HADevice._refresh_heating_progress` | Bereits direkt: Der Geräteadapter verwendet das Ergebnis für bestehenden HA-Hinweis, dessen Aufhebung und Archiv. Zusätzliche Fortschritts-/Episodenformen bleiben vorbereitet; keine zweite Trend- oder Hinweisentscheidung. |
| 21 | Aufheizschätzung: ETA; Beobachtungen: `WarmupEstimateSnapshot`, `WarmupSourceContext`, `WarmupEstimateObservation` | `HADevice._refresh_warmup`, `estimated_ready_seconds` | Bereits direkt: API und Anzeige verwenden die unter Quellen- und Gültigkeitsprüfung gelesene ETA. Zusätzliche Beobachtungen bleiben vorbereitet; keine zweite Schätzung oder historische Abfrage. |
| 22 | Licht: `LightPlan`, `LightAfterRun`; Beobachtungen: `LightOutputSnapshot`, `LightQuantizerSnapshot`, `LightQuantization` | Lichtregelung, Planer und Quantisierung | Bereits direkt: `HADevice.apply_light` konsumiert Plan und quantisierten Befehl; Session und API verwenden denselben Nachlauf. Zusätzliche Planner-/Quantizerbeobachtungen bleiben vorbereitet; kein zweites Update oder Quantisieren. |
| 23 | Geräteausgabe und Diagnose: `HeaterServiceResult`, `LightServiceResult`, `DeviceDiagnostics` | Bestehende Geräteaufrufe und Fehlerverarbeitung | Bereits direkt: Faults, Logs und Archiv erhalten bestehende Fehler-/Dienstinformationen. Die zusätzlichen Ergebnisformen bleiben vorbereitet. Dienstabschluss, Caller-Timeout und physische Rückmeldung sind getrennte Beobachtungen. |
| 24 | Phasen und Messfenster: `PhaseProjection`, `PhaseInterval`, `ReadinessPause`; Beobachtung: `MeasurementWindow` | `project_session/project_archive`, `measurement_window` | Bereits direkt: Controller, `Archive.read`, API und Darstellung verwenden berechnete Projektionen und Messfenster samt Korrekturen und Vollständigkeit. `MeasurementWindow` bleibt vorbereitete Form; keine erneute Rekonstruktion. |
| 25 | Umgebung: `EnvironmentSnapshot` mit Unterformen | `environment_snapshot` | Bereits direkt: State-API und Panel konsumieren die vollständige Anzeigeprojektion. `EnvironmentSnapshot` bleibt vorbereitet; die Umgebungsdaten sind keine zusätzlichen Saunasteuerungseingänge. |
| 26 | Archiv: Record-, Sitzungslisten- und Sitzungsergebnisformen in `system_handoff.py` | `Archive.read` | Bereits direkt: Archiv-API und Export verwenden die bestehenden geschlossenen Ergebnisse. Zusätzliche Formen bleiben vorbereitet. Leere Liste, fehlende Sitzung, Pagination und offene Payloads bleiben verschieden beziehungsweise vollständig erhalten. |
| 27 | Darstellung: `PanelSnapshot`, öffentliche Form in `presentation_handoff.py` | State-API und `public_state` | Bereits direkt: Panel konsumiert den bestehenden JSON-Wirevertrag; öffentliche Redaktion liegt bei `public_state`. Zusätzliche Panelbeobachtungen bleiben vorbereitet; entfernte Adminfelder werden nicht als `None` wieder eingeführt. |
| 28 | Empfänger: `RecipientAssignment`, `RecipientResolution`, `ResolvedRecipient`, `SessionRecipients` | Explizite Auflösung, Zuordnung und Akkumulation | Vorbereitete Formen ohne produktiven Benachrichtigungsverbraucher. Fähigkeiten und Bedienidentität werden nicht aus Anwesenheit abgeleitet. Keine Aktionen oder Zustellung. |
| 29 | Austausch: Envelope- und Korrelationssnapshots in `interaction_handoff.py` | Explizite Transportresultate und `ReplyCorrelator` | Vorbereitete Formen ohne Runtime-Transport, eingehenden Reply-Producer oder Companion-Verbraucher. Transportannahme, Geräteempfang und Antwort bleiben getrennte Aussagen. |
| 30 | Fachereignisverbrauch: Originalereignis; Beobachtung: `ConsumerEventOutput` | Bestehender Ereignisproducer; explizite Bindung an damalige Sitzung beziehungsweise Präsenzmeldung | Bereits direkt: `_publish_consumer` und Archiv konsumieren das ursprüngliche Fachereignis mit bestehender Wiederholungserkennung. `ConsumerEventOutput` bleibt vorbereitet; keine Sitzungsvollkopie je Archiv-Event. |

Backupbarrieren, Workerqueues, HTTP-Anfragen, Registryobjekte, Setup/Unload und
Abonnentenverwaltung sind Infrastrukturmechanismen. Handles und Locks sind
keine zusätzlichen Fachmodelle. Ihre fachlich sichtbaren Ergebnisse liegen in
den genannten Konfigurations-, Archiv-, Diagnose- und Darstellungswerten.

Ein Formnachweis belegt übernommene Werte, Zeit- und Identitätsbezüge sowie
Unabhängigkeit von späterer Mutation. Er belegt für sich keine angeschlossene
Steuerungsfunktion, Companion-Zustellung oder reale Gerätewirkung.
