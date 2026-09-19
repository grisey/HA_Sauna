# Betrieb, Sessiongrenze und Heizlaufzeit

## Vorrangige Nutzerpräzisierung vom 19.09.2026

Diese Regeln ersetzen die älteren Aussagen zur einheitlichen Heizzeit und zur
Thermostatbandbreite weiter unten:

- Solltemperatur bezieht sich auf den oberen Sensor während des Gangs.
  Bereitschaftsziel = Solltemperatur + einstellbarer Aufschlag (Standard 5 °C).
  Erst beim Erreichen dieses Ziels wird Bereitschaft gemeldet. Ausschalten am
  Bereitschaftsziel, Wiedereinschalten um die einstellbare Hysterese darunter
  (Standard 3 °C), unter Beachtung des separaten Thermostat-Cooldowns.
- Tatsächliches Heizbudget zunächst standardmäßig 90 Minuten. Nach dem ersten
  abgeschlossenen Kühlvorgang derselben Session wird der eingestellte Wert
  einmalig um standardmäßig 30 Minuten reduziert. Danach bleibt er konstant;
  eine neue Session beginnt wieder mit dem ursprünglichen Wert. Die verbrauchte
  Heizzeit des nächsten Intervalls beginnt bei null. Eine reine lokale
  Rücksetzung nach Ofen-Auszeit gilt nicht als abgeschlossene Kühlung.
- Zwangskühlung dauert standardmäßig 15 Minuten. Nachlauf hält den Ofen aus
  und sperrt alle neuen Gangstarts. Nachlaufanrechnung bleibt vollständig und
  einmalig, sein Endzeitpunkt bleibt auch bei Betrieb-Aus unverändert.
- Alle genannten Zahlen sind änderbare Ausgangswerte in der zentralen
  Parameterverwaltung. Die Grundkonfiguration bleibt während einer gesamten
  Session einschließlich kurzer Betriebsunterbrechungen gesperrt.
- Fehlende Schutzwerte werden nicht durch vermeintlich sichere Defaults ersetzt.
  Ohne gültige obere Temperatur und konfigurierte Abschalttemperatur bleibt
  die Heizentscheidung aus; Ein-Sensor-Erkennung ist davon getrennt.


Stand: 18.09.2026, einschließlich der präzisierten Reihenfolge Nachlauf und
Restzwangskühlung. Diese Festlegungen ergänzen das [Gangmodell](gangmodell.md).
Sie dokumentieren die Besprechung; Session-, Heizzeit- und Kühlungssteuerung
sind damit noch nicht implementiert.

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
es wurde noch kein fester Minutenwert ausgewählt. Automatische Fortsetzung nach
HA-Neustart ist keine Pflichtanforderung und soll nur bei sehr einfacher
Umsetzung ergänzt werden. Die dauerhafte Historie bleibt davon unabhängig;
siehe [Speicherung](speicherung.md).

## Ausschalten und Gangzählung

**Ausdrückliches Ausschalten beendet jeden laufenden Gang sofort**, unabhängig
davon, ob er vorläufig erkannt oder durch einen Aufguss bestätigt ist. Der
Ausschaltzeitpunkt ist sein Ende, der Beendigungsgrund lautet „ausgeschaltet“.
Beginn, Bestätigungsstand und bereits erkannte Aufgüsse bleiben dokumentiert.
Eine noch laufende Aufgussbestätigungsfrist dieses Gangs endet ebenfalls.
Ein solcher Gang wird beim Wiedereinschalten nicht wieder aktiviert.

**Jeder beendete Gang mit mindestens einem zugeordneten Aufguss zählt genau
einmal, unabhängig vom Beendigungsgrund.** Das gilt auch beim Ausschalten,
folgt aber aus derselben allgemeinen Bestätigungszuordnung. Ein zusätzlicher
Sonderzähler oder ein unabhängig gesetzter Zählberechtigungsmerker ist nicht
vorgesehen. Ende und Bestätigung sind getrennte Voraussetzungen der Zählung;
weder ein vorläufiger Gang noch ein weiterer Aufguss erhöht allein den Zähler.

## Einheitliche Heizzeitgrenze

**Für sämtliche Heizabschnitte gilt dieselbe einstellbare Heizzeitgrenze.**
Die frühere Aufteilung in Anheizdauer und kürzere Folgedauer entfällt. Die
Heizzeitwahl benötigt deshalb keinen Aufheizmerker. Eine etwaige Anzeige des
erstmaligen Aufheizens ist hiervon getrennt und ändert die Heizzeitgrenze nicht.

Hysterese und normaler Thermostat-Cooldown sind unmittelbar konfigurierbare
Variablen. Weder dafür noch für die einheitliche Heizzeit oder die
Zwangskühlungsdauer wird mit dieser Dokumentation ein neuer Zahlenwert festgelegt.

## Heizlaufzeit innerhalb der Session

