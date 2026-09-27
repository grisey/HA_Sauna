# Prüfstand

Diese Seite beschreibt die geprüften Fallgruppen, nicht die Fehlerfreiheit oder
Widerspruchsfreiheit der gesamten Implementierung. Ein erfolgreicher Test belegt
nur seine Eingaben und Assertions; fachliche Regeln und ihre Verbraucher sind
zusätzlich am Code zu prüfen. Das
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

- Ein aktiver Gang fordert Heizen an; Schutz, Betrieb-AUS und Ofenkühlung
  bleiben übergeordnet.
- Die Türhilfe gilt einmalig nach geeignetem Türschluss; die Mindestheizzeit
  beginnt mit tatsächlichem Heiznachweis.
- Ofenkühlung sperrt sofort, ihre Zeit beginnt erst bei bestätigtem Schütz-AUS.
  Heizzeiten und Bereitschaftspausen bestimmen ihre einmal eingefrorene Dauer.
  Eine manuelle Heizwahl unterbricht die Kühlung nicht.
- Phasenprojektionen korrigieren die Darstellung rückwirkend, ohne historische
  Aktorbefehle zu verändern. Archivierte Altphasen bleiben lesbar.
- Übersteuerungen enden an ihren Rückkehrpunkten oder spätestens nach ihrer
  Höchstdauer. Unveränderte Mess- und Zustandsmeldungen verlängern sie nicht.
- Einstellungen und Darstellungsprognosen ändern keine Heizsperren oder Fristen.

Es gelten [Präsenz, Ofen und Phasen](praesenz-ofen-phasen.md) und
[Ofenkühlung](ofenkuehlung.md). Frühere Prüfziele zu Heizbudgets, Türwartefristen,
pausierter Kühlung und eigenständiger Zwangskühlung sind keine aktuellen
Abnahmeregeln. Negativtests können weiterhin belegen, dass alte Parameter keine
solche Steuerwirkung mehr auslösen.

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
