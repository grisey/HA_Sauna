# Oberfläche

Stand 19.09.2026, einschließlich der jüngsten Präzisierung: zwei Hauptansichten.
Das native HA-Custom-Panel ist unter `/ha-sauna` registriert. Es verwendet die
vorhandene HA-Anmeldung und authentifizierte APIs; es führt keinen eigenen
Regelungszustand. Browsernachweise stehen im [Abnahmebericht](abnahme.md).

## Normal

Einfache Steuerung mit Status, Betrieb-An/Aus, Temperaturwahl vor einer Session,
Temperatur und Feuchte. Temperaturkacheln verwenden zentral konfigurierte
Ausgangstemperatur, Abstand und Anzahl; Vorlage: 70 bis 95 °C in 5-Grad-Schritten.
Die Kacheln setzen die Solltemperatur und starten über denselben Backendpfad.
Gemischte alte Start-/Endtemperatur-Szenen werden nicht übernommen: Die aktuelle
Grundkonfiguration bleibt während der Session unverändert.

Auf dem eigenen Blatt **Sessionverlauf & Archiv** sind die laufende Session und
historische Sessions auswählbar. Gestaltung aus der gelieferten Vorlage:

| Element | Darstellung |
|---|---|
| Temperatur | `#ff6b4a`, linke °C-Achse |
| Luftfeuchte | `#42a5ff`, rechte Prozentachse |
| Messposition | Oben durchgezogen, unten gestrichelt; getrennt ein-/ausblendbar |
| Tür offen | Gelbe Zeitfläche |
| Gang | Magentafarbene Fläche; vorläufig mit gestrichelter Kontur |
| Aufguss | Weiße Zeitmarke |
| Heizen / bereit / lüften | Orange / grüne / blaugraue Zeitfläche |
| Reale Heizaktivität | Eigener schmaler Streifen aus tatsächlichen Heizintervallen |

Gangflächen stammen aus dem neuesten zugeordneten Sessionobjekt. Eine spätere
Bestätigung verändert weder ID noch Beginn. Tooltip zeigt Originalwert und
zugehörigen Empfangszeitpunkt jeder Messreihe. Zoom, verschiebbarer Ausschnitt
und Rückkehr zur Gesamtsession sind Darstellungsfunktionen. Die Übertragung
lädt das Archiv seitenweise; eine Begrenzung der SVG-Punkte erhält Extrema pro
Bildspalte und verändert niemals gespeicherte Originale.

## Details

**Betrieb & Fristen:** beide Messpositionen, Bereitschaftsziel, tatsächliche
Heizzeit und Budget, Gangstatus, Nachlauf, Kühlung, Türwartefrist, mechanischer
Timer als Schätzung, Heizentscheidungsgrund und aktuelle Fehler.

**Erkennungskontrolle:** eigene Verlaufskurven der tatsächlich im Detektor
berechneten Tür-, Personen- und Aufgussmerkmale. Die für die ausgewählte Session
gespeicherten Schwellen werden eingeblendet. Haltezähler, Kontext,
Sensorverfügbarkeit und ausgelöste Signale bleiben nachvollziehbar. Es wird
kein zweiter Detektor im Browser nachgebaut. Historische Fehler und die
ursprünglichen Erkennungszeiten stehen hier getrennt vom normalen Verlauf.

**Einstellungen & Export:** zentrale Parameter, eingeklappte Expertenwerte,
Verweis zum HA-Zuordnungsdialog und ZIP-Download des Archivs. Während einer
Session sind Änderungen im Frontend und im Backend gesperrt. Steuerung und
Parameteränderung verlangen HA-Administratorrechte, auch bei direktem API-Aufruf.

## Grenzen

Die Oberflächenprüfung verwendet synthetische Daten im tatsächlichen HA-Frontend.
Die private Bildschirmaufnahme und ihre Entity-IDs werden nicht veröffentlicht.
Eine Vorhersage der verbleibenden Aufheizzeit ist keine vereinbarte/validierte
Regel und wird nicht als zuverlässige Restzeit erfunden. Vorhandene Timer und
Fristen werden mit ihrem tatsächlichen Zeitbezug angezeigt.
