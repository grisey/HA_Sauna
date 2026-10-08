# Bedienungsanleitung

Eine Saunasitzung kann mehrere Saunagänge enthalten. Beginn und Bestätigung
hängen von der gewählten [Präsenzquelle](gangmodell.md) ab. Die markierte
Ofenwahl bezeichnet den gewünschten Modus; die Ofenrückmeldung zeigt den
beobachteten Zustand.

## Betriebsstart und Temperaturwahl

Der Start setzt gültige Messwerte und eine vollständige Einrichtung voraus.
Fehlende Voraussetzungen werden im Panel angezeigt.

Die direkte Wahl am Temperaturbogen gilt sofort und setzt eine konstante
Solltemperatur. Programm- und Schnellwahlen benötigen während einer bestehenden
Sitzung dagegen **Programm übernehmen**. **Abbrechen** erhält die bisherige Wahl.
Laufende Gänge und ihre Zeiten bleiben bei einer Temperaturänderung erhalten.

Ohne Sitzung werden gültige Programmwahlen und abgeschlossene individuelle
Eingaben direkt gespeichert. Angefangene oder ungültige Eingaben bleiben als
Entwurf erhalten. Während einer Übertragung kann weitergearbeitet werden; der
zuletzt abgeschlossene gültige Stand folgt dem laufenden Speicherauftrag.
Ein Speicherfehler erhält den Entwurf und stoppt weitere automatische Übernahmen.
Beginnt inzwischen eine Sitzung, benötigen wartende Änderungen eine Bestätigung.
Auch das Sitzungsende übernimmt einen noch offenen Entwurf nicht automatisch.

