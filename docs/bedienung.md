# Bedienungsanleitung

> Maßgeblicher Stand vom 26.09.2026: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> und [Ofenkühlung](ofenkuehlung.md). Jeder aktive Gang fordert vorläufig wie
> bestätigt durchgehend Heizen an; Ofenkühlung und technische Sperren haben Vorrang.
> Die vorübergehende Türhilfe wirkt nur nach geeignetem Türschluss. Ofenkühlung
> ist dynamisch und wird durch Tür, Präsenz oder manuelles Heizen nicht unterbrochen.
> Entgegenstehende ältere Angaben unten zu Öffnungsfristen, Gangveto, Kühlpausen,
> Wiedereinstieg, Heizbudgets und Zwangskühlung sind ausschließlich historisch.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

HA Sauna begleitet eine **Saunasitzung** von der Temperaturwahl bis zum
Lichtnachlauf. Sie kann mehrere **Saunagänge** enthalten. Ein erkannter Gang
ist zunächst vorläufig; ein Aufguss bestätigt ihn. Oben neben der Sauna stehen **Übersicht**,
**Details** und **Einstellungen**. Die Übersicht ist für die tägliche Bedienung
mit den normalen Home-Assistant-Bedienrechten gedacht. Hier wählen Sie Betriebsart,
Temperatur und Licht. Administratoren finden weitergehende Steuerungen unter **Details**.

## Persönliche Startanzeige

Unter **Einstellungen** finden Sie ganz unten **Als Startseite festlegen**.
Damit wird die Saunaübersicht zur persönlichen Home-Assistant-Startseite des
angemeldeten Benutzers. Eine andere Startseite wählen Sie später im
Home-Assistant-Profil. Diese Einstellung gilt auch für normale Benutzer.

## Einschalten und Temperatur auswählen

Öffnen Sie **Übersicht → Steuerung** und wählen Sie **Einschalten**. Die Anzeige
zeigt Phase, Anzahl der Saunagänge, Temperatur und Luftfeuchte sowie den
Heizzustand. Eine Meldung auf der Übersicht erklärt, wenn ein Start noch nicht
möglich ist oder Einstellungen beziehungsweise Messwerte fehlen.

Wählen Sie die Solltemperatur direkt am Bogen der Temperaturanzeige: klicken,
ziehen oder bedienen Sie ihn mit der Tastatur. Beschriftete Skalenpunkte helfen
bei der Auswahl. Die Schnellwahltasten bieten weitere feste Werte; die Taste
der aktuell geltenden Solltemperatur ist hervorgehoben. Der Bereich beginnt standardmäßig bei 60 °C und
endet bei 100 °C; die Untergrenze und die Schnellwahl sind einstellbare
Standards.

Eine direkte Sollwahl ist eine **konstante** Wahl. Sie wirkt sofort und bleibt
auch nach weiteren Saunagängen bestehen. Die angezeigte Solltemperatur ist die
aktuell wirksame Wahl; die Temperaturregelung und Schutzfunktionen entscheiden
weiter über das tatsächliche Heizen.

## Konstante Wahl oder Temperaturautomatik

Unter **Temperaturwahl** wählen Sie **Programm**, **Individuell** oder
**Konstant**. **Programm** zeigt die benannten Programme als Auswahlzeilen mit
Name und Temperaturfolge. **Konstant** zeigt die festen Temperaturtasten.
Mit **Individuell** öffnen Sie die Eingaben für Ihr eigenes Programm:

- **Gleichmäßig:** Start, Ende und Verteilung eingeben. Das Infosymbol bei
  **Verteilung** erklärt die Stufenzahl mit einem Beispiel.
- **Einzelne Stufen:** Anzahl wählen und jede Temperatur einzeln eintragen.

**Übernehmen** wird bei einer geänderten Auswahl hervorgehoben und aktiviert.
Nach erfolgreichem Speichern steht dort **Übernommen**. Die Auswahlzeilen und
Eingaben bleiben bis zum Übernehmen ein Entwurf. Temperaturtasten und der
Anzeigebogen stellen die konstante Temperatur unmittelbar ein.

