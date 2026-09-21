# Parameter und Entitätsrollen

`Parameters` ist die einzige Quelle für veränderliche Betriebswerte. Config Flow,
Einstellungen, Number-Entitäten, Klimaregelung und Panel lesen und schreiben
denselben in `ConfigEntry.options` gespeicherten Stand. Die Prüfung weist
unbekannte Felder, unendliche Werte und unzulässige Beziehungen ab. Fehlende
Werte älterer Konfigurationen erhalten die zentralen Standardwerte; bereits
gespeicherte Werte bleiben erhalten.

Während einer offenen Sitzung lassen sich nur Solltemperatur, Endtemperatur und
Verteilung der Temperaturautomatik ändern. Diese Änderung geht direkt an den
führenden Controller; sie startet keine Timer neu und hebt weder Kühlung noch
Schutz auf. Alle übrigen Werte und Entitätszuordnungen bleiben bis zum
Sitzungsende gesperrt.

## Temperatur und Programme

Die wählbare Solltemperatur liegt zwischen **60 und 100 °C**. Die direkte
Sollwahl startet bei **80 °C** und bleibt konstant. Die freie
Temperaturautomatik verteilt den Weg von **80 auf 95 °C** über **vier Gänge**.
Nach Erreichen des Endwerts gilt dieser auch für weitere Gänge; die Verteilung
ist keine Obergrenze der Gangzahl. Benannte Programme werden separat als
validierter Programmkatalog gespeichert und unterliegen demselben Bereich.

Bereit ist die Sauna ab Erreichen der Solltemperatur. Die obere Regeltemperatur
des Ofens liegt durch die **Temperaturreserve** standardmäßig 5 °C darüber;
der Schaltabstand beträgt 3 °C. Nach einer regulären Temperaturabschaltung gilt
eine Heizpause von 5 min,
ein begonnenes Heizintervall dauert mindestens 10 min. Die vollständigen
Ablaufbeziehungen stehen in [Betrieb](betrieb.md) und
[Zeitmodell](zeitmodell.md).

## Betrieb, Schutz und Energie

Die Standardwerte für die Sitzung sind: Unterbrechungsfrist 15 min,
Aufgussbestätigung 12 min, Heizbudget 90 min und einmalige Verringerung nach
der ersten abgeschlossenen Kühlung um 30 min. Zwangskühlung dauert 15 min,
Nachlauf 8 min und die lokale Heizzeit-Rücksetz-Auszeit 10 min. Die
Türwartewerte betragen 4 min nach dem Schließen und höchstens 10 min bei offen
bleibender Tür.

Messwerte sind 180 s gültig. Rückmeldungen müssen innerhalb von 10 s vorliegen;
ein Fehler wird nach 60 s bestätigt. Die Übertemperaturgrenze liegt bei 105 °C,
mit 10 min Nachweis und doppelter Kühlvorgabe. Der mechanische Timer ist eine
Anzeige mit 240 min; eine Vorwarnzeit kann leer bleiben.

Der optionale Leistungssensor ersetzt, wo vorhanden, die Schätzung. Ohne ihn
rechnet die Energieanzeige mit **4,5 kW** und der bestätigten Heizzeit; die
Heizleistungsgrenze ist 50 W. Messung, Schätzung und fehlende Abschnitte bleiben
in der Anzeige unterscheidbar.

## Licht und Anzeige

Die automatische Lichtkurve beginnt bei **5 % bei 30 °C** und steigt bis zur
Bereitschaft. Die Normalhelligkeit beträgt tagsüber 40 % und nachts 25 %.
Nachlauf verwendet 15 %, Zwangskühlung 5 %. Übergänge dauern 30 s. Nach dem
endgültigen Sitzungsende leuchtet das Licht 10 min mit 50 % weiter. Eine
manuelle Lichtwahl endet mit dem passenden Phasenwechsel, spätestens nach
10 min.

Die Aufheizschätzung verwendet ein 5-minütiges Fenster. Sie ist reine Anzeige:
Sie nutzt zuerst einen stabilen Anstieg der aktuellen Aufheizphase und sonst den
brauchbaren Durchschnitt der letzten archivierten Aufheizphase. Die Darstellung
rundet auf Fünf-Minuten-Stufen und verändert weder Ofen noch Fristen; siehe
[Darstellung](darstellung.md).

## Erkennung und externe Entitäten

Temperatur und relative Luftfeuchte oben und unten, Heizaktor, Bedienquelle und
dimmbares Licht werden über HA-Selektoren zugeordnet. Leistungsmesser,
unabhängige Heizrückmeldung und Statusquellen sind optional. Die Metadatenprüfung
sichert Domain, Einheit, Geräteklasse und Dimmbarkeit; konkrete Entity-IDs
gehören nicht in den Ablaufkern.

Aus jedem gültigen, frischen Temperatur-/Feuchte-Paar erzeugt die Integration
zusätzlich eine diagnostische Entität für den absoluten Wassergehalt oben bzw.
unten. Sie wird bei einer Lücke, alten Quellen oder einem nicht passenden Paar
unavailable und behält keinen alten berechneten Wert. Regeln und
Expertenparameter der produktiven Erkennung stehen in [Erkennung](erkennung.md).

## Migration statt Bedienoption

Die frühere feste Steigerung von 5 °C, die frühere optionale Endtemperatur und
die ergänzende Türgrenze von 70 °C sind keine aktiven Bedienwerte. Solche
Altwerte bleiben beim Laden kompatibel, erscheinen aber nicht als neue
Produktivkonfiguration. Neue Werte werden ausschließlich über die beschriebenen
zentralen Parameter und den Programmkatalog festgelegt.
