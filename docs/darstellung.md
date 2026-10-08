# Darstellungsreferenz

[Bedienungsanleitung](bedienung.md) · [Betriebsablauf](betrieb.md) ·
[Daten- und Zeichenvertrag](livekurve.md)

## Ansichten und Aktualisierung

Das Panel übernimmt den aktuellen Zustand über die API. Die Bedienrechte folgen
dem angemeldeten Home-Assistant-Benutzer. Die obere Navigation gliedert die
Oberfläche nach Aufgabe:

| Ansicht | Aufgabe |
|---|---|
| Steuerung | Betrieb, Temperaturwahl und Licht mit aktuellem Phasenstatus |
| Verlauf | Aktuelle und archivierte Saunasitzungen |
| Details | Technische Zustände und Erkennungskontrolle für Administratoren |
| Einstellungen | Programmbibliothek und Tasterwahl; technische Konfiguration und Darstellung für Administratoren |

Bei mehreren eingerichteten Saunen steht eine Instanzauswahl bereit. Ein Wechsel
führt zunächst durch eine Ladeansicht. Die passende Statusantwort gibt die
Bedienung der ausgewählten Sauna frei. Auch spätere Antworten werden ihrer
Instanz und dem zugehörigen Vorgang zugeordnet.

Die regelmäßige Aktualisierung erhält den Wert einer gerade bearbeiteten
Eingabe und übernimmt zugleich aktuelle Sperrattribute. Ein geöffneter Entwurf
bleibt bis zum Übernehmen oder Verwerfen bestehen. Auswahlmarkierungen beziehen
sich auf die vom Backend bestätigte Einstellung.

## Temperaturwahl und Eingabezustände

Die Temperaturanzeige verbindet Messwert und Sollschieber auf derselben Skala.
Der zulässige Sollbereich beginnt bei einer einstellbaren Untergrenze,
standardmäßig 60 °C, und endet bei der festen Obergrenze von 100 °C. Eine direkte
Wahl setzt eine konstante Solltemperatur. Das tatsächliche Heizen folgt den
Regel- und Schutzbedingungen des Betriebsablaufs.

Der Sollschieber verwendet die ganzen Celsiusgrade im gemeinsamen Bereich aus
Anzeigeskala und zulässiger Solltemperatur. Für Zeiger- und Tastatureingaben
müssen ein solcher Bereich und passende Bedienrechte vorliegen. Die Schnellwahl
hebt den aktuell bestätigten Wert hervor.
Benannte Programme zeigen ihren Namen und ihre Temperaturfolge. Die Hilfe zur
gleichmäßigen Verteilung verwendet denselben gültigen Entwurf wie die
Eingabefelder.

| Eingabezustand | Darstellung |
|---|---|
| Bestätigte Auswahl | Hervorgehobene Auswahl und wirksamer Programmname bzw. **Konstant** oder **Individuell** |
| Bearbeiteter Entwurf | Neutrale Fläche mit **Vorgemerkt** bzw. **Noch nicht übernommen** |
| Laufende Übertragung | Gesperrte Bestätigungsschaltfläche mit **Wird übernommen …** |
| Erfolgreiche Übernahme | **✓ Übernommen** für zwei Sekunden auf derselben Fläche |
| Eingabefehler oder fehlgeschlagene Anfrage | Erklärender Text bei weiterhin bearbeitbarem Entwurf |

## Zustand und Zeit

Die Steuerung stellt Phase und passende Zeit nebeneinander dar. Rechts steht
die Saunagangzahl. Die Ofenrückmeldung erscheint als **Ofen an**, **Ofen aus**
oder **Ofen unbekannt**. Die markierte Betriebs- oder Übersteuerungsauswahl
beschreibt den gewählten Modus; die Ofenrückmeldung beschreibt den beobachteten
Heizzustand.

