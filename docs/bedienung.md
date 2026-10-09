# Bedienung

## Betriebsstart und Temperaturwahl

| Auswahl | Wirkung |
| --- | --- |
| Direkte Temperaturwahl am Rundinstrument | Gilt sofort; setzt eine konstante Solltemperatur. |
| Programm- oder Schnellwahl ohne Sitzung | Gültige Auswahl wird direkt gespeichert. |
| Programm- oder Schnellwahl während einer Sitzung | Wird erst mit **Programm übernehmen** wirksam. |

Eine Temperaturänderung erhält laufende Gänge und deren Zeiten.
Unvollständige oder nach einem Speicherfehler offene Eingaben bleiben Entwürfe;
auch das Sitzungsende übernimmt sie nicht automatisch.
[Stufenfolge und Programmänderungen](betrieb.md#temperaturprogramm)

## Ausschalten und Fortsetzen

| Aktion | Wirkung |
| --- | --- |
| **Ausschalten** | Heizung aus; Sitzungspause mit Lichtnachlauf. |
| **Fortsetzen** innerhalb der Wiederaufnahmezeit | Dieselbe Sitzung wird fortgesetzt. |
| **Endgültig beenden** oder Ablauf der Wiederaufnahmezeit | Sitzung abgeschlossen, Licht aus. Der nächste Start beginnt eine neue Sitzung. |

Beim langen Saunatasterdruck beginnt der Lichtnachlauf erst beim Loslassen.
Die übrigen [Tasterwirkungen](betrieb.md#bedienhandlungen-und-betriebsart) und
[Kühlregeln](ofenkuehlung.md) sind gesondert beschrieben.

## Betriebsart Manuell und Übersteuerung

| | Manuelle Betriebsart | Ofenübersteuerung in Automatik |
| --- | --- | --- |
| Rechte | Normale Bedienrechte | Administrator |
| Wahl | Nach vollständigem Sitzungsende | Während des Automatikbetriebs |
| Dauer | Bis zum Betriebsartwechsel | Bis Rückgabe, passendem Phasen-/Schaltwechsel oder Fristablauf |
| Anfang | Ofen und Licht aus | Ausdrücklich gewählte Ofenstellung |

Technische Schutzbedingungen bleiben wirksam. Die [Heizpriorität](betrieb.md#heizpriorität)
bestimmt, wann eine Ofen-EIN-Wahl ausgeführt wird.

## Licht

Manuelle Lichtwahl in Automatik gilt bis zum passenden Phasenwechsel oder zum
Ablauf der Übersteuerungsdauer. Das gilt auch für Änderungen am Lichttaster.
Im manuellen Betrieb bleibt die Lichtwahl unbefristet.
Administratoren können zusätzlich eine freie Helligkeit vorgeben.
[Automatische Lichtkurve](betrieb.md#licht)

## Programme und Tastervorgabe

Die Programmbibliothek ist nach Sitzungsende mit normalen Bedienrechten
bearbeitbar. **Fertig** übernimmt eine Eingabe in den Entwurf;
**Programme speichern** speichert den gesamten Katalog. Die Reihenfolge lässt
sich auch mit Pfeiltasten am fokussierten Griff ändern.

Die Startvorgabe für Taster oder Betriebsschalter wird sofort gespeichert.
Der Start im Panel verwendet dessen aktuelle Temperaturwahl.

## Verlauf und Anzeigen

| Anzeige | Bedeutung |
| --- | --- |
| **Bereit** | Solltemperatur erreicht; weiteres Heizen ist möglich. |
| Ofenwahl / Ofenrückmeldung | Gewünschte Stellung / beobachteter Zustand. Fehlende Rückmeldung bedeutet nicht AUS. |
| Aufheizprognose | Schätzung aus früherem Aufheizverlauf und aktuellem Temperaturtrend, in Fünf-Minuten-Stufen. |
| Dauern | Verstrichene Minuten ab-, verbleibende aufgerundet. Ereigniszeitpunkte behalten Sekunden. |
| Verlaufskurve | Führende Messposition; fehlende oder veraltete Messwerte unterbrechen die Kurve. Die Wertanzeige verwendet Originalmesspunkte. |
| Detailverlauf und Erkennungskontrolle | Für Administratoren: Schaltgründe und Nachweise der Erkennung mit den damaligen Einstellungen. |

Strg/Cmd mit dem Mausrad oder eine Vergrößerungsgeste zoomt den Verlauf;
normales Scrollen bewegt den Inhalt. Aufbewahrung, Datenlücken und Export
beschreibt [Archiv und Export](speicherung.md).

## Darstellung

Darstellungsänderungen gelten nach dem Speichern für alle Benutzer der Sauna.
Auch **Standarddarstellung wiederherstellen** ändert zunächst nur den Entwurf.
Die Home-Assistant-Startseite wird dagegen je Benutzerprofil gewählt.

Die Anzeigeskala begrenzt den direkt am Rundinstrument wählbaren Sollbereich,
ändert aber keine Regelparameter. Werte außerhalb der Skala bleiben als Zahl
sichtbar. Verlaufsachsen richten sich nach den gespeicherten Messungen.

## Technische Einstellungen und Rücksetzen

Anlagenparameter und Gerätezuordnungen sind nach Sitzungsende mit
Administratorrechten änderbar. Protokollstufe und Darstellung bleiben während
einer Sitzung anpassbar. Technische Änderungen erhalten die aktuelle
Temperaturwahl. Der [Rücksetzumfang](parameter.md#standardwerte-wiederherstellen)
unterscheidet Softwarevorgaben, Gerätezuordnungen und Archiv.
