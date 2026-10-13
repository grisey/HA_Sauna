# Einstellungen und Entitäten

## Wo Einstellungen geändert werden

| Ort | Bereich und Inhalt |
| --- | --- |
| Home-Assistant-Integrationskonfiguration | **Geräte und Erkennungsverfahren**: Sensoren, Schaltausgänge, Bedieneingang und Erkennungsverfahren. |
| Home-Assistant-Integrationskonfiguration | **Betrieb und Ofen**, **Sensoren und Erkennung**, **Licht**: dauerhafte Einstellungen, nach Funktion in Untergruppen gegliedert. |
| Home-Assistant-Integrationskonfiguration | **Daten und Laufzeit**: Messkontext vor und nach archivierten Sitzungen. |
| Sauna-Panel → **Einstellungen** | **Programme und Start**: Programmbibliothek sowie Taster- und Startvorgaben. |
| Sauna-Panel → **Einstellungen** | **Darstellung**: Instrumente, Farben, Diagramme, Aktualisierung, Bedienung, Seitengrößen und Gestaltung sowie konstante Temperaturen der Schnellwahl. |
| Sauna-Panel → **Einstellungen** | **Daten und Wartung**: Archiv, Protokollierung und Zurücksetzen. |
| Sauna-Panel → **Einstellungen** | **Persönlich**: Sauna als Startseite des aktuellen Home-Assistant-Profils. |
| Sauna-Panel → Steuerung | Aktuelle Temperaturwahl und vorübergehende Ofen- oder Lichtwahl. |

Die Einstellungen für Betrieb, Erkennung und Licht gelten unmittelbar und bleiben
nach einem Neustart erhalten. Sie und die Gerätezuordnungen sind mit
Administratorrechten nach vollständigem Sitzungsende änderbar.

Integrationskonfiguration und Panel verwenden dieselbe
Anwendbarkeit: Eingabefelder erscheinen nur für zugeordnete Messpositionen und
Quellen, Personenschwellen nur beim indirekten Verfahren und die Haltezeit nur
bei Binärtastern. Ein vorübergehender Sensorausfall ändert
die Anwendbarkeit nicht. Gespeicherte Werte bleiben für einen späteren
Konfigurationswechsel erhalten.

Jeder veränderbare dauerhafte Parameter hat eine zuständige Einstellungsstelle.
Die bisherigen zusätzlichen Zahlenentitäten für dauerhafte Einstellungen werden
beim Laden der Integration entfernt. Gespeicherte Einstellungen bleiben dabei
erhalten. Die Bedienung der aktuellen Temperaturwahl und ihrer Programmstufen
folgt weiterhin dem laufenden Bedienablauf.

Werkseinstellungen, Eingabegrenzen, Einheiten, Schrittweiten und Zuordnung der
Einstellungen stehen im gemeinsamen Katalog
[`defaults.json`](../custom_components/ha_sauna/defaults.json). Die Integration
und das Panel beziehen ihre Werte aus diesem Katalog und der gespeicherten
Konfiguration der jeweiligen Sauna. Neu hinzugekommene Einstellungen verwenden
ihren Katalogstandard, bis ein eigener Wert gespeichert wird. Ungültige Werte
und unvereinbare Kombinationen werden vor dem Speichern abgewiesen.

Die Katalogangabe `configuration_surface` legt für jede numerische Standardvorgabe
ihre Zuständigkeit fest:

| Zuständigkeit | Standardvorgaben |
| --- | --- |
| `catalog` – Pflege im Katalog | Auswertungsraster und Laufzeittakt; Seiten-, Cache- und Übertragungsgrößen des Archivs; numerische Toleranzen und interne Aufheizschätzungsfaktoren; technische Anzeige-, Geometrie- und Gestaltungsvorgaben. |
| `integration` – Integrationskonfiguration | Anlagen-, Betriebs-, Erkennungs- und Lichtvorgaben sowie der Archiv-Messkontext. |
| `panel` – Einstellungsmenü | Programm- und Startvorgaben, Temperaturschnellwahl, Verlauf-Aktualisierung, Seitengrößen der Detailansicht, Anzeigepräzision, Zoomverhalten sowie ausgewählte Abstände, Höhen und Rundungen. |

Kataloggebundene Standards werden beim Laden aus dem Katalog bezogen. Die
gespeicherte Instanzkonfiguration enthält die veränderbaren Vorgaben. Eine
Sitzungsaufzeichnung hält den vollständigen damals wirksamen Parameterstand
fest, damit ihre spätere Auswertung dieselben fachlichen Vorgaben verwendet.
Farben, Diagrammskalen und Instrumentenformen werden unter **Darstellung**
gepflegt; die Programmbibliothek unter **Programme und Start**.

Unter **Betrieb und Ofen → Heizzeit und Verbrauch** ist der **Strompreis**
optional in €/kWh erfassbar. Der Werkwert ist leer; durch Löschen der Eingabe
wird der gespeicherte Strompreis wieder entfernt.

### Darstellung und Aktualisierung

Unter **Darstellung** sind Verlauf-Aktualisierung, Datensatzgrenzen,
Bedienverhalten und die veränderbaren Gestaltungsvorgaben nach Aufgabe gruppiert. Die Einstellungen
gelten für die jeweilige Sauna. **Speichern**, **Verwerfen** und die Rücksetzung
der Darstellung verwenden denselben vollständigen Einstellungsstand.

Die Statusanzeige und der laufende Archivverlauf haben getrennte
Aktualisierungsabstände. Nachgeladene Daten ergänzen die geöffnete Ansicht;
Zoom, Fensterausschnitt, Markierung, Fokus und aufgeklappte Abschnitte bleiben
erhalten. Die Ladeanzeige gehört zum ersten Abruf einer Ansicht.

### Daten und Laufzeit

Der Messkontext legt fest,
welcher Zeitraum vor und nach einer Sitzung im Archiv berücksichtigt wird.
Eine archivierte Sitzung verwendet den bei ihr gespeicherten Kontextwert.

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
| Auswertungsraster | Median, Vergleichsfenster, Lüftungsreferenz und Auswertungsabstand müssen ganzzahlige Vielfache des Rasters sein. Haltezeiten werden am ersten Rasterpunkt nach ihrem Ablauf erfüllt. |
| Höchstalter der Messwerte | Muss die regulären Meldeabstände der jeweiligen Quelle abdecken. |
| Sonnenstand für das Licht | Der Nachtgrenzwert muss unter dem Tagesgrenzwert liegen; dazwischen wird die Helligkeit überblendet. |
| Aufheizschätzung | Mindestzahl und Zeitraum der Messpunkte, Rausch- und Abfallgrenzen sowie Glättungs- und Bestätigungszeiträume bestimmen, wann und wie eine verbleibende Aufheizzeit ausgewiesen wird. |

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
Ereignistyp sowie Darstellung und Archiv. Die Darstellung einschließlich ihrer
Aktualisierungs- und Bedienvorgaben hat eine eigene Rücksetzung unter **Darstellung**.
Alle Rücksetzungen der Betriebsparameter sind erst nach Sitzungsende möglich.
