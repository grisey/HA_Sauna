# Modellübergaben

Die vorbereitete Schnittstelle übergibt vollständige Beobachtungen eines
Systemmodells. Der konkrete Datentyp bezeichnet das Modell; dessen vorhandene
Felder und Fachwerte bleiben erhalten. Es gibt keinen zusätzlichen Katalog
von Benachrichtigungsanlässen oder eine parallel gepflegte Liste möglicher
Zustandswechsel.

`ModelHandoff[T]` enthält einen `HandoffContext` sowie den vollständigen Wert
vor und nach einer ausdrücklich gewählten Auswertungsgrenze. Beide Werte
gehören zu demselben konkreten Modelltyp. `None` bezeichnet fehlenden Bestand;
mindestens ein Modellwert ist vorhanden. Gleiche Werte sind zulässig: Eine
ausgewertete Eingabe kann einen unveränderten Zustand ergeben.

Der Kontext enthält Übergabeidentität, Beobachtungszeit sowie optional eine
gemeinsame Anlagenreferenz, den bereits bekannten Bedienenden und eine
vorhandene Sitzungsreferenz.
Die ursprünglichen Ereignis-, Erkennungs- und Buchungszeiten bleiben in den
Fachwerten erhalten. Eine Sitzungsreferenz im Kontext gilt für alle enthaltenen
sitzungsgebundenen Werte. Eine Übergabe über mehrere Sitzungen hat dort `None`
und bewahrt die einzelnen Sitzungsidentitäten in ihren Modellwerten.
Anlagenübergreifende Aggregate verwenden entsprechend `entry_id=None` und
bewahren die tatsächlichen Anlagenidentitäten in ihren enthaltenen Modellen.
Widersprechende Anlagen- oder Sitzungsidentitäten werden abgewiesen.

## Vollständigkeit und Eigentum

Bereits vollständige unveränderliche Fachwerte können unmittelbar übergeben
werden. Verschachtelte Modelle werden als vorhandene typisierte Werte
übernommen. Die Konstruktion prüft ihre deklarierten Feldtypen und rekursiv
die Unveränderlichkeit; sie führt keine Fachregeln erneut aus.

Die zusätzlichen Ausgänge in `core/control_handoff.py` und
`core/observation_handoff.py` sowie `core/thermal_handoff.py` schließen die
Stellen, an denen ein Ergebnis bislang über mehrere Eigentümerfelder verteilt
war. Ihre Konstruktoren sind
der führende Feldvertrag. Rein lesende Fabriken übernehmen vorhandenen Zustand;
explizite Ergebnisformen erhalten die bereits berechneten Rückgaben.
Kein solcher Aufruf schreitet die Modellzeit fort oder löst einen Dienst aus.

Die Controllerausgabe erhält aktuelle und abgeschlossene Sitzungen, äußere
Steuerdaten und die getrennten automatischen und ausgegebenen Entscheidungen.
Sie ist eine Beobachtungsausgabe, kein Wiederherstellungsformat für interne
Caches. `ConsumerEventOutput` verbindet das ursprüngliche referenzgebundene
Fachereignis mit seinem tatsächlichen vollständigen Sitzungs- beziehungsweise
Präsenzbeleg. Der zugehörige Gang ist aus dem enthaltenen Modell verfügbar;
eine Suche in der inzwischen weitergelaufenen Runtime ist nicht nötig.
Ein nacktes `ConsumerEvent` genügt dieser Übergabegrenze nicht.

`DetectionEvaluation` gehört zu genau einem vorhandenen Detektorraster.
Rasterzeit und vollständige Signalfolge müssen mit den übergebenen
Erkennungen übereinstimmen. `Detector.advance()` kann mehrere Raster
auswerten: Seine gesamte Rückgabe darf nicht mit der Diagnose des letzten
Rasters gepaart werden. Beim späteren Anschluss werden die bereits vorhandenen
Originaldiagnosen und ihre zugehörigen Erkennungen gemeinsam übergeben.

`MethodEvaluation` übernimmt für andere Verfahren den vollständigen tatsächlichen
Diagnosebestand als `FrozenMap`, den Auswertungszeitpunkt und die zugehörige
Erkennungsfolge. Alte Heuristikfelder wie `holds` oder `checks` sind darin keine
Pflicht. Bereits vollständige unveränderliche Fachausgaben eines neuen
Erkennungsowners werden unmittelbar als `ModelHandoff[T]` verwendet.

