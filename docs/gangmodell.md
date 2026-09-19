# Gangmodell

Der führende Kern liegt in `core/timeline.py`, fachliche Fristen in
`core/controller.py`. Erkennung, Zuordnung und Aktorsteuerung sind getrennt.

Personenfrüherkennung legt einen vorläufigen Gang an. Ein zugeordneter Aufguss
bestätigt denselben Gang; Bestätigung wird aus den Aufgussobjekten abgeleitet.
ID und Beginn bleiben unverändert. Ohne Früherkennung kann ein Aufguss den Gang
unmittelbar bestätigt anlegen. Ein schwaches Personensignal benötigt den
vorherigen Durchlüftungskontext, starkes Signal und Aufguss nicht.

Der Beginn gehört zur passenden Türschließung derselben Episode und Session.
Ohne bekannten Schließungsanker bei geschlossenem Türzustand beginnt der Gang
mit der Erkennung (`recognition_only`); eine Schließung wird nicht erfunden.
Ersterkennung und Aufgussbestätigung bleiben getrennte Zeitangaben. Eine
nachträgliche Zuordnung erzeugt keine historischen Heizbefehle.

## Aufhebung, Abschluss und Zählung

| Eingang | Wirkung |
|---|---|
| Personensignal | Vorläufiger Gang, sofortige Anzeige und Gang-Heizbehandlung. |
| Zugeordneter Aufguss | Derselbe Gang bestätigt; keine Änderung von ID/Beginn. |
| Weitere Personensignale | Bestätigungsfrist nicht neu starten. |
| Kurze Türbetätigung | Gang bleibt bestehen. |
| Fristablauf ohne Aufguss | Vorläufigen Gang vollständig aufheben, ohne Zählung, Abschluss oder Nachlauf. |
| Bestätigtes Durchlüften vor Aufguss | Ebenso vollständig aufheben. |
| Bestätigtes Durchlüften nach Aufguss | Gang zum tatsächlichen Bestätigungszeitpunkt abschließen. |
| Ausdrückliches Betrieb-Aus | Gang sofort beenden, auch vorläufig; kein Wiederaufleben bei Aus/Ein. |

Die Bestätigungsfrist bezieht sich auf den zugeordneten Beginn. 12 oder 13 Minuten
waren Orientierung, kein gewählter Default; bei Einrichtung muss ein Wert gesetzt
werden. Die ungefähre Gangdauer von 15 Minuten ist kein automatisches Gangende.
Aufgehobene Erkennungen bleiben diagnostisch erhalten, sind aber keine Gänge im
Verlauf oder Zähler. Wiederholte Personensignale derselben verworfenen Episode
starten keine neue Frist. Ein späterer Aufguss kann die Erkennung nachholen.

Jeder beendete Gang mit mindestens einem Aufguss zählt genau einmal, unabhängig
vom Endgrund. Laufende, unbestätigte oder aufgehobene Gänge zählen nicht. Kein
separat schreibbarer Bestätigungs- oder Zählmerker.

Nachlauf und laufende Kühlung sperren neue Gangstarts. Ein bereits laufender
Gang verschiebt fällige Kühlung. Türwartefrist vor einer möglichen Erkennung,
Heizbehandlung und Nachlaufanrechnung: [Betrieb](betrieb.md).

Tests: `test_timeline.py`, `test_operation.py`, `test_cooling.py`, reale HA-Ketten
in `tests/integration/test_device_path.py` und Browserprüfung der unveränderten
Gang-ID und rückzugeordneten Startzeit. Zeitmodell: [Zeitbezüge](zeitmodell.md).
