# Parameter und Entitätsrollen

[defaults.json](../custom_components/ha_sauna/defaults.json) führt Standardwerte,
Grenzen, Einheiten, Programmvorlagen und Darstellungsvorgaben. Gespeicherte
Anlagenwerte haben Vorrang. Änderungen am Katalog werden nach einem Neustart von
Home Assistant eingelesen und wirken auf neue Instanzen, fehlende Einstellungen
und eine ausdrücklich ausgelöste Standardrücksetzung.

`instance` enthält die allgemeinen Instanzvorgaben, `setup` die Abweichungen bei
Neueinrichtung. Eine Standardrücksetzung verwendet `instance`.
`legacy_parameters` dient ausschließlich der Lesekompatibilität älterer
Konfigurationen und des Referenzadapters.

Einstellungen sind dauerhafte Anlagenvorgaben. Aktuelle Solltemperatur und
Programmwahl werden in der Steuerung geändert. Technische Einstellungen und
Programmbibliothek sind nach Sitzungsende bearbeitbar; Protokollstufe und
Darstellung auch während der Sitzung.

## Temperatur und Ofen

Der zulässige Sollbereich gilt gemeinsam für direkte Temperaturwahl,
Programmstufen und Tastervorgabe. Sollwerte und berechnete Zwischenstufen werden
auf ganze Grad gerundet; ab einem halben Grad wird aufgerundet. Diese Werte
verwendet auch die Regelung.

