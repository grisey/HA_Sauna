# Saunabetrieb

## Sitzung

Betrieb-EIN beginnt eine Sitzung. Betrieb-AUS beendet den offenen Gang und
startet die gemeinsame Frist für Wiederaufnahme und Lichtnachlauf, standardmäßig
15 Minuten. Ein erneutes Einschalten innerhalb dieser Frist setzt dieselbe
Sitzung fort. Nach Ablauf der Frist beginnt das nächste Einschalten eine neue
Sitzung. Jeder Sitzungsstart beendet einen laufenden Lichtnachlauf.

Während der Sitzung lassen sich Solltemperatur und Temperaturprogramm anpassen.
Grundlegende Einstellungen und Gerätezuordnungen werden nach Sitzungsende
geändert. Die Wiederaufnahmefrist gehört zur bestehenden Sitzung. Bei einem
Neustart von Home Assistant bleibt der gespeicherte Verlauf erhalten; der
Ofenbetrieb beginnt wieder durch ausdrückliches Einschalten.

Genannte Standardwerte sind in den [Parametern](parameter.md) einstellbar.

[Gangmodell](gangmodell.md) · [Heizpriorität](praesenz-ofen-phasen.md) ·
[Zeitmodell](zeitmodell.md)

## Temperatur und Bereitschaft

Die Regelung verwendet den gültigen oberen Temperaturwert. Bei Ausfall der oberen
Messung übernimmt der gültige untere Wert. Sobald oben wieder gültige Werte
vorliegen, führt erneut die obere Messung. Eine vollständige Temperatur- und
Feuchteposition genügt für den vollen Betrieb.

Im freigegebenen Automatikbetrieb merkt sich die Steuerung das erste Erreichen
der Solltemperatur als Bereitschaft. Diese bleibt auch bei sinkender Temperatur
und geänderter Sollwahl bestehen. Der erste Aufguss eines neuen Gangs beendet
diese Bereitschaft; nach Gang und Ofenkühlung kann sie erneut entstehen. Eine
vorläufige Personenerkennung bewahrt die bereits erreichte Bereitschaft.

Die automatische Thermostatregelung heizt bis zur Solltemperatur zuzüglich
Temperaturaufschlag für die Heizungsabschaltung, standardmäßig 1 °C. Die
Wiedereinschaltschwelle liegt um den eingestellten Temperaturabstand unter der
Solltemperatur, standardmäßig ebenfalls 1 °C. Bei 80 °C Soll schaltet die
Regelung damit bei 81 °C ab und fordert bei 79 °C oder darunter wieder Heizen an.
Nach einer regulären Temperaturabschaltung gilt zunächst eine Heizpause von
5 Minuten. Innerhalb des Hysteresebands bleibt der bisherige Zustand erhalten.

Ein tatsächlich bestätigter Heizbeginn startet die Mindestheizzeit von
standardmäßig 10 Minuten. Ein bereits laufendes Heizintervall behält seinen
ursprünglichen Beginn. Die Heizpriorität ordnet diese Mindestzeit in die
[Gangheizung und Ofenkühlung](praesenz-ofen-phasen.md) ein. Ein aktiver Gang
fordert im Automatikbetrieb ab seiner vorläufigen Erkennung durchgehend Heizen
an, solange die übergeordneten Freigaben gelten.

Beim Aufheizen steht neben der Phase eine Prognose aus einem geeigneten
früheren Aufheizverlauf und dem zunehmend belastbaren aktuellen Temperaturtrend.
Sie erscheint in Fünf-Minuten-Stufen.

