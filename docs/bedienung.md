# Bedienungsanleitung

HA Sauna begleitet eine **Saunasitzung**, die mehrere **Saunagänge** enthalten
kann. Ein erkannter Gang ist zunächst vorläufig; ein Aufguss bestätigt ihn.
**Steuerung** enthält die Betriebs-, Temperatur- und Lichtwahl. **Verlauf**
zeigt Messungen und Ereignisse einer Sitzung. Die technischen Zustände sind
für Administratoren unter **Details** sichtbar.

## Navigation und Vollbild

Die Ansichtsauswahl bleibt innerhalb von Home Assistant unmittelbar sichtbar.
Das Symbol **Vollbild** zeigt ausschließlich das Saunapanel im nativen
Browser-Vollbild; die Home-Assistant-Seitenleiste liegt außerhalb dieser Ansicht.
Nur dort öffnet das dreistrichige Menüsymbol die Ansichtsauswahl. Eine Auswahl
schließt das Menü. **Vollbild verlassen** oder die Vollbild-Beenden-Funktion des
Browsers stellt die eingebettete Ansicht wieder her. Bietet der Browser diese
Vollbildfunktion nicht an, erscheint das Vollbildsymbol nicht.

## Betriebsstart und Temperaturwahl

**Einschalten** auf **Steuerung** startet den Saunabetrieb. Die Anzeige nennt
die aktuelle Phase und zeigt die Messwerte. **Ofen an**, **Ofen aus** oder
**Ofen unbekannt** bezeichnet die tatsächliche Rückmeldung. Eine Meldung
erläutert die Voraussetzungen für den Start, falls noch Angaben oder gültige
Messwerte erforderlich sind.

Eine Temperaturtaste legt eine **konstante Solltemperatur** fest. Während einer
Sitzung wird diese Auswahl erst mit **Programm übernehmen** wirksam. Die direkte
Wahl am Anzeigebogen gilt sofort. Der Anzeigebogen unterstützt Klicks,
Ziehbewegungen und Pfeiltasten in ganzen Celsiusgraden. Die gewählte Temperatur
gilt sofort und bleibt auch nach weiteren Saunagängen erhalten.

Unter **Temperaturwahl** stehen **Programm** und **Individuell** für eine
Temperaturfolge zur Verfügung. Eigene Programme haben zwei Eingabeformen:

- **Gleichmäßig:** Start, Ende und Verteilung bestimmen die Stufen. Das
  Infosymbol bei **Verteilung** zeigt die daraus entstehenden Temperaturen.
- **Einzelne Stufen:** Die Stufenzahl und jede Temperatur sind einzeln einstellbar.

Nach jedem beendeten, bestätigten Gang gilt die nächste Stufe. Die Bestätigung
folgt der gewählten [Präsenzquelle](gangmodell.md).
Nach der letzten Stufe bleibt deren Temperatur für weitere Gänge erhalten.
Die Untergrenze beträgt standardmäßig 60 °C und ist einstellbar; die
Obergrenze beträgt 100 °C.

Ohne bestehende Sitzung ist die Auswahl unter **Temperaturwahl** unmittelbar
sichtbar. Benannte, konstante und individuelle Wahlen werden direkt übernommen. Bei individuellen Programmen löst ein abgeschlossener,
gültiger Eingabevorgang die Übernahme aus; angefangene oder ungültige Eingaben
bleiben zur Bearbeitung stehen. Das gilt auch für den Wechsel zwischen
**Gleichmäßig** und **Einzelne Stufen**. Während der Übertragung bleiben die
Eingabefelder, die Eingabeart sowie die Wahl zwischen **Programm**,
**Individuell** und **Konstant** bedienbar. Der zuletzt abgeschlossene gültige
Stand folgt nach dem laufenden Speicherauftrag; eine danach angefangene Eingabe
bleibt im Entwurf. Ein Speicherfehler erhält den Entwurf und stoppt weitere
automatische Übernahmen. Beginnt zwischenzeitlich eine Sitzung, benötigen
wartende Änderungen die Bestätigung.
Während einer bestehenden Sitzung zeigt **Temperaturwahl** die bestätigte
Auswahl mit ihren Temperaturen. **Ändern** öffnet die Auswahl. Die Markierung
der Auswahlbuttons bezeichnet dort den Entwurf; die bestätigte Einstellung
bleibt bis zur Übernahme erhalten.
Eine Änderung bleibt bis zur Bestätigung mit **Programm übernehmen** als
Entwurf sichtbar. **Abbrechen** erhält die bisherige Wahl. Der Entwurf
bleibt während der laufenden Messwertaktualisierung geöffnet. Ein beim
Sitzungsende noch vorhandener Entwurf bleibt ausdrücklich übernehmbar oder
abbrechbar; das Sitzungsende übernimmt ihn nicht automatisch.

