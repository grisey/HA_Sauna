# Entscheidungen und offene Punkte

Grundlage: Nutzerfestlegungen der Besprechung, fortgeschrieben am 18.09.2026.
Der [Messkandidat](kandidat.md) dokumentiert die eingefrorene Kalibrierung.
[Gangmodell](gangmodell.md), [Betrieb](betrieb.md) und
[Parameter](parameter.md) halten die fachlichen Regeln und deren Status fest.
Diese Fortschreibung betrifft die Dokumentation, nicht die Implementierung.

## Vereinbart

**Aufbau:** eigene Home-Assistant-Integration mit eigenem Thermostat und einem
zentralen Python-Ablaufkern. Messung, Erkennung, Gang/Session, Temperaturregelung,
Bedienung und Speicherung haben getrennte Zuständigkeiten.

**Sensoren:** Kanal 3 auf Kopfhöhe der obersten Bank, Kanal 6 etwa 20–30 cm
darunter. Beide sind im Normalbetrieb fest eingebunden. Bei Ausfall eines
Sensors erfolgt Weiterbetrieb mit dem verbleibenden Kanal und Fehlermeldung.
Die Temperaturen werden weder gleichgesetzt noch mit einem erfundenen festen
Höhenoffset umgerechnet. IBS wird nicht verwendet.

**Vorbereitung und Gang:** Durchlüften und anschließende Schließung können die
Personenfrüherkennung stützen. Das Personenmuster legt einen vorläufigen Gang
an, der bereits als Saunagang angezeigt und behandelt wird. Ausschließlich
ein Aufguss bestätigt den Gang. Er kann einen verpassten Start unmittelbar
bestätigt nachholen. Im unveränderten Kandidaten benötigt nur der schwache
Personenpfad vorbereitendes Durchlüften.

**Türereignisse:** schnelle Türmeldungen, Durchlüftungseinordnung und Gangablauf
bleiben getrennt. Kurze Türbetätigung erhält Gang, Zeitbasis und Aufgüsse.
Bestätigtes Durchlüften nach Aufguss ermöglicht den regulären Abschluss.
Ein Türereignis allein belegt keinen Öffnungszweck und keine Personenzahl.

**Zeitbezug und Bestätigungsfrist:** zugeordneter Beginn bei der Türschließung,
erste Erkennung und Aufgussbestätigung bleiben getrennt. Für die vorläufige
Gangerkennung ist eine konfigurierbare Bestätigungsfrist vorgesehen. Als
angemessen wurden 12 oder 13 Minuten ab der zugehörigen Türschließung benannt;
ein fester Ausgangswert ist noch nicht ausgewählt. Die ungefähre Gangdauer von
15 Minuten ist kein automatisches Gangende. Die konkreten Folgen eines
unbestätigten Fristablaufs bleiben als Vorschlag gekennzeichnet.

**Session und Sessiongrenze:** Die Session ist das übergeordnete Objekt für alle
sessionbezogenen Laufzeitobjekte. Nach Ablauf der konfigurierten Frist seit dem
Ausschalten des Saunabetriebs beginnt beim nächsten Einschalten eine vollständig
neue Session mit neu initialisierten Unterobjekten. Das ist keine bloße
Heizzeit-Rücksetzung. Bei früherem Wiedereinschalten bleibt dieselbe Session
bestehen. Konfiguration, archivierte Daten und übergeordnete Schutzfunktionen
bleiben vom Sessionwechsel getrennt. Normale Heizpausen und Zwangskühlung sind
kein Ausschalten des Saunabetriebs und lösen keinen Sessionwechsel aus.

**Ausschalten im Gang:** Ein ausdrücklicher Ausschaltbefehl beendet den Gang
sofort, auch wenn er bereits bestätigt ist. Ende und Grund „ausgeschaltet“
werden festgehalten. Bei rechtzeitigem Wiedereinschalten wird ausschließlich
die Session fortgesetzt, nicht der ausgeschaltete Gang.

**Physischer Schalter:** Der Shelly-Schalter soll sich wie ein einfacher
An-/Ausschalter für den Saunabetrieb anfühlen. Maßgeblich ist die Betriebsfreigabe,
nicht der momentane Heizrelaiszustand oder die noch fortsetzbare Session.

**Heizung:** engere Hysterese im eigenen Thermostat. Bereits im vorläufigen
Gang werden reguläre Hysterese- und betriebliche Ablaufabschaltungen unterdrückt.
Sicherheitsabschaltung und ausdrückliches Ausschalten bleiben übergeordnet.
Dynamische Solltemperaturnachführung innerhalb eines Gangs ist verworfen.

**Anheizen:** Der Sessionmerker `aufgeheizt` wird beim temperaturbedingten
Übergang zu `idle` an der oberen Hysteresegrenze gesetzt und innerhalb derselben
Session beibehalten. Die wirksame Heizdauer wird aus Anheizdauer (Ausgangspunkt
100 Minuten) und späterer kürzerer Dauer gewählt.