Die Verteilung legt fest, über wie viele Stufen die Temperatur von Start zu
Ende steigt. Jeder beendete, bestätigte Saunagang führt zur nächsten Stufe.
Nach der letzten Stufe bleibt deren Temperatur für weitere Saunagänge gültig. Während einer
Saunasitzung dürfen Sie Solltemperatur, Ende und Verteilung ändern; laufende
Gänge und Zeiten bleiben erhalten.

Die sechs mitgelieferten Programme sind ebenfalls einstellbare Vorlagen:

| Programm | Temperaturstufen |
| --- | --- |
| Genusszeit | 80 → 85 → 90 °C |
| Gipfelstürmer | 84 → 92 → 100 °C |
| Ewigkeit | 80 → 84 → 88 → 92 → 96 °C |
| Liegewiese | 75 → 80 → 85 °C |
| Höhenwanderung | 90 → 95 → 100 °C |
| Schnellstarter | 70 → 90 °C |

Unter **Einstellungen → Programme** können Nutzer mit normalen
Home-Assistant-Bedienrechten Vorlagen umbenennen, anpassen, ergänzen oder
entfernen. Auch dort können Temperaturen gleichmäßig verteilt oder einzeln
festgelegt werden. Unter **Einstellungen → Saunataster** wird die Startvorgabe gesetzt:
Sie verwendet ein benanntes Programm oder eine unabhängig gespeicherte konstante
Temperatur. Programme und Tasterwahl lassen sich nur zwischen abgeschlossenen
Sitzungen ändern.

## Während der Saunasitzung

Die Übersicht nennt die aktuelle Phase: **Heizen**, **Bereit**,
**Saunagang**, **Nachlauf** oder **Zwangskühlung**. **Bereit** erscheint, sobald
die Solltemperatur erreicht ist, und bleibt bis zum nächsten Saunagang oder
zur Zwangskühlung bestehen. Der Ofen kann dabei weiterheizen, um seine
Temperaturreserve aufzubauen. Die höhere **obere Regeltemperatur** sehen
Administratoren in den Details.

Neben dem Zustand steht eine kurze Zeitangabe: beim Aufheizen etwa
**Heizen – noch 10 Minuten bis bereit**, nach Erreichen des Ziels etwa
**Bereit – noch 20 Minuten**. Geschätzte Aufheizzeiten erscheinen ohne Sekunden:
unter fünf Minuten als **noch unter 5 Minuten bis bereit**, sonst auf der nächstliegenden
Fünf-Minuten-Stufe. Bei Bereit zeigt die Zeit, wie lange noch mindestens ein
Gang begonnen werden kann; Heizpausen können diesen Zeitraum verlängern. Fehlt
eine belastbare Schätzung, bleibt die Startzeit offen.

Zu Beginn hilft der durchschnittliche Temperaturanstieg der letzten Sitzung.
Der aktuelle Verlauf übernimmt die Schätzung schrittweise, sobald er stabil ist.
Kurze Schwankungen verlängern eine bereits verkürzte Zeit nicht wieder.
Ein anhaltend langsameres Aufheizen oder Wärmeverlust durch eine erkannte
Türöffnung kann eine längere Prognose erforderlich machen.

Bei Gang, Nachlauf, Kühlung oder einer Sperre zeigt die Zeile stattdessen die
passende Phase oder Wartezeit. In
**Details → Betrieb & Fristen** stehen zusätzlich Heizsumme, mechanischer
Ofentimer, Nachlauf, Zwangskühlung und vorübergehende Übersteuerungen zusammen.

Nach einem bestätigten Saunagang kann ein Nachlauf folgen. Eine Zwangskühlung
schaltet den Ofen für ihre einstellbare Dauer aus. Administratoren können einen
laufenden Nachlauf oder eine laufende Zwangskühlung in den Details mit
**… jetzt beenden** abschließen. Der reguläre Folgeablauf läuft dann weiter.

Der mechanische Ofentimer ist eine einstellbare Anzeige und Erinnerung. Er
zählt nur bei eingeschaltetem Saunabetrieb und bestätigtem Heizschütz; während
Heizpause, Nachlauf oder Zwangskühlung hält er an. Stellen Sie den tatsächlichen
Drehschalter weiterhin selbst ein.

