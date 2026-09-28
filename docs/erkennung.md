# Erkennung aus Temperatur und Feuchte

HA Sauna erkennt Türbewegungen, Personen, Aufgüsse und Durchlüften aus den
zugeordneten Temperatur- und Feuchtemessungen. Veränderungen liefern zunächst
einen Hinweis. Die jeweilige Ereignisprüfung sucht anschließend einen
zusammenhängenden Nachweis und berücksichtigt Gegenzeichen. Die Verarbeitung
folgt der Empfangsreihenfolge; Messlücken bleiben sichtbar und das Archiv
bewahrt die Originalwerte.

## Türbewegungen

Die Öffnungsprüfung verbindet fallende Temperatur und Feuchte innerhalb der
eingestellten Beobachtungsfenster. Beide Änderungen können zeitlich versetzt
eintreffen. Während eines durchgehend bestätigten Heizvorgangs kann auch ein
ausreichender Temperaturabfall die Öffnung belegen. Die Prüfung verwendet die
verfügbaren vollständigen Messpositionen. Eine Position genügt für den vollen
Betrieb; bei zwei gültigen Positionen belegen beide den Abfall. Diese Regeln
gelten auch in der heißen Sauna.

Eine Schließprüfung setzt eine erkannte Öffnung voraus. Sie folgt der
Temperaturerholung an den verfügbaren Messpositionen. Gültige Temperaturwerte
können die Schließung auch während einer Feuchtestörung belegen. Fehler
konfigurierter Quellen bleiben sichtbar.

## Personen und Aufgüsse

Eine Zunahme der relativen Feuchte zusammen mit zunehmendem absolutem
Wassergehalt belegt den Feuchteanstieg. Dadurch bleibt ein Aufguss auch bei
sinkender Temperatur, etwa während des anschließenden Wedelns, erkennbar.

Ein ausgeprägtes Personensignal benötigt ein vollständiges Messfenster und die
eingestellte Bestätigungsdauer. Das empfindlichere Signal ordnet einen neuen
Feuchteanstieg einer erkannten Türöffnung mit anschließender Schließung zu.
Es setzt eine neue Feuchteentwicklung nach der Öffnung voraus. Während eines
Gangs führt die Erkennung die Aufgussprüfung fort. Personenprüfung ist für die
Eröffnung eines neuen Gangs vorgesehen; die Übergänge erklärt das
[Gangmodell](gangmodell.md).

## Absoluter Wassergehalt und Durchlüften

Aus jedem verbundenen Temperatur- und Feuchtepaar entsteht eine diagnostische
Entität für den absoluten Wassergehalt dieser Messposition. Ihre Verfügbarkeit
setzt frische, gültige Werte beider Quellen voraus.

Beim ersten Öffnungshinweis speichert die Erkennung eine vollständige Messung
aus der Zeit davor als Lüftungsreferenz. Anschließend vergleicht sie damit den
Temperaturverlust und den relativen Verlust an absolutem Wassergehalt. Die beim
Beginn verwendete Quellenzuordnung gilt für den gesamten Lüftungsnachweis.

Bei zwei anfangs gültigen Messpositionen ist das Durchlüften mit dem beidseitigen
Erreichen der Verlustschwellen bestätigt: standardmäßig **3 °C** Temperaturverlust
und **30 %** Verlust an absolutem Wassergehalt. Bei anfangs einer gültigen
Position kommt eine Öffnungsdauer von standardmäßig **60 Sekunden** hinzu.
Ein mit zwei Positionen begonnener Nachweis setzt beide ursprünglichen
Positionen voraus.

Eine kurze Messlücke pausiert den Vergleich und erhält seine Referenz.
Sobald dieselben gültigen Quellen wieder vorliegen, wird der Vergleich
fortgesetzt. Ein Quellenwechsel verwirft die bisherige Referenz. Eine
Temperaturerholung leitet die Schließprüfung ein und beendet den noch offenen
Lüftungsnachweis. So erhält eine kurze Öffnung mit anschließender Schließung
den bestehenden Gang.

## Bestätigung und Diagnose

Die zentral validierten [Parameter](parameter.md) bestimmen Beobachtungsfenster
und Bestätigungsdauern. Ein Wechsel oder Ausfall der vollständigen Messpositionen
sowie das Ende einer Türöffnung beendet den zugehörigen zusammenhängenden
Bestätigungsnachweis. Für die gesonderte Lüftungsreferenz gilt die oben
beschriebene Pausenregel.

Ein fortlaufend belegter Öffnungsweg kann mehrere Merkmalsfenster bis zum Ende
seiner Bestätigungsdauer durchlaufen. Das Fenster begrenzt jeweils das Alter
der verwendeten Hinweise. Die Erkennungskontrolle zeigt die archivierten
Merkmale des produktiven Detektors. Ihre Haltezähler geben die Anzahl erfüllter
Prüfpunkte an; deren zeitlicher Abstand folgt dem eingestellten Prüfschritt.

Beim Aufholen bereits empfangener Messungen gelten die Betriebsfreigaben ihres
jeweiligen Beobachtungszeitpunkts. Nach einer erneuten Freigabe stammen die
beitragenden Messfenster aus dem neu freigegebenen Abschnitt. Die zeitliche
Zuordnung zur Gangführung beschreibt das [Zeitmodell](zeitmodell.md).

Aktuelle Erkennungsdatensätze verbinden das Ereignis über `trace_at` mit genau
dem Diagnosepunkt, der es ausgelöst hat. Das gilt auch für einen früheren
fachlichen Beginn und eine spätere Verarbeitung. Archive mit dem früheren
Datenschema verwenden für diese Zuordnung den fachlichen Ereignisbeginn.

Das [Replayprogramm](kandidat.md) wertet gespeicherte Messreihen mit einem
festgehaltenen Erkennungsverfahren aus und unterstützt den Vergleich von
Parametern. Die laufende Steuerung verwendet die auf dieser Seite beschriebenen
Erkennungsregeln.
