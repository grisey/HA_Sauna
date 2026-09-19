# Entscheidungen und Umsetzungsstand

## Geltender Stand vom 19.09.2026

Die späteren Nutzerkorrekturen sind in [Betrieb](betrieb.md),
[Gangmodell](gangmodell.md), [Zeitmodell](zeitmodell.md),
[Parameter](parameter.md), [Oberfläche](darstellung.md) und
[Speicherung](speicherung.md) konsolidiert. Diese Darstellung ersetzt die zuvor
widersprüchlichen Zwischenstände. Der ursprüngliche Verlauf bleibt in Git erhalten.

- Unbestätigte Erkennung bei Fristablauf oder Durchlüften vollständig aufheben,
  ohne Gangzählung oder Nachlauf. Bestätigte beendete Gänge zählen genau einmal.
- Grundkonfiguration bleibt während der Session gesperrt, auch bei kurzem Aus/Ein.
- Bereitschaftsziel ist Solltemperatur plus einstellbarer Aufschlag, Standard 5 °C;
  einstellbare Hysterese Standard 3 °C. Keine Zielnachführung innerhalb eines Gangs.
- Tatsächliches Heizbudget zunächst 90 Minuten, nach erster abgeschlossener Kühlung
  einmalig um 30 Minuten verringert, danach konstant. Kühlvorgabe 15 Minuten.
  Alle Zahlen sind einstellbare Defaults. Neue Session erhält wieder das Anfangsbudget.
- Thermostat-Cooldown Standard 5 Minuten; Mindestheizzeit Standard 10 Minuten ab
  tatsächlichem Einschalten. Nachlauf, Kühlung, Aus und technische Schutzabschaltung
  haben Vorrang. Nachlauf hält den Ofen aus und sperrt neue Gänge.
- Laufender Gang wird von fälliger Kühlung nicht abgebrochen. Danach Nachlauf und
  nur die verbleibende Kühlung. Nachlauf wird einmal vollständig angerechnet.
- Türöffnung in Bereitschaft/Aufheizen verschiebt fällige Kühlung: standardmäßig
  10 Minuten ab Öffnung, falls offen geblieben; bei rechtzeitiger Schließung
  stattdessen 4 Minuten ab Schließung. Ohne Personensignal beginnt danach die
  fällige Kühlung. Bereits laufende Kühlung wird nicht rückgängig gemacht.
- Mehr als 105 °C länger als 10 Minuten führt zu doppelter Kühlvorgabe ohne
  Sessionabbruch. Grenze, Dauer und Faktor sind einstellbar. Die bestehenden
  Gang-/Nachlaufregeln gelten. Sofortige temperaturbedingte Sessionabschaltung
  ist verworfen. Bestätigte technische Dauerausfälle bleiben ein eigener Schutzgrund.
- Mechanischer Timer trennt physisch den Strom. Die Integration führt eine
  getrennte Schätzung von standardmäßig 4 Stunden, mit einstellbarer Vorwarnung.
  Die tatsächliche Heizrückmeldung bleibt maßgeblich.
  Präzisierung: ausschließlich Anzeige/Erinnerung zum erneuten Einstellen des
  Drehschalters, keinerlei Steuer- oder Schutzwirkung aus der geschätzten Frist.
- Zwei Hauptansichten: Normal mit einfacher Steuerung und eigenem Blatt für
  Sessionverlauf samt Archivauswahl; Details mit übersichtlich sortiertem Betrieb,
  Fristen, Fehlern, eigener Erkennungskontrolle und Einstellungen/Export.
- Gestaltung entspricht der gelieferten Diagrammvorlage. Zoom, Achsen, Tooltips
  und Aktualisierung dürfen verbessert werden. Erkennungsdetails stehen separat.

## Unveränderte Architekturentscheidungen

Ein führender Session-/Ablaufkern; gleiche Bedienung durch physischen Eingang,
HA-Entitäten und Panel. Eine neue Session initialisiert ihre Unterobjekte gemeinsam.
Keine Rückdatierung realer Schaltbefehle. Konfiguration, Schutzgründe und Archiv
überleben den Sessionwechsel. Kein automatischer Betriebsstart nach HA-Neustart.

Erkennung verwendet beide Messhöhen getrennt. Ein-Sensor-Erkennung bleibt möglich
mit Fehleranzeige. Keine Temperaturmittelung oder erfundener Höhenoffset, keine
IBS-Sensoren. Die Heizregelung benötigt einen gültigen oberen Wert; eine sichere
untere Ersatztemperatur wurde nicht festgelegt und wird nicht angenommen.

Vollauflösung und Ereignisrevisionen in SQLite unter HA-Konfiguration. Echte
HA-Backup-Einbindung mit Restore-Prüfung. Authentifizierter ZIP-Download in den
Einstellungen; keine Archive unter www. Keine automatische Löschung oder Verdichtung.

## Grenzen und verworfene Varianten

Keine zusätzliche prozentuale Idle-Gutschrift, keine dynamische Vergrößerung des
Heizbudgets, kein automatisches Gangende nach ungefähr 15 Minuten. Keine separate
Schreibquelle für Bestätigung oder Zählbarkeit. Keine adaptive Erkennung als
unbesprochener Ersatz des eingefrorenen Kandidaten. Keine voreilige rückwirkende
Zuordnung des Gangendes zur Türöffnung.

Noch nicht vereinbarte Ausgangswerte werden bei Einrichtung verlangt. Dazu zählen
Session-, Bestätigungs-, Heizzeit-Rücksetz- und Nachlaufdauer; erforderliche
Mess-/Aktor-/Ausfallfristen sind an der Installation zu bestimmen. Prognosen der
Aufheizzeit sind nicht aus Softwaretests als zuverlässige Regel abzuleiten.

Der Auftrag autorisiert Repositoryarbeit und isolierte Tests, keine reale
Ofenaktivierung, keine Produktionsinstallation, keine Versionserhöhung, kein
Release und keine Lizenzwahl. Privater Recorderexport und Nutzerkonfiguration
bleiben lokal. Ausgeführte Tests und noch fehlende Hardwareabnahme werden
getrennt im [Abnahmebericht](abnahme.md) benannt.
