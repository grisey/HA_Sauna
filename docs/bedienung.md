# Bedienungsanleitung

HA Sauna begleitet eine **Saunasitzung**, die mehrere **Saunagänge** enthalten
kann. Ein erkannter Gang ist zunächst vorläufig; ein Aufguss bestätigt ihn.
**Steuerung** enthält die Betriebs-, Temperatur- und Lichtwahl. **Verlauf**
zeigt Messungen und Ereignisse einer Sitzung. Die technischen Zustände sind
für Administratoren unter **Details** sichtbar.

## Betriebsstart und Temperaturwahl

**Einschalten** auf **Steuerung** startet den Saunabetrieb. Die Anzeige nennt
die aktuelle Phase und zeigt die Messwerte. **Ofen an**, **Ofen aus** oder
**Ofen unbekannt** bezeichnet die tatsächliche Rückmeldung. Eine Meldung
erläutert die Voraussetzungen für den Start, falls noch Angaben oder gültige
Messwerte erforderlich sind.

Eine Temperaturtaste oder die direkte Wahl am Anzeigebogen legt eine
**konstante Solltemperatur** fest. Der Anzeigebogen unterstützt Klicks,
Ziehbewegungen und Pfeiltasten in ganzen Celsiusgraden. Die gewählte Temperatur
gilt sofort und bleibt auch nach weiteren Saunagängen erhalten.

Unter **Temperaturwahl** stehen **Programm** und **Individuell** für eine
Temperaturfolge zur Verfügung. Eigene Programme haben zwei Eingabeformen:

- **Gleichmäßig:** Start, Ende und Verteilung bestimmen die Stufen. Das
  Infosymbol bei **Verteilung** zeigt die daraus entstehenden Temperaturen.
- **Einzelne Stufen:** Die Stufenzahl und jede Temperatur sind einzeln einstellbar.

Nach jedem beendeten, durch Aufguss bestätigten Gang gilt die nächste Stufe.
Nach der letzten Stufe bleibt deren Temperatur für weitere Gänge erhalten.
Die Untergrenze beträgt standardmäßig 60 °C und ist einstellbar; die
Obergrenze beträgt 100 °C.

Ohne bestehende Sitzung werden benannte, konstante und individuelle Wahlen
direkt übernommen. Bei individuellen Programmen löst ein abgeschlossener,
gültiger Eingabevorgang die Übernahme aus; angefangene oder ungültige Eingaben
bleiben zur Bearbeitung stehen.
Während einer bestehenden Sitzung öffnet **Programm ändern** die Auswahl.
Eine Änderung bleibt bis zur Bestätigung mit **Programm übernehmen** als
Entwurf sichtbar. **Abbrechen** erhält die bisherige Wahl. Der Entwurf
bleibt während der laufenden Messwertaktualisierung geöffnet.

Während der Übertragung erscheint **Wird übernommen …**, anschließend
**✓ Übernommen**. Die bestätigte Auswahl steht als Programmname,
**Konstant** oder **Individuell** neben **Programm ändern**. Laufende Gänge
und ihre Zeiten bleiben bei einer Temperaturänderung erhalten.

## Phasen und Zeitangaben

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
ab der bestätigten AUS-Rückmeldung des Heizschützes. Administratoren können
die laufende Phase auf **Steuerung** mit **Ofenkühlung jetzt beenden** vorzeitig
abschließen. Die Grundlage für die nächste Dauerberechnung bleibt dann die
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

**Endgültig beenden** schließt die Sitzung ab und schaltet das Licht sofort
aus. Auch mit Ablauf der Wiederaufnahmezeit endet die Sitzung und das Licht
geht aus. Beim nächsten Einschalten beginnt eine neue Sitzung.

## Licht

Im Automatikbetrieb stehen **Aus**, **Automatik** und **Hell** zur Verfügung.
**Automatik** folgt der Temperatur und der aktuellen Phase. **Hell** verwendet
die dafür eingestellte Helligkeit, standardmäßig 50 %. Administratoren können
zusätzlich **Gedimmt** und eine freie Helligkeit wählen. **Gedimmt** verwendet
die zur Tageszeit passende Normalhelligkeit.

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

