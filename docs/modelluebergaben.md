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

Der Kontext enthält Anlagen- und Übergabeidentität, Beobachtungszeit sowie
optional den bereits bekannten Bedienenden und eine vorhandene Sitzungsreferenz.
Die ursprünglichen Ereignis-, Erkennungs- und Buchungszeiten bleiben in den
Fachwerten erhalten. Eine Sitzungsreferenz im Kontext gilt für alle enthaltenen
sitzungsgebundenen Werte. Eine Übergabe über mehrere Sitzungen hat dort `None`
und bewahrt die einzelnen Sitzungsidentitäten in ihren Modellwerten.
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
