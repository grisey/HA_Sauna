# Sitzung starten, ändern und beenden

## Starten und nach einer Pause fortsetzen

Erneutes Einschalten vor Ablauf der Wiederaufnahmefrist setzt dieselbe Sitzung
fort. Das gilt in Automatik, in Manuell und bei einem Wechsel dazwischen.
Beginn, Messungen, Heizverlauf, Energie und erkannte Gänge bleiben erhalten;
ein automatischer Lichtnachlauf endet beim Einschalten. Ab dem Fristende beginnt
eine neue Sitzung.

Die unter **Einstellungen → Programme und Start → Sitzung starten und beenden**
gewählte Tastergeste startet Automatik
mit der gespeicherten Tastervorgabe, auch aus dem manuellen Modus. Die Auswahl
richtet sich nach den Ereignismeldungen des Tasters. Vom Gerät erkannte Gesten
werden unmittelbar übernommen; die Integration misst deren Haltezeit nicht erneut.
Nur ein Binärtaster, der lediglich Drücken und Loslassen meldet, verwendet die
eingestellte Haltezeit. Einmaliges Drücken bleibt der direkten Ofensteuerung
vorbehalten. Nicht als Sitzungsgeste gewählte Mehrfachgesten lösen keine
zusätzlichen Ofenhandlungen aus. Die Tastervorgabe
gilt unabhängig von der Temperaturwahl im Panel. Bei **Konstante Temperatur**
verwendet sie die Standardtemperatur aus der Integrationskonfiguration;
gespeicherte Programme verwenden ihre eigenen Temperaturen.
Läuft vorher bereits manuelles Vorheizen, setzt die Sitzungsgeste diese
Aufzeichnung ohne Unterbrechung im gewählten Automatikprogramm fort.

Bei gewählter Haltegeste und vorhandenen Drück- und Loslassmeldungen geht das
Licht beim Drücken zunächst aus. Sobald der Langdruck erkannt ist, bestätigt
die eingestellte Einschalthelligkeit den Start
bis zum Loslassen. Ein kurzer Druck stellt die vorherige Helligkeit wieder her
und schaltet den Ofen. Meldet das Gerät nur die erkannte Langdruckgeste, startet
oder beendet diese die Sitzung ohne eine Rückmeldung während des Haltens.

## Temperatur während der Sitzung ändern

Beim Wechsel zu **Konstant** oder **Manuell** gilt die einstellbare
Standardtemperatur aus der Integrationskonfiguration. Danach bleibt eine
direkt gewählte Solltemperatur erhalten. Gespeicherte Programme behalten
ihre eigenen Temperaturen.

Bei **Konstant** wirkt eine Sollwertänderung am Temperaturinstrument sofort.
Während eines Temperaturprogramms ändert das Instrument nur die Temperatur für
den nächsten Gang. Ein laufender Gang behält seinen Sollwert. Nach dem
angepassten Gang gelten wieder die ursprünglichen Programmstufen. Programm- und Schnellwahlen
benötigen während der Sitzung **Programm übernehmen**; außerhalb gelten sie
sofort. Gangzählung und laufende Zeiten bleiben erhalten.

Programme können steigende oder fallende Temperaturstufen verwenden, auch
gleichmäßig aus Start, Ende und Anzahl verteilt. Jeder beendete bestätigte
Gang schaltet einmal weiter; nach der letzten Stufe bleibt deren Temperatur.

Eine alleinige Änderung der Endtemperatur erhält das aktuelle Ziel und verteilt
die verbleibenden Schritte neu. Ein neuer Startwert beginnt eine neue Verteilung.
Die Instrumentenskala begrenzt den direkt wählbaren Sollbereich, verändert aber
weder Regelparameter noch Verlaufsachsen.

## Ofen und Licht vorübergehend selbst steuern

Ofen- und Lichtwahl einschließlich freier Helligkeit stehen allen Benutzern
mit Steuerrechten gleichermaßen zur Verfügung. Außerhalb einer Sitzung ist
in Automatik zunächst der ausdrückliche Wechsel über **Manuell steuern**
erforderlich. Eine automatische
Sitzung beginnt durch **Einschalten** im Automatikmodus oder die gewählte
Tastergeste.

