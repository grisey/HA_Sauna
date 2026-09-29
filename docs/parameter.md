# Parameter und Entitätsrollen

Gespeicherte Werte Ihrer Anlage haben Vorrang vor den Standards. Fehlende
Einstellungen werden beim Laden mit dem zentralen Standard ergänzt.

Die Konfiguration einer Sauna ist die gemeinsame Quelle für Einrichtungsdialog,
Home-Assistant-Entitäten und Saunapanel. Eingaben werden vor dem Speichern auf
ihre zulässigen Werte und Zusammenhänge geprüft. Während einer Sitzung bleibt
die aktuelle Temperaturwahl anpassbar. Administratoren können außerdem
Protokollstufe und Darstellung ändern. Die technische Konfiguration und der
Programmkatalog werden nach Sitzungsende bearbeitet.

## Temperatur und Ofen

Die Untergrenze für Solltemperaturen ist einstellbar und beträgt standardmäßig
60 °C. Die feste Obergrenze beträgt 100 °C. Dieser Bereich gilt auch für
Programmzwischenstufen und die Tastervorgabe. Bereits gespeicherte niedrigere
Temperaturvorgaben können bei der Übernahme einer älteren Konfiguration durch
eine entsprechend übernommene Untergrenze erhalten bleiben.

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Mindesttemperatur der Sauna | 60 °C | Untergrenze für Sollwerte und Schnellauswahl |
| Solltemperatur | 80 °C | Eine direkte Wahl legt eine konstante Solltemperatur fest. |
| Temperaturreserve | 5 °C | Aufschlag auf die Solltemperatur zur oberen Regeltemperatur |
| Schaltabstand der Temperaturregelung | 3 °C | Abstand unter der oberen Regeltemperatur, ab dem erneutes Heizen zulässig ist |
| Heizpause nach Temperaturabschaltung | 5 min | Wartezeit nach einer regulären Abschaltung an der oberen Regeltemperatur; 0 ermöglicht den unmittelbaren weiteren Regelablauf. |
| Mindestheizzeit nach dem Einschalten | 10 min | Mindestdauer eines tatsächlich begonnenen Heizintervalls im regulären Thermostatbetrieb; 0 gibt kurze Intervalle frei. |

Die Sauna gilt ab Erreichen der Solltemperatur als bereit. Die Temperaturreserve
bestimmt die höhere Regeltemperatur des Ofens. Ausschalten, technische
Schutzabschaltungen und Ofenkühlung haben Vorrang vor der Mindestheizzeit.