Bei Temperaturfolgen gilt nach jedem beendeten, bestätigten Gang die nächste
Stufe; nach der letzten bleibt deren Temperatur erhalten. Gleichmäßige
Verteilung und einzeln vorgegebene Stufen sind alternative Eingabeformen.
Grenzen und Berechnung stehen unter [Temperaturprogramm](betrieb.md#temperaturprogramm).

## Phasen und Zeitangaben

**Bereit** bedeutet, dass die Solltemperatur erreicht wurde. Der Ofen kann
weiterheizen, um die eingestellte Temperaturreserve aufzubauen. Regeln und
Vorränge beschreibt [Temperatur und Bereitschaft](betrieb.md#temperatur-und-bereitschaft).

Die Kühlzeit zählt erst ab bestätigtem Schütz-AUS. Ein vorzeitiges
**Kühlung beenden** ändert die Grundlage der nächsten Dauerberechnung nicht:
Dafür bleibt die letzte vollständig beendete Kühlung maßgeblich.
Siehe [Ofenkühlung](ofenkuehlung.md).

Aufheizschätzung und Rundung der Zeitangaben sind in der
[Darstellungsreferenz](darstellung.md#zustand-und-zeit) erklärt. Der mechanische
Ofentimer in den technischen Details ist eine Schätzung aus bestätigter
Schütz-EIN-Zeit, keine direkte Ablesung des Geräts.

## Ausschalten und Fortsetzen

**Ausschalten** beendet das Heizen und beginnt die Sitzungspause mit
Lichtnachlauf. Innerhalb der eingestellten Wiederaufnahmezeit setzt
**Fortsetzen** dieselbe Sitzung fort.

**Endgültig beenden** schließt die Sitzung und schaltet das Licht sofort aus.
Dasselbe geschieht beim Ablauf der Wiederaufnahmezeit. Der nächste Start eröffnet
eine neue Sitzung. Der lange Tasterdruck hat einen eigenen Lichtabschluss;
siehe [Tasterbedienung](betrieb.md#bedienhandlungen-und-betriebsart).

## Licht

Im Automatikbetrieb ist eine manuelle Lichtwahl eine befristete Übersteuerung.
Sie endet beim passenden Phasenwechsel oder spätestens nach der eingestellten
Dauer. Ohne eingeschalteten Betrieb sind die Automatik-Lichttasten gesperrt.
Administratoren können zusätzlich eine freie Helligkeit vorgeben; ein offener
Eingabeentwurf verändert den Lichtzustand noch nicht.

Der angezeigte Lichtstatus verwendet die Geräterückmeldung. Fehlt sie, wird die
bestätigte Lichtvorgabe entsprechend gekennzeichnet. Fehlen beide, bleibt der
Status unbekannt. Rückkehrregeln und Lichtnachlauf beschreibt
[Licht](betrieb.md#licht).

## Saunataster und Lichttaster

Die Tastergeste kann eine andere Wirkung als der Betriebsbutton im Panel haben.
Insbesondere schließt der lange Druck die Sitzung ab; der Lichtnachlauf beginnt
erst beim Loslassen. Ein kurzer Druck im Automatikbetrieb verwendet die
hinterlegte Startvorgabe oder übersteuert bei laufendem Betrieb vorübergehend
den Ofen. Ofenkühlung und Schutzabschaltungen behalten Vorrang.

Die vollständige [Gestenzuordnung](betrieb.md#bedienhandlungen-und-betriebsart)
ist für Ereignistaster, binäre Taster und Betriebsschalter beschrieben.
Vorhandene Lichttaster bleiben unmittelbar bedienbar; ihre Lichtwahl wird als
manuelle Übersteuerung übernommen.

## Betriebsart Manuell und Übersteuerung

Der Wechsel zwischen Automatik und Manuell ist erst nach Sitzungsende möglich;
die Wiederaufnahmezeit gehört noch zur Sitzung. Dafür genügen normale
Home-Assistant-Bedienrechte.

Im manuellen Betrieb bleiben Ofen- und Lichtvorgaben ohne Übersteuerungsfrist
bestehen. Gültige Regeltemperatur und freigegebene technische Überwachung sind
weiterhin Voraussetzungen fürs Heizen. Messung und Archivierung laufen weiter.

Die manuelle Ofenübersteuerung innerhalb des Automatikbetriebs ist dagegen
Administratoren vorbehalten und zeitlich begrenzt. Ein passender Phasen- oder
Schaltwechsel kann sie früher beenden. Schutzabschaltungen und Ofenkühlung haben
Vorrang. Details stehen unter [Bedienhandlungen und Betriebsart](betrieb.md#bedienhandlungen-und-betriebsart).

## Programme und Tastervorgabe

Normale Bedienrechte genügen zum Bearbeiten der Programmbibliothek nach
Sitzungsende. **Fertig** übernimmt eine Programmeingabe zunächst nur in den
Katalogentwurf; erst **Programme speichern** speichert den gesamten Entwurf
dauerhaft. **Änderungen verwerfen** stellt den gespeicherten Katalog wieder her.
Die Reihenfolge lässt sich auch mit Pfeiltasten am fokussierten Griff ändern.

Die Startvorgabe unter **Programme und Start** wird dagegen sofort gespeichert.
Sie gilt beim externen Start über Taster oder Betriebsschalter. Der Start im
Panel verwendet die dortige Temperaturwahl.

## Verlauf und technische Details

Der normale Verlauf zeigt die führende Messposition. Strg/Cmd mit dem Mausrad
oder eine Vergrößerungsgeste verändert den Ausschnitt; normales Scrollen bewegt
die Inhalte. Die Wertanzeige bezieht sich auf empfangene Originalmesspunkte,
nicht auf interpolierte Kurvenwerte.

Administratoren finden im Detailverlauf die historischen Betriebszustände und
Schaltgründe. Die Erkennungskontrolle verknüpft gespeicherte Ereignisse mit ihren
Diagnosemarkern. Zum Messverlauf siehe [Daten- und Zeichenvertrag](livekurve.md).

Das Löschen einzelner Sitzungen oder des gesamten Archivs benötigt eine
Bestätigung und ist erst nach Sitzungsende möglich. Einstellungen und
Gerätezuordnungen bleiben dabei erhalten. Abgeschlossene Versuche ohne
bestätigten Gang werden automatisch verworfen. Export und Sicherung beschreibt
[Archiv, Backup und Export](speicherung.md).

## Darstellung und persönliche Startseite

Darstellungsänderungen sind zunächst eine Vorschau und gelten nach dem Speichern
für alle Benutzer dieser Sauna. Das Wiederherstellen der Standarddarstellung
ändert zunächst nur den Entwurf. Einzelheiten zu Skalen und Kontrast stehen in
der [Darstellungsreferenz](darstellung.md).

Die Wahl der Home-Assistant-Startseite gilt nur für das angemeldete Profil und
lässt sich dort wieder ändern.

## Technische Einstellungen und Rücksetzen

Panel-Einstellungen und Home-Assistant-Optionsdialog enthalten Dauerparameter.
Administratoren können diese und die Gerätezuordnungen nach Sitzungsende ändern.
Aktuelle Solltemperatur und Programmwahl gehören zur Steuerung; technische
Änderungen erhalten diese Werte. Protokollstufe und Darstellung bleiben während
einer Sitzung anpassbar.

**Standardwerte wiederherstellen** setzt nach Sitzungsende die
Betriebsparameter und Temperaturvorgaben zurück. Gerätezuordnungen,
Taster-/Schalterkonfiguration, Darstellung und Sitzungsarchiv bleiben erhalten.
Der genaue [Rücksetzumfang](parameter.md#standardwerte-wiederherstellen) ist
zentral beschrieben.