Während der Übertragung erscheint **Wird übernommen …**, anschließend
für zwei Sekunden **✓ Übernommen** innerhalb der vorhandenen Bedienfläche.
Die Rückmeldung verändert deren Größe und die Anordnung nicht.
Die bestätigte Auswahl steht als Programmname,
**Konstant** oder **Individuell** neben **Ändern**. Laufende Gänge
und ihre Zeiten bleiben bei einer Temperaturänderung erhalten.

## Phasen und Zeitangaben

Dauern erscheinen in ganzen Minuten ohne Sekundenanteil. Verstrichene Zeit wird
abgerundet, verbleibende Zeit aufgerundet. Unter einer Minute lautet die Angabe
**unter 1 Minute**, bei null **0 Minuten**. Fehlende Zeitwerte erscheinen als
**–**. Ereigniszeitstempel behalten ihre Sekundenangabe.

Beim Aufheizen steht neben **Heizen** die geschätzte Zeit bis zur
Bereitschaft. Die Schätzung nutzt einen geeigneten früheren Aufheizverlauf und
übernimmt den aktuellen Temperaturtrend schrittweise. Sie erscheint in
Fünf-Minuten-Stufen; unter fünf Minuten lautet die Angabe **noch unter
5 Minuten bis bereit**.

Sobald die Solltemperatur erreicht ist, erscheint **Bereit**. Der Ofen kann
weiterheizen, um seine Temperaturreserve aufzubauen. Ein aktiver Saunagang
erscheint als **Saunagang** mit seiner bisherigen Dauer.

Die **Ofenkühlung** fordert Ofen-AUS an. Die Zeitzeile zeigt
die verbleibende Kühlzeit oder den Stand der Vorbereitung. Die Kühlzeit zählt
ab der bestätigten AUS-Rückmeldung des Heizschützes. Benutzer mit Bedienrechten
können die laufende Phase auf **Steuerung** mit **Kühlung beenden** vorzeitig
abschließen. Während der Kühlung teilt sich diese Schaltfläche den Betriebsbutton
mit **Ausschalten**. Die Grundlage für die nächste Dauerberechnung bleibt dann die
letzte vollständig beendete Kühlung.

[Ofenkühlung](ofenkuehlung.md)

Die Timeranzeige unter **Details → Betrieb & Fristen** ist für Administratoren
sichtbar. Sie schätzt die Restlaufzeit des Ofentimers anhand der gezählten Zeit
bei eingeschaltetem Saunabetrieb und bestätigtem Schütz-EIN.

## Ausschalten und Fortsetzen

**Ausschalten** schaltet den Ofen aus und beginnt die Sitzungspause.
Das Licht folgt seinem Nachlauf. Innerhalb der **Wiederaufnahmezeit** setzt
**Fortsetzen** dieselbe Sitzung fort. Diese Frist beträgt standardmäßig
15 Minuten und bestimmt zugleich die Dauer des Lichtnachlaufs.
Während dieser Frist stehen **Endgültig beenden** und **Fortsetzen** als
geteilter Schalter an der Stelle des Betriebsbuttons.

**Endgültig beenden** schließt die Sitzung ab und schaltet das Licht sofort
aus. Auch mit Ablauf der Wiederaufnahmezeit endet die Sitzung und das Licht
geht aus. Beim nächsten Einschalten beginnt eine neue Sitzung.

## Licht

Im laufenden Automatikbetrieb stehen berechtigten Benutzern **Automatik**,
**Aus** und **Hell** zur Verfügung. Ohne eingeschalteten Betrieb sind diese
Lichttasten gesperrt. **Automatik** folgt der Temperatur und der aktuellen
Phase. **Hell** verwendet die dafür eingestellte Helligkeit, standardmäßig
50 %. Die Lichttasten sind auch für Administratoren unmittelbar sichtbar.
Sie können zusätzlich eine freie Helligkeit wählen.

Der Helligkeitsstatus steht bei Ofenzustand und Gangzahl. **Licht … % · gemeldet**
bezeichnet die verfügbare Geräterückmeldung. Ohne verwertbare Rückmeldung steht
die bestätigte **Lichtvorgabe** mit diesem Hinweis; fehlen beide Werte, bleibt der
Status unbekannt. Ein gemeldetes ausgeschaltetes Licht entspricht 0 %.

Im Automatikmodus öffnet **Freie Helligkeit einstellen** den Eingabeeditor.
**Übernehmen** setzt die Vorgabe in ganzen Prozent. Ein Eingabeentwurf verändert
den Status nicht. Im manuellen Betriebsmodus ist die freie Eingabe unmittelbar
sichtbar. Die verbleibende Übersteuerungsdauer steht unter **Details**.

