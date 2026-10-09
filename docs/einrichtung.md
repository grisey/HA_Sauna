# Einrichtung

Voraussetzungen: Home Assistant ab **2026.9.0**, Administratorrechte und bereits
in Home Assistant eingerichtete Sensoren und Aktoren. Andere Automationen für
denselben Ofen oder dasselbe Licht müssen deaktiviert sein; vorhandene
Lichttaster können weiterverwendet werden.

## Installation

1. Benutzerdefiniertes HACS-Repository: `https://github.com/grisey/HA_Sauna`, Typ **Integration**.
2. Nach dem Herunterladen: Neustart von Home Assistant.
3. Einrichtung unter **Geräte & Dienste → Integration hinzufügen → HA Sauna**.

Vorabversionen erfordern die Freigabe in HACS. Auch Updates benötigen einen
Neustart; gespeicherte Einstellungen und Gerätezuordnungen bleiben erhalten.

## Gerätezuordnung

Erforderlich sind:

- Ein zusammengehöriges Temperatur-/Feuchtepaar am selben Messort; eine zweite Position ist optional.
- Ein Schalter für das Heizschütz. Seine bestätigte Stellung dient als Relaisrückmeldung.
- Ein Saunataster oder Betriebsschalter als Bedieneingang, getrennt vom Heizaktor.
- Ein dimmbares Saunalicht.

Beim Ereignistaster muss der passende Ereignistyp ausgewählt sein. Ein binärer
Taster liefert Drücken und Loslassen; ein Betriebsschalter seine Ein-/Ausstellung.
Die [Tasterwirkungen](betrieb.md#bedienhandlungen-und-betriebsart) unterscheiden
sich von den Panelaktionen.

Zusätzliche Präsenz-, Leistungs- und Umgebungssensoren sowie passende
Entitätstypen stehen unter [Entitätsrollen](parameter.md#entitätsrollen).
Die Präsenzquelle bestimmt das [Verfahren der Gangerkennung](gangmodell.md).

## Anlagenparameter

Besonders anlagenabhängig sind Sensor-Meldeabstände, Lichtgeräteskala und
Ofenleistung für die Verbrauchsschätzung. Ihre Zuordnung erklärt die
[Parameterreferenz](parameter.md). Startvorgabe und Temperaturprogramme werden
unter [Programme und Start](bedienung.md#programme-und-tastervorgabe) eingestellt.
