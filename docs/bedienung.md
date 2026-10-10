# Sitzung starten, ändern und beenden

## Starten und nach einer Pause fortsetzen

Erneutes Einschalten innerhalb der Wiederaufnahmefrist setzt die vorherige
Sitzung fort; der Lichtnachlauf endet dabei.

Die unter **Einstellungen → Programme und Start → Sitzung starten und beenden**
gewählte Tastergeste startet Automatik
mit der gespeicherten Tastervorgabe, auch aus dem manuellen Modus. Die Auswahl
richtet sich nach den Ereignismeldungen des Tasters. Einmaliges Drücken bleibt
der direkten Ofensteuerung vorbehalten. Die Tastervorgabe
gilt unabhängig von der Temperaturwahl im Panel.

Beim Gedrückthalten bestätigt helles Licht den Start bis zum Loslassen.
Dafür muss der Taster Halten und Loslassen melden.

## Temperatur während der Sitzung ändern

Eine direkte Sollwertänderung am Temperaturinstrument wirkt sofort und ersetzt das
Temperaturprogramm durch einen konstanten Sollwert. Programm- und Schnellwahlen
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
erforderlich. Ofen und Licht schalten dort ohne Sitzung. Eine automatische
Sitzung beginnt durch **Einschalten** im Automatikmodus oder die gewählte
Tastergeste.

In Automatik endet eine vorübergehende Ofenwahl bei Rückgabe, Phasenwechsel,
Änderung der automatischen EIN-/AUS-Anforderung oder Ablauf der Übersteuerungsfrist.
Ofen-AUS unterbricht auch Gangheizung und Mindestheizzeit. Ofen-EIN bleibt der
[Heizfreigabe und Ofenkühlung](betrieb.md#heizpriorität) untergeordnet.

Die Ein-/Aus-Tasten zeigen die Rückmeldung des Geräts. Eine bereits erreichte
Wahl erzeugt keine Übersteuerung und verlängert keine Frist. **Automatik**
beendet die jeweilige Übersteuerung auch bei unverändertem Gerätezustand.

Während einer Sitzung wechselt ein kurzer Tasterdruck die vorübergehende
Ofenwahl oder gibt an die Automatik zurück.

In Automatik endet eine manuelle Lichtwahl beim passenden Phasenwechsel oder spätestens
mit ihrer Übersteuerungsfrist. Die Rückkehr verlängert weder Kühlung noch
Lichtnachlauf. Eine spätere Raumlichtwahl startet keinen neuen Nachlauf.

## Dauerhaft manuell betreiben

Die Betriebsart **Manuell** ist mit normalen Bedienrechten nach vollständigem
Sitzungsende wählbar und bleibt bis zur erneuten Betriebsartwahl erhalten.
Ofen und Licht beginnen beim Wechsel und nach einem Home-Assistant-Neustart
mit AUS. Die Bedienwahlen gelten ohne Übersteuerungsfrist.

Ein kurzer Tasterdruck außerhalb einer Sitzung wechselt in den manuellen Modus
und schaltet den Ofen um. Auch manuelles EIN benötigt eine gültige Regeltemperatur
und Heizfreigabe; Schutz hat Vorrang. Zurückschalten auf
Automatik gibt Ofen und Licht wieder für die automatische Steuerung frei.

Manuelle Bedienung erzeugt keine Sitzung und keine Schaltaufzeichnung im
Sauna-Archiv. Messkurven im Vor- und Nachlauf automatischer Sitzungen bleiben
erhalten.

## Ausschalten oder sofort beenden

**Ausschalten** beendet den offenen Gang und startet die gemeinsame
Wiederaufnahme- und Lichtnachlauffrist. Erst nach dieser Frist ist die Sitzung
abgeschlossen. Bis dahin bleiben Betriebsartwechsel und technische
Konfiguration gesperrt.

Dieselbe Tastergeste beendet bei laufendem Betrieb die Sitzung sofort.
Beim Gedrückthalten bleibt das Licht bis zur Loslassmeldung AUS;
anschließend beginnt der Lichtnachlauf.

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
