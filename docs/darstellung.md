# Darstellungsreferenz

[Bedienung](bedienung.md) · [Betriebsablauf](betrieb.md) ·
[Daten- und Zeichenvertrag](livekurve.md)

## Aktualisierung und Eingaben

Bei einem Instanzwechsel bleiben Bedienelemente gesperrt, bis der Zustand der
gewählten Sauna vorliegt. Antworten der zuvor gewählten Instanz geben die neue
Ansicht nicht frei.

Statusaktualisierungen erhalten bearbeitete Eingaben, aktualisieren aber deren
Grenzen und Sperren. Ein Programmfehler verwirft den Entwurf nicht.
Wann eine Temperaturwahl wirksam wird, beschreibt die
[Bedienungsanleitung](bedienung.md#betriebsstart-und-temperaturwahl).

Der Sollschieber verwendet ganze Celsiusgrade im gemeinsamen Bereich von
Anzeigeskala und zulässiger Solltemperatur. Eine engere Anzeigeskala begrenzt
also auch den dort direkt wählbaren Bereich; sie ändert keine Regelparameter.

## Zustand und Zeit

Die markierte Ofenwahl ist eine Vorgabe, die Ofenrückmeldung eine Beobachtung.
Eine fehlende Rückmeldung wird nicht als ausgeschalteter Ofen dargestellt.

Verstrichene Dauern werden auf ganze Minuten abgerundet, verbleibende
aufgerundet. Unter einer Minute erscheint **unter 1 Minute**; fehlende Werte
erscheinen als **–**. Ereigniszeitstempel behalten ihre Sekundenangabe, die
Wertanzeige am Diagramm verwendet eine kompakte Uhrzeit.

Die Aufheizprognose verbindet einen geeigneten früheren Aufheizverlauf mit dem
aktuellen Temperaturtrend. Sie wird in Fünf-Minuten-Stufen angezeigt; unter
fünf Minuten als **noch unter 5 Minuten bis bereit**. Sie ist eine Schätzung,
keine zugesagte Bereitschaftszeit.

## Farben und Kontrast

Farbrollen und Vorgaben werden ausschließlich im
[Darstellungskatalog](../custom_components/ha_sauna/defaults.json), Abschnitt
`appearance`, geführt. Gespeicherte Anpassungen gelten je Sauna.
Text- und Fokusfarben werden für die tatsächlich dargestellte Hintergrundfläche
auf ausreichenden Kontrast angepasst. Das berücksichtigt auch Phasentönungen.

Temperatur und Luftfeuchte verwenden jeweils dieselbe Messfarbe in Bogen,
Kurve und zugehöriger Achse. Die Zeitachse bleibt neutral. Im normalen Verlauf
sind beide Kurven durchgezogen; die Erkennungskontrolle unterscheidet obere und
untere Messposition zusätzlich durch durchgezogene bzw. gestrichelte Linien.
Frühere Messhöhenfarben bleiben in gespeicherten Konfigurationen kompatibel,
haben aber keine eigenen Farbfelder mehr.

## Darstellungsentwurf und Skalen

Gültige Änderungen erscheinen sofort als Vorschau. Erst Speichern übernimmt
sie für alle Benutzer der gewählten Sauna. Verwerfen stellt den gespeicherten
Stand wieder her. **Standarddarstellung wiederherstellen** füllt nur den Entwurf
mit Katalogvorgaben; auch dieser muss gespeichert werden.

Ein Messwert außerhalb der gewählten Skala bleibt als Zahl sichtbar. Der Bogen
endet am Skalenrand und kennzeichnet die Überschreitung. Skalen müssen eine
kleinere Unter- als Obergrenze haben; Feuchtegrenzen liegen im physikalischen
Bereich von 0 bis 100 %.

Die Verlaufsachsen richten sich nach den Verlaufsdaten, nicht nach den
Anzeigeskalen der Messbögen. Das allgemeine Zurücksetzen der Betriebsparameter
verändert die gespeicherte Darstellung nicht.

## Verlauf und Diagnose

Der normale Verlauf verwendet die führende Messposition. Seine Phasenflächen
stammen aus den gespeicherten Zeitabschnitten des Ablaufkerns. Die Wertanzeige
verwendet den empfangenen Originalpunkt und die Phase am ausgewählten Zeitpunkt;
sie wird beim Verlassen des Diagramms ausgeblendet.

Zeitfenstersteuerung und Diagramm verwenden dieselbe Zeitdomäne und horizontalen
Grenzen. Datenlücken, Kurvenaufbereitung und Navigation sind im
[Daten- und Zeichenvertrag](livekurve.md) beschrieben.

Der administrative Detailverlauf zeigt Betriebszustände, Schaltgründe und
Ereignisse. Die Erkennungskontrolle stellt gespeicherte Detektormerkmale mit den
Schwellen der jeweiligen Sitzung dar. Ereignisse werden nur bei vorhandenem
Signal- und Metriknachweis mit Diagnosemarkern verknüpft. Haltezähler bezeichnen
erfüllte Prüfpunkte. Ein Ereignissprung setzt den Fokus auf das Ziel.

Verlaufstabellen und Beschriftungen bleiben unabhängig von der Canvas-Zeichnung
als zugänglicher Text erhalten.
