# Konfigurierbare Parameter und abgeleitete Werte

Stand: 18.09.2026. Fachliche Vorgabe für die eigene Integration; die
Konfigurationsoberfläche ist noch nicht implementiert.

## Vereinbart: Einstellungen in der Integration

Notwendige anpassbare Größen werden direkt in Home Assistant konfigurierbar,
über einheitliche eigene Parameterentitäten. Änderungen benötigen keine
Bearbeitung von Python-Code oder YAML. Eingaben werden validiert und dauerhaft
über die gemeinsame Parameterverwaltung gespeichert.

**Pro einstellbarer Größe gibt es genau eine konsumierte Quelle.** Erkennung,
Ablaufsteuerung und Timer erhalten ihre Eingaben daraus. Werden dieselben Werte
in einem Konfigurationsdialog und als Entitäten angezeigt, bearbeiten beide
Zugänge denselben Eintrag; sie erzeugen keine zweite Parametrierung.

Die Bedienung gliedert sich in Gangablauf, Heizregelung und Erkennung.
Kalibrierungsdetails bleiben in einem erweiterten Bereich zugänglich. Namen,
Einheiten, zulässige Bereiche und Schrittweiten werden bei der Umsetzung
verbindlich festgelegt, ohne notwendige Größen im Code zu verstecken.

## Grundwerte, Relationen und Ergebnisse

| Art | Behandlung |
|---|---|
| Fachlicher Grundparameter, etwa Bestätigungsfrist oder Rücksetz-Auszeit | Einmal einstellen und an alle Verbraucher übergeben. |
| Begründeter Relationsparameter, etwa ein Verhältnis zusammenhängender Messfenster | Konfigurierbar halten; der Code bildet die Beziehung ab. |
| Abgeleitete Größe, etwa wirksame Heizdauer, Erkennungsschwelle oder Restzeit | Berechnen und lesbar anzeigen; nicht nochmals unabhängig einstellen. |
| Parameterschnappschuss für Replay oder Sessionarchiv | Herkunft und Auswertung dokumentieren; keine konkurrierende aktuelle Laufzeitquelle. |

Es sollen möglichst wenige voneinander unabhängige Absolutwerte erforderlich
sein. Eine Relation wird nur eingeführt, wenn sie einen sachlichen Zusammenhang
abbildet. Einen festen Zahlenwert durch einen ebenso unbegründeten Faktor zu
ersetzen, genügt diesem Ziel nicht. Sicherheitsgrenzen werden nicht ungeprüft
von adaptiven Messgrenzen abgeleitet.

## Aufgussbestätigungsfrist

Die übliche Gangdauer beträgt nach Nutzerangabe ungefähr 15 Minuten. Für die
Bestätigung eines vorläufigen Gangs wurden **12 oder 13 Minuten ab der
zugeordneten Türschließung** als angemessen benannt. Die Frist wird einstellbar;
es ist noch nicht zwischen diesen Ausgangswerten entschieden.

`Fristende = zugeordneter Gangbeginn + eingestellte Bestätigungsfrist`

Die spätere Personenfrüherkennung verschiebt den Bezugspunkt nicht. Eine kurze
Türbetätigung desselben Gangs ändert dessen Beginn und damit das Fristende nicht.
Die ungefähre Gangdauer von 15 Minuten ist kein automatisches Ende eines durch
Aufguss bestätigten Gangs. Eine Kopplung der Frist an einen festen Anteil der
Gangdauer wurde nicht festgelegt. Folgen eines unbestätigten Fristablaufs stehen
mit ihrem Entscheidungsstatus im [Gangmodell](gangmodell.md).

## Diskutierter Ansatz: relative Erkennungsgrenzen

Als mögliche Überarbeitung wurde besprochen, aktuelle Änderungen gegenüber dem
lokalen Vorverlauf und dessen üblicher Schwankungsbreite zu bewerten. Kanal 3
und Kanal 6 behalten dafür eigene Bezugswerte. Wenige gemeinsame
Empfindlichkeitsparameter könnten mehrere absolute Einzelgrenzen ersetzen.

Ebenfalls zu prüfen ist die Zusammenfassung von Messfenstern auf eine kurze
Zeitskala für Tür- und Aufgussereignisse sowie eine längere für Personenmuster.
Die Fenster müssen zur tatsächlichen Messaktualisierung und zum Ereignis passen;
sie werden nicht aus der ungefähren Saunagangdauer abgeleitet.

Ein möglicher robuster Vergleichsmaßstab ist die mediane absolute Abweichung.
Ein dafür verwendeter Hintergrund müsste aus geeigneten ereignisarmen Abschnitten
stammen und bei einem möglichen Ereignis zunächst festgehalten werden. Eine
technische Untergrenze wäre nötig, damit nahezu rauschfreie Werte keine beliebig
großen relativen Signalstärken erzeugen.

**Diese relative Auswertung ist ein noch ungeprüfter Änderungsvorschlag.** Sie
wird nicht durch die Zustimmung zur Konfigurierbarkeit automatisch zum
vereinbarten Detektor. Konfigurierbarkeit funktioniert auch mit den bisherigen
empirisch kalibrierten Prüfwerten. Der eingefrorene Kandidat und seine
Reproduktionsdaten bleiben unverändert, bis eine geänderte Auswertung besprochen
und gesondert gegen die vorhandenen Referenzereignisse geprüft wurde.

## Weiterhin festzulegen

Verhalten von Parameteränderungen bei bereits laufenden Fristen, endgültige
Ausgangswerte und Eingabegrenzen sowie die tatsächlich geprüften Relationen.
Es wird insbesondere nicht stillschweigend zwischen „nur beim nächsten Start“
und „bestehende Frist sofort neu berechnen“ gewählt.
