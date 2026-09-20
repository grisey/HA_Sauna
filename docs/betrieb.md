# Betrieb

Diese Seite beschreibt den geltenden Ablauf. Die tägliche Bedienung steht in
[Bedienung](bedienung.md), die Zuordnung von Erkennung und Aufguss in
[Gangmodell](gangmodell.md) und Fristen in [Zeitmodell](zeitmodell.md).
Alle hier genannten Werte sind einstellbare Standardwerte; gespeicherte lokale
Werte bleiben erhalten. Einzig die Solltemperatur hat eine feste Obergrenze von
100 °C.

## Sitzung

Eine Sitzung ist das führende Laufzeitobjekt für Heizung, Gänge, Nachlauf,
Kühlung und Licht. Betrieb-Ein startet sie, Betrieb-Aus beendet einen offenen
Gang sofort und beginnt die Unterbrechungsfrist. Ein rechtzeitiges Einschalten
setzt nur dieselbe Sitzung fort; nach der Session-Unterbrechungsfrist (15 min)
beginnt die nächste Einschaltung eine neue Sitzung. Konfiguration, Archiv und
Schutzgründe sind davon getrennt.

Während einer offenen Sitzung sind Solltemperatur, Endtemperatur und Verteilung
der Temperaturautomatik änderbar. Andere Grundeinstellungen und
Gerätezuordnungen bleiben gesperrt. Eine neue Sitzung beendet einen alten
Lichtnachlauf. Nach einem HA-Neustart bleibt die Historie erhalten, der Betrieb
und offene Fristen werden nicht automatisch fortgesetzt.

Die Phase zeigt Aufheizen, Bereit, Saunagang, Nachlauf, Zwangskühlung, Manuell
oder Aus. Eine Bereitschaftsprognose ist ausschließlich eine Anzeige: Solange
die aktuelle Sitzung keinen ausreichend konstanten positiven Anstieg liefert,
darf sie den durchschnittlichen Aufheizanstieg der letzten Sitzung verwenden.
Geschätzte Bereitschaft und Restfenster erscheinen grob in Fünf-Minuten-Stufen
neben der Phase. Sie verändern weder Heizentscheidung noch Schutz oder Fristen;
Darstellung und Parameter sind in [Darstellung](darstellung.md) beschrieben.

## Heizung und Temperatur

Die Regelung verwendet die obere gültige Temperatur. Sobald die Solltemperatur
erreicht ist, gilt die Sauna als bereit. Dieser Zustand bleibt bis zum nächsten
Saunagang oder Beginn einer Zwangskühlung erhalten, auch wenn die Temperatur
danach sinkt oder die Sollwahl geändert wird. Ausschalten und bestehende
Sperren behalten Vorrang.

Der Ofen heizt weiter bis zur oberen Regeltemperatur: Solltemperatur plus
Temperaturreserve (5 °C). Die Hysterese beträgt 3 °C, die Heizpause nach einer
regulären Temperaturabschaltung 5 min. Ein tatsächlich begonnenes Heizintervall läuft
mindestens 10 min. Betrieb-Aus, Nachlauf, Kühlung und technischer Schutz gehen
dieser Mindestzeit vor. Schon ein vorläufiger Gang unterdrückt reguläre
Thermostatabschaltungen.

Direkte Sollwahl bedeutet konstante Temperatur. Die Temperaturautomatik verteilt
Start und Ende über die eingestellte Zahl von Gängen (Standard: 80 bis 95 °C in
vier Gängen). Die Verteilung begrenzt keine tatsächlichen Gänge; nach der
Endtemperatur gilt diese für beliebig viele weitere Gänge. Wird nur die
Endtemperatur geändert, bleibt das nächste Ziel erhalten; die restlichen
Steigerungen werden bis zum neuen Endwert verteilt. Eine neue Starttemperatur
beginnt dagegen eine neue Verteilung. Änderungen lassen
Gangzählung, laufende Fristen, Kühlung und Schutz unverändert. Details stehen in
[Bedienung](bedienung.md).

Das Heizbudget beträgt zunächst 90 min. Nach der ersten abgeschlossenen Kühlung
derselben Sitzung sinkt es einmalig um 30 min und bleibt danach konstant. Eine
Kühlvorgabe beträgt 15 min. Heizzeit folgt einer gültigen Leistungsmessung,
sonst Heizrückmeldung oder Schützstellung; ohne unabhängige Messung ist sie eine
sichtbare Schätzung. Eine zusammenhängende rückgemeldete Auszeit von 10 min
setzt nur die lokale Heizsumme zurück, keine Sitzung. Der mechanische Ofentimer
ist ebenfalls nur Anzeige: Standard 240 min, gezählt nur bei Betrieb-Ein und
bestätigtem Schütz-Ein; Schütz-Aus oder unbekannte Schützstellung hält ihn an.
Nach einer beendeten Sitzung mit gezählten Gängen beginnt die Timeranzeige beim
nächsten Einschalten wieder mit voller Dauer. Ein kurzer Test ohne gezählte Gänge
behält die Restzeit. Die Anzeige misst die Stellung des Drehschalters nicht und
löst keine Steuerung aus.

Der Energieverbrauch wird ohne Leistungsmesser aus gezählter Heizzeit und
Ofenleistung geschätzt (Standard 4,5 kW). Eine gültige Leistungsmessung ersetzt
die Schätzung für den jeweiligen Zeitraum. Gemessene, geschätzte und fehlende
Anteile bleiben unterscheidbar. Kühlung und lokale Heizzeitrücksetzung löschen
den Verbrauch der Sitzung nicht.

