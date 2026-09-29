# Daten- und Einstellungsverträge

Die gemeinsamen Datenklassen stehen in `core/contracts.py`. Sie verbinden
Quellen, Ablaufkern und Anzeige. Die Runtime übernimmt die zeitliche Verarbeitung;
der Controller führt den fachlichen Zustand, der Geräteadapter die Ausgabe.

## Präsenz und Verbraucherereignisse

| Vertrag | Inhalt und Bedeutung |
| --- | --- |
| `PresenceReport` | `report_id` identifiziert die Meldung. `occupancy` unterscheidet `present`, `absent` und `unknown`. `assertion` beschreibt die Aussageart; `source` und `source_ref` erhalten Quelle und Beleg. `available` beschreibt die Verfügbarkeit der Quelle. |
| `ConsumerEvent` | `event_id` ist die stabile Ereignisidentität. `kind`, `session_id`, `gang_id` und `source_ref` ordnen die fachliche Änderung ihrer Sitzung, ihrem Gang und ihrem Beleg zu. Ein enthaltenes `presence` trägt den zugehörigen Präsenzbericht. |

Beide Verträge verwenden `effective_at` für den fachlichen Zeitpunkt und
`received_at` für den Zeitpunkt, zu dem die Aussage im jeweiligen Vertrag
verfügbar wurde. Bei abgeleiteten Gangereignissen stammt dieser zweite Zeitpunkt
aus der Erkennung beziehungsweise Zustellung der zugrunde liegenden Änderung.
Der Ablaufkern unterscheidet daneben die Buchungszeit eines `Event`.

[Zeitbezüge und Reihenfolge](zeitmodell.md)

Eine `proxy_retraction` nimmt den Beleg für eine bisher angenommene Belegung
zurück und meldet `unknown`. Beobachtete Abwesenheit wird durch `absent`
ausgedrückt. Eine Rücknahme gehört zu ihrem ursprünglichen Beleg; eine jüngere
Präsenzmeldung mit anderer Referenz bleibt eigenständig.

Die Proxyquelle führt die Gangzuordnung. Direkte Präsenzmeldungen werden mit
ihrem eigenen Herkunfts- und Verfügbarkeitsnachweis beobachtet.

