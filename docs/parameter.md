# Einstellungen und Entitäten

## Wo Einstellungen geändert werden

| Ort | Bereich und Inhalt |
| --- | --- |
| Home-Assistant-Integrationskonfiguration | **Geräte und Erkennungsverfahren**: Sensoren, Schaltausgänge, Bedieneingang und Erkennungsverfahren. |
| Home-Assistant-Integrationskonfiguration | **Betrieb und Ofen**, **Sensoren und Erkennung**, **Licht**: dauerhafte Einstellungen, nach Funktion in Untergruppen gegliedert. |
| Sauna-Panel → **Einstellungen** | **Programme und Start**: Programmbibliothek sowie Taster- und Startvorgaben. |
| Sauna-Panel → **Einstellungen** | **Darstellung**: Instrumente und konstante Temperaturen der Schnellwahl. |
| Sauna-Panel → **Einstellungen** | **Daten und Wartung**: Archiv, Protokollierung und Zurücksetzen. |
| Sauna-Panel → **Einstellungen** | **Persönlich**: Sauna als Startseite des aktuellen Home-Assistant-Profils. |
| Sauna-Panel → Steuerung | Aktuelle Temperaturwahl und vorübergehende Ofen- oder Lichtwahl. |

Die Einstellungen für Betrieb, Erkennung und Licht gelten unmittelbar und bleiben
nach einem Neustart erhalten. Sie und die Gerätezuordnungen sind mit
Administratorrechten nach vollständigem Sitzungsende änderbar.

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
| Lichtstärke am Präsenzsensor, optional | `sensor`, Klasse `illuminance`, lx |
| Präsenz, optional | `binary_sensor`, Klasse `occupancy`, `presence` oder `motion` |

Nach Auswahl eines Sensorgeräts werden eindeutig passende Entitäten zugeordnet.
Bei mehreren passenden Messkanälen bleibt die Auswahl offen; einzelne Zuordnungen
können korrigiert werden.

Die Lichtstärke des Präsenzsensors wird ausschließlich zur Sitzung archiviert,
mit Originalzustand, Einheit und HA-Zeitstempeln. Sie beeinflusst die Lichtsteuerung nicht.

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

| Einstellung | Zusammenhang |
| --- | --- |
| Solltemperaturen | Gemeinsame Grenzen für direkte Wahl, Programme und Tastervorgabe. Eingaben und Zwischenstufen werden auf ganze Grad gerundet; ab einem halben Grad aufwärts. |
| Helligkeitsskala | Muss dem konfigurierten Maximalwert des Lichtgeräts entsprechen, damit eigene Befehle und Geräterückmeldungen zusammenpassen. |
| Vergleichszeiten der Personenerkennung | Müssen ganzzahlige Vielfache des Auswertungsabstands sein. |
| Höchstalter der Messwerte | Muss die regulären Meldeabstände der jeweiligen Quelle abdecken. |

[Ausfälle und Schutzabschaltung](betrieb.md#bei-sensorausfall-oder-schutzabschaltung)

Zusammenhänge: [Heizregelung und Licht](betrieb.md),
[Ofenkühlung](ofenkuehlung.md), [Gangerkennung](gangmodell.md),
[Erkennungsschwellen](erkennung.md).

## Fehler im Home-Assistant-Protokoll untersuchen

Die Protokollstufe unter **Daten und Wartung → Protokollierung** wirkt sofort auf
das Home-Assistant-Protokoll.
Der Datenumfang des Sitzungsarchivs bleibt unverändert.

## Werkseinstellungen

**Daten und Wartung → Werkseinstellungen wiederherstellen** setzt Betriebsparameter,
Programmbibliothek, Betriebsart, aktuelle Temperaturwahl, Tastervorgabe,
Temperaturschnellwahl und Protokollstufe zurück.

Erhalten bleiben Gerätezuordnungen, Erkennungsverfahren, Taster-/Schalterart samt
Ereignistyp sowie Farben, Instrumente, Diagramme und Archiv. Farben, Instrumente
und Diagramme haben eine eigene Rücksetzung unter **Darstellung**.
Alle Rücksetzungen der Betriebsparameter sind erst nach Sitzungsende möglich.
