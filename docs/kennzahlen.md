# Kennzahlen und Statistik

Kennzahlen abgeschlossener Sitzungen und die Statistik über mehrere Sitzungen
werden an einer Stelle im Fachkern berechnet
([`core/key_figures.py`](../custom_components/ha_sauna/core/key_figures.py)).
Jede Kennzahl kommt als Rohwert und als gerundeter Anzeigewert
([`core/key_figure_display.py`](../custom_components/ha_sauna/core/key_figure_display.py));
die Oberfläche setzt nur Einheit, Dezimalkomma und Zeitformat. Die Nummern
(K-S, K-G, K-T, K-E) folgen dem Kennzahlenkatalog des Pakets B1.

## Quellen

| Quelle | Inhalt |
| --- | --- |
| Messwerte | Temperatur und relative Feuchte oben und unten der Sitzung mit ihrer Empfangszeit. Absolute Feuchte aus Temperatur und relativer Feuchte derselben Position. |
| Erkennung | Türvorgänge und Aufgüsse aus den archivierten Erkennungen, Aufgussende aus `infusion_update`, Gänge aus der Gangprojektion der Zeitleiste ([Gangmodell](gangmodell.md)). |
| Steuerung | Letzter Sitzungsstand: Phasen, Bereitverlauf, Sollverlauf, Heizintervalle, Energie, Ofenkühlungen. |
| Parameter | Gespeicherte Konfiguration der Sitzung. Fehlt sie, gilt die der nächsten späteren Sitzung; die Herkunft steht als `parameter_origin` dabei. Die heutige Einstellung gilt nicht rückwirkend. |
| Einstellungen | **Kabinenvolumen** und **Strompreis** aus der aktuellen Integrationskonfiguration ([Einstellungen](parameter.md)). |
| Gelernte Größe | Nachlauf des Temperaturfühlers nach dem Türschließen (siehe unten). |
| Umgebung | Archivierte [Umgebungseinträge](speicherung.md#umgebung-und-erkennungsverlauf) der Sitzung (`environment`). |
| Erkennungsverlauf | Archivierte verworfene Kandidaten und ihre Episoden (`recognition_rejected`, `recognition_episode`). |

Die Außentemperatur zum Sitzungsbeginn ist der Wert des Umgebungseintrags bei
Sitzungsbeginn, also der Wert, der zu diesem Zeitpunkt in Home Assistant aktuell
war, mit der Messzeit der Quelle (`outside_temperature_measured_at`; ohne
Messzeit fehlt sie). Für einen Zeitpunkt in der Sitzung gilt der letzte
Umgebungseintrag davor. Eine Altersgrenze gibt es nicht. Sitzungen ohne
Umgebungseintrag (ältere Sitzungen oder ohne gebundene Quelle) haben keine
Außentemperatur (K-S20) und zählen nicht für den Kälterekord.

Wert zum Zeitpunkt ist der letzte Messwert zum Zeitpunkt oder davor; liegt der
Zeitpunkt vor dem ersten Messwert, gilt dieser, wenn er höchstens 5 s später
kommt. Fehlende Daten ergeben einen fehlenden Wert, nicht 0.

## Sitzung und Gang

| Bereich | Kennzahlen |
| --- | --- |
| Gänge (K-S2 bis K-S8, K-G1 bis K-G9) | Anzahl, Dauern, Saunazeit, Ruhe, Aufgüsse, Höchsttemperatur mit Einordnung und Soll, Eintritts- und Austrittstemperatur beim Öffnen der Tür, Feuchteanstieg und Wasser je Aufguss, höchste Feuchte, Soll und Sollabweichung im Gang. |
| Aufheizen (K-S9 bis K-S11, K-S15, K-S16, K-S22, K-S23) | Vom ersten Heizintervall bis zum ersten Bereit oder zum Öffnen der Eintrittstür des ersten Gangs, je nachdem was früher kommt; Art, Temperatur und Soll am Ende, Energie davor und danach, Türöffnungen, Anstieg je kWh. |
| Anlage (K-S12 bis K-S14, K-S17 bis K-S19, K-S24, K-S25) | Heizzeit und Heizanteil, Energie mit Gegenprobe und Kosten, Heizen in den Gängen, Ofenkühlungen mit auslösendem Gang, Regelung im Bereit, Sollwerte und Programmart. |
| Türvorgänge (K-S21) | Alle Paare Öffnen → Schließen der Sitzung mit Klasse Eintritt, Austritt, Schlusslüften oder sonstige; offen, Verlust und Wiedererreichen. |
| Außentemperatur (K-S20) | Außentemperatur zum Sitzungsbeginn mit ihrer Messzeit. |
| Verworfene Kandidaten (K-E2) | Öffnungskandidaten mit unpassender Kurvenform (`candidates`); davon endgültig verworfen (`rejected`), in Episoden ohne Übernahme (`rejected_in_episodes`, Episoden `rejected_episodes`) oder einzeln (`rejected_single`). Kandidaten einer über die Anwesenheit übernommenen Episode zählen als Türvorgang (`taken_as_door`, Episoden `door_episodes`), nicht als verworfen. |

Bereit stammt aus dem Bereitverlauf des Sitzungsstands (`ready_history`), in
Automatik und im manuellen Modus
([Bereitschaft](betrieb.md#aufheizen-und-bereitschaft)); gezählt wird es nur bei
eingeschaltetem Betrieb. Ältere Sitzungen ohne diesen Verlauf verwenden die
Phase Bereit aus `base_phases`; im manuellen Modus haben sie keine, ihr Aufheizen
endet beim ersten Gang. Die Regelung im Bereit verwendet dieselben
Bereit-Intervalle.

Eintritt ist der Türvorgang, dessen Schließen genau der Gangbeginn ist;
Austritt die letzte Öffnung im Gang; Schlusslüften eine Öffnung nach dem Ende
des letzten Gangs. Fehlt einem Gang ein belegter Beginn oder ein belegtes Ende,
fehlen seine Dauer und die davon abhängigen Summen, Mittel und Einordnungen;
Türvorgänge, deren Klasse von dieser Grenze abhängt, bleiben ohne Klasse. Ein
Aufguss ohne erkanntes Ende hat keinen Feuchteanstieg. Ohne Kabinenvolumen gibt
es keine Wassermenge, ohne Strompreis keine Kosten. Die verworfenen Kandidaten
fehlen bei Sitzungen ohne archivierten Zustandsverlauf der Erkennung
(`recognition_state`), also bei älteren Sitzungen.

Der Türverlust ist die Temperatur beim Öffnen minus der kleinsten Temperatur
vom Öffnen bis zum Schließen plus Nachlauf des Fühlers. Der Nachlauf ist der
Median der Zeit vom Schließen bis zum Tiefpunkt bei Türvorgängen, nach denen
die Temperatur vor dem nächsten Ereignis (Öffnung, Aufguss, Betrieb aus) wieder
den Wert beim Öffnen erreicht. Wert, Quartile und Lernmenge stehen in
[`key_figure_parameters.json`](../custom_components/ha_sauna/key_figure_parameters.json);
neu geschätzt wird er nur auf ausdrückliche Bestätigung.

## Statistik

Die Statistik umfasst alle abgeschlossenen Sitzungen des Archivs, eines Jahres
oder eines Monats nach Ortszeit des Sitzungsbeginns. Jeder Wert nennt die Zahl
der Sitzungen, auf denen er beruht (`sessions`). Summen (Sitzungen,
Betriebszeit, Saunazeit, Gänge, Aufgüsse, Energie, Kosten) gelten über alle
Sitzungen; Aufheizen, Regelung und Sollabweichung stehen zusätzlich getrennt
nach Programmart (`by_program`: Handbetrieb, Konstant, Programm).

## Anzeigewerte

Jeder Anzeigewert wird für sich aus dem Rohwert kaufmännisch gerundet:
Temperatur, Feuchte, Minuten und Energie auf eine Nachkommastelle, Kosten und
Wasser auf zwei, Anteile auf ganze Prozent. Stunden werden als ganze Minuten für
die Darstellung h:mm geliefert. Anteile, die zusammen 100 % ergeben (unter, im
und über dem Regelband), folgen der Methode der größten Reste. Summen
angezeigter Teile können daher in der letzten Stelle vom angezeigten Gesamtwert
abweichen.

Die Ergebnisse einer Sitzung werden zwischengespeichert, bis eine neue
Sitzungsrevision geschrieben wird oder sich Kabinenvolumen oder Strompreis
ändern. [API-Zugriff](speicherung.md#technische-referenz-api-zugriff)