In Automatik endet eine vorübergehende Ofenwahl bei Rückgabe, Phasenwechsel,
Änderung der automatischen EIN-/AUS-Anforderung oder Ablauf der Übersteuerungsfrist.
Ofen-AUS unterbricht auch Gangheizung und Mindestheizzeit. Ofen-EIN bleibt der
[Heizfreigabe und Ofenkühlung](betrieb.md#heizpriorität) untergeordnet.

Die Ein-/Aus-Tasten zeigen die Rückmeldung des Geräts. In Automatik erzeugt eine
bereits erreichte Wahl keine Übersteuerung und verlängert keine Frist. **Automatik**
beendet die jeweilige Übersteuerung auch bei unverändertem Gerätezustand.

Während einer Sitzung wechselt ein kurzer Tasterdruck die vorübergehende
Ofenwahl oder gibt an die Automatik zurück. Solltemperatur und Temperaturprogramm
bleiben erhalten.

In Automatik endet eine manuelle Lichtwahl beim passenden Phasenwechsel oder spätestens
mit ihrer Übersteuerungsfrist. Die Rückkehr verlängert weder Kühlung noch
Lichtnachlauf. Eine spätere Raumlichtwahl startet keinen neuen Nachlauf.

## Dauerhaft manuell betreiben

Die Betriebsart **Manuell** ist mit normalen Bedienrechten bei ausgeschaltetem
Automatikbetrieb wählbar und bleibt bis zur erneuten Betriebsartwahl erhalten.
Ofen und Licht beginnen beim Wechsel über das Panel und nach einem Home-Assistant-Neustart
mit AUS. Die Bedienwahlen gelten ohne Übersteuerungsfrist.

Ofen-EIN aktiviert die Temperaturregelung um den am Instrument eingestellten
Sollwert. Sie verwendet dieselben Hystereseabstände wie die Automatik, jedoch
ohne Mindestheizzeit oder Heizpause. Ofen-AUS beendet die Regelung sofort.
Gangheizung, Türhilfe, Kühlung und Lichtautomatik bleiben deaktiviert.
Die umrandete Ofenwahl zeigt, ob die Regelung eingeschaltet ist; die Tastenfarbe
zeigt weiterhin die tatsächliche Ofenrückmeldung.

Ein kurzer Tasterdruck außerhalb einer automatischen Sitzung wechselt in den manuellen Modus
und schaltet die Temperaturregelung um; die vorherige Helligkeit bleibt erhalten.
Auch manuelles EIN benötigt eine gültige
Regeltemperatur und Heizfreigabe; Schutz hat Vorrang. Zurückschalten auf
Automatik gibt Ofen und Licht wieder für die automatische Steuerung frei.

Ofen-EIN beginnt eine Aufzeichnung oder setzt sie innerhalb der
Wiederaufnahmefrist fort. Ofen-AUS beendet die Heizfreigabe und den offenen Gang;
erst nach der Wiederaufnahmefrist ist die Aufzeichnung abgeschlossen.
Thermostatzyklen unterbrechen sie nicht. Tür, Anwesenheit, Gänge und Aufgüsse werden nach
denselben [Erkennungsregeln](gangmodell.md) erfasst, ohne Ofen oder Licht zu
steuern. Für die Speicherung gelten dieselben [Archivregeln](speicherung.md)
wie im Automatikbetrieb.

## Ausschalten und Lichtnachlauf beenden

**Ausschalten** beendet den offenen Gang und startet die Wiederaufnahmefrist.
Im Automatikbetrieb läuft währenddessen der Lichtnachlauf. Erst nach dieser
Frist ist die Sitzung abgeschlossen. Ein Betriebsartwechsel bei ausgeschaltetem
Betrieb erhält die Aufzeichnung; technische Konfiguration bleibt bis zum
Abschluss gesperrt.

Dieselbe Tastergeste schaltet einen laufenden Automatikbetrieb sofort aus.
Bei gewählter Haltegeste und vorhandenen Drück- und Loslassmeldungen wird zuvor
ausgeschaltetes Licht beim Drücken mit der eingestellten Einschalthelligkeit
eingeschaltet. Sobald der Langdruck erkannt ist, bleibt es bis zur Loslassmeldung
AUS; anschließend beginnt der Lichtnachlauf. Bei Geräten ohne diese Kantenmeldungen
beginnt der Lichtnachlauf unmittelbar mit der erkannten Sitzungsgeste. In beiden
Fällen endet er spätestens mit der beim Ausschalten begonnenen
Wiederaufnahmefrist.

**Lichtnachlauf beenden** schaltet das Nachlauflicht sofort aus. Die
Wiederaufnahmefrist und die bis dahin zusammengehörige Aufzeichnung bleiben
erhalten.

Nach einem Home-Assistant-Neustart setzt sich der Ofenbetrieb nicht fort;
er benötigt ausdrückliches Einschalten. Die unterbrochene Sitzung bleibt im
[Archiv](speicherung.md#welche-sitzungen-erhalten-bleiben) historisch offen.

## Programme und Einstellungen speichern

| Änderung | Besonderheit |
| --- | --- |
| Programmbibliothek | **Fertig** ändert nur den Entwurf; erst **Programme speichern** übernimmt den gesamten Katalog. Nach Sitzungsende auch ohne Administratorrechte. |
| Darstellung | Auch die Rücksetzung ändert nur den Entwurf; Speichern wirkt für alle Benutzer der Sauna. |
| Anlagenwerte und Gerätezuordnungen in der Integrationskonfiguration | Administratorrechte und vollständiges Sitzungsende erforderlich. Die aktuelle Temperaturwahl bleibt erhalten. |

Die gemeinsame Instrumentenform kann für Temperatur, Luftfeuchte und Licht
einzeln überschrieben werden. Die konstanten Temperaturen der Schnellwahl werden
ebenfalls unter **Darstellung** eingestellt.

Offene oder nach Speicherfehlern verbliebene Entwürfe werden durch Sitzungsende
nicht übernommen. Protokollstufe und Darstellung bleiben während der Sitzung
änderbar. [Umfang der Rücksetzungen](parameter.md#werkseinstellungen)
