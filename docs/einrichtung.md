# Einrichtung

Die Einrichtung erfordert Home-Assistant-Administratorrechte und
Home Assistant ab **2026.9.0**. Sensoren und Aktoren müssen bereits als
Home-Assistant-Entitäten verfügbar sein.

## Installation

HACS verwendet `https://github.com/grisey/HA_Sauna` als benutzerdefiniertes
Repository vom Typ **Integration**. Nach dem Herunterladen und einem Neustart
von Home Assistant lässt sich HA Sauna unter **Geräte & Dienste** einrichten.
Auch nach Updates ist ein Neustart erforderlich; gespeicherte Einstellungen
und Gerätezuordnungen bleiben erhalten. Vorabversionen sind verfügbar, wenn sie
für HA Sauna in HACS aktiviert sind.

Bisherige Automationen für denselben Ofen oder dasselbe Licht müssen deaktiviert
sein, damit sie nicht gegen HA Sauna steuern. Vorhandene Lichttaster bleiben
Teil der Bedienung.

## Entitätszuordnung

Eine Messposition besteht aus einem zusammengehörigen Temperatur- und
Feuchtesensor. Eine vollständige Position genügt; eine zweite ergänzt die
Beobachtung. Den Vorrang und Ersatz ausgefallener Messungen beschreibt die
[Temperaturregelung](betrieb.md#temperatur-und-bereitschaft).

Bedieneingang und Heizschütz sind getrennte Zuordnungen: Der Taster oder
Betriebsschalter bedient die Sitzung, HA Sauna steuert den Heizaktor.
Die bestätigte Stellung des Heizschütz-Schalters dient als Relaisrückmeldung.
Leistungssensor und unabhängiger binärer Heiznachweis sind optionale Ergänzungen.
Das Saunalicht muss dimmbar sein.

Unterstützte Entitätstypen, Einheiten und optionale Umgebungsdaten stehen unter
[Entitätsrollen](parameter.md#entitätsrollen). Die Wahl eines direkten
Präsenzsensors verändert die Gangzuordnung; siehe [Präsenzquelle](gangmodell.md).

## Bedieneingang

Beim Ereignistaster muss der passende Ereignistyp zugeordnet sein. Ein binärer
Taster liefert Drücken und Loslassen; seine Langdruckdauer ist einstellbar.
Ein Betriebsschalter überträgt seine Ein-/Ausstellung auf den Saunabetrieb.
Die Wirkungen unterscheiden sich von einzelnen Panelaktionen; maßgeblich ist
die [Gestenzuordnung](betrieb.md#bedienhandlungen-und-betriebsart).

## Anlageneinstellungen

Sensorfristen, Helligkeitsskala und weitere Anlagenparameter sind in der
[Parameterreferenz](parameter.md) beschrieben; die
[Bedienungsanleitung](bedienung.md#technische-einstellungen-und-rücksetzen)
nennt die erforderlichen Rechte und Sitzungssperren.

Die Programmbibliothek und die Startvorgabe für Taster oder Betriebsschalter
stehen unter **Programme und Start**. Geltungsbereich und Speicherregeln erklärt
[Programme und Tastervorgabe](bedienung.md#programme-und-tastervorgabe).