| Situation | Zeitdarstellung |
|---|---|
| Aufheizen mit belastbarer Prognose | Geschätzte Zeit bis bereit, auf Fünf-Minuten-Stufen gerundet; unter fünf Minuten **noch unter 5 Minuten bis bereit** |
| Aktiver Saunagang | Seit Gangbeginn verstrichene Zeit |
| Angeforderte Ofenkühlung | Hinweis auf die Vorbereitung bis zur bestätigten AUS-Rückmeldung des Ofenschalters |
| Laufende Ofenkühlung | Verbleibende, aus bestätigter AUS-Zeit berechnete Dauer |
| Zeitlich bestimmte Startsperre | Verbleibende Wartezeit und zugehöriger Grund |
| Lichtnachlauf | Verbleibende Zeit der aktuellen Lichtfrist |

Die Aufheizprognose verbindet einen geeigneten Verlauf der letzten Sitzung mit
dem zunehmend belastbaren aktuellen Temperaturtrend. Technische Fristen und
der geschätzte mechanische Ofentimer stehen in den Details; dessen Status
erklärt, wann seine Zählung läuft oder pausiert.

## Farbrollen und Lesbarkeit

Der zentrale
[Darstellungskatalog](../custom_components/ha_sauna/defaults.json)
im Abschnitt `appearance`
definiert die Farbrollen und Standardwerte. Gespeicherte Anpassungen gelten je
Sauna. Rollen mit einem Home-Assistant-Standard übernehmen die entsprechende
Themenfarbe.

Die Auswahlfarbe kennzeichnet wirksame Optionen und die aktuelle Navigation.
Befehle verwenden standardmäßig die Aktionsfarbe `#216551`, Ausschalten und
Beenden die Stoppfarbe `#A34029`.
Nebenaktionen liegen auf neutralen Flächen mit dezenter Kontur. Beschriftung,
Markierung und Farbe vermitteln den jeweiligen Zustand gemeinsam. Der
Tastaturfokus erhält eine deutlich sichtbare Umrandung. Beim Überfahren eines
bedienbaren Buttons erscheint ebenfalls eine kontrastierende Umrandung ohne
Layoutverschiebung; der Text wird dabei nicht unterstrichen.

Die große Bedienkachel erhält bei einem aktuellen automatischen Phasenstatus
eine Tönung mit 8 % der Phasenfarbe auf dem Kartenhintergrund. Aus, Manuell und
unbekannte oder veraltete Zustände verwenden die neutrale Fläche. Text- und
Fokusfarben werden aus der tatsächlich dargestellten Hintergrundfläche
abgeleitet; dabei fließen auch Tönungen und ausgewählte Ereigniszeilen ein.

Temperaturbogen und Temperaturkurve teilen eine Messfarbe, ebenso
Feuchtebogen und Feuchtekurve. Im normalen Verlauf ist die Temperaturkurve
durchgezogen und die Feuchtekurve gestrichelt. Im Detailverlauf und bei den
Temperatur- und Feuchtemerkmalen der Erkennungskontrolle kennzeichnet die
Linienart die Messposition: oben durchgezogen, unten gestrichelt. Achsenzahlen, Titel und
Einheiten verwenden die Farbe der zugehörigen Messgröße; die Zeitachse bleibt
neutral. Die Messwerte und Skalenbeschriftungen der Bögen folgen derselben
Zuordnung. Gespeicherte frühere Messhöhenfarben bleiben kompatibel erhalten,
werden aber nicht mehr als unwirksame Farbfelder angeboten.

## Darstellungsentwurf und Skalen

Unter **Einstellungen → Darstellung** stehen die beiden Messgrößenfarben und
Anzeigeskalen zuerst. **Erweiterte Farben** enthält die übrigen Farbrollen.
Alle Farbfelder verwenden dieselbe Komponente aus Farbauswahl, Hexeingabe und
Rücksetzen auf den jeweiligen Standard.

Farbfeld und Hexeingabe bearbeiten denselben Darstellungsentwurf. Eine gültige
Änderung erscheint unmittelbar als Vorschau in der aktuellen Ansicht. Speichern
übernimmt den Entwurf für die ausgewählte Sauna; Verwerfen stellt den
gespeicherten Stand wieder her. **Standarddarstellung wiederherstellen** belegt
den Entwurf mit den Katalogvorgaben. Die Übernahme erfolgt über das gesonderte
Speichern der Darstellung.

