# Gangmodell: Vorbereitung, vorläufige Erkennung und Bestätigung

Stand: 18.09.2026. Die Messverfahren und Prüfwerte des festgehaltenen
[Kandidaten](kandidat.md) bleiben unverändert. Diese Fassung beschreibt die
Bedeutung seiner Ereignisse und ergänzt die jüngsten Ablaufentscheidungen.
Bestätigungsfristen, Betriebsfreigabe, Ausschaltverarbeitung und allgemeine
Gangzählung sind Anforderungen für die weitere Umsetzung, noch keine
hinzugefügten Funktionen des Python-Kerns.

## 1. Begriffe und Zuständigkeit

Die Session umfasst den gesamten Saunabetrieb. Ein Saunagang ist ein
Nutzungsabschnitt innerhalb der Session. Ein tatsächlicher Gang beinhaltet
nach Nutzerangabe einen Aufguss.

Die Sensoren liefern Hinweise auf diesen Ablauf. Deshalb unterscheiden wir
zwischen vorläufig erkanntem und durch Aufguss bestätigtem Gang. Beides ist
derselbe Gang mit derselben ID und Startzeit, nicht zwei aufeinanderfolgende
Saunagänge. Auch ein starkes Personenmuster bleibt eine Vorstufe.

Eine Durchlüftungsbestätigung bestätigt die Einordnung einer Türöffnungsepisode.
Eine Gangbestätigung entsteht ausschließlich durch einen erkannten Aufguss.

## 2. Ablauf und Anzeige

Die folgende Zuordnung beschreibt zulässige Gangstarts. Eine bereits laufende
Zwangskühlung verhindert einen neuen Gangstart, auch wenn ein Messdetektor ein
Personen- oder Aufgusssignal liefert. Erkennung und Ablaufentscheidung bleiben
getrennt; die Aufgussunabhängigkeit bezieht sich auf die Personenfrüherkennung,
nicht auf das Umgehen einer laufenden Kühlsperre.

| Erkenntnis | Verarbeitung | Vorgesehene Anzeige |
|---|---|---|
| Durchlüften erkannt und Tür anschließend geschlossen | Vorbereitung und zugehörige Schließung festhalten. Es besteht noch kein Gang. | Noch kein Saunagang. |
| Personenmuster erkannt | Vorläufigen Gang anlegen; Beginn der zugehörigen Schließung zuordnen. | Saunagang, Bestätigungsstand vorläufig; Dauer ab zugeordnetem Beginn. |
| Erster Aufguss erkannt | Bestehenden Gang bestätigen; ID, Beginn und erste Erkennungszeit erhalten. | Derselbe Saunagang, bestätigt. |
| Aufguss ohne vorherige Personenfrüherkennung | Gang unmittelbar bestätigt anlegen; Beginn derselben Schließungsepisode zuordnen. | Saunagang, bestätigt. |
| Weitere Aufgüsse | Ereignisse demselben Gang zuordnen. | Derselbe bestätigte Gang. |

Die Phase lautet bereits ab Personenfrüherkennung Saunagang. Der
Bestätigungsstand ist aus den Belegen abgeleitet und kein unabhängig
bedienbarer Phasenschalter. Der Aufguss bestätigt das ganze zugeordnete
Intervall, setzt dessen Laufzeit aber nicht neu an.

## 3. Vorbereitung

Durchlüften allein belegt keine Personenanwesenheit. Der letzten abgeschlossenen
Lüftungsepisode wird zusammen mit dem anschließenden Personenmuster Bedeutung
für einen Gang gegeben. Ihre Referenz bleibt am angelegten Gang erhalten.

Im unveränderten Kandidaten benötigt der schwache Personenpfad bestätigtes
Durchlüften und anschließende Schließung. Der starke Pfad kann ohne diesen
Zusatzkontext vorläufig erkennen. Aufguss bestätigt unabhängig von beiden.

Eine neue Öffnung ersetzt den ungenutzten Vorbereitungskontext. Ein bereits
angelegter Gang behält Beginn und Herkunft. Es wird keine zusätzliche
Ablauffrist für den Vorbereitungskontext eingeführt.

## 4. Bestätigungsfrist

Für einen ungefähr 15-minütigen Saunagang wurden 12 oder 13 Minuten ab der
zugeordneten Türschließung als angemessene Aufgussbestätigungsfrist benannt.
Der Wert wird direkt in der Integration einstellbar, nicht im Code festgelegt.
Die endgültige Wahl des Ausgangswerts bleibt offen.

**Fristende = zugeordneter Gangbeginn + eingestellte Bestätigungsfrist.**

Eine spätere Personenfrüherkennung erhält nur die verbleibende Zeit. Kurze
Türbetätigung desselben Gangs und zusätzliche Personenmeldungen verschieben
seinen Beginn und damit die Frist nicht. Die ungefähren 15 Minuten bewirken
keinen automatischen Abschluss eines bestätigten Gangs.

### Festlegung vom 19.09.2026 und Umsetzung