Zum Ausschalten wählen Sie **Ausschalten**. Der Ofen geht aus und der
Lichtnachlauf beginnt sofort. Innerhalb der einstellbaren Wiederaufnahmezeit
(standardmäßig 15 Minuten) setzt **Fortsetzen** dieselbe Sitzung fort.
**Endgültig beenden** schaltet das Licht sofort aus und beendet die Sitzung;
beim nächsten Einschalten beginnt eine neue Sitzung. Mit Ablauf der
Wiederaufnahmezeit endet die Sitzung ebenfalls und das Licht geht aus. Für den
Lichtnachlauf gibt es keine zusätzliche Dauer.

## Licht

Im Automatikbetrieb stehen auf der Übersicht **Aus**, **Automatik** und **Hell** zur
Verfügung. **Automatik** folgt der Temperatur und der aktuellen Phase;
**Hell** verwendet die einstellbare Helligkeit für diese Stufe, standardmäßig
50 %. Die Prozentwerte sind nachgeordnet und lassen sich in den Einstellungen
ändern.

In **Details** kann ein Administrator zusätzlich **Gedimmt** wählen oder eine
freie Helligkeit setzen. **Gedimmt** verwendet die zur Tageszeit passende
Normalhelligkeit. Beide Helligkeitsstufen sind einstellbar.

Im Automatikbetrieb folgt das Licht einer Temperaturkurve. Standardmäßig beginnt
sie am einstellbaren Kaltpunkt von 30 °C bei 5 %, erreicht tagsüber 40 % und
nachts 25 %. Nachlauf und Zwangskühlung verwenden eigene einstellbare
Helligkeiten, standardmäßig 15 % beziehungsweise 5 %. Die automatischen
Übergänge dauern standardmäßig 30 Sekunden und verändern die Helligkeit in
Schritten von einem Prozentpunkt. Beim Ausschalten fährt das Licht auf
standardmäßig 50 % und bleibt bis zum Ende der Wiederaufnahmezeit an. Danach
geht es ohne weiteren Dimmübergang aus. Wiedereinschalten beendet den
Lichtnachlauf und gibt das Licht an die laufende Automatik zurück.

Eine manuelle Lichtwahl gilt bis zum nächsten Phasenwechsel oder höchstens bis
zum Ende der einstellbaren Übersteuerungsdauer von standardmäßig zehn Minuten.

## Saunataster und Lichttaster

Der Saunataster startet aus dem ausgeschalteten Zustand im Automatikbetrieb.
Er verwendet die für den Taster gewählte Vorgabe: ein festgelegtes
Temperaturprogramm oder eine unabhängig gespeicherte konstante Temperatur. Ein kurzer Druck während
der Saunasitzung schaltet den Ofen vorübergehend manuell; der nächste kurze
Druck übergibt ihn wieder an die Automatik. Halten Sie den Taster gedrückt, um
die Saunasitzung zu beenden. Das Licht zeigt den Abschluss mit einer stark
gedimmten, einstellbaren Helligkeit an, bis Sie loslassen; danach beginnt der
Lichtnachlauf. Auch dieser verwendet die eingestellte Wiederaufnahmezeit als
Dauer; die mit langem Drücken beendete Sitzung selbst bleibt abgeschlossen.

Während der Ofenkühlung bleibt der Ofen ausgeschaltet. Ein kurzer Tastendruck,
eine Türschließung oder ein Personensignal kann die Kühlung nicht unterbrechen.
In den Details kann die Ofenkühlung ausdrücklich beendet werden; die dabei
verkürzte Kühlzeit gilt nicht als vollständig abgeschlossene Kühlung für die
nächste Dauerberechnung. Die eigenständige Zwangskühlung ist entfernt.

Ein kurzer Lichttasterdruck schaltet das Licht aus. Gehaltenes Drücken dimmt je
nach Taster heller oder dunkler. Diese tatsächliche Lichtwahl wird als manuelle
Übersteuerung übernommen und folgt anschließend wieder den Regeln aus dem
vorigen Abschnitt.

## Betriebsart Manuell und zeitbegrenzte Übersteuerung

