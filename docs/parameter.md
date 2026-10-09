# Einstellungen und Entitäten

## Entitätsrollen

| Rolle | Geeignete Entität |
| --- | --- |
| Temperatur oben / unten | `sensor`, Klasse `temperature`, °C |
| Relative Feuchte oben / unten | `sensor`, Klasse `humidity`, % |
| Heizschütz | `switch` |
| Bedieneingang | `event` der Klasse `button` mit Ereignistypen oder `binary_sensor` |
| Saunalicht | `light` mit Helligkeitssteuerung |
| Unabhängiger Heiznachweis, optional | `switch` oder `binary_sensor` |
| Ofenleistung, optional | `sensor`, Klasse `power`, W oder kW |
| Sensorstatus, optional | `binary_sensor` oder klassen- und einheitenloser Statussensor |
| Präsenz, optional | `binary_sensor`, Klasse `occupancy`, `presence` oder `motion` |

Mindestens ein vollständiges Temperatur-/Feuchtepaar ist erforderlich.
Beide Werte müssen denselben Messort abbilden, auch für die Berechnung der
absoluten Feuchte. Ein vorübergehend fehlender Sensor behält seine Zuordnung;
er wird nicht automatisch durch eine andere Entität ersetzt.

### Umgebungsdaten

Eine `weather`-Entität kann Temperatur, relative Feuchte, Taupunkt, Luftdruck und
Wind liefern. Gewählte Einzelquellen haben Vorrang, auch bei deren Ausfall.

| Einzelquelle | Geräteklasse und Einheit |
| --- | --- |
| Temperatur / Taupunkt | `temperature`, °C |
| Relative Feuchte | `humidity`, % |
| Absolute Feuchte | `absolute_humidity`, g/m³ |
| Luftdruck | `pressure`, hPa |
| Windgeschwindigkeit | `wind_speed`, km/h |
| Windrichtung | `wind_direction`, ° |
| Niederschlagsintensität | `precipitation_intensity`, mm/h |
| Mess- / Vorhersagezeit | Je ein `timestamp`-Sensor |

Umgebungsdaten beeinflussen weder Heizregelung noch Gangerkennung.
Absolute Feuchte wird aus der Quelle übernommen; Mess-, Vorhersage- und
HA-Aktualisierungszeit bleiben getrennt.

## Anlagenparameter

Standardwerte, zulässige Grenzen und Programmvorlagen stehen im
[zentralen Katalog](../custom_components/ha_sauna/defaults.json).
Gespeicherte Anlagenwerte haben Vorrang.

### Betrieb und Ofen

| Einstellung | Zu beachten |
| --- | --- |
| Solltemperaturen | Gemeinsame Grenzen für direkte Wahl, Programme und Tastervorgabe. Eingaben und Zwischenstufen werden auf ganze Grad gerundet; ab einem halben Grad aufwärts. |
| Ofentimer-Vorwarnung | `0` deaktiviert die Warnung. |
| Ofenleistung | Grundlage der Verbrauchsschätzung ohne Leistungsmessung. |
| Leistungsschwelle | Grenze, ab der eine Leistungsmessung als Heizen zählt. |

### Licht

| Einstellung | Zu beachten |
| --- | --- |
| Lichthysterese | Angabe in Prozentpunkten. |
| Helligkeitsskala | Muss dem konfigurierten Maximalwert des Lichtgeräts entsprechen; die Bedienung verwendet Prozent. |

### Erkennung

| Einstellung | Zu beachten |
| --- | --- |
| Feuchteänderungen | Relative Feuchteänderung in Prozentpunkten; Wasserverlust beim Durchlüften als Anteil des absoluten Wassergehalts. |
| Personenprüffenster | Müssen ganzzahlige Vielfache des Personen-Prüfabstands sein. |

### Überwachung

| Frist | Bezug |
| --- | --- |
| Höchstalter eines Messwerts | Muss die üblichen Meldeabstände des Sensors abdecken. |
| Schützrückmeldefrist | Zeit zur Bestätigung eines Schaltbefehls. |
| Störungsbestätigung | Dauer eines abschaltrelevanten Fehlers bis zur verriegelten Schutzabschaltung. |

[Ausfälle und Schutzabschaltung](betrieb.md#rückmeldungen-und-schutz)

Zusammenhänge: [Heizregelung und Licht](betrieb.md),
[Ofenkühlung](ofenkuehlung.md), [Gangerkennung](gangmodell.md),
[Erkennungsschwellen](erkennung.md).

## Wartung

### Protokollierung

Die Protokollstufe wirkt sofort auf das Home-Assistant-Protokoll.
Der Datenumfang des Sitzungsarchivs bleibt unverändert.

### Standardwerte wiederherstellen

Zurückgesetzt werden Betriebsparameter, Programmbibliothek, Betriebsart,
aktuelle Temperaturwahl, Tastervorgabe, Präsenzquelle und Protokollstufe.

Erhalten bleiben Gerätezuordnungen, Taster-/Schalterart samt Ereignistyp,
Darstellung und Archiv. Die Darstellung hat eine eigene Rücksetzung.
Alle Rücksetzungen der Betriebsparameter sind erst nach Sitzungsende möglich.
