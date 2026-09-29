# Einrichtung

Die Einrichtung erfordert Home-Assistant-Administratorrechte und
Home Assistant ab **2026.9.0**. Die Zuordnung erfolgt über die bereits in
Home Assistant vorhandenen Entitäten Ihrer Anlage.

## Integration installieren

1. Fügen Sie in HACS `https://github.com/grisey/HA_Sauna` als
   benutzerdefiniertes Repository vom Typ **Integration** hinzu und laden Sie
   HA Sauna herunter.
2. Starten Sie Home Assistant neu.
3. Öffnen Sie **Einstellungen → Geräte & Dienste → Integration hinzufügen**
   und wählen Sie **HA Sauna**.

Für eine Vorabversion aktivieren Sie den HACS-Schalter für Vorabversionen von
HA Sauna. Gegebenenfalls aktivieren Sie zuvor dessen Entität. Wählen Sie bei
einem Update die gewünschte Version in HACS und starten Sie Home Assistant
anschließend neu. Gespeicherte Einstellungen und Gerätezuordnungen werden
übernommen.

## Anlage zuordnen

Ordnen Sie die vorhandenen Entitäten ihren Aufgaben zu:

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

## Bedieneingang einrichten

Wählen Sie den **Taster oder Betriebsschalter** und den **Schalter des
Heizschützes** als getrennte Rollen. Der Taster bedient die Saunasitzung;
HA Sauna steuert den Heizschütz.

Bei einem Ereignistaster wählen Sie den passenden Ereignistyp. Ein binärer
Taster meldet Drücken und Loslassen; die eingestellte **Langdruckdauer des
Saunatasters** bestimmt den langen Druck. Ein Betriebsschalter überträgt seine
Ein-/Ausstellung auf den Saunabetrieb. Die entkoppelten Lichttaster bleiben
unmittelbar bedienbar.

## Einstellungen an die Anlage anpassen

Öffnen Sie im Saunapanel **Einstellungen → Grundeinstellungen**. Prüfen Sie
zuerst, ob das **Höchstalter eines Messwerts** die üblichen Meldeabstände Ihrer
Sensoren abdeckt. Kontrollieren Sie anschließend die Schützrückmeldung und die
**Helligkeitsskala des Lichtgeräts** anhand der Gerätekonfiguration.

[Parameterreferenz](parameter.md)

Die Untergrenze für Solltemperaturen beträgt bei einer neuen Einrichtung
60 °C und lässt sich einstellen. Die feste Obergrenze beträgt 100 °C.
Gespeicherte Temperaturvorgaben bleiben bei einem Update erhalten; eine bereits
ausdrücklich eingestellte Untergrenze wird übernommen.

Unter **Einstellungen → Programme** passen Sie die Temperaturprogramme an.
Unter **Einstellungen → Saunataster** wählen Sie das Programm oder die konstante
Temperatur für den Start mit dem Taster.

Deaktivieren Sie vor dem ersten Betrieb die bisherigen Sauna-Automationen,
die denselben Ofen oder dasselbe Licht steuern. Damit führt HA Sauna den
automatischen Ablauf; die vorhandenen Lichttaster bleiben Teil der Bedienung.

Gerätezuordnungen und technische Grundeinstellungen lassen sich nach Ende einer
Sitzung über **Einstellungen** erneut bearbeiten.

[Bedienungsanleitung](bedienung.md)