Bei Aufguss innerhalb der Frist ist die Bestätigungsanforderung erfüllt.
Ohne Aufguss wird die vorläufige Zuordnung vollständig aufgehoben, als sei kein
Gang gestartet worden. Kein abgeschlossener Gang, keine Zählung und kein
Gang-Nachlauf entstehen daraus. Ein weiteres Personensignal derselben Episode
vergibt keine neue Frist. Ein späterer eindeutiger Aufguss bleibt verwertbar
und kann die erhaltene Schließung als Beginn verwenden.

Bestätigtes Durchlüften vor dem ersten Aufguss hebt den vorläufigen Gang ebenso
vollständig auf. Der vorhandene Gangkern führt diese Regeln selbst aus;
`retracted` enthält nur die Diagnose der verworfenen Erkennung. Die tatsächliche
Gerätewirkung wird separat im Regelungs-/Adapterpaket geprüft.

## 5. Heizbehandlung, Fortsetzung und Ende

Bereits der vorläufige Gang erhält ab seiner Erkennung die vereinbarte
Gang-Heizbehandlung: reguläre Hysterese- und betriebliche Ablaufabschaltungen
werden unterdrückt. Aufguss ist dafür keine zusätzlich abzuwartende Freigabe.
Schutzabschaltung und ausdrückliches Ausschalten bleiben übergeordnet.

Eine während eines laufenden Gangs fällige Zwangskühlung unterbricht diesen
Gang nicht, sondern bleibt bis zu seinem Ende ausstehend. Umgekehrt darf
während einer bereits laufenden Zwangskühlung kein neuer Gang beginnen.

Eine Türöffnung allein beendet keinen Gang. Kurze Türbetätigung erhält ID,
Beginn, Bestätigungsstand und Aufgüsse. Sie besagt nicht, wie viele Personen
die Sauna verlassen haben. Bestätigtes Durchlüften nach einem zugeordneten
Aufguss schließt den Gang regulär ab.

**Ausdrückliches Ausschalten beendet auch einen laufenden Gang sofort.**
Sein Ende ist der Ausschaltzeitpunkt, sein Grund „ausgeschaltet“. Zeitbasis,
Bestätigungsstand und bisherige Aufgüsse bleiben erhalten. Eine offene
Bestätigungsfrist endet. Beim Wiedereinschalten wird dieser Gang nicht
wiederhergestellt, auch wenn dieselbe Session fortsetzbar bleibt.

### Einheitliche Gangzählung

**Ein beendeter Gang zählt genau dann und genau einmal, wenn ihm mindestens
ein Aufguss zugeordnet ist.** Der Beendigungsgrund ändert diese Voraussetzung
nicht. Insbesondere zählt auch ein durch Ausschalten beendeter bestätigter Gang.

Die Zahl ergibt sich aus den beendeten Gangobjekten und ihren Aufgusszuordnungen.
Ein zusätzlicher Aufguss innerhalb desselben Gangs zählt nicht nochmals.
Vorläufige Erkennung oder Aufgussbestätigung ohne Gangende erhöhen den Zähler
noch nicht. Ein separater Merker für „zählbar“ und eine Sonderlogik nur für
manuelles Ausschalten sind damit unnötig.

Ein bereits laufender Nachlauf bleibt bei einer Betriebsunterbrechung zeitlich
unverändert; seine verstrichene Dauer wird auf die zugehörige Zwangskühlung
angerechnet. Einzelheiten und Sessiongrenze: [Betrieb](betrieb.md).

## 6. Datenmodell und Implementierungsgrenze

`Timeline.active` enthält im vorhandenen Fachkern den vorläufigen oder
bestätigten Gang. `Gang.confirmation`, `confirmed_at` und
`confirmation_event_id` werden ausschließlich aus den zugeordneten Aufgüssen
abgeleitet; sie sind keine weiteren schreibbaren Wahrheiten.

| Bezug | Vorhandenes Feld |
|---|---|
| Vorbereitendes Durchlüften | `preparation_event_id` |
| Für den Beginn verwendete Türschließung | `start_source_event_id` |
| Erste, gegebenenfalls vorläufige Erkennung | `recognition_event_id`, `recognition_kind` |
| Erster bestätigender Aufguss | `confirmation_event_id`, `confirmed_at` |
| Zugeordnete Aufgüsse | `infusion_events` |

Durchlüftung wird zunächst in `open_ventilation`, nach Schließung in
`preparation` geführt. Neue Öffnung ersetzt nur den ungenutzten Kontext;
`Gang.preparation_event_id` eines bestehenden Gangs bleibt erhalten.

Die neuen Anforderungen zu Fristen, Sessiongrenze, ausdrücklichem Ausschalten,
Zählung sämtlicher beendeter bestätigter Gänge, Heizzeitsumme, Zwangskühlung und
Nachlauf sind dokumentiert, nicht im Code umgesetzt. Das vorhandene Modell
führt bisher nur den regulären Abschluss aus. Der Code schaltet keine Geräte.
Zeitangaben: [Zeitmodell](zeitmodell.md). Dauerhafte Ablage:
[Speicherblock](speicherung.md).
