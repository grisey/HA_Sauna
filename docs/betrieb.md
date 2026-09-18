# Betrieb, Sessiongrenze und Heizlaufzeit

Stand: 18.09.2026, einschließlich der Korrektur zur übergeordneten Session.
Diese Festlegungen ergänzen das [Gangmodell](gangmodell.md). Sie dokumentieren
die Besprechung; der Session- und Heizzeitautomat ist damit noch nicht implementiert.

## Saunabetrieb und Bedienung

Der physische Schalter über den Shelly soll sich wie ein einfacher
An-/Ausschalter für den **Saunabetrieb** verhalten. Er schaltet logisch den
Betrieb, nicht unmittelbar den momentanen Heizrelaiszustand und nicht die
noch fortsetzbare Session. Eine normale Thermostatpause bedeutet daher nicht
„Saunabetrieb aus“. Die konkrete Shelly-Eingangskonfiguration bleibt Teil der
späteren Geräteanbindung.

Physischer Schalter und Bedienung über die Oberfläche verwenden dieselbe
Betriebsentscheidung. Ein ausdrücklicher Ausschaltbefehl beendet den Betrieb
sofort und hat Vorrang vor der Heizbehandlung eines Saunagangs.

## Session als übergeordnetes Laufzeitobjekt

**Nach Ablauf der konfigurierten Frist seit dem Ausschalten des Saunabetriebs
beginnt beim nächsten Einschalten eine vollständig neue Session. Bei früherem
Wiedereinschalten bleibt es dieselbe Session.**

Die Session besitzt sämtliche sessionbezogenen Laufzeitobjekte. Mit einer neuen
Session werden diese gemeinsam mit ihren definierten Anfangswerten neu angelegt.
Die Rücksetzung ist eine Folge des Sessionwechsels, keine auf die Heizzeitsumme
beschränkte Einzelaktion. Alte Fristen und Ereigniszuordnungen dürfen nicht in
eine neue Session hineinwirken. Konfiguration, archivierte Sessions und
übergeordnete Schutzfunktionen sind vom Sessionwechsel getrennt.

Die Frist beginnt beim Ausschalten des **Saunabetriebs**. Eine normale
Thermostatpause oder eine Zwangskühlung löst keinen Sessionwechsel aus. Bei
rechtzeitiger Fortsetzung bleibt das bisherige Sessionobjekt bestehen; ein
bereits durch Ausschalten beendeter Gang wird dadurch nicht wieder aktiviert.

Die Dauer der Session-Unterbrechungsfrist ist ein konfigurierbarer Parameter;
es wurde noch kein fester Minutenwert ausgewählt. Eine Wiederaufnahme nach
HA-Neustart ist damit nicht entschieden.

## Ausschalten während eines Gangs

**Ausdrückliches Ausschalten beendet jeden laufenden Gang sofort**, unabhängig
davon, ob er vorläufig erkannt oder durch einen Aufguss bestätigt ist. Der
Ausschaltzeitpunkt ist sein Ende, der Beendigungsgrund lautet „ausgeschaltet“.
Beginn, Bestätigungsstand und bereits erkannte Aufgüsse bleiben dokumentiert.
Eine noch laufende Aufgussbestätigungsfrist dieses Gangs endet ebenfalls.

Ein solcher Gang wird beim Wiedereinschalten innerhalb der Sessionfrist nicht
wieder aktiviert. Fortsetzbar ist die übergeordnete Session, nicht der durch
Ausschalten beendete Gang. Die seltene Nutzung dieses Bedienwegs ändert die Regel
nicht. Es wird daraus kein regulärer, durch Aufguss und Durchlüften festgestellter
Gangabschluss abgeleitet. Die Anrechnung auf Gangzähler und Temperaturstufen wird
nicht durch eine neue stillschweigende Regel ergänzt.

## Heizlaufzeit innerhalb der Session

**Heizlaufzeit ist die Summe der tatsächlichen Heizzeiten seit der letzten
Rücksetzung. Eine zusammenhängende Ausschaltpause des Ofens von mindestens der
konfigurierten Rücksetzdauer setzt diese Summe zurück.**

Kurze Ausschaltzeiten werden nicht mitgezählt und löschen die bereits angefallene
Heizzeit nicht. Es zählt die bestätigte Heizaktivität, nicht allein eine
Heizanforderung, eine GUI-Phase oder die verstrichene Sessionzeit. Die technische
Rückmeldungs- und Fehlerdiagnose wird bei der Aktoranbindung festgelegt.

Die Rücksetzung dieser Summe während einer Session ist eine lokale Regel der
Heizzeitführung. Sie setzt weder das übergeordnete Sessionobjekt noch dessen
übrige Laufzeitobjekte zurück. Sie legt auch keine Dauer der Zwangskühlung fest.
Eine neue Session initialisiert dagegen sämtliche zu ihr gehörenden Laufzeitobjekte.

## Anheizen und spätere Heizzeitgrenze

Vereinbart ist der Sessionmerker `aufgeheizt`: Er wird beim temperaturbedingten
Übergang zu `idle` an der oberen Hysteresegrenze gesetzt. Innerhalb derselben
Session bleibt er gesetzt. Für die Heizzeitbegrenzung stehen eine Anheizdauer
(Ausgangspunkt 100 Minuten) und eine spätere kürzere Dauer zur Verfügung.
Die wirksame Dauer wird daraus abgeleitet, nicht unabhängig nochmals eingestellt.

Der genaue Wechsel auf die kürzere Grenze bei einem bereits begonnenen, noch
nicht zurückgesetzten Heizzeitabschnitt bleibt zu besprechen. Tatsächliche
Heizzeiten weiterzuzählen ist bereits entschieden und wird nicht erneut zur
Auswahl gestellt.

## Eigenständige Bedeutung der Zeitmechanismen

**Session-Unterbrechungsfrist:** bestimmt nach dem Ausschalten des Betriebs,
ob beim nächsten Einschalten dieselbe oder eine vollständig neue Session gilt.
Ihr Wirkungsbereich ist das gesamte Sessionobjekt mit seinen Unterobjekten.

**Thermostat-Cooldown:** Wiederanlaufhemmung im Zusammenhang mit der normalen
temperaturbedingten Abschaltung an der Hysteresegrenze. Er gehört zur laufenden
Temperaturregelung und beendet keine Session.

**Zwangskühlung:** eigenständiger Ablauf bei ausgeschöpfter zulässiger
Heizlaufzeit innerhalb einer Session. Sie hat eigene Auslöse-, Dauer- und
Beendigungsregeln. Sie ist weder ein Ausschalten des Saunabetriebs noch ein
Cooldown nach Erreichen der Solltemperatur und setzt die Session nicht zurück.
Laufzeitbedingte Zwangskühlung darf einen erkannten Saunagang nicht unterbrechen;
Schutzabschaltungen und ausdrückliches Ausschalten bleiben übergeordnet.

Diese fachlich verschiedenen Fristen bleiben getrennt parametriert. Auch die
lokale Rücksetz-Auszeit der Heizzeitführung ist kein Ersatz für eine dieser
Fristen. Der Vorschlag, Zwangskühlungsdauer und Rücksetz-Auszeit zusammenzulegen,
ist vom Nutzer ausdrücklich verworfen. Gleiche Zeitwerte würden keine gemeinsame
Bedeutung und keine automatische Anrechnung begründen.

Die konkrete Zwangskühlungsdauer und noch offene Ablaufdetails werden als
eigenständiges Thema besprochen. Eine gemeinsame Kühl-/Ausschalt-/Sessionfrist
wird nicht eingeführt.