Eine manuelle Lichtwahl gilt bis zum passenden Phasenwechsel, längstens für
die eingestellte Übersteuerungsdauer. Danach folgt das Licht wieder der
Automatik. Standardwert und feste Obergrenze betragen zehn Minuten.

Die automatische Lichtkurve steigt beim Aufheizen bis zur Normalhelligkeit.
Zu Beginn der Ofenkühlung verwendet sie standardmäßig 15 % und steigt zum
Ende wieder auf den temperaturabhängigen Wert. Beim Ausschalten folgt der
Lichtnachlauf bis zum Ende der Wiederaufnahmezeit. **Fortsetzen** gibt das
Licht wieder an die laufende Automatik zurück. Die einzelnen Helligkeiten
und Übergangszeiten stehen in der [Parameterreferenz](parameter.md#licht).

## Saunataster und Lichttaster

Ein kurzer Druck auf den Saunataster startet die ausgeschaltete Sauna im
Automatikbetrieb mit der hinterlegten Tastervorgabe. Die Startgeste wird dabei
einmal ausgeführt. Bloßes Drücken und ein langer Druck aus AUS starten nicht.
Im laufenden Betrieb der Betriebsart **Automatik** übersteuert
ein kurzer Druck den Ofen vorübergehend; der nächste kurze Druck gibt ihn wieder
an die Automatik zurück.
Die Ofenkühlung behält dabei Vorrang.
Bei eingeschaltetem Betrieb in **Manuell** wechselt ein kurzer Druck die
Ofenvorgabe zwischen EIN und AUS.

Ein langer Druck auf den Saunataster schließt die laufende Sitzung
ab. Nach Erkennen dieses Enddrucks bleibt das Licht während des Haltens aus.
Beim Loslassen beginnt der Lichtnachlauf mit der
eingestellten Helligkeit und der Dauer der Wiederaufnahmezeit.
Die mit dem langen Druck abgeschlossene Sitzung bleibt
beendet.

[Gestenzuordnung und Lichtabschluss](betrieb.md#bedienhandlungen-und-betriebsart)

Die vorhandenen Lichttaster bedienen das Licht unmittelbar. Ihre tatsächliche
Lichtwahl wird als manuelle Übersteuerung übernommen und folgt derselben
Rückkehrregel wie eine Wahl im Panel.

## Betriebsart Manuell und Übersteuerung

Nach Ende einer Sitzung ist auf **Steuerung** die Betriebsart **Automatik**
oder **Manuell** wählbar. Dafür genügen normale Home-Assistant-Bedienrechte.
Die Wiederaufnahmezeit gehört zur bestehenden Sitzung.

In **Manuell** schalten **Ein** und **Aus** den Ofen. Für das Licht stehen
**Aus**, **Gedimmt** und **Hell** bereit. Diese Betriebsart bleibt bis
zum nächsten Betriebsartwechsel gewählt. Ofen- und Lichtwahl haben hier keine
Übersteuerungsfrist. Gültige Regeltemperatur und
freigegebene technische Überwachung sind Voraussetzungen für das Heizen.
Messung und Archivierung laufen weiter. Nach Sitzungsende steht mit
**Automatik** wieder die Temperaturregelung zur Verfügung.

Für einen zeitweiligen Eingriff während des laufenden Automatikbetriebs
verwenden Administratoren den zunächst eingeklappten Bereich **Manuelle
Ofenübersteuerung** auf **Steuerung**. Sein Offen-Zustand bleibt bei
Statusaktualisierungen erhalten. Ohne eingeschalteten Betrieb sind diese
Bedienelemente gesperrt. **Automatik** gibt die Ofenregelung wieder frei. Ein
passender Phasen- oder Schaltwechsel oder der
Ablauf der eingestellten Dauer führt ebenfalls zurück zur aktuellen Automatik.
Standardwert und feste Obergrenze dieser Dauer betragen zehn Minuten.
Schutzabschaltungen und Ofenkühlung haben Vorrang. Die markierte Taste zeigt die gewählte
Steuerungsart; die Ofenrückmeldung zeigt den tatsächlichen Zustand.

## Programme und Tastervorgabe

**Einstellungen → Programme und Start → Programme** enthält den Katalog benannter Programme.
Dafür genügen normale Home-Assistant-Bedienrechte. Die Bearbeitung ist nach
Ende einer Sitzung verfügbar.

**Bearbeiten** öffnet das gewählte Programm. **Fertig** übernimmt es in den
Katalogentwurf; **Abbrechen** erhält den bisherigen Eintrag. Ziehen am Griff
oder Pfeiltasten bei fokussiertem Griff ändern die Reihenfolge.
**Programme speichern** übernimmt den gesamten Entwurf dauerhaft.
**Änderungen verwerfen** stellt den gespeicherten Katalog wieder her.

Unter **Einstellungen → Programme und Start → Saunataster** steht ein benanntes Programm oder eine
eigene konstante Temperatur als Startvorgabe zur Wahl. Diese Vorgabe bleibt
gesondert von der aktuellen Temperaturwahl gespeichert.

## Verlauf und technische Details

**Verlauf** enthält die laufende, letzte oder eine frühere Sitzung zur Auswahl.
Der Verlauf zeigt die führende Messposition. Die Zoomtasten, eine
Vergrößerungsgeste oder Strg/Cmd mit dem Mausrad verändern den sichtbaren
Ausschnitt. Normales Scrollen bewegt die Inhalte; die Menüs bleiben sichtbar.
In der schmalen Übersicht ist das Zeitfenster verschiebbar. Der Zoomfaktor
zwischen Minus und Plus setzt den Ausschnitt auf die ganze Sitzung zurück.
Wertanzeige und Ausschnittbedienung stehen gemeinsam unter der Kurve.

Administratoren finden unter **Details → Betrieb & Fristen** die technischen
Zustände und unter **Detailverlauf** den Vergleich beider Messpositionen. Die
**Erkennungskontrolle** verbindet Ereigniszeilen mit den zugehörigen Markern.
Unter **Einstellungen → Daten und Wartung** lädt **Archiv als ZIP herunterladen** das Sitzungsarchiv
herunter.

**Sitzungen verwalten** zeigt die gespeicherten Sitzungen mit einer eigenen
Löschfunktion. **Datenbank zurücksetzen** entfernt sämtliche Archivdaten der
Sauna. Beide Aktionen benötigen eine Bestätigung und sind nur nach Abschluss
der laufenden Sitzung verfügbar. Einstellungen und Gerätezuordnungen bleiben
erhalten. Abgeschlossene Versuche ohne bestätigten Gang werden automatisch
verworfen; beim Laden der Integration gilt dies auch für ältere Versuche.

[Archivinhalt](speicherung.md#archivexport)

## Darstellung und persönliche Startseite

Unter **Einstellungen → Darstellung** können Administratoren Farben und
Anzeigeskalen anpassen. Die Änderungen erscheinen sofort als Vorschau.
**Darstellung speichern** übernimmt den Entwurf; **Änderungen verwerfen**
stellt die gespeicherte Darstellung wieder her. **Standarddarstellung
wiederherstellen** setzt zunächst den Entwurf auf die Vorgaben.

Die Skalen bestimmen den sichtbaren Bereich der Messbögen. Die Zahlenwerte
bleiben auch außerhalb dieses Bereichs lesbar. Der Sollschieber verwendet den
gemeinsamen Bereich von Anzeigeskala und zulässigen Solltemperaturen.

[Darstellungsreferenz](darstellung.md)

Unter **Einstellungen → Persönlich** macht **Als Startseite festlegen** die
Saunaübersicht zur persönlichen Home-Assistant-Startseite. Diese Auswahl gilt
für das angemeldete Benutzerprofil. Eine andere Startseite ist im
Home-Assistant-Profil wählbar.

## Technische Einstellungen und Rücksetzen

Die Bereichsnavigation gliedert die Einstellungen in **Programme und Start**,
**Betrieb und Ofen**, **Sensoren und Erkennung**, **Licht**, **Darstellung**,
**Daten und Wartung** sowie **Persönlich**. Mobil übernimmt ein Abschnittswähler
die Navigation. Nicht administrative Profile sehen Programme und persönliche
Einstellungen. Sensorzuordnung und Erkennungsparameter stehen zusammen;
Expertenparameter sind nach Signalverarbeitung, Tür, Lüften, Präsenz und Aufguss
geordnet. Protokollierung, Archiv und das getrennte Rücksetzen der
Grundeinstellungen stehen unter **Daten und Wartung**.

Administratoren bearbeiten die **Grundeinstellungen** und Gerätezuordnungen
nach Ende einer Sitzung. Während einer Sitzung bleiben die aktuelle
Temperaturwahl, die Protokollstufe und die Darstellung anpassbar.
**Einstellungen speichern** übernimmt geänderte Grundeinstellungen.

**Standardwerte wiederherstellen** stellt nach Sitzungsende die zentrale
Standardkonfiguration wieder her. Dazu gehören die Betriebsparameter und die
Temperaturvorgaben. Gerätezuordnungen, die Taster-/Schalterkonfiguration und die
gespeicherte Darstellung bleiben erhalten.

[Umfang und Standardwerte](parameter.md#standardwerte-wiederherstellen)