[Präsenzquellen](praesenz-ofen-phasen.md#präsenzquellen-im-aktuellen-programm)

`ConsumerEvent.delivery` unterscheidet `live` und `archive_correction`.
Archivkorrekturen aktualisieren Zuordnung und Darstellung. Gerätebefehle
entstehen im aktuellen Regelzyklus aus der aktuellen Entscheidung des
Controllers. Die stabile Ereignisidentität ermöglicht eine einmalige Zustellung
auch über erneute Archivabfragen hinweg.

## Regelanforderungen und Phasenansicht

`ControlInputs` fasst die laufenden Anforderungen zusammen:
`gang_heat_demand` beschreibt den Heizbedarf des Gangs,
`temporary_door_heat` die bestehende Türhilfe und `cooling` die Ofenkühlung.
Der Controller wertet diese Anforderungen zusammen mit Betriebsart, Betrieb-AUS
und technischen Schutzbedingungen aus. `gang_veto` und `door_request` sind
kompatible Namen für die beiden ersten Werte und lesen denselben Zustand.

`PhaseProjection` enthält Hauptphasenintervalle und einzelne Bereitschaftspausen.
Grundlage sind `Session.base_phases`, `Session.contactor_history` sowie die
Gang- und Ofenkühlungsintervalle. Jedes ausgegebene Intervall hat genau eine
Hauptphase. `complete` und `corrections` kennzeichnen Vollständigkeit und
Korrekturhinweise. Die gespeicherte Schützspur bleibt die maßgebliche Beobachtung;
die Projektion ordnet ihr eine fachliche Ansicht zu.

## Einstellungen übernehmen

Die Programmauswahl führt Bedienmodus und benannte Programmkennung getrennt.
Im Panel enthält eine benannte Auswahl den Bedienmodus `program` und ihre
vollständige ID; `individual` bezeichnet als Bedienmodus ein freies Programm.
Eine gültige benannte ID `individual` bleibt eine benannte Auswahl. Entwurf, Beschriftung,
gespeicherte Auswahl und Übernahme verwenden diese Zuordnung gemeinsam.
Der bestätigte Konfigurationsstand führt die benannte ID in `selected_program_id`.
Der Programmauftrag übermittelt benannte IDs als `profile`, beispielsweise
`{"profile":"individual"}`. Auch IDs mit Doppelpunkten bleiben vollständig.

Der gemeinsame Einstellungspfad prüft einen vollständigen Konfigurationskandidaten,
einschließlich der Innenstufen eines freien Temperaturprogramms. Ein gültiger
Kandidat wird übernommen; bei Abweisung bleibt der bisherige Options- und
Laufzeitstand wirksam. Auch der Optionsflow einer ungeladenen Integration verwendet
diesen vollständigen Kandidaten.

Ein vollständiges Parameterformular erhält das laufende Temperaturprogramm,
wenn sein mitgesendeter Sollwert unverändert ist. Eine direkte Sollwertwahl über
die partielle Temperaturschnittstelle ist eine ausdrückliche Auswahl,
auch bei derselben Zahl. Das Panel übermittelt den Sollwert im Vollformular;
`settings.py` entscheidet zentral über die Bedeutung der Änderung.

Wird ein freies Programm durch eine neue Start-/End-/Verteilungsvorgabe ersetzt,
bilden gespeicherte Werte und aktive Stufen denselben gewählten Stand ab. Gültige
Live-Änderungen werden in den bestehenden Controller übernommen und erhalten die
laufende Sitzung.

[Änderbarkeit](parameter.md) · [Bedienrechte](bedienung.md)

Die Antworten auf Änderungen von Betriebsart und Tasterprogramm stammen aus
dem unveränderlichen Ergebnis des gemeinsamen Einstellungsschreibers. Der
Antwortstand gehört damit zu der tatsächlich gespeicherten Konfiguration,
auch bei einem Runtimewechsel während des Einlesens des HTTP-Bodys.

## Tasterereignisse

Der Geräteadapter normalisiert Tasterereignisse für `core/button.py`.
Ein gültiger neuer Ereigniszeitstempel kennzeichnet eine neue Meldung.
Der Adapter verarbeitet denselben Zeitstempel einmal und filtert ältere
Zustellungen.
Native Druck- und Loslassmeldungen sowie die Langklassifikation gehören zu
derselben Geste. Binärtaster melden ihre Druck- und Loslassflanken; die Runtime
prüft die Haltezeit mit dem Empfangszeitbezug.

Eine reine `short`- oder `long`-Klassifikation wird auch ohne vorherige Druckflanke
verarbeitet. Jede neue eigenständige Langklassifikation erhält den aktuellen
Betriebskontext. Ein Start bei AUS verbraucht seine eigene Geste. Erst eine neue
lange Geste bei laufendem Betrieb erzeugt den Sitzungsabschluss `END_HOLD` und
fordert Licht-AUS an. Eine reine Langklassifikation liefert für sich den
Langdrucknachweis. Der bestätigte Loslassnachweis erzeugt `END_RELEASE` und
startet den Lichtnachlauf.

[Tasterbedienung](betrieb.md#bedienhandlungen-und-betriebsart)

## Technisches Neuladen und Geräteübergabe

Scheitert das technische Neuladen, stellt die Rücknahme Optionen, Startfreigabe
und Lichtzuständigkeit für den noch aktuellen, offenen bisherigen Runtimebesitzer
wieder her. Sie prüft dazu die Identität der Runtime und den weiterhin gültigen
Optionsauftrag. Neuere Optionen sowie ein bereits ersetzter oder geschlossener
Besitzer behalten ihren eigenen Stand. Home Assistants Entladefehler bleibt
sichtbar.

Beim Wechsel der Leuchtenzuordnung wartet die Übergabe begrenzt auf laufende
Ausgaben an die bisherige Leuchte. Deren tatsächlicher Dienstabschluss geht dem
abschließenden AUS voraus; die beobachtete AUS-Rückmeldung bestätigt die Übergabe.
Die Zeitgrenze des Wartenden und die Lebensdauer des tatsächlichen Dienstes sind
getrennt. Ein noch laufender Dienst behält seinen Herkunftsbeleg bis zum Abschluss.

Jede Runtimeübergabe wartet auch bei gleicher Leuchtenzuordnung vor dem
Plattformentladen auf den tatsächlichen Abschluss alter Lichtdienste. Bei
technischen Optionsänderungen erfolgt diese Prüfung vor dem Home-Assistant-Reload.
Die Freigabe setzt den Abschluss innerhalb des vorhandenen Dienstbudgets voraus.
Andernfalls bleiben die bisherige Runtime und ihr Archiv zuständig und die
Optionsänderung wird zurückgenommen. Die Lichtausgabe bleibt während dieser
Rücknahme entzogen und wird mit den wiederhergestellten Optionen freigegeben.

Ein direkter Runtimeabschluss versucht zuerst Ofen-AUS. Eine weiterhin laufende
Lichtaufgabe behält das offene Archiv für ihren Abschluss. Der Runtimeabschluss
kann erneut versucht werden; die Übernahme durch eine neue Runtime setzt den
erfolgreichen Abschluss des bisherigen Besitzers voraus.

Archivierte Originaldaten bleiben auch bei einer späteren Neuzuordnung von
Geräten ihrer ursprünglichen Quelle zugeordnet.

[Archivzugriff, Seitenzeiger und Downloadberechtigung](speicherung.md)
