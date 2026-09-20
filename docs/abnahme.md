# Prüfstand

Diese Seite beschreibt, was die automatisierten Prüfungen nachweisen. Das
Ergebnis des jeweiligen Commits steht in den
[GitHub-Prüfläufen](https://github.com/grisey/HA_Sauna/actions/workflows/tests.yml).
Die [Einrichtung](einrichtung.md) und die [Bedienung](bedienung.md) sind getrennte
Anleitungen für die Nutzung.

## Geprüfte Ebenen

| Ebene | Geprüftes Verhalten |
| --- | --- |
| Fachkern | Gangzuordnung, Temperaturprogramme, Heizzeit, Nachlauf, Kühlung, manuelle Übersteuerung, Licht und Anzeigeprognosen mit kontrollierter Zeit. |
| Messverarbeitung | Allgemeine Erkennungsregeln, Sensorausfall, Quellenwechsel, abgeleiteter Wassergehalt und Abgrenzung unvollständiger Messungen. |
| Home Assistant | Einrichtung, Entitäten, Optionen, reale HA-Listener und Dienste, Rückmeldungen, Berechtigungen und HTTP-Schnittstellen. Externe Geräte sind Testquellen. |
| Archiv | Originalauflösung, Zuordnungsrevisionen, Export sowie tatsächliches HA-Backup mit Wiederherstellung in eine getrennte Testinstanz. |
| Browser | Bedienung im echten HA-Frontend: Temperaturwahl, Programme, manuelle Bedienung, Verlauf, Archiv und Einstellungen. |

## Wichtige Ablaufgrenzen

Die gezielten Szenarien prüfen insbesondere:

- Eine Türöffnung kurz vor fälliger Kühlung lässt Zeit für die Personenerkennung.
- Ein laufender Gang bleibt bei erreichtem Heizbudget erhalten; Nachlauf wird
  genau einmal auf die folgende Kühlung angerechnet.
- Reicht das restliche Heizbudget nicht für die Mindestheizzeit, folgt Kühlung
  auf den Nachlauf ohne Zwischenheizen.
- Manuelles Heizen pausiert Nachlauf und Kühlung. Ein aufgehobener vorläufiger
  Gang erhält den alten Nachlauf; erst ein bestätigter neuer Gang ersetzt ihn.
- Zusatzkühlung bei Übertemperatur wartet auch im manuellen Betrieb einen
  laufenden Gang ab. Technische Sperren behalten Vorrang.
- Übersteuerungen enden an ihren Rückkehrpunkten oder spätestens nach ihrer
  Höchstdauer. Unveränderte Mess- und Zustandsmeldungen verlängern sie nicht.
- Einstellungen und Darstellungsprognosen ändern keine Heizsperren oder Fristen.

## Durchführung

Der Fachkern benötigt nur Python mit Standardbibliothek. Öffentliche Läufe
enthalten keine privaten Recorderdaten. Die optionalen privaten Replays dienen
der Kalibrierung und dem Vergleich von Änderungen; dieselben Aufzeichnungen
sind keine unabhängige Bestätigung der Erkennungsqualität.

HA- und Browserprüfungen laufen unter Linux mit dem in der
[Prüfkonfiguration](../.github/workflows/tests.yml) festgelegten HA-Stand.
Jeder Testschritt ist auf zwei Minuten begrenzt; der gesamte HA- beziehungsweise
Browserjob einschließlich Einrichtung auf fünf Minuten. Veraltete parallele
Läufe werden abgebrochen.

Installation und Prüfung an der echten Anlage erfolgen durch den Benutzer über
HACS. Softwaretests weisen keine physische Schütz- oder Lichtwirkung an dieser
Anlage nach. Ein optionaler realer Leistungsmesser und die zurückgestellte
Erkennung eines internen Ofen-Aus allein aus Temperaturkrümmung gehören nicht
zum bisherigen Hardware-Nachweis.