Bestehende offene Diagnose- und Quellattribute werden vollständig als
`FrozenMap` kopiert. Weitere Schlüssel bleiben erhalten; veränderliche
Sammlungen werden abgelöst. Lebende HA-Objekte, zyklische Daten und nicht
endliche Zahlen sind kein Bestandteil dieser Form. Dienstresultate und
physische Geräterückmeldungen bleiben getrennte Beobachtungen.

Die Konfigurationsübergabe übernimmt den vollständigen vorhandenen
`Configuration.as_options()`-Wert zusammen mit dem bestehenden
`ConfigurationModel`. Sie definiert keine eigene Auswahl zulässiger Optionen.
Die thermischen Beobachtungsformen erhalten die vorhandenen Heizfortschritts-
und Aufheizschätzungen einschließlich ihrer Belege. Der bestehende HA-Hinweis
bei fehlendem Temperaturanstieg bleibt beim Geräteadapter; eine zweite
Hinweisentscheidung wird durch die Übergabe nicht eingeführt.

## Expliziter Verbrauch

Ein Verbraucher erhält eine vollständige Übergabe, beispielsweise über
`ModelConsumer.consume`. Die Form selbst besitzt keine Warteschlange,
Abonnenten, Wiederholungserkennung oder Zustellbestätigung. Die Herkunft des
Bedienenden wird über `ActorRef` getragen; sie wird weder aus Anwesenheit noch
aus einem Empfänger abgeleitet.

Die Auswahl der tatsächlichen Übergabegrenzen liegt bei einem späteren
Anschluss an die führenden Modelle. Ein einziger Vergleich am Ende von
`notify()` könnte Zwischenzustände mehrerer Eingänge verlieren. Auch ein
unveränderter Endzustand ersetzt keine zuvor ausgewerteten Eingaben.

Die Formen sind derzeit ausdrücklich konstruierbar und konsumierbar. Sie
werden noch nicht automatisch von der Runtime erzeugt, gesammelt oder an
einen Companion-Transport übergeben. Die Grenze und die zugehörigen
Vertragsprüfungen sind in der [Prüfanleitung](../tests/README.md) beschrieben.

## Normalisierter Steuerungseingang

Die Steuerung erhält einen normalisierten fachlichen Zustand. Auswahl und
Auswertung der Quelle liegen vor dieser Grenze. Gerätekennung, Sensor- oder
Proxybetrieb, Rohereignisse und mathematische Erkennungsverfahren gehören zu
getrennten Beobachtungen. Eine neue Erkennung muss keine alten Proxyheuristiken
nachbilden, um diesen Vertrag zu bedienen.

Gleicher Ausgangszustand, gleiche fachliche Konfiguration und gleiche Folge
normalisierter Zustände einschließlich relevanter Zeiten ergeben dieselben
Steuerungsübergänge und Ausgänge. Die Herkunft beeinflusst diese Entscheidung
nicht. Fachliche Unterschiede bleiben erhalten: unbekannte Heizrückmeldung
ist nicht AUS, unbekannte Anwesenheit ist nicht Abwesenheit, und zwei Aufgüsse
bleiben zwei unterscheidbare Vorkommnisse.

Die Formen in `core/input_handoff.py` beschreiben diesen Eingang. Sie treffen
keine Auswahlentscheidung und führen keine Erkennung aus. Insbesondere gibt
es keinen Konverter, der eine vorläufige Proxybehauptung ungeprüft zur
bestätigten Anwesenheit erklärt. Der zuständige Eigentümer liefert das bereits
ausgewertete fachliche Ergebnis. Bestehende Zulassungsregeln müssen vor der
Steuerungsgrenze erhalten bleiben, ohne sie dort ein zweites Mal auszuführen.

`InputTiming` trennt fachlichen Beginn (`effective_at`), tatsächlichen Eingang
(`received_at`), erstmalige fachliche Nutzbarkeit (`first_usable_at`) und kausale
Auswertung (`evaluated_at`). Alle vier Werte sind explizite Zeitpunkte. Die
Nutzbarkeit bezeichnet das tatsächliche Vorliegen des erforderlichen
Beleggehalts; sie wird weder aus dem früheren Beginn noch aus einer
Komfortproperty eines Ereignisses abgeleitet. Auch ein fachlich ausgewerteter
unbekannter Zustand besitzt eine Nutzbarkeitszeit. Die Beobachtungszeit der
Hülle ersetzt keine dieser Zeiten.
Vorhandene Ereignisse behalten zusätzlich ihre Erkennungs- und Buchungszeiten.
Ein später bestätigter früherer Beginn erlaubt keine rückwirkende Aktorwirkung.
Kandidatenzeiten und mathematische Belegzeiten bleiben in der Diagnose.