In **Manuell** schalten **EIN** und **AUS** den Ofen. Für das Licht stehen
**Aus**, **Gedimmt** und **Hell** bereit. Diese Betriebsart bleibt bis
zum nächsten Betriebsartwechsel gewählt. Ofen- und Lichtwahl haben hier keine
Übersteuerungsfrist. Gültige Regeltemperatur und
freigegebene technische Überwachung sind Voraussetzungen für das Heizen.
Messung und Archivierung laufen weiter. Nach Sitzungsende steht mit
**Automatik** wieder die Temperaturregelung zur Verfügung.

Für einen zeitweiligen Eingriff während des laufenden Automatikbetriebs
verwenden Administratoren den zunächst eingeklappten Bereich **Manuelle
Übersteuerung** auf **Steuerung**. Sein Offen-Zustand bleibt bei
Statusaktualisierungen erhalten. Ohne eingeschalteten Betrieb sind diese
Bedienelemente gesperrt. **Automatik** gibt die Ofenregelung wieder frei. Ein
passender Phasen- oder Schaltwechsel oder der
Ablauf der eingestellten Dauer führt ebenfalls zurück zur aktuellen Automatik.
Standardwert und feste Obergrenze dieser Dauer betragen zehn Minuten.
Schutzabschaltungen und Ofenkühlung haben Vorrang. Die markierte Taste zeigt die gewählte
Steuerungsart; die Ofenrückmeldung zeigt den tatsächlichen Zustand.

## Programme und Tastervorgabe

**Einstellungen → Programme** enthält den Katalog benannter Programme.
Dafür genügen normale Home-Assistant-Bedienrechte. Die Bearbeitung ist nach
Ende einer Sitzung verfügbar.

**Bearbeiten** öffnet das gewählte Programm. **Fertig** übernimmt es in den
Katalogentwurf; **Abbrechen** erhält den bisherigen Eintrag. Ziehen am Griff
oder Pfeiltasten bei fokussiertem Griff ändern die Reihenfolge.
**Programme speichern** übernimmt den gesamten Entwurf dauerhaft.
**Änderungen verwerfen** stellt den gespeicherten Katalog wieder her.

Unter **Einstellungen → Saunataster** steht ein benanntes Programm oder eine
eigene konstante Temperatur als Startvorgabe zur Wahl. Diese Vorgabe bleibt
gesondert von der aktuellen Temperaturwahl gespeichert.

## Verlauf und technische Details

**Verlauf** enthält die laufende, letzte oder eine frühere Sitzung zur Auswahl.
**Messhöhen vergleichen** blendet die Messpositionen ein. Die Zoomtasten, eine
Vergrößerungsgeste oder Strg/Cmd mit dem Mausrad verändern den sichtbaren
Ausschnitt. Normales Scrollen bewegt die Seite. In der schmalen Übersicht ist
das Zeitfenster verschiebbar; **Gesamt** zeigt die ganze Sitzung.

Administratoren finden unter **Details → Betrieb & Fristen** die technischen
Zustände und unter **Detailverlauf** die Messungen beider Positionen. Die
**Erkennungskontrolle** verbindet Ereigniszeilen mit den zugehörigen Markern.
Unter **Einstellungen** lädt **Archiv als ZIP herunterladen** das Sitzungsarchiv
herunter.

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

Ganz unten unter **Einstellungen** macht **Als Startseite festlegen** die
Saunaübersicht zur persönlichen Home-Assistant-Startseite. Diese Auswahl gilt
für das angemeldete Benutzerprofil. Eine andere Startseite ist im
Home-Assistant-Profil wählbar.

## Technische Einstellungen und Rücksetzen

Administratoren bearbeiten die **Grundeinstellungen** und Gerätezuordnungen
nach Ende einer Sitzung. Während einer Sitzung bleiben die aktuelle
Temperaturwahl, die Protokollstufe und die Darstellung anpassbar.
**Einstellungen speichern** übernimmt geänderte Grundeinstellungen.

**Standardwerte wiederherstellen** stellt nach Sitzungsende die zentrale
Standardkonfiguration wieder her. Dazu gehören die Betriebsparameter und die
Temperaturvorgaben. Gerätezuordnungen, die Taster-/Schalterkonfiguration und die
gespeicherte Darstellung bleiben erhalten.

[Umfang und Standardwerte](parameter.md#standardwerte-wiederherstellen)
