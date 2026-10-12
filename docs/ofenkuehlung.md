# Ofenkühlung

## Nach einem Gang abkühlen

Im Automatikbetrieb folgt die Kühlung auf einen abgeschlossenen bestätigten Gang:
beim Proxyverfahren nach Durchlüften im Anschluss an die Austrittsöffnung,
bei direkter Präsenz nach belegtem Austritt
([Gangerkennung](gangmodell.md)). Betrieb-AUS verwendet stattdessen den
[Lichtnachlauf](bedienung.md#ausschalten-und-lichtnachlauf-beenden).
In Manuell bleibt die Bedienwahl maßgeblich.

## Kühlung abwarten oder vorzeitig beenden

Die Kühlanforderung schaltet den Ofen aus und sperrt erneutes Heizen. Erst
bestätigtes Schütz-AUS startet die Uhr und legt die berechnete Kühldauer samt
Mindestdauer für diesen Kühlzyklus fest. Nur bestätigte AUS-Zeit zählt; Schütz-EIN oder
unbekannte Rückmeldung pausieren die Uhr, ohne die Heizsperre aufzuheben.

Ein mit direktem Präsenzsensor erkannter neuer Gang beendet die Kühlung sofort.
Im Proxybetrieb begrenzt jedes erkannte Personensignal, einschließlich eines
Aufgusses als Personenbeleg, die laufende Kühlung auf ihre Mindestgesamtdauer.
Bereits bestätigte AUS-Zeit seit Kühlbeginn wird dabei vollständig angerechnet.
Ist die Mindestdauer bereits erfüllt, endet die Kühlung mit diesem Signal;
andernfalls bleibt nur die fehlende AUS-Zeit. Weitere Signale starten keine neue
Frist und ziehen keine Zeit erneut ab. Ein Signal vor der Ausschaltbestätigung
merkt diese Begrenzung für den tatsächlichen Kühlbeginn vor.

Gangerkennung und Aufzeichnung laufen während der Kühlung weiter. Die Begrenzung
setzt weder eine neue vollständige Türfolge noch eine Gangbestätigung voraus;
die zusätzlichen Eintrittskriterien gelten weiterhin für den Gangbeginn.
Sitzungskontext und phasenabhängige Mindesttemperatur bleiben Voraussetzungen
für die Personenbelege. Nach dem Kühlende werden Gangzustand und
Heizanforderung im selben Verarbeitungsschritt neu bewertet; Schutzabschaltungen
und die geltende Übersteuerung bleiben maßgeblich. Das Ende eines während der
Kühlung erkannten Gangs ersetzt keine noch laufende Kühlphase und setzt deren
bereits bestätigte AUS-Zeit nicht zurück.

Das bewusste vorzeitige Beenden bleibt bei beiden Präsenzquellen möglich.
Eine vorzeitig verkürzte Kühlung, einschließlich der Begrenzung auf ihre
Mindestdauer, verkürzt den Bemessungszeitraum für die nächste Kühlung nicht:
Nur die vollständig durchlaufene ursprüngliche Kühldauer setzt dessen Beginn
auf ihren Abschluss.

## Wie die Kühldauer entsteht

Die Kühldauer berücksichtigt bisheriges Heizen und Bereitschaft seit dem Ende der letzten
vollständigen Kühlung, bei der ersten Kühlung seit Sitzungsbeginn.
Alle Zeiten beziehen sich auf den tatsächlichen Kühlbeginn. Jüngere Anteile
zählen stärker, mit der eingestellten Halbwertszeit \(h\):

\[
w(Alter)=2^{-Alter/h}
\]

- \(H\): gewichtete Zeit mit bestätigtem Schütz-EIN, über alle Betriebsphasen.
- \(I\): gewichtete Bereitschaftszeit bei Betrieb-EIN und bestätigtem Schütz-AUS.
- \(r\): eingestelltes Verhältnis von Heiz- zu Bereitschaftszeit.

Die Dauer in Minuten lautet:

\[
\text{Basis}+\operatorname{clip}(H/r-I,\,0,\,\text{Maximum}-\text{Basis})
\]

`clip` begrenzt die Zusatzdauer zwischen null und der Differenz aus Höchst- und
Basisdauer. Die Höchstdauer darf nicht unter der Basisdauer liegen.

Unbekannte Schützzeiten liefern keine Bereitschaftsgutschrift und kennzeichnen
die Berechnung als unvollständig. Das Archiv bewahrt Zeitraum, Einstellungen
und gewichtete Anteile des Kühlzyklus. Eine Begrenzung auf die Mindestdauer,
die wirksame Gesamtdauer und die tatsächlich gezählte AUS-Zeit bleiben getrennt
nachvollziehbar ([Speicherung](speicherung.md)).