**Der Heizzeittimer läuft ausschließlich während tatsächlichen Heizens und
pausiert bei idle.** Normale Idle-Zeiten sind damit vollständig berücksichtigt.
Sie verlängern nicht zusätzlich das zulässige Heizbudget und werden auch nicht
zu einem weiteren Prozentsatz auf die Zwangskühlungsdauer gutgeschrieben.
Die diskutierte 50-Prozent-Anrechnung wird nicht eingeführt.

Es zählt die bestätigte Heizaktivität, nicht allein eine Heizanforderung, eine
GUI-Phase oder die verstrichene Sessionzeit. Die technische Rückmeldungs- und
Fehlerdiagnose wird bei der Aktoranbindung festgelegt.

Die zuvor vereinbarte lokale Rücksetzung bleibt erhalten: Eine zusammenhängende
Ausschaltpause des Ofens von mindestens der konfigurierten Rücksetzdauer setzt
die Heizzeitsumme zurück. Kürzere Pausen löschen die bisher angefallene Heizzeit
nicht. Diese Regel betrifft nur die Heizzeitführung innerhalb der Session;
sie setzt weder die Session noch deren übrige Laufzeitobjekte zurück und legt
keine Dauer der Zwangskühlung fest.

## Zwangskühlung und Gangstart

**Eine laufende Zwangskühlung verhindert den Start eines neuen Saunagangs.
Ein bereits laufender Saunagang wird dagegen nicht durch Zwangskühlung
unterbrochen.** Das gilt bereits für einen vorläufig erkannten Gang mit der
vereinbarten Gang-Heizbehandlung.

Wird die Heizzeitgrenze während eines Gangs erreicht, bleibt die Kühlung bis
zum Gangende ausstehend. Anschließend gilt die reguläre Folge:

**Gangende → Nachlauf → gegebenenfalls verbleibende Zwangskühlzeit.**

Es wird weder schon während des Gangs gekühlt noch nach dem Nachlauf nochmals
eine vollständige Kühlfrist angesetzt. Der Ablauf verwendet die unten beschriebene
Nachlaufanrechnung; ein zusätzlicher Sondertimer für diesen Fall ist nicht nötig.
Eine neue Gangerkennung hebt eine bereits laufende Zwangskühlung nicht auf.

**Stark gedimmtes Licht kennzeichnet die laufende Zwangskühlung.** Daraus wird
kein unbedingter Vorrang gegenüber einem schon laufenden Gang abgeleitet.
Schutzabschaltungen und ausdrückliches Ausschalten bleiben übergeordnet.

## Nachlauf und Anrechnung

**Ein bereits laufender Nachlauf wird durch eine Betriebsunterbrechung nicht
verändert.** Sein ursprünglicher Endzeitpunkt bleibt erhalten; Aus- und
Wiedereinschalten pausiert ihn nicht und startet ihn nicht neu. Diese Fortführung
betrifft das Laufzeitobjekt seiner Session. Bei einem echten Sessionwechsel
werden keine alten Nachlaufobjekte in die neue Session übernommen.

**Die verstrichene Nachlaufdauer wird vollständig auf die zugehörige
Zwangskühlungsdauer angerechnet.** Angerechnet wird bereits vergangene Zeit,
nicht eine noch ausstehende geplante Nachlaufdauer. Nach Ende des Nachlaufs gilt:

`Restkühlzeit = max(0, konfigurierte Zwangskühlungsdauer − angerechnete Nachlaufdauer)`

Nur eine positive Restzeit ergibt einen anschließenden Zwangskühlungsabschnitt.
Deckt der Nachlauf die Kühlvorgabe bereits ab, entfällt dieser Abschnitt.
Derselbe Zeitabschnitt darf im selben Kühlvorgang nicht zweimal angerechnet werden.
Eine ausstehende Kühlanforderung ist dabei von einer tatsächlich laufenden
Zwangskühlungsphase zu unterscheiden.

Nachlauf und Zwangskühlung bleiben eigenständige Abläufe mit eigenen Parametern.
Die ausdrücklich vereinbarte Anrechnung ersetzt keine Sessionregel und begründet
keine zusätzliche Anrechnung normaler Idle-Zeiten.

## Eigenständige Bedeutung der Zeitmechanismen

Die Session-Unterbrechungsfrist bestimmt den Lebenszyklus des übergeordneten
Sessionobjekts. Der Thermostat-Cooldown gehört zur normalen Wiederanlaufhemmung
nach temperaturbedingter Abschaltung. Die Zwangskühlung ist ein eigenständiger
Ablauf nach ausgeschöpfter Heizlaufzeit; sie ist kein Ausschalten des Betriebs.

Diese Fristen bleiben getrennt parametriert. Die lokale Heizzeit-Rücksetz-Auszeit
wird mit keiner von ihnen gleichgesetzt. Die Nachlaufanrechnung ist eine konkret
vereinbarte Beziehung zwischen Abläufen, keine Zusammenlegung ihrer Bedeutung.
Darstellung und Archivierung: [Sessionansicht](darstellung.md),
[Speicherung](speicherung.md).