Die Temperaturanzeige umfasst standardmäßig 40–110 °C, die Feuchteanzeige
0–60 %. Jede Skala hat eine kleinere Unter- als Obergrenze; Feuchtegrenzen liegen
zwischen 0 und 100 %. Ein Messwert außerhalb der Skala bleibt als Zahl sichtbar.
Der Bogen endet am Skalenrand und erhält einen Überschreitungshinweis.

Die Verlaufsachsen werden aus den Verlaufsdaten berechnet, die zulässigen
Solltemperaturen aus der Parameterdefinition. Eine Darstellungsänderung wird in
die bestehende laufende Ansicht übernommen. Beim allgemeinen
**Standardwerte wiederherstellen** bleiben die gespeicherte Darstellung und
die Gerätezuordnungen erhalten.

## Verlauf und Diagnose

Die aktuelle Auswahl heißt bei laufendem Betrieb **Laufende Sitzung**, sonst
**Letzte Sitzung**. Archivierte Sitzungen tragen ihren Beginn im Titel. Der
Standardverlauf verwendet die führende Messposition; der Detailverlauf erlaubt
den Vergleich beider Positionen.

| Element | Darstellung |
|---|---|
| Temperatur | Gemeinsame Messfarbe von Anzeige und Kurve, linke °C-Achse; im normalen Verlauf durchgezogen |
| Relative Luftfeuchte | Gemeinsame Messfarbe von Anzeige und Kurve, rechte Prozentachse; im normalen Verlauf gestrichelt |
| Messposition im Detailverlauf | Oben durchgezogen, unten gestrichelt |
| Tür offen | Fläche in der Türfarbe |
| Saunagang | Fläche in der Gangfarbe; vorläufiger Gang mit gestrichelter Umrandung |
| Aufguss | Zeitmarke in der Aufgussfarbe |
| Betriebsphase | Fläche nach der zugehörigen Phasenrolle |

Die Legende gliedert sich in **Messkurven**, **Phasen** und **Ereignisse**.
Konturen und Strichmuster ergänzen die bestehenden Farben.
Die Phasenflächen übernehmen die vom Ablaufkern gelieferten Zeitabschnitte.
Lüftungsereignisse und Ofenaktivität erscheinen ausschließlich in den technischen Details.

Die Übersichtskurve zeigt die Temperatur der führenden Messposition in derselben
Messfarbe; ihre Auswahlmarkierung ordnet den sichtbaren Ausschnitt in die
gesamte Sitzung ein. Hauptkurve und Übersicht verwenden dieselbe Zeitdomäne.
Die Wertanzeige ist unter der Kurve integriert und greift auf den
empfangenen Originalpunkt zurück.

[Kurvenaufbereitung und Ausschnittbedienung](livekurve.md)

Die Erkennungskontrolle stellt gespeicherte Detektormerkmale mit den
Schwellen der ausgewählten Sitzung dar. Ein vorhandener Signal- und
Metriknachweis verbindet das Ereignis mit seinem Diagnosemarker. Marker und
Ereigniszeile bilden ein wechselseitiges Navigationsziel. Haltezähler werden
als erfüllte Prüfpunkte beschriftet. Sichtbare Merkmalskurven und aktive
Bestätigungswerte geben den jeweiligen Nachweis wieder.

## Tastatur und zugängliche Beschriftung

Die Navigation kennzeichnet die aktuelle Seite mit `aria-current`. Der
Temperaturschieber und die Ausschnittgriffe erhalten numerische Werte und
Grenzen; lesbare Werttexte ergänzen die Zeitangaben der Übersicht. Fokus und
Auswahl bleiben visuell unterscheidbar. Ereignissprünge führen zum zugehörigen
Marker bzw. Listeneintrag.

Verlaufstabellen und Beschriftungen bleiben als DOM-Inhalt zugänglich.