## Kühlung und Nachlauf

Ein bestätigter, beendeter Gang erhält einen Nachlauf (8 min). Dieser hält den
Ofen im automatischen Ablauf aus. Fällige Kühlung bricht einen Gang nie ab:
Gangende, Nachlauf und erst danach die verbleibende Kühlung folgen aufeinander.
Die tatsächlich gelaufene Nachlaufdauer wird genau einmal auf die nächste
Kühlung gutgeschrieben, auch wenn sie erst später fällig wird. Die manuell
beendete Phase schreibt nur ihre bis dahin verstrichene Zeit gut.

Ein manueller Heizstart in der Automatik pausiert Nachlauf und bereits laufende
Kühlung. Ohne bestätigten neuen Gang laufen ihre Restzeiten nach Rückkehr zur
Automatik weiter. Beginnt während eines pausierten Nachlaufs ein vorläufiger
Gang, bleibt der alte Nachlauf bis zur Aufgussbestätigung bestehen. Erst die
Bestätigung storniert ihn; wird der vorläufige Gang aufgehoben oder läuft seine
Bestätigungsfrist ab, setzt der alte Rest fort. Seine bis dahin gezählte Zeit
wird nicht erneut angerechnet. Nach einem bestätigten neuen Gang beginnt dessen
vollständiger Nachlauf. Eine laufende Kühlung setzt nach dem Nachlauf mit ihrer
echten Restzeit fort.

Reicht das Budget beim Gangende nicht mehr für die Mindestheizzeit, wird die
Kühlung bereits an den Nachlauf angehängt. Ein Budgetablauf ist ein
Fälligkeitsmerker und beachtet die Türwartephase: bei offener Tür höchstens
10 min ab Öffnung, nach rechtzeitiger Schließung 4 min ab Schließung. Bereits
laufende Kühlung wird nicht zurückgenommen.

## Licht

Im Automatikbetrieb steigt das Licht linear von 5 % bei 30 °C zur
Normalhelligkeit: tagsüber 40 %, nachts 25 %, mit linearem Übergang in der
bürgerlichen Dämmerung. Die Ausgabe erfolgt in ganzen Prozentpunkten; ein
unveränderter Zielwert erzeugt während der Rückmeldungsfrist keinen erneuten
Lichtbefehl. Ein Gang hält die Normalhelligkeit auch bei fallender
Temperatur. Nachlauf verwendet 15 %, Kühlung 5 %. Der Übergang dauert 30 s;
danach steigt das Licht bis zum Ende der Phase wieder zum temperaturbezogenen
Ziel. Nach endgültigem
Sitzungsende leuchtet es 10 min mit 50 % und schaltet dann aus. Diese Lichtfrist
hat keine Wirkung auf Ofen oder Kühlung.

Eine manuelle Lichtwahl gilt bis zum Phasenwechsel oder höchstens 10 min.
Tatsächliches Ausschalten und Dimmen am Lichttaster sind solche Wahlen; eigene
Integrationsbefehle und unveränderte Rückmeldungen nicht.

## Manuelle Bedienung

Bei ausgeschaltetem Betrieb startet der Saunataster mit dem hinterlegten
Programm in Automatik.
Während der Sitzung schaltet ein kurzer Druck zwischen vorübergehender
Ofenübersteuerung und Automatik, langes Drücken beendet die Sitzung. Ofen- und
Lichtübersteuerungen in Automatik enden spätestens nach 10 min; früher durch
Rückgabe an Automatik sowie beim passenden Phasenwechsel oder automatischen
Heizwechsel. Schutz hat stets Vorrang.

Wird ein manuell eingeschalteter Ofen frühzeitig an die Automatik zurückgegeben,
läuft seine noch offene Mindestheizzeit ab dem tatsächlichen Heizbeginn weiter.
Bewusstes manuelles Ausschalten hebt diesen Mindestlauf auf. Eine verzögerte
EIN-Rückmeldung darf ihn danach nicht erneut auslösen. Nachlauf, Kühlung und
Schutz behalten bei jeder Rückgabe ihren Vorrang.

Die Betriebsart **Manuell** kann nur außerhalb einer offenen Sitzung gewählt
werden und ist nicht zeitbegrenzt. Dort bedienen Nutzer Ofen und Licht direkt;
Thermostat, Heizbudget, reguläre Kühlpausen und Lichtautomatik wirken nicht.
Messung, Archivierung, technische Sperren und bestätigte Übertemperatur bleiben
aktiv. Übertemperaturbedingte Kühlung wird auch in Manuell bis zum Ende eines
schon aktiven Gangs aufgeschoben; technischer Schutz bleibt sofort vorrangig.

## Schutz

Liegt die gültige obere Temperatur länger als 10 min über 105 °C, wird eine
zusätzliche Kühlung in doppelter Vorgabedauer angefordert. Sie beendet keine
Sitzung und folgt derselben Gang-, Nachlauf- und Anrechnungsreihenfolge.
Unterbrechung der Bedingung setzt den Nachweis zurück.

Messwertgültigkeit (180 s), Rückmeldungsfrist und Fehlerbestätigung (60 s) sind
getrennte Werte. Fehlt die obere Regeltemperatur nach ihrer Gültigkeit, pausiert
die Heizung. Bestätigte technische Schutzgründe verriegeln die Heizfreigabe und
werden erst nach Betrieb-Aus und bestätigtem Ofen-Aus quittiert. Ein Sensorfehler
bleibt sichtbar; es gibt keine Mittelung oder erfundenen Höhenoffset.
