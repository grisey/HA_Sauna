# Saunabetrieb

Eine Sitzung verbindet den Saunabetrieb mit seinem Verlauf. Diese Seite erklärt
Bedienung und Regelung im Zusammenhang. Die Zuordnung von Aufgüssen erläutert
das [Gangmodell](gangmodell.md); die [Heizpriorität](praesenz-ofen-phasen.md)
und das [Zeitmodell](zeitmodell.md) beschreiben die zugehörigen Regeln.
Genannte Standardwerte sind in den [Parametern](parameter.md) einstellbar.

## Sitzung

Betrieb-EIN beginnt eine Sitzung. Betrieb-AUS beendet den offenen Gang und
startet die gemeinsame Frist für Wiederaufnahme und Lichtnachlauf, standardmäßig
15 Minuten. Ein erneutes Einschalten innerhalb dieser Frist setzt dieselbe
Sitzung fort. Nach Ablauf der Frist beginnt das nächste Einschalten eine neue
Sitzung. Jeder Sitzungsstart beendet einen laufenden Lichtnachlauf.

Während der Sitzung lassen sich Solltemperatur und Temperaturprogramm anpassen.
Grundlegende Einstellungen und Gerätezuordnungen werden nach Sitzungsende
geändert. Die Wiederaufnahmefrist gehört zur bestehenden Sitzung. Bei einem Neustart von Home Assistant bleibt der gespeicherte
Verlauf erhalten; der Ofenbetrieb beginnt wieder durch ausdrückliches Einschalten.

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
Temperaturreserve, standardmäßig 5 °C. Nach einer regulären Temperaturabschaltung
gilt eine Heizpause von 5 Minuten. Nach deren Ablauf fordert der Thermostat bei
Erreichen oder Unterschreiten der oberen Regeltemperatur abzüglich 3 °C
Hysterese wieder Heizen an. Innerhalb des Hysteresebands bleibt sein bisheriger
Zustand erhalten.

Ein tatsächlich bestätigter Heizbeginn startet die Mindestheizzeit von
standardmäßig 10 Minuten. Ein bereits laufendes Heizintervall behält seinen
ursprünglichen Beginn. Die Heizpriorität ordnet diese Mindestzeit in die
[Gangheizung und Ofenkühlung](praesenz-ofen-phasen.md) ein. Ein aktiver Gang
fordert im Automatikbetrieb ab seiner vorläufigen Erkennung durchgehend Heizen
an, solange die übergeordneten Freigaben gelten.

Die Phasenanzeige beschreibt den aktuellen Betriebsabschnitt. Beim Aufheizen
steht daneben eine Prognose aus einem geeigneten früheren Aufheizverlauf und
dem zunehmend belastbaren aktuellen Temperaturtrend. Sie dient der Anzeige
und erscheint in Fünf-Minuten-Stufen.
Ihre Berechnung erläutert [Darstellung](darstellung.md).

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
Gangzählung und laufende Zeitabläufe bleiben erhalten. Die Auswahl und
Bearbeitung der Programme beschreibt [Bedienung](bedienung.md).

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
zuletzt berechnete Rest erhalten. Nach einer abgeschlossenen Sitzung mit gezählten
Gängen beginnt die Anzeige beim nächsten Start mit der vollen Dauer. Bei einer
Gangzahl von null übernimmt die nächste Sitzung den bisherigen Timerrest. Der
mechanische Drehschalter wird am Ofen bedient; die Timerberechnung dient
ausschließlich der Anzeige.

Die Verbrauchsschätzung ergibt sich aus gezählter Heizzeit und eingestellter
Ofenleistung, standardmäßig 4,5 kW. Eine gültige Leistungsmessung übernimmt die
Berechnung für ihren jeweiligen Zeitraum. Die Anzeige unterscheidet gemessene,
geschätzte und unbekannte Anteile. Der Sitzungsverbrauch bleibt über Kühlphasen
und lokale Heizzeitrücksetzungen hinweg erhalten.

## Ofenkühlung

Eine angeforderte Ofenkühlung fordert Ofen-AUS an. Mit der bestätigten
Schützstellung AUS berechnet die Steuerung die Dauer und beginnt, die bestätigte
AUS-Laufzeit zu zählen. Die Standarddauer liegt zwischen 5 und 15 Minuten.
Auslösung, Berechnungsregel und ausdrückliches vorzeitiges Ende beschreibt
[Ofenkühlung](ofenkuehlung.md).

## Licht

Die automatische Helligkeit folgt dem Betriebsabschnitt und der Temperatur.
Die Lichtkurve reicht von 5 % bei 30 °C bis zur Normalhelligkeit an der aktuellen
Solltemperatur. Die Normalhelligkeit beträgt tagsüber 40 % und nachts
25 %; während der bürgerlichen Dämmerung geht sie gleitend zwischen beiden
Werten über. Während eines Saunagangs bleibt die Normalhelligkeit erhalten.
Die Ausgabe erfolgt in ganzen Prozentwerten.