Die Betriebsarten **Automatik** und **Manuell** wählen Sie zwischen abgeschlossenen
Saunasitzungen auf der Übersicht. Dafür genügen die normalen Home-Assistant-Bedienrechte.
In **Manuell** schalten Sie den Ofen mit **EIN** und **AUS** und wählen für das
Licht **Aus**, **Gedimmt** oder **Hell**. Zur Temperaturwahl wechseln Sie zurück
zu **Automatik**. Messung, Archivierung und technische Schutzabschaltungen bleiben
aktiv; die Temperaturautomatik und automatische Lichtwechsel sind ausgesetzt.
Manuell endet nicht nach einer festen Zeit. Technische Schutzsperren und
fehlende gültige Regeltemperatur verhindern auch manuelles Heizen.

Davon getrennt ist die **vorübergehende Übersteuerung** während der
Betriebsart Automatik. Administratoren finden sie unter **Details → Betrieb &
Fristen** in den Kacheln **Ofen** und **Licht**: Ofen **EIN**, **AUS** oder **Automatik**;
Licht Aus, Automatik, Gedimmt, Hell oder freie Helligkeit. Diese Eingriffe
übernimmt die Automatik beim nächsten passenden Wechsel oder spätestens nach
der einstellbaren Höchstdauer, standardmäßig zehn Minuten. Schutzabschaltungen
behalten Vorrang.

Die markierte Taste zeigt die gewählte Steuerung. Bei **Automatik** kann der
Ofen deshalb tatsächlich ein- oder ausgeschaltet sein; seinen aktuellen
Zustand lesen Sie getrennt als **Ofen an** beziehungsweise **Ofen aus**.
Die Umgebungskachel unter Temperatur und Luftfeuchte zeigt im Automatikbetrieb
Tür- und Ofenstatus. Im manuellen Betrieb erscheint kein Türhinweis und keine
zweite Ofenstatuskachel.

## Verlauf und Archiv

Öffnen Sie **Übersicht → Verlauf und Archiv**, um die aktuelle oder eine frühere
Saunasitzung auszuwählen. Die Auswahl bezeichnet eindeutig die **laufende**
oder **letzte Sitzung**. Der Standardverlauf zeigt Temperatur und Luftfeuchte,
Phasen, Türöffnungen, Saunagänge und Aufgüsse. Über **Messhöhen vergleichen**
blenden Sie obere und untere Messposition ein oder aus.

Vergrößern Sie mit den Zoomtasten, einer Vergrößerungsgeste oder Strg/Cmd und
Mausrad. Normales Scrollen bewegt die Seite. Die schmale Übersicht am Verlauf
zeigt den Ausschnitt innerhalb der gesamten Sitzung und lässt ihn verschieben.

Administratoren können unter **Details → Detailverlauf** beide Messhöhen mit
vollständigen Angaben betrachten. Die **Erkennungskontrolle** ordnet Marker und
Ereignisliste einander zu. Unter **Einstellungen** lädt der Button
**Archiv als ZIP herunterladen** Messwerte, Sitzungsverläufe und
Ereigniszuordnungen herunter.

## Details und technische Einstellungen

**Details** sowie technische Bereiche der **Einstellungen** sind
Administratoren vorbehalten. Dort liegen Grundeinstellungen und
Gerätezuordnungen, Betrieb und Fristen, Erkennung, freie Lichtwerte,
Übersteuerungen im Automatikbetrieb, Protokollierung und Archivexport.
Änderungen an Grundeinstellungen und Gerätezuordnungen sind nach Ende einer
Saunasitzung verfügbar. Während einer Sitzung bleiben Solltemperatur,
Endtemperatur und Verteilung der Temperaturautomatik änderbar.

**Standardwerte wiederherstellen** setzt die einstellbaren Werte,
Temperaturprogramme und Protokollstufe auf zentrale Standards zurück. Es lässt
die Zuordnung von Sensoren, Geräten, Tastern und Schaltern erhalten und ist
nicht während einer Saunasitzung verfügbar.

Die Protokollstufe wählen Sie als **ERROR**, **INFO** (Standard) oder **DEBUG**.
Sie wirkt sofort; das Sitzungsarchiv bleibt davon unabhängig.

Für Hintergründe zur Anlage und zu einzelnen Einstellungen siehe
[Betrieb](betrieb.md) und [Parameter](parameter.md).