[Aufheizprognose](darstellung.md#zustand-und-zeit)

## Temperaturprogramm

Direkte Sollwahl hält eine konstante Zieltemperatur. Ein Temperaturprogramm
legt eine Folge von Stufen fest. Es kann als benanntes Programm ausgewählt oder
individuell zusammengestellt werden. Einzelne Stufen erlauben steigende und
fallende Folgen; eine gleichmäßige Verteilung berechnet die Stufen aus Start,
Ende und Stufenzahl. Deren Standardvorgabe lautet 80 → 85 → 90 → 95 °C.
Jeder beendete, bestätigte Gang führt zur nächsten Stufe. Nach der letzten
Stufe gilt deren Temperatur auch für weitere Gänge.

Bei einer gleichmäßigen Verteilung erhält eine Änderung ausschließlich der
Endtemperatur das nächste Ziel; die verbleibenden Steigerungen verteilen sich
bis zum neuen Endwert. Eine neue Starttemperatur beginnt eine neue Verteilung.
Gangzählung und laufende Zeitabläufe bleiben erhalten.

[Programme auswählen und bearbeiten](bedienung.md#betriebsstart-und-temperaturwahl)

## Heizzeit, Timer und Energie

Die Heizzeiterfassung verwendet vorrangig eine gültige Leistungsmessung. Als
weitere Quellen dienen eine eingerichtete Heizrückmeldung und schließlich die
native Schalterrückmeldung des Ofens. Die bestätigte Schalterstellung des Shelly
genügt als Rückmeldung. Die Anzeige kennzeichnet die verwendete Quelle und eine
daraus abgeleitete Schätzung. Eine durchgehend bestätigte Auszeit von
standardmäßig 10 Minuten setzt die lokale Heizsumme zurück.

Die Timeranzeige schätzt die verbleibende Laufzeit des mechanischen Ofentimers.
Sie zählt standardmäßig von 240 Minuten herunter, solange Betrieb und bestätigte
Schützstellung EIN sind. Bei Schütz-AUS oder unbekannter Rückmeldung bleibt der
zuletzt berechnete Rest erhalten. Nach jeder abgeschlossenen Sitzung beginnt
die Anzeige beim nächsten Sitzungsstart mit der vollen Dauer, unabhängig von
der erkannten Gangzahl. Fortsetzen innerhalb derselben Sitzung erhält die
Restzeit. Die Anzeige stellt den mechanischen Timer am Gerät nicht zurück.
Die Timerberechnung dient ausschließlich der Anzeige.

Bei alleiniger Schützrückmeldung zeigt das Panel **Heizfreigabe EIN/AUS**.
Die tatsächliche Heizleistung hinter einem internen Ofenthermostat ist daraus
nicht bekannt. Eine gültige Leistungsmessung oder unabhängige Heizrückmeldung
ermöglicht die Anzeige **Ofen an/aus**.

Die Verbrauchsschätzung ergibt sich aus gezählter Heizzeit und eingestellter
Ofenleistung, standardmäßig 4,5 kW. Eine gültige Leistungsmessung übernimmt die
Berechnung für ihren jeweiligen Zeitraum. Die Anzeige unterscheidet gemessene,
geschätzte und unbekannte Anteile. Der Sitzungsverbrauch bleibt über Kühlphasen
und lokale Heizzeitrücksetzungen hinweg erhalten.

## Ofenkühlung

Zur Ofenkühlung fordert die Steuerung Ofen-AUS an. Mit der bestätigten
Schützstellung AUS berechnet die Steuerung die Dauer und beginnt, die bestätigte
AUS-Laufzeit zu zählen. Die Standarddauer liegt zwischen 5 und 15 Minuten.

[Auslösung, Dauer und vorzeitiges Ende der Ofenkühlung](ofenkuehlung.md)

## Licht

Die automatische Helligkeit folgt dem Betriebsabschnitt und der Temperatur.
Die Lichtkurve steigt linear von 5 % bei 30 °C bis zur Normalhelligkeit an der aktuellen
Solltemperatur. Die Normalhelligkeit beträgt tagsüber 40 % und nachts
25 %; während der bürgerlichen Dämmerung geht sie gleitend zwischen beiden
Werten über. Aufheizen und Bereitschaft verwenden dieselbe stetige Lichtkurve;
die Bereitschaftshysterese verändert das Lichtziel nicht. Während eines
Saunagangs bleibt die Normalhelligkeit erhalten.
Die Ausgabe erfolgt in ganzen Prozentwerten. Automatische Stellwerte wechseln
erst, wenn die Kurve die halbe Prozentgrenze um zusätzlich 0,1 Prozentpunkte
überschreitet. Dieses einstellbare Hystereseband beruhigt kleine Schwankungen
an Rundungsgrenzen. Manuelle Wahlen und fälliges AUS gelten unmittelbar.
Die internen Kurven und gemessenen Helligkeitswerte behalten ihre Genauigkeit.

Zu Beginn der Ofenkühlung geht das Licht auf 15 % zurück. Anschließend steigt es
über die verbleibende Kühlzeit linear zur temperaturabhängigen Helligkeit an.
Automatische Übergänge dauern standardmäßig 30 Sekunden; die Kühlphase begrenzt
den ersten Übergang auf höchstens die Hälfte ihrer verbleibenden Dauer.
Das Ganzzahlraster begrenzt die Änderung nicht auf einen Prozentpunkt pro Sekunde;
die eingestellten Übergangszeiten und Phasenfristen bleiben maßgeblich.

Betrieb-AUS startet den Lichtnachlauf bei 50 %. Er endet mit der gemeinsamen
Wiederaufnahmefrist durch Ausschalten des Lichts. Beim langen Enddruck einer
neuen Saunatastergeste bleibt das Licht während des Haltens aus; bestätigtes
Loslassen beginnt den Lichtnachlauf mit derselben eingestellten Dauer.

Eine manuelle Lichtwahl in Automatik gilt bis zum nächsten passenden Phasenwechsel
oder längstens für die eingestellte Übersteuerungsdauer. Deren Standardwert und
feste Obergrenze betragen 10 Minuten. Ausschalten und Dimmen am Lichttaster zählen
als manuelle Wahl.
Rückmeldungen eigener Lichtbefehle ordnet die Integration dem automatischen
Verlauf zu. Bei der Rückkehr zur Automatik beginnt der Übergang an der zuletzt
beobachteten Helligkeit und blendet zum aktuellen automatischen Verlauf über.
Eine laufende Kühl- oder Lichtnachlauffrist begrenzt diesen Übergang; sie beginnt
dadurch nicht neu. Das fällige Ausschalten beendet auch einen laufenden Übergang.

Enden Lichtnachlauf und manuelle Lichtwahl gleichzeitig, bleibt die automatische
Endphase AUS. Eine anschließend ausdrücklich gesetzte Raumlichtwahl gilt als
neue manuelle Bedienung. Bei ihrer Rückkehr zur Automatik bleibt die Grundlage
der beendeten Lichtphase 0 %.

## Bedienhandlungen und Betriebsart

Ein kurzer Tastendruck bei Betrieb-AUS startet den Automatikbetrieb mit der
gespeicherten Tastervorgabe: einem benannten Programm oder einer eigenen
konstanten Temperatur. Bei nativen Ereignisquellen startet ausschließlich die
Kurzklassifikation, auch ohne vorherige Druckmeldung. Drücken und Loslassen
allein starten den Betrieb nicht. Beim Binärtaster bestätigt das Loslassen
vor Erreichen der eingestellten Langdruckdauer den kurzen Druck.

Ein langer Druck aus Betrieb-AUS startet die Sauna nicht. Zugehöriges Loslassen
und nachlaufende Meldungen derselben Langgeste erzeugen keinen Kurzstart.
Die nächste eigenständige Kurzbetätigung kann regulär starten. Die Kurzstartgeste
wird einmal ausgeführt; zugehörige Folgemeldungen erzeugen keine zusätzliche
Ofenübersteuerung. Bei eingeschaltetem Automatikbetrieb
schaltet ein kurzer Druck die vorübergehende Ofenwahl um beziehungsweise gibt
an die Automatik zurück. Bei eingeschaltetem Betrieb in Manuell wechselt ein
kurzer Druck die Ofenvorgabe zwischen EIN und AUS.

Ein langer Druck bei laufendem Betrieb beendet den Betrieb und schließt die
Sitzung ab. Das Licht bleibt während des Haltens AUS. Erst das bestätigte
Loslassen startet den Lichtnachlauf. Das gilt auch für eine eigenständige
Langklassifikation im laufenden Betrieb; sie liefert den Langdrucknachweis.

[Tastermeldungen und einmalige Verarbeitung](schnittstellen.md#tasterereignisse)

Die vorübergehende Ofenwahl in Automatik gilt bis zur Rückgabe, einem Wechsel
der Phase oder der automatischen EIN-/AUS-Anforderung, längstens für die
eingestellte Übersteuerungsdauer. Deren Standardwert und feste Obergrenze
betragen 10 Minuten. Beim Fristende gilt wieder die aktuelle Automatik.
Ausdrücklich gewähltes Ofen-AUS wirkt auch während eines aktiven Gangs.
Ofen-EIN setzt die gültige Heizfreigabe einschließlich einer gültigen
Regeltemperatur voraus. Schutz, Betrieb-AUS und Ofenkühlung haben Vorrang.

Bei der Rückgabe eines manuell eingeschalteten Ofens an die Automatik bleibt
eine noch laufende Mindestheizzeit mit ihrem ursprünglichen tatsächlichen
Beginn erhalten. Ausdrücklich gewähltes Ofen-AUS beendet diese Anforderung.

[Bedienrechte und Übersteuerung](bedienung.md#betriebsart-manuell-und-übersteuerung)

Die Betriebsart Manuell wird nach Sitzungsende gewählt und bleibt bis zur
nächsten ausdrücklichen Betriebsartwahl bestehen. Beim Wechsel zu Manuell
beginnen Ofen und Licht mit AUS; vorherige Übersteuerungen werden nicht
übernommen. Das gilt auch beim Neustart mit gespeicherter Betriebsart Manuell.
Die Rückkehr zur Automatik gibt beide manuellen Wahlen frei.
Ofen- und Lichtwahl sind dort
direkt und unbefristet; die Übersteuerungsfrist der Automatik gilt hier nicht.
Messung und Archivierung begleiten den Betrieb;
technische Schutzgründe und Betrieb-AUS behalten Vorrang.

## Rückmeldungen und Schutz

Gültige Temperaturmessungen und bestätigte Geräterückmeldungen bilden die
Grundlage der Heizfreigabe. Messwerte und Befehlsrückmeldungen haben eigene
Gültigkeits- beziehungsweise Bestätigungsfristen. Eine ausgelöste
Schutzabschaltung verriegelt die Heizfreigabe.

[Überwachungsfristen](parameter.md#überwachung)

Während einer Lücke der gültigen Regeltemperatur bleibt die Heizung aus.
Fehler einzelner konfigurierter Quellen bleiben sichtbar; gültige Ersatzquellen
führen ihre vorgesehenen Aufgaben weiter. Die Quittierung technischer
Schutzgründe setzt Betrieb-AUS und bestätigtes Ofen-AUS voraus.