**Heizlaufzeit:** Nur tatsächliches Heizen wird seit der letzten Rücksetzung
aufsummiert. Kurze Auszeiten zählen nicht mit und setzen die Summe nicht zurück.
Eine zusammenhängende Ausschaltpause des Ofens von mindestens der konfigurierten
Rücksetzdauer setzt diese Summe zurück. Diese lokale Rücksetzung innerhalb einer
Session ist vom Wechsel des gesamten Sessionobjekts getrennt. Die Zeitrechnung
ist damit geklärt.

**Getrennte Zeitmechanismen:** Die Session-Unterbrechungsfrist gehört zum
Lebenszyklus der Session. Der Thermostat-Cooldown gehört zur normalen
Wiederanlaufhemmung nach temperaturbedingter Abschaltung. Die Zwangskühlung
ist ein eigenständiger Ablauf nach ausgeschöpfter Heizlaufzeit. Auslöser,
Bedeutung und Parameter bleiben getrennt. Die Zwangskühlungsdauer wird weder
mit der Sessionfrist noch mit Thermostat-Cooldown oder Rücksetz-Auszeit der
Heizzeitführung gleichgesetzt oder aus ihnen abgeleitet.

**Parameter und Anzeige:** notwendige Werte direkt in der Integration
konfigurierbar machen. Genau eine konsumierte Quelle je Einstellung, gemeinsame
Validierung und Speicherung. Sinnvolle Relationen statt unnötiger unabhängiger
Absolutwerte verwenden; fachlich verschiedene Fristen nicht zusammenlegen.
Abgeleitete Werte und Restzeiten nur lesbar anzeigen. Phase und Bestätigungsstand
stammen aus dem Ablaufkern. Die automatische relative Erkennungsanpassung ist
noch kein freigegebener Ersatzdetektor.

**Daten:** gesicherter Betriebszustand und Sessionarchiv unabhängig vom Recorder.
Speicherverfahren und Datenumfang werden gesondert besprochen.

## Korrekturen früherer Annahmen

Die Gefäßentnahme um 22:39:40 ist das zusätzlich bestätigte 15. Türereignis der
Referenzsession. Nicht jede Öffnung nach Aufguss beendet einen Gang. Manuelle
Eingriffe waren einmalige Reparaturversuche, keine konkurrierenden Sollvorgaben
für die Automatik. Normale Hystereseabschaltungen im Gang wurden durch die
vereinbarte zustandsabhängige Heizbehandlung ersetzt.

Die frühere Überlegung, einen Gang über ausdrückliches Aus-/Wiedereinschalten
fortzusetzen, gilt nicht mehr. Fortgesetzt werden kann nur die Session.
Ausschaltzeiten sind keine Heizzeiten. Diese beiden Fragen sind entschieden
und werden nicht erneut als Varianten angeboten.

Die vorgeschlagene Kopplung von Zwangskühlungsdauer und Rücksetz-Auszeit ist
verworfen. Die Frist nach dem Ausschalten des Saunabetriebs bestimmt die
Sessiongrenze und damit die Neuinitialisierung aller sessionbezogenen
Unterobjekte, nicht lediglich die Heizzeitsumme. Session-Unterbrechung,
Thermostat-Cooldown und Zwangskühlung sind keine Varianten derselben Pause.

## Noch zu klären, in Besprechungsreihenfolge

1. **Heizregelung:** eigenständige Dauer und noch offene Ablaufdetails der
   Zwangskühlung; Wechsel der Heizzeitgrenze beim bereits begonnenen Abschnitt,
   Gangbeginn bei bestehender Kühlsperre und Verhalten nach Gangende. Hysterese,
   spätere Heizdauer, lokale Rücksetz-Auszeit, Thermostat-Cooldown, Nachlauf,
   Schutzgrenzen und Ersatztemperatur für Kanal 3 bleiben zu konkretisieren.
   Die verworfene Zusammenlegung der Fristen ist kein offener Vorschlag mehr.
2. **Verbleibende Gang-/Sessiondetails:** Folgen eines unbestätigten
   Fristablaufs beziehungsweise erneuten Durchlüftens, Behandlung eines danach
   erkannten Aufgusses, rückwirkende Abschlusszeit, Zählung eines durch
   Ausschalten beendeten Gangs, Wert der Sessionfrist und Wiederaufnahme eines
   Nachlaufs. Bereits beendete Gänge werden nicht wieder aufgenommen.
3. **Bedienung und Parameter:** Bedeutung von `bereit`, endgültige
   Parametergrenzen und Einheiten, Änderungen während laufender Fristen,
   geprüfte Relationen, Start-/Endstufen, Shelly-Eingangsanbindung, Licht,
   Musik und Benachrichtigungen.
4. **Speicherung:** Messauflösung, Verfahren, Wiederanlauf nach HA-Neustart,
   Aufbewahrung, Export und Sicherung; siehe [Speicherblock](speicherung.md).

Die Besprechung erfolgt anhand zusammenhängender Regeln und Fristen, jeweils
zu einem Thema. Bereits geklärte Selbstverständlichkeiten werden nicht erneut
abgefragt. Bestandswerte der alten Automationen sind keine automatisch
beschlossenen neuen Produktivwerte. Die Dokumentation kennzeichnet weiterhin
Vereinbarung, Vorschlag, Messbefund und tatsächlichen Implementierungsstand.
