# Einrichtung

Die Einrichtung erfordert Home-Assistant-Administratorrechte und
Home Assistant ab **2026.9.0**. Die Zuordnung erfolgt über die bereits in
Home Assistant vorhandenen Entitäten der Anlage.

## Installation

In HACS wird `https://github.com/grisey/HA_Sauna` als benutzerdefiniertes
Repository vom Typ **Integration** hinterlegt. Nach dem Herunterladen von
HA Sauna und einem Neustart von Home Assistant steht die Integration unter
**Einstellungen → Geräte & Dienste → Integration hinzufügen → HA Sauna**
zur Einrichtung bereit.

Vorabversionen sind bei aktiviertem HACS-Schalter für Vorabversionen von
HA Sauna verfügbar. Dazu gehört gegebenenfalls die Aktivierung der zugehörigen
Entität. Updates erfolgen durch Versionswahl in HACS und anschließenden Neustart
von Home Assistant. Gespeicherte Einstellungen und Gerätezuordnungen bleiben
erhalten.

## Entitätszuordnung

Die vorhandenen Entitäten werden folgenden Aufgaben zugeordnet:

| Aufgabe | Benötigte Zuordnung |
| --- | --- |
| Messposition | Ein zusammengehöriges Paar aus Temperatur und relativer Luftfeuchte, oben oder unten |
| Heizaktor | Schalter des Heizschützes |
| Bedienung | Saunataster oder Betriebsschalter |
| Beleuchtung | Dimmbares Saunalicht |

Eine vollständige Messposition ermöglicht den gesamten Betrieb einschließlich
der zusätzlichen thermischen Türerkennung. Eine zweite Messposition ergänzt
die Beobachtung. Für die Temperaturregelung führt der obere Temperaturwert;
bei dessen Ausfall übernimmt der gültige untere Wert. Sobald oben wieder ein
gültiger Wert vorliegt, führt dieser erneut. Der Ausfall einer konfigurierten
Messposition bleibt als Störung sichtbar, während der Betrieb mit der
verfügbaren Position weiterläuft.

Die bestätigte Schalterstellung des Heizschützes liefert die erforderliche
Relaisrückmeldung. Dafür genügt auch die bestätigte Stellung des verwendeten
Shelly-Schalters. Ein Leistungssensor oder ein unabhängiger binärer Heiznachweis
kann die Anlage ergänzen.

[Entitätsrollen und Einheiten](parameter.md#entitätsrollen)

## Bedieneingang

**Taster oder Betriebsschalter** und **Schalter des Heizschützes** sind
getrennte Zuordnungen. Der Taster bedient die Saunasitzung; HA Sauna steuert
den Heizschütz.

Die Konfiguration eines Ereignistasters enthält den zugehörigen Ereignistyp.
Ein binärer Taster meldet Drücken und Loslassen; die eingestellte
**Langdruckdauer des Saunatasters** bestimmt den langen Druck. Ein
Betriebsschalter überträgt seine Ein-/Ausstellung auf den Saunabetrieb. Die
entkoppelten Lichttaster bleiben unmittelbar bedienbar.

[Tasterbedienung und Gestenzuordnung](betrieb.md#bedienhandlungen-und-betriebsart)

## Anlageneinstellungen

Im Saunapanel enthält **Einstellungen → Grundeinstellungen** die
Betriebsparameter. Voraussetzung für gültige Messwerte ist ein zu den üblichen
Meldeabständen der Sensoren passendes **Höchstalter eines Messwerts**. Die
Zuordnung der Schützrückmeldung und die **Helligkeitsskala des Lichtgeräts**
entsprechen der Gerätekonfiguration.

[Parameterreferenz](parameter.md)

Die Untergrenze für Solltemperaturen beträgt bei einer neuen Einrichtung
60 °C und lässt sich einstellen. Die feste Obergrenze beträgt 100 °C.
Gespeicherte Temperaturvorgaben bleiben bei einem Update erhalten; eine bereits
ausdrücklich eingestellte Untergrenze wird übernommen.

Unter **Einstellungen → Programme** sind die Temperaturprogramme bearbeitbar.
**Einstellungen → Saunataster** enthält das Programm oder die konstante
Temperatur für den Start mit dem Taster.

Der Betrieb mit HA Sauna setzt voraus, dass bisherige Automationen zur Steuerung
desselben Ofens oder Lichts deaktiviert sind. Die vorhandenen Lichttaster
bleiben Teil der Bedienung.

Gerätezuordnungen und technische Grundeinstellungen lassen sich nach Ende einer
Sitzung über **Einstellungen** erneut bearbeiten.

[Bedienungsanleitung](bedienung.md)
