# Saunabetrieb

## Sitzung

Betrieb-EIN beginnt eine Sitzung. Betrieb-AUS beendet den offenen Gang und
startet die gemeinsame Frist für Wiederaufnahme und Lichtnachlauf. Erneutes
Einschalten innerhalb dieser Frist setzt dieselbe Sitzung fort. Nach Ablauf der
Frist beginnt das nächste Einschalten eine neue Sitzung. Jeder Sitzungsstart
beendet einen laufenden Lichtnachlauf.

Die Wiederaufnahmefrist gehört zur bestehenden Sitzung. Bei einem
Neustart von Home Assistant bleibt der gespeicherte Verlauf erhalten; der
Ofenbetrieb beginnt wieder durch ausdrückliches Einschalten.

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

Die Thermostatgrenzen beziehen sich auf die Solltemperatur:

- Abschalten bei Solltemperatur plus eingestelltem Abschaltaufschlag.
- Wiedereinschalten bei Solltemperatur minus eingestelltem Unterabstand.

Innerhalb dieses Bands bleibt der bisherige Zustand erhalten. Nach einer
regulären Temperaturabschaltung muss vor dem Wiedereinschalten zusätzlich die
Heizpause ablaufen. Ein tatsächlich bestätigter Heizbeginn startet die
Mindestheizzeit; ein laufendes Intervall behält seinen ursprünglichen Beginn.
Übergeordnete Anforderungen und Sperren folgen der
[Heizpriorität](praesenz-ofen-phasen.md#heizpriorität).

[Aufheizprognose](darstellung.md#zustand-und-zeit)

## Temperaturprogramm

Direkte Sollwahl hält eine konstante Zieltemperatur. Ein Temperaturprogramm
legt eine Folge von Stufen fest. Es kann als benanntes Programm ausgewählt oder
individuell zusammengestellt werden. Einzelne Stufen erlauben steigende und
fallende Folgen; eine gleichmäßige Verteilung berechnet die Stufen aus Start,
Ende und Stufenzahl.
Jeder beendete, bestätigte Gang führt zur nächsten Stufe. Nach der letzten
Stufe gilt deren Temperatur auch für weitere Gänge.

Bei einer gleichmäßigen Verteilung erhält eine Änderung ausschließlich der
Endtemperatur das aktuelle Ziel; die verbleibenden Steigerungen verteilen sich
bis zum neuen Endwert. Eine neue Starttemperatur beginnt eine neue Verteilung.
Gangzählung und laufende Zeitabläufe bleiben erhalten.

[Programme auswählen und bearbeiten](bedienung.md#betriebsstart-und-temperaturwahl)

## Heizzeit, Timer und Energie

Die Heizzeiterfassung verwendet vorrangig eine gültige Leistungsmessung. Als
weitere Quellen dienen eine eingerichtete Heizrückmeldung und schließlich die
native Schalterrückmeldung des Ofens. Die Anzeige kennzeichnet die verwendete
Quelle und eine daraus abgeleitete Schätzung. Die lokale Heizsumme wird
zurückgesetzt, sobald die durchgehend bestätigte Auszeit die eingestellte
Rücksetzdauer erreicht.

Die Timeranzeige schätzt die verbleibende Laufzeit des mechanischen Ofentimers.
Sie zählt von der eingestellten Laufzeit herunter, solange Betrieb und bestätigte
Schützstellung EIN sind. Bei Schütz-AUS oder unbekannter Rückmeldung bleibt der
zuletzt berechnete Rest erhalten. Nach jeder abgeschlossenen Sitzung beginnt
die Anzeige beim nächsten Sitzungsstart mit der vollen Dauer, unabhängig von
der erkannten Gangzahl. Fortsetzen innerhalb derselben Sitzung erhält die
Restzeit. Die Anzeige stellt den mechanischen Timer am Gerät nicht zurück.

Bei alleiniger Schützrückmeldung zeigt das Panel **Heizfreigabe EIN/AUS**.
Die tatsächliche Heizleistung hinter einem internen Ofenthermostat ist daraus
nicht bekannt. Eine gültige Leistungsmessung oder unabhängige Heizrückmeldung
ermöglicht die Anzeige **Ofen an/aus**.

Die Verbrauchsschätzung ergibt sich aus gezählter Heizzeit und eingestellter
Ofenleistung. Eine gültige Leistungsmessung übernimmt die Berechnung für ihren
jeweiligen Zeitraum. Die Anzeige unterscheidet gemessene, geschätzte und
unbekannte Anteile. Der Sitzungsverbrauch bleibt über Kühlphasen
und lokale Heizzeitrücksetzungen hinweg erhalten.

## Ofenkühlung

[Auslösung, Berechnung und Abschluss der Ofenkühlung](ofenkuehlung.md)

## Licht

Aufheizen und Bereitschaft verwenden dieselbe lineare Lichtkurve: von der
Grundhelligkeit an der eingestellten Referenztemperatur bis zur Normalhelligkeit
an der aktuellen Solltemperatur. Die Thermostatgrenzen verändern das Lichtziel
nicht. Während eines Saunagangs gilt die Normalhelligkeit. Diese geht während
der bürgerlichen Dämmerung gleitend zwischen eingestelltem Tag- und Nachtwert über.

Die Ausgabe wird auf ganze Prozent gerundet. Bei automatischen Änderungen
kommt zur Rundungsgrenze die eingestellte Ausgabehysterese hinzu; interne
Kurven und Messwerte behalten ihre Genauigkeit. Manuelle Wahlen und fälliges
AUS werden nicht durch diese Hysterese verzögert.

Zu Beginn der Ofenkühlung dimmt das Licht auf die eingestellte Kühlhelligkeit.
Anschließend steigt es über die verbleibende Kühlzeit linear zur
temperaturabhängigen Helligkeit. Der erste Übergang ist auf die Hälfte der
verbleibenden Kühlzeit begrenzt.

Betrieb-AUS startet den Lichtnachlauf mit der dafür eingestellten Helligkeit.
Mit der Wiederaufnahmefrist endet auch der Lichtnachlauf. Beim langen Enddruck
bleibt das Licht während des Haltens aus; erst bestätigtes Loslassen startet
den Nachlauf mit der eingestellten Dauer.

Eine manuelle Lichtwahl in Automatik gilt bis zum nächsten passenden Phasenwechsel
oder längstens für die eingestellte Übersteuerungsdauer. Ausschalten und Dimmen
am Lichttaster zählen als manuelle Wahl.
Rückmeldungen eigener Lichtbefehle ordnet die Integration dem automatischen
Verlauf zu. Bei der Rückkehr zur Automatik beginnt der Übergang an der zuletzt
beobachteten Helligkeit und blendet zum aktuellen automatischen Verlauf über.
Eine laufende Kühl- oder Lichtnachlauffrist begrenzt diesen Übergang; sie beginnt
dadurch nicht neu. Das fällige Ausschalten beendet auch einen laufenden Übergang.

Enden Lichtnachlauf und manuelle Lichtwahl gleichzeitig, bleibt die automatische
Endphase AUS. Eine anschließend ausdrücklich gesetzte Raumlichtwahl gilt als
neue manuelle Bedienung. Bei ihrer Rückkehr zur Automatik bleibt die Grundlage
der beendeten Lichtphase AUS.

## Bedienhandlungen und Betriebsart

Ein kurzer Tastendruck bei Betrieb-AUS startet den Automatikbetrieb mit der
gespeicherten Tastervorgabe: einem benannten Programm oder einer eigenen
konstanten Temperatur.

Ein langer Druck aus Betrieb-AUS startet die Sauna nicht. Bei eingeschaltetem Automatikbetrieb
schaltet ein kurzer Druck die vorübergehende Ofenwahl um beziehungsweise gibt
an die Automatik zurück. Bei eingeschaltetem Betrieb in Manuell wechselt ein
kurzer Druck die Ofenvorgabe zwischen EIN und AUS.

Ein langer Druck bei laufendem Betrieb beendet den Betrieb und schließt die
Sitzung ab. Das Licht bleibt während des Haltens AUS. Erst das bestätigte
Loslassen startet den Lichtnachlauf.

[Tastermeldungen und einmalige Verarbeitung](schnittstellen.md#tasterereignisse)

Die vorübergehende Ofenwahl in Automatik gilt bis zur Rückgabe, einem Wechsel
der Phase oder der automatischen EIN-/AUS-Anforderung, längstens für die
eingestellte Übersteuerungsdauer. Beim Fristende gilt wieder die aktuelle Automatik.
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
Gültigkeits- beziehungsweise Bestätigungsfristen. Abschaltrelevant sind fehlende
Regeltemperatur oder Schützrückmeldung, ausgebliebene Schaltvollzüge,
fehlgeschlagene Schaltbefehle und nachgewiesenes Weiterheizen trotz AUS-Befehl.
Bestehen sie während der Betriebs- oder Heizüberwachung über die
Bestätigungsdauer fort, verriegelt die Schutzabschaltung die Heizfreigabe.

[Überwachungsfristen](parameter.md#überwachung)

Während einer Lücke der gültigen Regeltemperatur bleibt die Heizung aus.
Fehler einzelner konfigurierter Quellen bleiben sichtbar; gültige Ersatzquellen
führen ihre vorgesehenen Aufgaben weiter. Die Quittierung technischer
Schutzgründe setzt Betrieb-AUS und bestätigtes Ofen-AUS voraus.
