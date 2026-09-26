# Präsenz, Ofen und Phasen – geltender Stand

Diese Seite ersetzt frühere Aussagen über Türfristen, Gang-Heizveto,
pausierbare Kühlung und die Aktivierung externer Präsenz. Ausgangsbasis bleibt
der [rc4-Stand becfdb5](https://github.com/grisey/HA_Sauna/commit/becfdb5464e1219e42f75f03f9c5feea723c0d61),
zusammengeführt in [f23a7c9](https://github.com/grisey/HA_Sauna/commit/f23a7c93e5d9bf9cacaed0c609cef706bd25d04d).
Der Arbeitsbranch heißt `codex/rc4-praesenz-ofen-phasen`. Die produktiven
Erkennungsmuster und Schwellen bleiben gegenüber rc4 unverändert.

## Physischer Ablauf und Vorrang

Der aktuelle Aktorzustand wird aus dem laufenden physischen Ablauf abgeleitet.
Bei konkurrierenden Ursachen gilt diese Reihenfolge:

1. Schutz und ausdrückliches Betrieb-AUS
2. angeforderte oder laufende Ofenkühlung
3. live aktiver Saunagang (vorläufig oder bestätigt)
4. einmalige vorübergehende Tür-Heizanforderung
5. normale Thermostatregelung

Jeder live aktive Gang, ob vorläufig oder durch Aufguss bestätigt, fordert
kontinuierlich EIN, solange Betrieb und Schutz es erlauben. Er ist kein
rückwirkend erzeugter Befehl und keine Heizzeitbuchung. Schutz, Betrieb-AUS und
Ofenkühlung bleiben vorrangig. Nur das Ende eines bestätigten Gangs fordert
eine Ofenkühlung an und zählt im Timeline-Modell.

## Tür und vorübergehendes Heizen

Nur eine Tür**schließung** kann eine vorübergehende Heizanforderung auslösen.
Sie nutzt die bestehende tatsächliche Mindestheizzeit, standardmäßig 10 Minuten.
Diese beginnt mit der tatsächlichen Heizrückmeldung. Bereits laufendes Heizen
bekommt keine neue Mindestdauer. Es gibt keine Öffnungsfrist und keinen
Parameter `door_request_minutes` mehr.

Eine Türöffnung während eines aktiven Gangs oder einer angeforderten/laufenden
Ofenkühlung ist dauerhaft nicht berechtigt. Die Anforderung ist ein getrenntes,
verwerfbares Objekt: Sie bleibt nur bis zur tatsächlichen Heizbestätigung offen,
wird bei unzulässigem Ablauf verworfen und erfindet weder einen Gang noch eine
Heizrückmeldung.

## Phasen und historische Projektion

Der live physische Zustand, der aktuelle Aktorbefehl und die rückblickende
Phasenprojektion sind getrennte Darstellungen. `base_phases` und
`contactor_history` enthalten Beobachtungen. Die Projektion legt daraus
exklusive Abschnitte wie Aufheizen, Bereit, Saunagang, Ofenkühlung und AUS ab;
sie darf rückwirkend korrigiert werden, erzeugt aber niemals historische
Aktorbefehle.

`readiness_pauses` beschreibt nur korrigierte Bereitschaft bei Betrieb EIN und
bestätigtem Schütz AUS. Unbekannte Rückmeldung bleibt unbekannt. Diese Pausen
sind ausschließlich ein Eingabebeleg für die dynamische Ofenkühlung unter
[Ofenkühlung](ofenkuehlung.md).

## Präsenz und Audio

Die Proxyquelle bleibt aktiv und führend. Eine externe Präsenzquelle beobachtet
nur; sie startet, beendet oder übersteuert keinen Gang und keinen Ofen. Liefert
eine künftig fachlich zugelassene Präsenzquelle einen live aktiven Gang, gilt
dessen kontinuierliche Heizanforderung auch ohne Aufguss. Die aktuelle
Proxy-Bestätigungsregel wird damit noch nicht geändert. Eine spätere FP300-Anbindung darf Phasen
liefern, aber keine Aktorbefehle. Audio ist lediglich als Ausgabeziel vorbereitet;
es gibt keine Wiedergabeplanung.

Die austauschbare Türhilfe liegt ausschließlich in `core/temporary_door_heat.py`
und ihrer Zustandsführung im Controller. Sobald eine direkte Präsenzquelle die
Gangführung übernimmt, entfällt diese Türhilfe; ein paralleles ODER oder ein
automatischer Rückfall auf sie ist nicht vorgesehen. Ausfall-, Unterbrechungs-
und Enderegeln der direkten Präsenz werden vor deren Aktivierung festgelegt.

## Prüfung dieser Umsetzung

Der abschließende Lauf von `python -m unittest discover -s tests` umfasst
444 Prüfungen: 442 erfolgreich, zwei optionale private Prüfungen übersprungen.
`node --test tests/panel_*.test.js` besteht mit zwölf erfolgreichen Prüfungen.
Python-Kompilierung, JavaScript-Syntax und `git diff --check` sind geprüft.
Der Detector ist bytegleich mit der oben genannten rc4-Basis.

Separat wurden vollständige private Originalaufzeichnungen erneut durch die
produktiven Runtime-, Geräte-, Detector-, Controller- und Thermostatpfade
geführt. Alle Läufe beendeten sich regulär. Erkennungsereignisse und gezählte
Gänge stimmen mit dem Vergleich auf f23a7c9 überein. Während live aktiver Gänge
trat keine unbeabsichtigte Ausschaltanforderung auf; ausdrückliches manuelles
AUS bleibt wirksam. Die Projektionen decken die vollständigen Sitzungen ohne
Lücken ab und zeigen zurückgenommene Gänge wieder als belegte Grundphasen.
Kühldauern beginnen nach bestätigtem AUS und bleiben anschließend fest;
abgebrochene Kühlung wird nicht als vollständige Kühlung verbucht.

Die Aufzeichnungen enthalten keinen geeigneten Türschluss, der allein eine
vorübergehende Heizanforderung auslöst. Dieser Pfad wurde deshalb gesondert
mit allgemeinen Ablaufbeispielen geprüft: Start über der normalen oberen
Regelgrenze, ausbleibende und verzögerte Rückmeldung, tatsächliche Mindestdauer,
gesperrte Austrittszyklen sowie neue geeignete Zyklen nach einer Unterbrechung.

Die Replays verwenden unveränderte Messkurven und eine erfolgreiche simulierte
Schützrückmeldung nach 100 ms. Sie belegen den Befehls- und Phasenablauf bei
diesen Eingängen, keine dadurch neu entstehende Temperaturkurve. Private Daten
und Nutzungszeitpunkte bleiben außerhalb des Repositorys. Ein vollständiger
Lauf in einer installierten Home-Assistant-Umgebung und reale Hardwareprüfungen
wurden hier nicht ausgeführt. Manifestversion bleibt `1.0.0-rc4`; kein Release
und kein HA-Deployment.