Abschaltaufschlag und Wiedereinschaltabstand beziehen sich jeweils auf die
Solltemperatur. Heizpause und Mindestheizzeit ergänzen dieses Temperaturband.
Ihre Wirkung und Priorität beschreibt
[Temperatur und Bereitschaft](betrieb.md#temperatur-und-bereitschaft).

## Temperaturprogramme

Programme behalten beim Umbenennen oder Sortieren ihre Kennung. Eine bestehende
Programm- oder Tasterzuordnung bleibt dadurch erhalten. Die Tastervorgabe ist
von der aktuellen Temperaturwahl im Panel unabhängig.

[Stufenfolge und Änderungen während der Sitzung](betrieb.md#temperaturprogramm) ·
[Programme und Tastervorgabe speichern](bedienung.md#programme-und-tastervorgabe)

## Betrieb und Kühlung

Die Wiederaufnahmezeit bestimmt zugleich die Sitzungspause und den Lichtnachlauf.
Die Dauer manueller Übersteuerungen gilt nur in Automatik; die Betriebsart
Manuell besitzt keine solche Frist. Die Aufgussbestätigungsfrist gehört
allein zum Proxyverfahren der Gangerkennung.

Die maximale Kühldauer darf die Mindestdauer nicht unterschreiten. Die
[Ofenkühlung](ofenkuehlung.md) berechnet ihre Dauer beim Kühlbeginn aus der
Betriebshistorie und den eingestellten Gewichten.

[Bedienhandlungen und Betriebsart](betrieb.md#bedienhandlungen-und-betriebsart) ·
[Gangbestätigung](gangmodell.md)

## Licht

Die Lichtparameter formen die temperaturabhängige Helligkeit und den Übergang
während der Kühlung; der Ablauf ist unter [Licht](betrieb.md#licht) beschrieben.
Die Hysterese wird in Prozentpunkten angegeben und stabilisiert automatische
Stellwertwechsel. Manuelle Lichtwahl und fälliges Ausschalten umgehen sie.

Die Helligkeitsskala muss dem konfigurierten Maximalwert des Lichtgeräts
entsprechen. Sie dient der Zuordnung von Geräterückmeldungen; die Bedienung
verwendet weiterhin Prozentwerte.

## Überwachung

Das Höchstalter eines Messwerts muss die normalen Meldeabstände der Sensoren
abdecken. Die Rückmeldefrist überwacht die Bestätigung eines Schützbefehls.
Eine anhaltende technische Störung führt nach der Bestätigungsdauer zur
verriegelten Schutzabschaltung.

[Sensorvorrang](betrieb.md#temperatur-und-bereitschaft) ·
[Rückmeldungen und Schutz](betrieb.md#rückmeldungen-und-schutz)

## Timer und Energie

Die Ofentimer-Vorwarnung ist mit `0` deaktiviert. Bei älteren gespeicherten
Konfigurationen bleibt eine fehlende oder ehemals leere Warnvorgabe deaktiviert.

Die Ofenleistung dient der Verbrauchsschätzung. Bei zugeordneter Leistungsmessung
bestimmt die Leistungsschwelle, wann tatsächliches Heizen gezählt wird.
[Heizzeit, Timer und Energie](betrieb.md#heizzeit-timer-und-energie) erklärt
Quellenvorrang, Zählung und Datenlücken.

## Anzeige

Schnellauswahl und Sollschieber verwenden den zulässigen Sollbereich.
Messbogenskalen und Farben werden getrennt von den Regelparametern gespeichert.
Den Einfluss der Skalen und die Regeln für Zeitangaben beschreibt die
[Darstellungsreferenz](darstellung.md).

## Erkennungsparameter

Feuchteänderungen sind in Prozentpunkten angegeben. Der Wasserverlust beim
Durchlüften ist dagegen ein relativer Anteil des absoluten Wassergehalts.
Die Zeitfenster für deutliche und schwache Personensignale müssen ganzzahlige
Vielfache des Zeitabstands der Personenprüfung sein.

Die [Erkennungsregeln](erkennung.md) beschreiben das Zusammenwirken der
Schwellen. Für historische Diagnosen gelten die zur jeweiligen Sitzung
gespeicherten Einstellungen.

## Entitätsrollen

| Rolle | Zuordnung und Einheit |
| --- | --- |
| Temperatur oben / unten | Temperatursensor in °C, zusammen mit der Luftfeuchte derselben Position |
| Luftfeuchte oben / unten | Sensor für relative Luftfeuchte in %, zusammen mit der Temperatur derselben Position |
| Schalter des Heizschützes | `switch`; seine bestätigte Stellung dient als Relaisrückmeldung. |
| Taster oder Betriebsschalter | `event` oder `binary_sensor`, passend zur gewählten Bedienart |
| Dimmbares Saunalicht | `light` mit Helligkeitssteuerung |
| Unabhängiger binärer Heiznachweis (optional) | `switch` oder `binary_sensor` |
| Leistungsmessung des Ofens (optional) | Leistungssensor in W oder kW |
| Sensorstatus oben / unten | Optionaler binärer Sensor oder klassen- und einheitenloser Statussensor |
| Präsenzentität | Optionaler `binary_sensor` der Klasse `occupancy`, `presence` oder `motion` |
| Audioziel (vorbereitet) | Optionale hinterlegte Zuordnung eines `media_player` |

Die Auswahllisten und die Prüfung beim Speichern berücksichtigen Domain,
Geräteklasse, Einheit und erforderliche Fähigkeiten. Tasterereignisse benötigen
die Klasse `button` und ausgewiesene Ereignistypen. Eine unveränderte,
vorübergehend fehlende Zuordnung bleibt erhalten und wird nicht automatisch
durch eine andere Quelle ersetzt.

Mindestens eine vollständige Temperatur-/Feuchteposition ist erforderlich.
Messpaare müssen denselben Messort abbilden; aus ihnen wird der diagnostische
absolute Wassergehalt der jeweiligen Position berechnet.

### Umgebungsdaten

Eine optionale `weather`-Entität liefert Temperatur, relative Feuchte, Taupunkt,
Luftdruck und Wind aus ihren Attributen. Ausdrücklich gewählte Einzelquellen
haben Vorrang, auch wenn sie vorübergehend nicht verfügbar sind.

| Einzelquelle | Geräteklasse und Einheit |
| --- | --- |
| Außentemperatur und Taupunkt | `temperature`, °C |
| Relative Außenfeuchte | `humidity`, % |
| Absolute Außenfeuchte | `absolute_humidity`, g/m³ |
| Luftdruck | `pressure`, hPa |
| Windgeschwindigkeit | `wind_speed`, km/h |
| Windrichtung | `wind_direction`, ° |
| Niederschlagsintensität | `precipitation_intensity`, mm/h |
| Mess- und Vorhersagezeitpunkt | Je ein `timestamp`-Sensor |

Umgebungsdaten beeinflussen weder Heizregelung noch Gangerkennung. Die absolute
Feuchte wird aus der gewählten Quelle übernommen. Messzeit, Vorhersagezeit und
HA-Aktualisierung bleiben getrennt. Zuordnungen erfordern Administratorrechte.

## Protokollierung

**ERROR** beschränkt das Home-Assistant-Protokoll auf Fehler. **INFO** ergänzt
Betriebsereignisse, **DEBUG** Messwerte und Erkennungsprüfungen. Änderungen wirken
sofort; der Datenumfang des Sitzungsarchivs bleibt davon unabhängig.

## Standardwerte wiederherstellen

Die Rücksetzung ist nach Sitzungsende möglich. Sie umfasst Betriebsparameter,
Programmbibliothek, Betriebsart, aktuelle Temperaturwahl, Tastervorgabe,
Präsenzquelle und Protokollstufe. Maßgeblich sind die Katalogvorgaben.

Geräte- und Sensorzuordnungen, Taster-/Schalterart samt Ereignistyp, Darstellung
und Sitzungsarchiv bleiben erhalten. Die Darstellung besitzt eine
[eigene Rücksetzung](darstellung.md#darstellungsentwurf-und-skalen).
