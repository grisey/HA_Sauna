# Designentscheidungen

## Ein führender Ablauf pro Sauna

Der Controller besitzt den fachlichen Laufzeitzustand. Runtime, Geräteadapter
und Oberfläche greifen auf diesen gemeinsamen Zustand zu. Eine Bedienung und
eine Quellmeldung treffen damit auf dieselbe Sitzung und dieselben Fristen.

Die Sitzungsidentität bindet Gänge, Ereignisse, Entscheidungen und Fristen an
ihren Ursprung. Konfiguration, Archiv und Schutzgründe bestehen unabhängig vom
Sitzungswechsel.

## Trennung von Quellen, Zuordnung und Ausgabe

Quellmeldungen behalten ihre Messrolle, Herkunft und Zeitangaben. Die Erkennung
verarbeitet bereits verfügbare Messungen. Der Ablaufkern ordnet die Ergebnisse
fachlich ein; der Geräteadapter setzt die aktuelle Regelentscheidung um.

Eine fachliche Zuordnung darf einen früheren Zeitpunkt beschreiben. Ihre
Erkennung und ihre Buchung behalten jeweils den tatsächlichen Zeitbezug.
Geräteausgaben folgen dem aktuellen Regelzyklus. So können Archiv und
Phasenansicht rückwirkend präzisiert werden, während die tatsächliche
Schaltgeschichte erhalten bleibt.

[Zeitmodell](zeitmodell.md) · [Schnittstellen](schnittstellen.md)

Geräteauftrag und beobachtete Rückmeldung sind verschiedene Informationen.
Der Abschluss eines Dienstaufrufs beschreibt dessen Verarbeitung; die
Quellmeldung beschreibt den beobachteten Zustand. Diese Unterscheidung trägt
die Bewertung von Heizzeiten, Kühlzeiten und Gerätefehlern.

## Ein Konfigurationsstand mit reproduzierbaren Snapshots

Die Konfiguration des Home-Assistant-Eintrags ist die gemeinsame Quelle für
Gerätezuordnung und Einstellungen. Ein Änderungskandidat wird als Ganzes geprüft,
bevor er wirksam wird. Dies erhält die Beziehungen zwischen Grenzen,
Temperaturprogramm und seinen einzelnen Stufen auch bei verschiedenen
Bedienwegen. Gespeicherte gültige Werte bleiben maßgeblich; Defaults ergänzen
fehlende Werte.

Der laufende Zustand verwendet einen unveränderlichen Parameterstand.
Live-Änderungen erreichen den bestehenden Controller über den gemeinsamen
Einstellungspfad. Das Archiv speichert die zur Sitzung gehörenden
Konfigurationsrevisionen als historische Snapshots.

## Originaldaten und abgeleitete Ansichten

Das Archiv bewahrt Messungen in voller empfangener Auflösung und ergänzt neue
Zuordnungen durch weitere Revisionen. Dadurch bleiben ursprünglicher Beleg und
spätere fachliche Interpretation gemeinsam lesbar. Der neueste Sitzungssnapshot
erleichtert den Zugriff, während die Recordfolge den Entstehungsweg erhält.

SQLite im privaten Home-Assistant-Konfigurationsbereich bietet einen gemeinsamen
Speicher für Sitzungssnapshots und Originalrecords. Die Backup-Hooks koordinieren
alle Archivschreiber für eine konsistente Sicherung. Ein authentifizierter
ZIP-Export macht denselben Datenbestand außerhalb der Oberfläche auswertbar.

[Archivschema, Rechte und Wiederherstellung](speicherung.md)

Die Oberfläche leitet ihre Ansichten aus dem führenden Zustand und dem Archiv
ab. Ihre Diagrammaggregation dient dem Zeichnen; der Originalpunkt bleibt die
Quelle für Messwert und Tooltip. Die Aufheizprognose ist eine Anzeigehilfe mit
eigener Messgrundlage. Heizfreigaben und Schutzentscheidungen bleiben Aufgaben
des Controllers.
