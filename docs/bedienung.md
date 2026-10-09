# Bedienung

## Eingriffe in die Sitzung

Direkte Sollwertänderung am Rundinstrument wirkt sofort und ersetzt das
Temperaturprogramm durch einen konstanten Sollwert. Programm- und Schnellwahlen
benötigen während der Sitzung **Programm übernehmen**; außerhalb gelten sie
sofort. Gangzählung und laufende Zeiten bleiben erhalten.

**Ausschalten** lässt die Sitzung bis zum Ende der Wiederaufnahmefrist offen.
In dieser Zeit bleiben Betriebsartwechsel und technische Konfiguration gesperrt.
Der lange Tasterdruck schließt die Sitzung dagegen sofort; sein Lichtnachlauf
beginnt erst beim Loslassen.

Für Ofen- und Lichtübersteuerung gelten unterschiedliche Rückkehrbedingungen:
[Übersteuerung und Betriebsart](betrieb.md#bedienhandlungen-und-betriebsart).
Eine Ofen-EIN-Wahl bleibt der [Heizfreigabe](betrieb.md#heizpriorität) untergeordnet.

## Übernahme und Rechte

| Änderung | Übernahme / Freigabe |
| --- | --- |
| Programmbibliothek | **Fertig** ändert nur den Entwurf; der gesamte Katalog benötigt **Programme speichern**. Nach Sitzungsende auch ohne Administratorrechte. |
| Tastervorgabe | Sofort gespeichert, unabhängig von der aktuellen Temperaturwahl im Panel. |
| Darstellung | Auch die Rücksetzung ändert nur den Entwurf; Speichern wirkt für alle Benutzer der Sauna. |
| Betriebsart Manuell | Normale Bedienrechte, nach vollständigem Sitzungsende. Ofen und Licht beginnen mit AUS. |
| Ofenübersteuerung in Automatik | Administratorrechte. |
| Anlagenparameter und Gerätezuordnungen | Administratorrechte, nach Sitzungsende. Aktuelle Temperaturwahl bleibt erhalten. |

Offene oder nach Speicherfehlern verbliebene Entwürfe werden durch Sitzungsende
nicht übernommen. Protokollstufe und Darstellung bleiben während der Sitzung
änderbar. [Rücksetzumfang](parameter.md#standardwerte-wiederherstellen)

## Verlauf und Darstellung

Der Normalverlauf verwendet die führende Messposition. Diagnoseansichten sind
Administratoren vorbehalten und verwenden die zur Sitzung gespeicherten
Einstellungen. [Datenbasis und Export](speicherung.md)

Die Messbogenskala begrenzt zugleich den direkt wählbaren Sollbereich,
ändert aber keine Regelparameter oder Verlaufsachsen.
