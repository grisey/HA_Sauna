# Einstellungen und Entitäten

## Wo Einstellungen geändert werden

Die Einstellungen sind nach Funktion geordnet. Häufig genutzte Bereiche stehen
vorn, selten benötigte technische Werte hinten. Das Panel unter **Einstellungen**
und die Home-Assistant-Integrationskonfiguration (**Sauna konfigurieren**)
verwenden dieselben Bereiche in derselben Reihenfolge.

| Bereich | Inhalt | Geändert in |
| --- | --- | --- |
| **Programme und Start** | Start über Taster oder Betriebsschalter, Programmbibliothek, **Temperaturtasten** der Schnellwahl (Administratoren). | Panel |
| **Temperatur und Bedienung** | **Temperaturvorgaben** (Standardtemperatur, Mindesttemperatur); **Taster und Handbedienung** (Haltezeit des Tasters, Dauer manueller Ofen- und Lichtwahl). | Integrationskonfiguration |
| **Licht** | Helligkeit nach Temperatur und Tageszeit; Einschalten, Nachlauf und Ofenkühlung; Lichtgerät und Überblendung. | Integrationskonfiguration |
| **Anlage und Energie** | **Ofen, Strompreis und Kabine** (Ofenleistung, Strompreis, Kabinenvolumen); **Gezählte Heizzeit**. | Integrationskonfiguration |
| **Steuerung** | **Heizregelung** (Schaltpunkte, Mindestheizzeit, Heizpause); **Ofenkühlung**; **Sitzung fortsetzen**. | Integrationskonfiguration |
| **Überwachung** | **Messwerte** (Höchstalter); **Schaltbestätigung und Schutzabschaltung**. | Integrationskonfiguration |
| **Präsenz und Erkennung** | **Präsenzsensor** (Wartezeiten bei Ausfall und Rückkehr); **Gangbestätigung** beim Verfahren aus Temperatur und Feuchte. | Integrationskonfiguration |
| **Benachrichtigungen** | Testnachricht, Anlässe ein und aus, Zeitschaltuhr und Übertemperatur der [Benachrichtigungen](benachrichtigungen.md#einstellungen). | Panel |
| **Darstellung** | Instrumente, Farben, Anzeigeskalen, Seitengrößen in den Details, Zoom und Verschieben im Verlauf, Nachkommastellen. | Panel |
| **Persönlich** | Sauna als Startseite des aktuellen Home-Assistant-Profils. | Panel |
| **Daten und Wartung** | Archiv, [Lernsitzungen](erkennung.md#gelernte-größen-und-nachlernen), Protokollierung, **Standards für alle Bereiche**. | Panel |
| **Technik** | Aufheizschätzung, Archiv und Übertragung, Laufzeit, Toleranzen und weitere feste Werte; im Panel eingeklappt. | Integrationskonfiguration; feste Werte nur im Katalog |

Nur Administratoren sehen alle Bereiche. Andere Benutzer sehen **Programme und
Start** ohne Temperaturtasten, **Benachrichtigungen** ohne Änderungsmöglichkeit
sowie **Persönlich**.

Die Integrationskonfiguration enthält außerdem **Saunaname**, **Geräte und
Erkennungsverfahren**, **Benachrichtigungsempfänger**, **Standards und
Werkseinstellungen**, **Entität umbenennen** und **Entitäten bereinigen**
([Einrichtung](einrichtung.md)). In der Steuerungsansicht werden die aktuelle
Temperaturwahl und die vorübergehende Ofen- oder Lichtwahl bedient. **Details**
enthält für Administratoren außer dem [Kalibrierstart](kalibrierung.md#start-und-abbruch)
keine Einstellungen.

Jeder veränderbare dauerhafte Wert hat genau eine Einstellungsstelle. Die
Bereiche der Integrationskonfiguration erscheinen im Panel als Übersicht mit den
aktiven Werten, ihren Erklärungen und dem Hinweis **Ändern unter Sauna
konfigurieren**; Eingabefelder gibt es dort nicht. Bereichs- und Feldmenüs für
Standards und Werkseinstellungen stehen an beiden Stellen zur Verfügung.

Die Einstellungen für Temperatur, Licht, Anlage, Steuerung, Überwachung und
Erkennung gelten unmittelbar und bleiben nach einem Neustart erhalten. Sie und
die Gerätezuordnungen sind mit Administratorrechten nach vollständigem
Sitzungsende änderbar.

Integrationskonfiguration und Panel verwenden dieselbe
Anwendbarkeit: Felder erscheinen nur für zugeordnete Messpositionen und
Quellen, die Gangbestätigung nur beim Verfahren aus Temperatur und Feuchte, die
Wartezeiten des Präsenzsensors nur mit Präsenzsensor, die Mindestleistung für
gezählte Heizzeit nur mit Leistungsmessung und die Haltezeit nur bei Tastern,
die Drücken und Loslassen melden. Ein vorübergehender Sensorausfall ändert die
Anwendbarkeit nicht. Gespeicherte Werte bleiben für einen späteren
Konfigurationswechsel erhalten.

Die bisherigen zusätzlichen Zahlenentitäten für dauerhafte Einstellungen werden
beim Laden der Integration entfernt. Gespeicherte Einstellungen bleiben dabei
erhalten. Die Bedienung der aktuellen Temperaturwahl und ihrer Programmstufen
folgt weiterhin dem laufenden Bedienablauf.

Werkseinstellungen, Eingabegrenzen, Einheiten, Schrittweiten, Bereiche und
Erklärungen der Einstellungen stehen im gemeinsamen Katalog
[`defaults.json`](../custom_components/ha_sauna/defaults.json). Die Integration
und das Panel beziehen ihre Werte aus diesem Katalog und der gespeicherten
Konfiguration der jeweiligen Sauna. Neu hinzugekommene Einstellungen verwenden
ihre Werkseinstellung, bis ein Wert gespeichert wird. Ungültige Werte
und unvereinbare Kombinationen werden vor dem Speichern abgewiesen.

Die Katalogangabe `configuration_surface` legt für jede numerische Vorgabe
ihre Einstellungsstelle fest:

| Einstellungsstelle | Vorgaben |
| --- | --- |
| `catalog` – fest im Katalog | Auswertungsraster und Laufzeittakt; Seiten-, Cache- und Übertragungsgrößen des Archivs; numerische Toleranzen und interne Aufheizschätzungsfaktoren; technische Anzeige-, Geometrie- und Gestaltungsvorgaben. Im Panel unter **Technik** als **Fest vorgegeben** aufgeführt. |
| `integration` – Integrationskonfiguration | Temperatur-, Bedien-, Licht-, Anlagen-, Steuerungs-, Überwachungs- und Erkennungsvorgaben sowie Messkontext und Beobachtungszeit der Aufheizschätzung. |
| `panel` – Einstellungsmenü | Programm- und Startvorgaben, Temperaturtasten, Verlauf-Aktualisierung, Seitengrößen der Detailansicht, Nachkommastellen, Zoomverhalten sowie ausgewählte Abstände, Höhen und Rundungen. |

Kataloggebundene Vorgaben werden beim Laden aus dem Katalog bezogen. Die
gespeicherte Instanzkonfiguration enthält die veränderbaren Vorgaben. Eine
Sitzungsaufzeichnung hält den vollständigen damals wirksamen Parameterstand
fest, damit ihre spätere Auswertung dieselben fachlichen Vorgaben verwendet.

Unter **Anlage und Energie → Ofen, Strompreis und Kabine** ist der **Strompreis**
optional in €/kWh erfassbar. Die Werkseinstellung ist leer; durch Löschen der
Eingabe wird der gespeicherte Strompreis wieder entfernt. Ebenso optional ist das
**Kabinenvolumen** in m³ für die Wassermenge je Aufguss; ohne Wert gibt es keine
Wassermenge. Beide Werte gelten für die [Kennzahlen](kennzahlen.md) aller
archivierten Sitzungen.

### Darstellung und Aktualisierung

Unter **Darstellung** sind Instrumente, Farben, Anzeigeskalen, Seitengrößen in
den Details, Zoom und Verschieben im Verlauf sowie Nachkommastellen gruppiert.
Die Einstellungen gelten für die jeweilige Sauna. **Darstellung speichern**,
**Änderungen verwerfen** und **Standards für diesen Bereich** verwenden denselben
vollständigen Einstellungsstand.

Die Statusanzeige und der laufende Archivverlauf haben getrennte
Aktualisierungsabstände. Nachgeladene Daten ergänzen die geöffnete Ansicht;
Zoom, Fensterausschnitt, Markierung, Fokus und aufgeklappte Abschnitte bleiben
erhalten. Die Ladeanzeige gehört zum ersten Abruf einer Ansicht.

### Technik

**Technik** fasst selten benötigte Werte zusammen und ist im Panel eingeklappt.
Einstellbar sind die **Beobachtungszeit für das Aufheizen** und die **Messwerte
vor und nach Sitzungen**. Der Messkontext legt fest, welcher Zeitraum vor und
nach einer Sitzung im Archiv berücksichtigt wird. Eine archivierte Sitzung
verwendet den bei ihr gespeicherten Kontextwert. Die übrigen Werte dieses
Bereichs sind fest vorgegeben und werden nur zur Information angezeigt.

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

Die Steuerung zeigt Station, Wetterlage, Temperatur und relative Feuchte in einer
Zeile unter den Instrumenten. Aufgeklappt zeigt sie allen Benutzern alle Werte
mit Mess- und Aktualisierungszeit, wie die Details.

Umgebungsdaten beeinflussen weder Heizregelung noch Gangerkennung. Während
einer Sitzung werden sie bei jeder neuen Messung der Quelle
[archiviert](speicherung.md#umgebung-und-erkennungsverlauf).
Absolute Feuchte wird aus der Quelle übernommen; Mess-, Vorhersage- und
HA-Aktualisierungszeit bleiben getrennt.

## Betriebsparameter abstimmen

| Einstellung | Zusammenhang |
| --- | --- |
| Solltemperaturen | Gemeinsame Grenzen für direkte Wahl, Programme und Tastervorgabe. Eingaben und Zwischenstufen werden auf ganze Grad gerundet; ab einem halben Grad aufwärts. |
| Helligkeitsskala | Muss dem konfigurierten Maximalwert des Lichtgeräts entsprechen, damit eigene Befehle und Geräterückmeldungen zusammenpassen. |
| Erkennung | Ohne einstellbare Schwellen: Die Größen des Zustandsmodells stammen aus bewerteten Aufzeichnungen und werden auf Anforderung aus den Belegen freigegebener Sitzungen nachgelernt ([Erkennung](erkennung.md#gelernte-größen-und-nachlernen)). Gespeicherte Werte der früheren Kurvenerkennung bleiben lesbar, wirken aber nicht. |
| Höchstalter der Messwerte | Muss die regulären Meldeabstände der jeweiligen Quelle abdecken. |
| Sonnenstand für das Licht | Der Nachtgrenzwert muss unter dem Tagesgrenzwert liegen; dazwischen wird die Helligkeit überblendet. |
| Aufheizschätzung | Mindestzahl und Zeitraum der Messpunkte, Rausch- und Abfallgrenzen sowie Glättungs- und Bestätigungszeiträume bestimmen, wann und wie eine verbleibende Aufheizzeit ausgewiesen wird. |

[Ausfälle und Schutzabschaltung](betrieb.md#bei-sensorausfall-oder-schutzabschaltung)

Zusammenhänge: [Heizregelung und Licht](betrieb.md),
[Ofenkühlung](ofenkuehlung.md), [Gangerkennung](gangmodell.md),
[Erkennung](erkennung.md).

## Fehler im Home-Assistant-Protokoll untersuchen

Die Protokollstufe unter **Daten und Wartung → Protokollierung** wirkt sofort auf
das Home-Assistant-Protokoll.
Der Datenumfang des Sitzungsarchivs bleibt unverändert.

## Werkseinstellungen

Jeder dauerhafte Wert hat drei Stufen: die **Werkseinstellung** aus dem Katalog,
einen optional gespeicherten **Standard** und den **aktiven Wert**. Beim
Bearbeiten kommt der noch nicht gespeicherte **Entwurf** hinzu. Ohne
gespeicherten Standard gilt die Werkseinstellung als Standard.
Die **Standardtemperatur** behält ihre besondere Funktion bei der Temperaturwahl;
sie ist keine allgemeine Rücksetzung.

Jeder Bereich zeigt die drei Stufen gleich: Weicht der aktive Wert vom
gespeicherten Standard ab, steht unter dem Feld **Abweichend vom Standard** mit
dessen Wert; ohne gespeicherten Standard **Abweichend von der Werkseinstellung**.
Den Vergleich liefert die Integration. Die Integrationskonfiguration nennt die
abweichenden Felder eines Formulars unter **Abweichend**. Das Feldmenü **⋯** zeigt
Werkseinstellung, gespeicherten Standard, aktiven Wert und Entwurf. Im Panel
nennt **Nicht gespeichert** die Felder mit Entwurf.

| Aktion | Wirkung nach Vorschau und Bestätigung |
| --- | --- |
| **Standard überschreiben** | Speichert den gültigen Entwurf genau eines Feldes als dessen Standard. Der aktive Wert und weitere Entwürfe bleiben erhalten. |
| **Standard wiederherstellen** | Übernimmt gespeicherte Standards, ersatzweise Werkseinstellungen, in die aktiven Werte und betroffenen Entwürfe. |
| **Werkseinstellung** | Übernimmt Katalogwerte in die aktiven Werte und betroffenen Entwürfe; entfernt gespeicherte Standards im gewählten Umfang. |
| Speichern / **Änderungen verwerfen** | Speichern übernimmt gültige Entwürfe als aktive Werte. Verwerfen lädt aktive Werte als Entwurf. Gespeicherte Standards werden dabei nicht geändert. |

Feldmenüs **⋯** gelten für genau ein Feld. **Standards für diesen Bereich** gilt
für den jeweiligen Bereich der Einstellungen, **Daten und Wartung → Standards
für alle Bereiche** für alle. Beide umfassen auch derzeit ausgeblendete
gespeicherte Felder. Die Vorschau zeigt diesen vollständigen Umfang.

Keine Standards haben: die Programmbibliothek und die aktuelle Temperaturwahl,
der Start über Taster oder Betriebsschalter, die Protokollierung, die
Gerätezuordnung mit Saunaname, Erkennungsverfahren und
Benachrichtigungsempfängern sowie fest vorgegebene Werte. Die Darstellung hat
Standards, aber mit **Darstellung speichern** einen eigenen Speichervorgang. Die
Einstellungen der Benachrichtigungen haben Werkseinstellung und aktiven Wert,
aber keinen gespeicherten Standard; sie zeigen **Abweichend von der
Werkseinstellung** und werden mit **Benachrichtigungen speichern** gespeichert.

Änderungen der Betriebsparameter setzen vollständiges Sitzungsende voraus.
Unvereinbare Kombinationen und veraltete Bestätigungen werden vor der Anwendung
abgewiesen; es wird kein Teil der Vorschau angewendet. Andere Entwürfe bleiben
erhalten. Der optionale Strompreis gehört zum Feld-, Bereichs- und Gesamtumfang;
leer und der gültige Zahlenwert 0 bleiben dabei verschieden.