Die heutigen Methoden für Temperatur, Heiz- und Schützrückmeldung, Leistung,
Betrieb und Override besitzen überwiegend nur einen Zeitparameter `at`.
Beim Anschluss müssen ihre Verträge die vier Zeitbezüge getrennt erhalten;
eine bloße Umbenennung von `at` in Eingangszeit genügt nicht. `evaluated_at`
bezeichnet die kausale Auswertungsgrenze. Insbesondere ist bei Heizrückmeldungen
der tatsächliche Intervallbeginn separat zu bewahren, ohne die Controlleruhr
darauf zurückzustellen. Diese Anpassung wird durch die Formen vorbereitet und
ist noch nicht in den Methoden umgesetzt.

Ein `ControllerOutput` ist eine Beobachtung nach der Steuerungsverarbeitung.
Seine enthaltenen Quellenbelege machen ihn nicht zu einem normalisierten
Steuerungseingang. Ereignisidentität und Übergabeidentität bleiben verschieden;
es wird kein zusätzlicher globaler Revisionsmechanismus eingeführt.

### Anwesenheit, Türvorgang und Aufguss

`OccupancyInput` erhält neben Belegung und Verfügbarkeit zwei getrennte
Fachaussagen: `person_evidence` bezeichnet einen bereits zugelassenen
Personenbeleg; `qualified_presence` bezeichnet den qualifizierten Beleg für den
übergebenen Belegungszustand, auch für Abwesenheit. Keine dieser Aussagen ist
bereits eine Gangentscheidung. Türfolge, Temperaturzulassung, Gangbestätigung
und Gangende verbleiben beim bisherigen Gangowner. Ein zugelassener
Personenbeleg kann dort die Kühlung auf ihre Mindestdauer begrenzen, ohne einen
Gang zu bestätigen. Der Verbraucher benötigt dafür keine Quellenkennung.

Die vorhandene Berichtsidentität bleibt in `report_id` erhalten; `None`
bedeutet, dass kein Bericht zugrunde liegt. `events` enthält die tatsächlich
vorhandenen vollständigen Ereignisse in Originalreihenfolge. Dadurch bleiben
auch starke und schwache Personenbelege für den vorhandenen
`Gang.recognition_kind` unterscheidbar. Bestätigungs- oder Endeereignisse werden
nicht vorweg erzeugt. Unbekannt oder unverfügbar bedeutet keine gemessene
Abwesenheit. Veralteter Belegverlust kann nach vorgelagerter Auswertung einen
unveränderten Normalzustand ergeben; eine neue Rücknahme-ID ist nicht nötig.

`DoorInput` führt Öffnung, Lüftungsbefund und Schluss in einer gemeinsamen
Ereignisfolge. Ihre Reihenfolge bleibt auch bei gleichen Buchungszeiten
erhalten. `DoorOperation` übernimmt die bereits zugeordnete Öffnung, den
optionalen Schluss und den optionalen Lüftungsbefund. Die Öffnungs-ID
identifiziert den Vorgang. `ventilation_known=False` bezeichnet fehlendes
Wissen; `True` mit fehlendem Lüftungsbefund bezeichnet einen tatsächlich
ausgewerteten, nicht bestätigten Befund. Ein leerer Ereignisbestand erzeugt
diesen Wert nicht automatisch. Lage und Ereignisfolge bleiben auch ohne
gespeicherte Öffnung übergebbar. Die Timeline bleibt der einzige
Zuordnungsowner; die Form rekonstruiert keine Türvorgänge.

`InfusionInput` bewahrt alle bestätigten Aufgussereignisse mit ihren bestehenden
Identitäten. `InfusionEpisodeDiagnostics` übernimmt getrennt davon den vorhandenen
veröffentlichten Episodendiagnosebestand: Phase, Beginn, Bestätigungszeit, Ende,
Endbeleg, Unterbrechungsstatus sowie Wasser-, Abfall- und Niveauwerte. Ein
fehlendes Ende ist kein belegter Abschluss. Fortsetzung oder Ende erzeugen
keinen zusätzlichen Aufguss. Der Episodenoutput wird beim Erkennungsowner
gelesen und nicht aus bestätigten Ereignissen zurückgerechnet. Er ist kein
Restoreformat der privaten mathematischen Arbeitsdaten. Der bisherige Owner
besitzt keine stabile Episoden-ID; eine Zuordnung seiner späteren Beobachtungen
zu einem bestätigten Ereignis muss beim Anschluss ausdrücklich erhalten werden
und darf nicht aus Kandidaten-ID oder Beginn geraten werden.

