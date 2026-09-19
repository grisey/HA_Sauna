# Oberfläche

Stand 19.09.2026, einschließlich der jüngsten Präzisierung: zwei Hauptansichten.
Das native HA-Custom-Panel ist unter `/ha-sauna` registriert. Es verwendet die
vorhandene HA-Anmeldung und authentifizierte APIs; es führt keinen eigenen
Regelungszustand. Browsernachweise stehen im [Abnahmebericht](abnahme.md).

## Normal

Einfache Steuerung mit Status, Betrieb-An/Aus, Temperaturwahl vor einer Session,
Temperatur, Feuchte und Sessionenergie mit Kennzeichnung der Mess-/Schätzquelle.
Temperaturkacheln verwenden zentral konfigurierte
Ausgangstemperatur, Abstand und Anzahl; Vorlage: 70 bis 95 °C in 5-Grad-Schritten.
Die Kacheln setzen die Solltemperatur und starten über denselben Backendpfad.
Gemischte alte Start-/Endtemperatur-Szenen werden nicht übernommen: Die aktuelle
Grundkonfiguration bleibt während der Session unverändert.

Auf dem eigenen Blatt **Sessionverlauf & Archiv** sind die laufende Session und
historische Sessions auswählbar. Gestaltung aus der gelieferten Vorlage:

| Element | Darstellung |
|---|---|
| Temperatur | `#ff6b4a`, linke °C-Achse |
| Luftfeuchte | `#42a5ff`, rechte Prozentachse |
| Messposition | Oben durchgezogen, unten gestrichelt; getrennt ein-/ausblendbar |
| Tür offen | Gelbe Zeitfläche |
| Gang | Magentafarbene Fläche; vorläufig mit gestrichelter Kontur |
| Aufguss | Weiße Zeitmarke |
| Heizen / bereit / lüften | Orange / grüne / blaugraue Zeitfläche |
| Gezählt als Heizaktivität | Eigener schmaler Streifen; Details nennen Leistungsmessung, unabhängige Rückmeldung oder Schützschätzung |

Gangflächen stammen aus dem neuesten zugeordneten Sessionobjekt. Eine spätere
Bestätigung verändert weder ID noch Beginn. Tooltip zeigt Originalwert und
zugehörigen Empfangszeitpunkt jeder Messreihe. Zoom, verschiebbarer Ausschnitt
und Rückkehr zur Gesamtsession sind Darstellungsfunktionen. Die Übertragung
lädt das Archiv seitenweise; eine Begrenzung der SVG-Punkte erhält Extrema pro
Bildspalte und verändert niemals gespeicherte Originale.

## Details

**Betrieb & Fristen:** beide Messpositionen, Bereitschaftsziel, gezählte
Heizzeit und Budget, Rückmeldungsquelle, gemessene Leistung sowie Sessionenergie,
Gangstatus, Nachlauf, Kühlung, Türwartefrist, mechanischer Timer als Schätzung,
Heizentscheidungsgrund und aktuelle Fehler. Messlücken und geschätzte Energieanteile
bleiben gekennzeichnet; Kühlung und Heizbudgetrücksetzung löschen die Summe nicht.

**Erkennungskontrolle:** eigene Verlaufskurven der tatsächlich im Detektor
berechneten Tür-, Personen- und Aufgussmerkmale. Die für die ausgewählte Session
gespeicherten Schwellen werden eingeblendet. Haltezähler, Kontext,
Sensorverfügbarkeit und ausgelöste Signale bleiben nachvollziehbar. Es wird
kein zweiter Detektor im Browser nachgebaut. Historische Fehler und die
ursprünglichen Erkennungszeiten stehen hier getrennt vom normalen Verlauf.

**Einstellungen & Export:** zentrale Parameter, eingeklappte Expertenwerte,
Verweis zum HA-Zuordnungsdialog und ZIP-Download des Archivs. Während einer
Session sind Änderungen im Frontend und im Backend gesperrt. Steuerung und
Parameteränderung verlangen HA-Administratorrechte, auch bei direktem API-Aufruf.

## Grenzen

Die Oberflächenprüfung verwendet synthetische Daten im tatsächlichen HA-Frontend.
Die private Bildschirmaufnahme und ihre Entity-IDs werden nicht veröffentlicht.
Eine Vorhersage der verbleibenden Aufheizzeit ist keine vereinbarte/validierte
Regel und wird nicht als zuverlässige Restzeit erfunden. Vorhandene Timer und
Fristen werden mit ihrem tatsächlichen Zeitbezug angezeigt.


## Präzisierung nach der ersten Testinstallation

Die Hauptansichten heißen **Übersicht** und **Details**. Die Übersicht hat die
Blätter **Steuerung** und **Verlauf und Archiv**. Zustand, gezählte Heizzeit,
angehaltener/laufender mechanischer Ofentimer und die jeweils aktive Gang-,
Nachlauf- oder Kühlzeit erscheinen kompakt. Bereitschaftstemperatur und interne
Regelungswerte stehen ausschließlich in den Details. Gesperrte Temperaturtasten
bleiben sichtbar; der Grund der Sperre wird erklärt. Nach Sitzungsende bleibt
der letzte Verlauf sichtbar und jede ältere Sitzung im Archiv auswählbar.

Alle Einstellfelder besitzen deutsche Bezeichnungen und Zweckbeschreibungen.
Sie sind nach Temperatur, Ablauf, Überwachung, Ofentimer/Energie und Darstellung
geordnet. Erkennungsparameter stehen separat unter Experteneinstellungen.
Veraltete Messwerte und unvollständige Einrichtung werden unterschiedlich erklärt;
interne Fehlerkennungen erscheinen nicht als Fehlermeldung. Die frei einstellbare
Temperatursteigerung ist in der Steuerung unter einer eigenen Aufklappzeile wählbar.

Unter Details → Einstellungen steht **Protokollierung**. **INFO** ist Standard:
Fehler, Warnungen, Zustandswechsel, Sollwertwechsel und Schaltbefehle. **ERROR**
begrenzt die Ausgabe auf Fehler; **DEBUG** ergänzt Messwerte und Erkennungsprüfungen.
Die Auswahl wird gespeichert und wirkt ohne Neustart oder Sitzungsunterbrechung.
Home Assistants normale Logger und Loghandler führen die Ausgabe unter
`custom_components.ha_sauna.instance.…`; gleichbleibende Störungen werden nicht
sekündlich erneut gemeldet. Das vollständige Sitzungsarchiv ist unabhängig von
dieser Auswahl. Die gleiche Auswahl steht in den HA-Integrationsoptionen bereit.