Zu Beginn der Ofenkühlung geht das Licht auf 15 % zurück. Anschließend steigt es
über die verbleibende Kühlzeit linear zur temperaturabhängigen Helligkeit an.
Automatische Übergänge dauern standardmäßig 30 Sekunden; die Kühlphase begrenzt
den ersten Übergang auf höchstens die Hälfte ihrer verbleibenden Dauer.

Betrieb-AUS startet den Lichtnachlauf bei 50 %. Er endet mit der gemeinsamen
Wiederaufnahmefrist durch Ausschalten des Lichts. Beim langen Enddruck einer
neuen Saunatastergeste bleibt das Licht während des Haltens aus; bestätigtes
Loslassen beginnt den Lichtnachlauf mit derselben eingestellten Dauer.

Eine manuelle Lichtwahl gilt bis zum nächsten Phasenwechsel oder längstens
10 Minuten. Ausschalten und Dimmen am Lichttaster zählen als manuelle Wahl.
Rückmeldungen eigener Lichtbefehle ordnet die Integration dem automatischen
Verlauf zu.

Enden Lichtnachlauf und manuelle Lichtwahl gleichzeitig, bleibt die automatische
Endphase AUS. Eine anschließend ausdrücklich gesetzte Raumlichtwahl gilt als
neue manuelle Bedienung. Bei ihrer Rückkehr zur Automatik bleibt die Grundlage
der beendeten Lichtphase 0 %.

## Bedienhandlungen und Betriebsart

Ein Druck auf den Saunataster bei Betrieb-AUS startet den
Automatikbetrieb mit der gespeicherten Tastervorgabe: einem benannten Programm
oder einer eigenen konstanten Temperatur. Bei gemeldetem Drücken erfolgt der
Start unmittelbar; eine reine Kurz- oder Langklassifikation startet ebenfalls.
Die Startgeste bleibt verbraucht, einschließlich ihrer nachfolgenden Lang- und
Loslassmeldungen. Bei eingeschaltetem Automatikbetrieb
schaltet ein kurzer Druck die vorübergehende Ofenwahl um beziehungsweise gibt
an die Automatik zurück. Bei eingeschaltetem Betrieb in Manuell wechselt ein
kurzer Druck die Ofenvorgabe zwischen EIN und AUS.

Eine neue lange Geste bei laufendem Betrieb beendet den Betrieb und schließt die
Sitzung ab. Bei einer reinen Ereignisquelle ist jede neue Langklassifikation eine
eigenständige Geste mit dem aktuellen Betriebskontext: Die erste startet bei AUS,
die nächste beendet die laufende Sitzung. Tatsächliches Halten und bestätigtes
Loslassen bestimmen den oben beschriebenen Lichtabschluss. Die zugehörigen
Meldungen und ihre einmalige Verarbeitung beschreibt der
[Ereignisvertrag](schnittstellen.md#tasterereignisse).

Die vorübergehende Ofenwahl in Automatik gilt bis zur Rückgabe, einem Wechsel
der Phase oder der automatischen EIN-/AUS-Anforderung, längstens 10 Minuten.
Ausdrücklich gewähltes Ofen-AUS wirkt auch während eines aktiven Gangs.
Ofen-EIN setzt die gültige Heizfreigabe einschließlich einer gültigen
Regeltemperatur voraus. Schutz, Betrieb-AUS und Ofenkühlung haben Vorrang.

Bei der Rückgabe eines manuell eingeschalteten Ofens an die Automatik bleibt
eine noch laufende Mindestheizzeit mit ihrem ursprünglichen tatsächlichen
Beginn erhalten. Ausdrücklich gewähltes Ofen-AUS beendet diese Anforderung.
Die Rollen und verfügbaren Bedienelemente erläutert [Bedienung](bedienung.md).

Die Betriebsart Manuell wird nach Sitzungsende gewählt und bleibt bis zur
nächsten ausdrücklichen Betriebsartwahl bestehen. Dort steuern
Nutzer Ofen und Licht direkt. Messung und Archivierung begleiten den Betrieb;
technische Schutzgründe und Betrieb-AUS behalten Vorrang.

## Rückmeldungen und Schutz

Gültige Temperaturmessungen und bestätigte Geräterückmeldungen bilden die
Grundlage der Heizfreigabe. Messwerte und Befehlsrückmeldungen haben eigene
Gültigkeits- beziehungsweise Bestätigungsfristen. Die [Parameterreferenz](parameter.md#überwachung)
erläutert diese Zeitbedingungen. Die technische Überwachung bewertet jede
Störung nach den für sie festgelegten Bedingungen. Eine ausgelöste
Schutzabschaltung verriegelt die Heizfreigabe.

Während einer Lücke der gültigen Regeltemperatur bleibt die Heizung aus.
Fehler einzelner konfigurierter Quellen bleiben sichtbar; gültige Ersatzquellen
führen ihre vorgesehenen Aufgaben weiter. Die Quittierung technischer
Schutzgründe setzt Betrieb-AUS und bestätigtes Ofen-AUS voraus.
