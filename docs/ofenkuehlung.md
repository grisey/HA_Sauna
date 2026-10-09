# Ofenkühlung

## Auslösung

Im Automatikbetrieb folgt die Kühlung auf einen abgeschlossenen bestätigten Gang:
beim Proxyverfahren nach Durchlüften, bei direkter Präsenz nach belegtem Austritt
([Gangmodell](gangmodell.md)). Betrieb-AUS verwendet stattdessen den
[Lichtnachlauf](betrieb.md#licht). In Manuell bleibt die Bedienwahl maßgeblich.

## Laufzeit und Abschluss

Die Kühlanforderung schaltet den Ofen aus und sperrt erneutes Heizen. Erst
bestätigtes Schütz-AUS startet die Uhr und legt die Kühldauer fest. Diese bleibt
für den Kühlzyklus unverändert. Nur bestätigte AUS-Zeit zählt; Schütz-EIN oder
unbekannte Rückmeldung pausieren die Uhr, ohne die Heizsperre aufzuheben.

Vorzeitiges Beenden gibt die reguläre Steuerung wieder frei. Es verkürzt ebenso
wie Betrieb-AUS den Bemessungszeitraum für die nächste Kühlung nicht: Nur eine
vollständig durchlaufene Kühlung setzt dessen Beginn auf ihren Abschluss.
Bei der ersten Kühlung gilt der Sitzungsbeginn.

## Berechnung der Dauer

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
Basisdauer. Erst werden die gewichteten Zeiten verrechnet, dann wird begrenzt.
Eine gewichtete Minute Bereitschaft gleicht somit \(r\) gewichtete Heizminuten aus.
Die Höchstdauer darf nicht unter der Basisdauer liegen.

Unbekannte Schützzeiten liefern keine Bereitschaftsgutschrift und kennzeichnen
die Berechnung als unvollständig. Das Archiv bewahrt Zeitraum, Einstellungen
und gewichtete Anteile des Kühlzyklus ([Speicherung](speicherung.md)).