[Heizpriorität](praesenz-ofen-phasen.md#heizpriorität)

## Temperaturprogramme

Die Einstellung **Verteilung der Steigerung** bestimmt die Zahl der
Temperaturstufen einer gleichmäßigen Folge. Die Standardvorgabe führt von
80 auf 95 °C über vier Stufen: 80 → 85 → 90 → 95 °C. Nach jedem beendeten,
durch Aufguss bestätigten Gang folgt die nächste Stufe. Die letzte Temperatur
gilt anschließend für weitere Gänge.

Bei **Einzelne Stufen** erhält jede Stufe eine eigene Temperatur. Die Folge
kann dabei steigen oder fallen. Ihre Länge bestimmt die Stufenzahl;
erster und letzter Wert ergeben Start und Ende. Zulässig sind 1 bis 20 Stufen.

Die Programmbibliothek enthält diese anpassbaren Vorlagen:

| Programm | Temperaturfolge |
| --- | --- |
| Genusszeit | 80 → 85 → 90 °C |
| Gipfelstürmer | 84 → 92 → 100 °C |
| Ewigkeit | 80 → 84 → 88 → 92 → 96 °C |
| Liegewiese | 75 → 80 → 85 °C |
| Höhenwanderung | 90 → 95 → 100 °C |
| Schnellstarter | 70 → 90 °C |

Gespeicherte Programme besitzen eine feste Kennung. Umbenennen und Sortieren
erhalten die Zuordnung einer gewählten Programm- oder Tastervorgabe. Die
Tastervorgabe verwendet ein benanntes Programm oder eine gesondert gespeicherte
konstante Temperatur, standardmäßig 80 °C.

## Betrieb und Kühlung

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Wiederaufnahmezeit | 15 min | Dauer der Sitzungspause und des Lichtnachlaufs; erneutes Einschalten innerhalb dieser Zeit setzt die Sitzung fort. |
| Zeit für die Aufgussbestätigung | 12 min | Frist ab dem zugeordneten Beginn eines vorläufigen Gangs |
| Ofen-Auszeit zum Zurücksetzen der Heizzeit | 10 min | Zusammenhängende Auszeit, nach der der lokale Heizzeitzähler wieder bei null beginnt; Sitzung und Energieverbrauch bleiben erhalten. |
| Mindestdauer der Ofenkühlung | 5 min | Basis der berechneten Kühlzeit |
| Höchstdauer der Ofenkühlung | 15 min | Obergrenze der gezählten Kühlzeit mit bestätigtem Schütz-AUS |
| Halbwertszeit der Ofenkühlung | 15 min | Alter, nach dem sich das Gewicht eines Heiz- oder Bereitschaftsabschnitts halbiert |
| Heizminuten je Bereitschaftsminute | 2 | Eine gewichtete Bereitschaftsminute gleicht zwei gewichtete Heizminuten aus. |
| Höchstdauer manueller Übersteuerungen | 10 min | Spätester Rückkehrpunkt an die Automatik; ein passender Phasen- oder Schaltwechsel kann früher führen. |
| Langdruckdauer des Saunatasters | 2 s | Dauer, nach der ein bei laufendem Betrieb begonnener Binärtasterdruck die Sitzung beendet |

Die Ofenkühlung berechnet ihre Dauer beim tatsächlichen Kühlbeginn. Ihre Uhr
zählt bestätigte Schütz-AUS-Zeit; die einmal berechnete Dauer bleibt für diese
Kühlung fest. Das eingestellte Maximum muss mindestens der Mindestdauer
entsprechen.

[Kühlablauf und Gewichtung](ofenkuehlung.md)

Die Betriebsart **Manuell** bleibt bis zu einem Betriebsartwechsel gewählt.
Die Höchstdauer in der Tabelle gilt für vorübergehende Übersteuerungen des
Automatikbetriebs.

[Tastergesten und Lichtnachlauf](betrieb.md#bedienhandlungen-und-betriebsart)

## Licht

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Referenztemperatur für das Licht | 30 °C | Kaltpunkt der temperaturabhängigen Lichtkurve |
| Grundhelligkeit | 5 % | Helligkeit am Kaltpunkt |
| Lichthelligkeit tagsüber | 40 % | Tageswert der Normalhelligkeit |
| Lichthelligkeit nachts | 25 % | Nachtwert der Normalhelligkeit |
| Lichthelligkeit zu Beginn der Ofenkühlung | 15 % | Ausgangswert der Lichtkurve während der Ofenkühlung |
| Helligkeit für „Hell“ und Lichtnachlauf | 50 % | Vorgabe für die Stufe **Hell** und den Lichtnachlauf beim Ausschalten |
| Dauer des Lichtübergangs | 30 s | Dauer eines automatischen Helligkeitswechsels; 0 bewirkt einen unmittelbaren Wechsel. |
| Helligkeitsskala des Lichtgeräts | 255 | Technische Auflösung für die Zuordnung eigener Rückmeldungen |

Beim Aufheizen steigt das Licht vom Kaltpunkt zur Normalhelligkeit. Während der
Ofenkühlung steigt es vom eingestellten Ausgangswert wieder auf das
temperaturabhängige Niveau. Die Wiederaufnahmezeit bestimmt die Dauer des
Lichtnachlaufs. Für manuelle Lichtwahlen gilt die Rückkehrregel der
Übersteuerung.

Die Helligkeitsskala entspricht dem tatsächlich konfigurierten Maximalwert des
Lichtgeräts. Beispielsweise verwendet ein MQTT-Licht mit
`brightness_scale: 10` hier den Wert 10. Zulässig sind ganze Werte von 1 bis
65535. Die Bedienung zeigt weiterhin Prozentwerte.

## Überwachung

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Höchstalter eines Messwerts | 180 s | Zeit seit der letzten Meldung, innerhalb der ein Messwert als frisch gilt |
| Wartezeit auf die Schützrückmeldung | 10 s | Frist für die Bestätigung eines Ein- oder Ausschaltbefehls |
| Dauer bis zur bestätigten Störung | 60 s | Dauer einer durchgehenden zentralen technischen Störung bis zur verriegelten Schutzabschaltung |

Das Höchstalter muss die normalen Meldeabstände der Sensoren abdecken.
Für die Temperaturregelung führt oben. Bei Ausfall dieses Werts übernimmt
der gültige untere Temperaturwert, bis oben wieder verfügbar ist. Eine
gültige Regeltemperatur ist Voraussetzung für das Heizen. Ein Ausfall der
zweiten konfigurierten Messposition wird als Störung angezeigt; die verfügbare
Position ermöglicht den weiteren Betrieb.

## Timer und Energie

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Laufzeit des mechanischen Ofentimers | 240 min | Geschätzte Laufzeit bei Betrieb-EIN und bestätigtem Schütz-EIN |
| Erinnerung vor Ablauf des Ofentimers | Leer | Optionaler Zeitpunkt vor dem geschätzten Ablauf, an dem eine Erinnerung erscheint |
| Ofenleistung für die Verbrauchsschätzung | 4,5 kW | Nennleistung zur Umrechnung bestätigter Heizzeit in geschätzten Verbrauch |
| Leistungsschwelle für das Heizen | 50 W | Bei zugeordneter Leistungsmessung zählt Leistung oberhalb dieser Schwelle als Heizen. |

Der mechanische Timer folgt der bestätigten Schützstellung. Nach einer
beendeten Sitzung mit gezählten Gängen beginnt seine Anzeige beim nächsten
Start neu. Er unterstützt das Einstellen des tatsächlichen Drehschalters.

Ein zugeordneter Leistungssensor liefert die gemessene Grundlage der
Verbrauchsberechnung. Als Ersatz verwendet die Schätzung Nennleistung und
bestätigte Heizzeit. Die Anzeige unterscheidet gemessene und geschätzte
Abschnitte und kennzeichnet Lücken.

## Anzeige

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| Niedrigste Temperatur der Schnellauswahl | 70 °C | Wert der ersten Temperaturtaste |
| Temperaturabstand der Schnellauswahl | 5 °C | Abstand zwischen benachbarten Temperaturtasten |
| Anzahl der Temperaturtasten | 6 | Ganze Anzahl von 1 bis 20; Tasten verwenden den zulässigen Sollbereich. |
| Zeitfenster der Aufheizschätzung | 5 min | Beobachtungszeit für Änderungen des Temperaturanstiegs |

Die Aufheizschätzung verwendet einen geeigneten archivierten Aufheizverlauf und
den aktuellen Temperaturtrend. Sie unterstützt die Planung und erscheint in
Fünf-Minuten-Stufen. Farben und Messbogenskalen werden gesondert unter
**Darstellung** gespeichert. Die Vorgaben der Skalen sind 40–110 °C für
Temperatur und 0–60 % für relative Luftfeuchte.

[Darstellung](darstellung.md)

## Erkennungsparameter

Unter **Experteneinstellungen zur Erkennung** stehen die Werte nach ihrem
Zweck geordnet. Feuchteänderungen sind in Prozentpunkten angegeben; der
Wasserverlust beim Lüften ist ein
relativer Anteil des absoluten Wassergehalts.

| Messbasis | Standard |
| --- | --- |
| Zeitabstand der Auswertung | 1 s, festes Raster |
| Glättungszeit der Messwerte | 5 s |

| Tür | Standard |
| --- | --- |
| Zeitfenster für den Temperaturtrend an der Tür | 8 s |
| Zeitfenster für die Luftfeuchteänderung an der Tür | 10 s |
| Temperaturtrend bei Türöffnung | −1,8 °C/min |
| Luftfeuchteabfall bei Türöffnung oben / unten | 0,45 / 0,3 Prozentpunkte |
| Bestätigungsdauer der Türöffnung | 2 s |
| Türöffnung: Temperaturabfall trotz eingeschalteter Heizung | −0,8 °C/min |
| Bestätigungsdauer des Temperaturabfalls beim Heizen | 5 s |
| Temperaturtrend bei Türschließung | 0,15 °C/min |
| Bestätigungsdauer der Türschließung | 3 s |

| Lüftung | Standard |
| --- | --- |
| Vergleichszeit vor dem Lüften | 60 s |
| Mindestdauer des Durchlüftens | 60 s bei einer gültigen Messposition |
| Temperaturabfall beim Durchlüften oben / unten | Je 3 °C |
| Absoluter Wasserverlust beim Durchlüften | 30 % |

| Person | Standard |
| --- | --- |
| Zeitabstand der Personenprüfung | 5 s |
| Zeitfenster für deutliche Personensignale | 60 s |
| Luftfeuchtetrend bei deutlichem Personensignal oben / unten | Je 0,5 Prozentpunkte/min |
| Bestätigungsdauer deutlicher Personensignale | 10 s |
| Zeitfenster für schwache Personensignale | 120 s |
| Luftfeuchtetrend bei schwachem Personensignal oben / unten | Je 0,14 Prozentpunkte/min |
| Bestätigungsdauer schwacher Personensignale | 30 s |

| Aufguss | Standard |
| --- | --- |
| Zeitfenster für die Aufgusserkennung | 10 s |
| Luftfeuchteanstieg bei einem Aufguss | 2 Prozentpunkte |
| Bestätigungsdauer des Aufgusssignals | 3 s |

Die **Erkennungskontrolle** zeigt die gespeicherten Merkmale mit den zur Sitzung
gehörenden Einstellungen.
Die Zeitfenster für deutliche und schwache Personensignale müssen ganzzahlige
Vielfache des Zeitabstands der Personenprüfung sein.

[Erkennung mit einer oder zwei Messpositionen](erkennung.md)

## Entitätsrollen

| Rolle | Zuordnung und Einheit |
| --- | --- |
| Temperatur oben / unten | Temperatursensor in °C, zusammen mit der Luftfeuchte derselben Position |
| Luftfeuchte oben / unten | Sensor für relative Luftfeuchte in %, zusammen mit der Temperatur derselben Position |
| Schalter des Heizschützes | `switch`; seine bestätigte Stellung genügt als Relaisrückmeldung, auch bei einem Shelly-Schalter. |
| Taster oder Betriebsschalter | `event` oder `binary_sensor`, passend zur gewählten Bedienart |
| Dimmbares Saunalicht | `light` mit Helligkeitssteuerung |
| Unabhängiger binärer Heiznachweis (optional) | `switch` oder `binary_sensor` |
| Leistungsmessung des Ofens (optional) | Leistungssensor in W oder kW |
| Sensorstatus oben / unten | Optionaler `sensor` oder `binary_sensor` |
| Präsenzentität (beobachtend) | Optionaler `binary_sensor` für die beobachtete Anwesenheit |
| Audioziel (vorbereitet) | Optionale hinterlegte Zuordnung eines `media_player` |

Mindestens eine vollständige Temperatur-/Feuchteposition ermöglicht den
gesamten Betrieb. Oben führt die Regelung, unten übernimmt bei fehlendem
gültigem oberen Wert. Aus jedem frischen, gültigen Messpaar entsteht zudem
der diagnostische absolute Wassergehalt der jeweiligen Position.

## Protokollierung

**INFO** ist die Standardstufe für Betriebsereignisse und Fehler. **ERROR**
beschränkt das Home-Assistant-Protokoll auf Fehler; **DEBUG** ergänzt
Messwerte und Erkennungsprüfungen. Eine Änderung wirkt sofort. Das
Sitzungsarchiv erhält weiterhin seinen vollständigen Datenstrom.

## Standardwerte wiederherstellen

**Standardwerte wiederherstellen** stellt nach Sitzungsende die zentrale
Standardkonfiguration wieder her. Die Betriebsparameter erhalten ihre
Standardwerte, die Programmbibliothek erhält die mitgelieferten Vorlagen.
Die Betriebsart wird **Automatik**, die aktuelle Temperaturwahl und die
Tastervorgabe werden **Konstant** mit 80 °C; die Protokollstufe wird **INFO**.
Die Präsenzquelle wird auf die aus Messwerten abgeleitete Anwesenheit gestellt.

Erhalten bleiben die Geräte- und Sensorzuordnungen, die gewählte
Taster-/Schalterart samt Ereignistyp sowie die gespeicherte Darstellung.
**Standarddarstellung wiederherstellen** setzt unter **Darstellung** deren
Entwurf auf die Vorgaben; **Darstellung speichern** übernimmt diesen Entwurf.
