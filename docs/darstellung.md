# Sessionansicht

Stand: 18.09.2026. Der Nutzer hat dem vorgeschlagenen Aufbau zugestimmt.
Diese Datei beschreibt die vereinbarte Ansicht; Frontend und Datenabfrage
sind noch nicht implementiert.

## Gemeinsame Ansicht für aktuellen Ablauf und Zeitverlauf

| Bereich | Vereinbarter Inhalt |
|---|---|
| Aktueller Ablauf | Gegenwärtige Phase, zugehörige Dauer beziehungsweise Restzeit, Bestätigungsstand eines laufenden Gangs und Ganganzahl. |
| Temperaturregelung | Solltemperatur, Messwerte beider Höhen und tatsächliche Heizaktivität; getrennt von der Ablaufphase. |
| Sessionverlauf | Temperatur- und Feuchtekurven beider Höhen, Heizintervalle, Türereignisse, Aufgüsse und zusammenhängende Gangintervalle mit Beginn und Dauer. |
| Fehlerhinweise | Sensorfehler und dadurch eingeschränkter Ein-Sensor-Betrieb unmittelbar sichtbar. |

Kanal 3 und Kanal 6 behalten ihre Identität und ihre unterschiedliche Einbauhöhe.
Die Ansicht erzeugt weder ein gemitteltes Ersatzsignal noch eine eigene
Steuerungsphase. Phase, Bestätigungsstand und Ganganzahl werden aus den
vereinbarten Laufzeitobjekten abgeleitet.

## Gangintervalle und historische Einordnung

Vorläufige und bestätigte Gänge kennzeichnen denselben Zeitabschnitt
unterschiedlich. Bei späterer Erkennung erscheint der Gang ab der zugeordneten
Türschließung. Die Aufgussbestätigung verändert die Kennzeichnung, nicht Beginn
oder Identität. Tatsächlicher Erkennungszeitpunkt und Zeitpunkt der Bestätigung
bleiben im Ereignisverlauf nachvollziehbar.

Die eigene Historienansicht verwendet dafür das Sessionarchiv und nicht nur
native HA-Zustandswechsel. Sie kann in alte Sessions hineinzoomen; die gespeicherten
Originaldaten bleiben vollständig aufgelöst. Eine etwaige Reduktion der für eine
Übersicht übertragenen Kurvenpunkte darf ausschließlich die Darstellung betreffen,
nicht die Archivdaten ersetzen. Details: [Zeitmodell](zeitmodell.md) und
[Speicherung](speicherung.md).

## Einstellungen und Export

Konfiguration und Kalibrierung stehen in einem getrennten Einstellungsbereich.
Dort wird ein Downloadbutton für den Datenexport angeboten. Die Daten stammen
aus demselben Archiv wie die Ansicht; der Button erzeugt keine zweite
Datensammlung. Die technische Umsetzung des authentifizierten Downloads und
das Dateiformat stehen im [Speicherblock](speicherung.md).

Neue Phasenbedeutungen, zusätzliche Regelungsvorgaben oder eine bestimmte
Frontend-Technik werden mit dieser Darstellungsvereinbarung nicht eingeführt.
