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

## Betriebsparameter abstimmen

Standardwerte, zulässige Grenzen und Programmvorlagen stehen im
[zentralen Katalog](../custom_components/ha_sauna/defaults.json).
Gespeicherte Anlagenwerte haben Vorrang.

| Einstellung | Zusammenhang |
| --- | --- |
| Solltemperaturen | Gemeinsame Grenzen für direkte Wahl, Programme und Tastervorgabe. Eingaben und Zwischenstufen werden auf ganze Grad gerundet; ab einem halben Grad aufwärts. |
| Ofentimer-Vorwarnung | `0` deaktiviert die Warnung. |
| Helligkeitsskala | Muss dem konfigurierten Maximalwert des Lichtgeräts entsprechen, damit eigene Befehle und Geräterückmeldungen zusammenpassen. |
| Personenprüffenster | Müssen ganzzahlige Vielfache des Personen-Prüfabstands sein. |
| Höchstalter eines Messwerts | Muss die regulären Meldeabstände der jeweiligen Quelle abdecken. |

[Ausfälle und Schutzabschaltung](betrieb.md#bei-sensorausfall-oder-schutzabschaltung)

Zusammenhänge: [Heizregelung und Licht](betrieb.md),
[Ofenkühlung](ofenkuehlung.md), [Gangerkennung](gangmodell.md),
[Erkennungsschwellen](erkennung.md).

## Fehler im Home-Assistant-Protokoll untersuchen

Die Protokollstufe wirkt sofort auf das Home-Assistant-Protokoll.
Der Datenumfang des Sitzungsarchivs bleibt unverändert.

## Standardwerte wiederherstellen

Zurückgesetzt werden Betriebsparameter, Programmbibliothek, Betriebsart,
aktuelle Temperaturwahl, Tastervorgabe, Präsenzquelle und Protokollstufe.

Erhalten bleiben Gerätezuordnungen, Taster-/Schalterart samt Ereignistyp,
Darstellung und Archiv. Die Darstellung hat eine eigene Rücksetzung.
Alle Rücksetzungen der Betriebsparameter sind erst nach Sitzungsende möglich.