Lage, Belegung und Vorgangszuordnungen sind Zustandswerte. Die enthaltenen
`events` beziehungsweise `occurrences` sind der geordnete Eingangsbestand der
konkreten Auswertungsgrenze, keine erneut abzuspielende Gesamthistorie.
`ModelHandoff.before/after` beobachtet diese vollständigen Übergabewerte.
Die Hülle übernimmt weder Wiedergabesteuerung noch Wiederholungserkennung.

## Modelle und Anpassung der Verbraucher

Die Zuordnung umfasst fachliche Modelle und eigenständig konsumierte
Systemausgaben. Der vollständige Feldvertrag steht im jeweiligen Datentyp.
Vorhandene unveränderliche Fachtypen werden direkt mit `ModelHandoff[T]`
verwendet; Untermodelle erhalten keine parallelen Wrapper.

| Nr. | Modell und Form | Erzeuger | Nötige Verbraucheränderung beim Anschluss |
| --- | --- | --- | --- |
| 1 | Konfiguration: `ConfigurationOutput`, `ConfigurationModel`, `ButtonCapabilities` | `Configuration.as_options()`, bereits berechnetes `Runtime.configuration_model` | Setup, Optionslistener, Settings, API und Entitäten übernehmen denselben Konfigurationswert. Die wirksame Quelle darf nicht durch erneutes Ableiten nur aus gespeicherten Optionen verloren gehen. |
| 2 | Messung: `Measurement`, `ObservedSource`, `DeviceObservation` | `HADevice.ingest` | Archiv und Diagnose erhalten Originalwert, Zeiten und Herkunft; die Steuerung erhält daraus bereits ausgewählte fachliche Eingänge. |
| 3 | Regeltemperatur: `TemperatureInput` | Geräteadapter nach Messwertauswahl | `Controller.set_temperature` erhält Wert und Gültigkeitsende; sein bisheriger einzelner Zeitparameter wird gemäß der getrennten Zeitbezüge angepasst. Keine Entitäts- oder Messhöhenauswahl im Controller. |
| 4 | Heizrückmeldung: `HeatingFeedbackInput`; Beleg: `HeatingObservation` | Geräteadapter nach Rückmeldungsauswahl | `report_heating` verbraucht den normalisierten Dreifachwert. Der alte `report_fallback_heating`-Pfad wird in die vorgelagerte Auswahl/Berechnung eingeordnet. Gemessene und geschätzte Energie bleiben fachlich verschieden. |
| 5 | Schütz und Leistung: `ContactorFeedbackInput`, `PowerFeedbackInput`, `ContactorMark` | Geräteadapter | `report_contactor` und `report_power` übernehmen getrennte Zustände und Zeiten. Fehlende Leistung wird nicht durch null Watt ersetzt. |
| 6 | Anwesenheit: `OccupancyInput`; Belege: `PresenceReport`, `PresenceObservation`, `PresenceOutput`, `PresenceSourceSelectionSnapshot` | Präsenznormalisierung vor der Steuerung | `process_presence`, `observe_direct_presence` und Präsenzabstimmung konsumieren Belegung und qualifizierten Beleggehalt einschließlich Bericht-/Ereignisidentitäten. Quellenprüfung und Wechselentscheidung liegen davor; Gangzulassung bleibt beim Gangowner. Der Quellenwahlsnapshot übernimmt sechs vorhandene Zustandswerte und trifft keine Wechselentscheidung. |
| 7 | Türvorgang: `DoorInput`, `DoorOperation`, `Door` | Ausgewertete Erkennung oder direkte Zustandsübernahme; vorhandene Timeline-Zuordnung | `Controller.process` und `timeline.apply` übernehmen die gemeinsame geordnete Folge einschließlich Lüftung. Der letzte Zustand allein darf Öffnen und Schließen nicht verschlucken; gleiche Buchungszeiten dürfen nicht umsortiert werden. |
| 8 | Aufguss: `InfusionInput`; Diagnose: `InfusionEpisodeDiagnostics`; vollständiger Fachoutput direkt als `ModelHandoff[T]` | Bestätigende Erkennung und lesender Episodenoutput | Controller und Timeline übernehmen bestätigte Vorkommnisse samt Identitäten und Zeiten. Verlauf/Statistik konsumieren Fortsetzung und Ende separat aus dem führenden Episodenoutput; beim Anschluss wird die tatsächliche Ereignisbindung erhalten, keine Episode aus Events rekonstruiert. Die Diagnoseform ersetzt diesen Fachoutput nicht. |
| 9 | Lüftungsstadium innerhalb des Türvorgangs: `DoorOperation.ventilation`, `ventilation_known` | Bestehender Tür-/Timelineowner | Controller, Timeline und Türwärmemodell übernehmen Befund und Gültigkeit desselben Vorgangs. Es gibt keinen unabhängigen Lüftungszustand oder zweiten Zuordnungsowner. |
| 10 | Bedienzustand: `OperationInput`, `ControlModeInput`, `HeaterOverrideInput` | Bestehende Bedienverarbeitung | `set_operation`, `set_control_mode`, `set_heater_override` erhalten getrennte Zustände. Betrieb, Betriebsart und Rückgabe an Automatik bleiben verschieden. |
| 11 | Taster: `ButtonGestureSnapshot`, `ButtonEvaluation` | `ButtonGestures`, `Runtime._handle_button_event` | Runtime verwendet das bereits berechnete Gestenergebnis. Beobachter erhalten fortbestehenden Gestenzustand getrennt von Eingabe und Ergebnis; keine erneute Gestenauswertung. |
| 12 | Erkennung: `DetectionEvaluation`, `MethodEvaluation`, `Detection` | Erkennungsowner je Auswertungsgrenze | Runtime und Archiv paaren Originaldiagnose und genau ihre Erkennungen. Andere Verfahren benötigen keine künstlichen Alt-Heuristikfelder. Diagnose bleibt vom normalisierten Steuerungszustand getrennt. |
| 13 | Sitzung, Gang, Verlauf: `Session`, `Gang`, `Timeline`, `Event` | Controller und `timeline.apply` | Archiv, Ereignisverbraucher und Darstellung übernehmen vollständige Eigentümerwerte. Fachereignisse werden mit ihrer damaligen Sitzung verbunden. |
| 14 | Steuerungsausgabe: `ControllerOutput` | `ControllerOutput.capture` | API und Beobachter konsumieren denselben Snapshot statt einzeln veränderliche Controllerfelder zu lesen. Capture löst keine Auswertung aus. |
| 15 | Thermostat: `ControlInputs`, `ThermostatState`, `Decision` | Bedarfsbildung, `thermostat.evaluate` | Thermostat übernimmt quellenfreie Bedarfspegel. Zustand und Entscheidung bleiben getrennt; Quellenabhängigkeiten werden vor der Bedarfsübergabe aufgelöst. |
| 16 | Temperaturprogramm: `TemperatureProgram`, `TemperatureControl` | Temperaturprogramm und `temperature_control` | API, Climate- und Number-Entitäten übernehmen berechnete Ziele und Bedienprojektion; keine zweite Sollwertberechnung. |
| 17 | Heizzeit und Energie: `HeatingTime`, `Energy` | `heating.report/advance`, `energy.advance` | Controller, Sensoren und Archiv übernehmen vollständige Folgezustände einschließlich gemessener, geschätzter und unbekannter Anteile. |
| 18 | Kühlung und zeitgebundene Phasen: `OvenCoolingResult`, `TimedPhase`, `Deadline` | Kühlungsberechnung und Controller | Phasenverarbeitung und Darstellung konsumieren den vorhandenen Berechnungsbeleg. Deadline-Token behalten ihre bestehende Eigentums- und Ersetzungswirkung. |
| 19 | Temporäre Türwärme: `TemporaryDoorHeatState`, `DoorHeatTransition` | Bestehende Türwärmeauswertung | Controller übernimmt Zustand und Anforderung; keine zweite Türwärmeentscheidung im Verbraucher. |
| 20 | Heizfortschritt: Trend-, Fortschritts- und Episodenformen in `thermal_handoff.py` | Fortschrittsauswertung im Geräteadapter | Hinweisverarbeitung und Darstellung konsumieren Ergebnis samt Belegen. Die Entscheidung über fehlenden Temperaturanstieg verbleibt beim bestehenden Eigentümer. |
| 21 | Aufheizschätzung: `WarmupEstimateSnapshot`, `WarmupSourceContext`, `WarmupEstimateObservation` | Aufheizschätzung im Geräteadapter | Startverfügbarkeit und Darstellung konsumieren die bereits unter Quellen- und Gültigkeitsprüfung gelesene Schätzung. |
| 22 | Licht: `LightPlan`, `LightOutputSnapshot`, `LightQuantizerSnapshot`, `LightQuantization`, `LightAfterRun` | Lichtregelung, Planer und Quantisierung | Geräteadapter übernimmt den Plan; Beobachter erhalten Zustand und tatsächliches Ausgabeergebnis getrennt von physischer Rückmeldung. Der Quantizersnapshot erhält auch einen Reset ohne anschließende Planauswertung. |
| 23 | Geräteausgabe und Diagnose: `HeaterServiceResult`, `LightServiceResult`, `DeviceDiagnostics` | Bestehende Geräteaufrufe und Rückmeldungen | Runtime, Archiv und Diagnose erhalten vorhandene Aufrufresultate. Ein erfolgreicher Dienstaufruf beweist keine physische Heizwirkung. |
| 24 | Phasen und Messfenster: `PhaseProjection`, `PhaseInterval`, `ReadinessPause`, `MeasurementWindow` | `project_session/project_archive`, `measurement_window` | Archiv und Darstellung übernehmen vorhandene Projektion samt Korrekturen und Vollständigkeit; keine zweite Rekonstruktion. |
| 25 | Umgebung: `EnvironmentSnapshot` mit Unterformen | `environment_snapshot` | State-API übernimmt die vollständige Anzeige. Diese Umgebungsdaten sind keine zusätzlichen Saunasteuerungseingänge. |
| 26 | Archiv: Record-, Sitzungslisten- und Sitzungsergebnisformen in `system_handoff.py` | Ergebnisse von `Archive.read` | Archiv-API und Export konsumieren geschlossene Ergebnisse. Leere Liste und nicht gefundene Sitzung bleiben verschieden; bestehende offene Payloads bleiben vollständig erhalten. |
| 27 | Darstellung: `PanelSnapshot`, öffentliche Form in `presentation_handoff.py` | Vollständiger bereits berechneter State-API-Wert | API serialisiert die Form. Öffentliche Projektion verwendet ausschließlich `public_state`; entfernte Adminfelder werden nicht als `None` wieder eingeführt. Das Panel behält seinen Wirevertrag. |
| 28 | Empfänger: `RecipientAssignment`, `RecipientResolution`, `ResolvedRecipient`, `SessionRecipients` | Auflösung, Zuordnung und Akkumulation | Späterer Benachrichtigungsverbraucher übernimmt die aufgelöste Menge. Fähigkeiten und Bedienidentität werden nicht aus Anwesenheit abgeleitet. |
| 29 | Austausch: Envelope- und Korrelationssnapshots in `interaction_handoff.py` | Vorhandene Transportresultate und `ReplyCorrelator` | Beobachter erhalten vollständige Registrierungen und Antwortidentitäten. Opaquer Transport bleibt unverändert; nur beobachtbare unveränderliche Daten gehören in den Snapshot. |
| 30 | Fachereignisverbrauch: `ConsumerEventOutput` | Ereignis mit damaliger vollständiger Sitzung beziehungsweise Präsenzmeldung | `_publish_consumer`, Archiv und spätere Benachrichtigungsverbraucher erhalten die geschlossene Eigentümerbeobachtung statt eines nackten Gangverweises. |

Backupbarrieren, Workerqueues, HTTP-Anfragen, Registryobjekte, Setup/Unload und
Abonnentenverwaltung sind Infrastrukturmechanismen. Handles und Locks sind
keine zusätzlichen Fachmodelle. Ihre fachlich sichtbaren Ergebnisse liegen in
den genannten Konfigurations-, Archiv-, Diagnose- und Darstellungsformen.

Die Tabelle beschreibt erforderliche Anschlussänderungen, keine bereits
erfolgte Umstellung der Runtime. Ein Formnachweis zeigt tatsächlich übernommene
Werte, erhaltene Zeit- und Identitätsbezüge und Unabhängigkeit von späterer
Mutation. Ein bestandener Test belegt keine angeschlossene Steuerungsfunktion,
Companion-Zustellung oder reale Gerätewirkung.
