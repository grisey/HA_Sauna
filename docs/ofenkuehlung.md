# Ofenkühlung

Die Ofenkühlung hält den Ofen für eine aus dem bisherigen Betrieb berechnete
AUS-Laufzeit ausgeschaltet. Der technische Zustandsname lautet `after_run`.

## Auslösung

Im Automatikbetrieb löst ein abgeschlossener, bestätigter Gang die Ofenkühlung
aus. Mit direkter Präsenz beendet ein vollständiger Türvorgang mit zugehöriger
Abwesenheitsmeldung den Gang. Im Proxyverfahren beendet bestätigtes Durchlüften
einen durch Aufguss bestätigten Gang. Die Regeln stehen im [Gangmodell](gangmodell.md). Betrieb-AUS führt den eigenen Ausschaltablauf aus:
Der Gang endet, die Steuerung fordert Ofen-AUS an und das Licht folgt dem
[Ausschaltablauf](betrieb.md#licht).
In der Betriebsart Manuell folgt die Heizanforderung der Bedienwahl und der
technischen Freigabe.

Die Kühlanforderung fordert sofort Ofen-AUS. Ihr Countdown beginnt mit
der bestätigten Schützstellung AUS. Zu diesem tatsächlichen Beginn berechnet
und speichert die Steuerung die Dauer. Für die gesamte laufende Kühlung gilt
dieser gespeicherte Wert.

## Laufzeit und Abschluss

Ausschließlich bestätigte AUS-Laufzeit zählt zur Kühlung. Bei Schütz-EIN oder
unbekannter Rückmeldung hält die Uhr ihren Rest; die Kühlanforderung fordert
weiterhin Ofen-AUS. Die Heizfreigabe bleibt für die angeforderte und laufende
Ofenkühlung gesperrt, auch bei Tür- oder Personensignalen und manueller Ofenwahl.

Benutzer mit Home-Assistant-Bedienrechten für den Saunabetrieb können die
Ofenkühlung über die dafür vorgesehene [Bedienhandlung](bedienung.md)
ausdrücklich vorzeitig beenden.
Eine solche Verkürzung und Betrieb-AUS bewahren den
bisherigen Bemessungszeitraum. Ausschließlich eine vollständig durchlaufene
Ofenkühlung setzt dessen Beginn auf ihren Abschluss.

Der Bemessungszeitraum endet am tatsächlichen Beginn der neuen Ofenkühlung.
Er beginnt am Ende der letzten vollständig abgeschlossenen Ofenkühlung;
für die erste Kühlung der Sitzung gilt der Sitzungsbeginn.

## Berechnung der Dauer

Die Basisdauer `after_run_minutes` beträgt standardmäßig **5 Minuten**. Die
Höchstdauer `oven_cooling_max_minutes` beträgt **15 Minuten**. Bei der Übernahme
älterer Konfigurationen ohne gespeicherte Höchstdauer wird eine Basisdauer
oberhalb dieses Standardmaximums zugleich als Höchstdauer übernommen. Eine
ausdrücklich eingestellte Höchstdauer muss mindestens der Basisdauer entsprechen.

Die Berechnung gewichtet jüngere Zeiten stärker als ältere. Das Gewicht eines
Zeitpunkts hängt von seinem Alter in Minuten zum Kühlbeginn ab:

\[
w(Alter)=2^{-Alter/h}
\]

Die Halbwertszeit \(h\) ist über `oven_cooling_half_life_minutes` einstellbar und
beträgt standardmäßig **15 Minuten**. Ein 15 Minuten alter Zeitpunkt erhält
halbes Gewicht, ein 30 Minuten alter ein Viertel. Die Berechnung integriert
analytisch über die tatsächlichen Intervallgrenzen und verwendet für alle
Zeitanteile denselben Kühlbeginn als Bezug.

**H** bezeichnet die gewichtete Zeit mit bestätigtem Schütz-EIN.
**I** bezeichnet die gewichtete Bereitschaftszeit bei Betrieb-EIN und bestätigtem
Schütz-AUS. Diese Bereitschaftszeit stammt aus der korrigierten Phasenansicht.
Jeder Zeitpunkt geht einmal in die Berechnung ein. Zeiten mit unbekannter
Schützstellung erscheinen als unvollständiger Beleg; die Anrechnung als
Bereitschaftszeit setzt bestätigtes Schütz-AUS voraus.

Das Verhältnis \(r\) ist über `oven_cooling_heat_idle_ratio` einstellbar und
beträgt standardmäßig **2**. Die Dauer in Minuten lautet:

\[
\text{Basis}+\operatorname{clip}(H/r-I,\,0,\,
\text{wirksames Maximum}-\text{Basis})
\]

`clip` begrenzt die Zusatzdauer auf die angegebenen Grenzen. Die Steuerung
verrechnet zuerst die vollständigen gewichteten Heiz- und Bereitschaftszeiten
und begrenzt anschließend das Ergebnis. Mit den Standardwerten ergibt sich
`5 + clip(H / 2 - I, 0, 10)` Minuten. Eine gewichtete Minute Bereitschaft gleicht
zwei gewichtete Heizminuten aus. Ein verbleibender positiver Heizanteil
verlängert die Basisdauer.

Technische Schutzgründe wirken gemäß der
[Heizpriorität](praesenz-ofen-phasen.md).

## Nachvollziehbarkeit

Der gespeicherte Berechnungsbeleg enthält die verwendeten Einstellungen, die
gewichteten Zeitanteile und den betrachteten Zeitraum. Die Qualitätsangabe
`complete` bezeichnet eine vollständige Grundlage aus Schalterrückmeldungen
und Phasen. `incomplete` kennzeichnet Lücken oder unbekannte Zustände.

`contactor_history` bewahrt die tatsächlichen Schalterrückmeldungen. Die
historische Phasenansicht ordnet die Betriebsabschnitte rückblickend und
überlappungsfrei zu. Gerätebefehle entstehen ausschließlich aus dem aktuellen
Steuerungszustand zur tatsächlichen Verarbeitungszeit. Abgeschlossene Kühlzyklen
stehen als gespeicherter Verlauf zur Verfügung.
