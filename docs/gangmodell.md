# Gangmodell

> Maßgeblicher Stand vom 26.09.2026: [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md)
> und [Ofenkühlung](ofenkuehlung.md). Jeder aktive Gang fordert vorläufig wie
> bestätigt durchgehend Heizen an; Ofenkühlung und technische Sperren haben Vorrang.
> Die vorübergehende Türhilfe wirkt nur nach geeignetem Türschluss. Ofenkühlung
> ist dynamisch und wird durch Tür, Präsenz oder manuelles Heizen nicht unterbrochen.
> Entgegenstehende ältere Angaben unten zu Öffnungsfristen, Gangveto, Kühlpausen,
> Wiedereinstieg, Heizbudgets und Zwangskühlung sind ausschließlich historisch.

> Vorrang seit 26.09.2026: [Ofenkühlung](ofenkuehlung.md) ersetzt die unten
> beschriebenen eigenständigen Kühlzyklen, Heizbudgets und Kühlanrechnungen.
> Aktuelle Beschriftung des bisherigen Nachlaufs ist „Ofenkühlung“.

Der Ablaufkern führt Gänge, Fristen und Heizwirkung. Die Erkennung liefert nur
Signale; sie verwaltet keine zweite Bestätigung. Kühlung, Nachlauf und
Heizsteuerung stehen in [Betrieb](betrieb.md), die Zeitbezüge in
[Zeitmodell](zeitmodell.md).

Ein Personenzeichen legt einen vorläufigen Gang an. Ein zugeordneter Aufguss
bestätigt denselben Gang: ID und Beginn bleiben gleich. Ein Aufguss ohne vorher
erkannten Gang kann ihn sofort bestätigt anlegen. Der Beginn gehört zur passenden
Türschließung derselben Episode und Sitzung; ohne solchen Anker ist ausdrücklich
die Erkennungszeit der Beginn. Rückwirkende Zuordnung erzeugt keine historischen
Heizbefehle.

Vor dem Eintritt muss nicht vollständig durchgelüftet werden. Eine erkannte
Türöffnung mit anschließender Schließung genügt als Ausgangspunkt für die
empfindlichere Personenprüfung. Erst die passenden Temperatur- und
Feuchteverläufe lösen den vorläufigen Gang aus. Diese Gelegenheit endet mit der
Bestätigungsfrist ab Türschluss; eine erneute Öffnung verwirft sie. Nachträglich
eingetroffene Signale aus einer abgelaufenen oder ersetzten Türöffnungsepisode
starten keinen Gang.

| Ereignis | Wirkung |
| --- | --- |
| Personensignal | Legt einen vorläufigen Gang an und aktiviert die Gang-Heizbehandlung. |
| Erster zugeordneter Aufguss | Bestätigt denselben Gang. |
| Weitere Aufgüsse | Bleiben dem bestätigten Gang zugeordnet. |
| Bestätigungsfrist ohne Aufguss | Hebt den vorläufigen Gang vollständig auf. |
| Durchlüften vor Bestätigung | Hebt den vorläufigen Gang vollständig auf. |
| Durchlüften nach Bestätigung | Beendet den Gang zum tatsächlichen Bestätigungszeitpunkt. |
| Betrieb-Aus | Beendet den offenen Gang sofort; späteres Ein schließt ihn nicht wieder an. |

Die Aufgussbestätigung muss innerhalb von 12 min erfolgen. Eine ungefähre
Gangdauer ist keine automatische Endbedingung. Vorläufige oder aufgehobene Gänge
zählen nicht und erzeugen keinen Nachlauf. Jeder bestätigte, beendete Gang zählt
genau einmal, unabhängig vom Endgrund; die Zählbarkeit folgt aus dem Gang und
seinen Aufgüssen, nicht aus einem separaten Merker.

Nachlauf und aktive Kühlung sperren reguläre neue Gangsignale. Eine manuelle
Ofenübersteuerung kann sie pausieren und einen neuen Gang zulassen. Trifft ein
Personensignal während eines dadurch pausierten Nachlaufs ein, bleibt dieser
Nachlauf erhalten, bis ein Aufguss den neuen Gang bestätigt. Bei Aufhebung oder
Fristablauf läuft der alte Rest weiter; nur die Bestätigung storniert ihn. Die
bis dahin wirklich gelaufene Nachlaufzeit wird höchstens einmal auf eine Kühlung
angerechnet. Ein laufender Gang wird von fälliger Kühlung nicht unterbrochen.
