# Ofenkühlung

## Nach einem Gang abkühlen

Im Automatikbetrieb folgt die Kühlung auf einen abgeschlossenen bestätigten Gang:
beim Proxyverfahren nach Durchlüften im Anschluss an die Austrittsöffnung,
bei direkter Präsenz nach belegtem Austritt
([Gangerkennung](gangmodell.md)). Betrieb-AUS verwendet stattdessen den
[Lichtnachlauf](bedienung.md#ausschalten-oder-sofort-beenden).
In Manuell bleibt die Bedienwahl maßgeblich.

## Kühlung abwarten oder vorzeitig beenden

Die Kühlanforderung schaltet den Ofen aus und sperrt erneutes Heizen. Erst
bestätigtes Schütz-AUS startet die Uhr und legt die Kühldauer fest. Diese bleibt
für den Kühlzyklus unverändert. Nur bestätigte AUS-Zeit zählt; Schütz-EIN oder
unbekannte Rückmeldung pausieren die Uhr, ohne die Heizsperre aufzuheben.

Vorzeitiges Beenden gibt die reguläre Steuerung wieder frei. Es verkürzt ebenso
wie Betrieb-AUS den Bemessungszeitraum für die nächste Kühlung nicht: Nur eine
vollständig durchlaufene Kühlung setzt dessen Beginn auf ihren Abschluss.

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
und gewichtete Anteile des Kühlzyklus ([Speicherung](speicherung.md)).
