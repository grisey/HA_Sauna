# Konfigurierbare Parameter und verknüpfte Entitäten

## Vorrangige Nutzerpräzisierung vom 19.09.2026

Diese Regeln ersetzen die älteren Aussagen zur einheitlichen Heizzeit und zur
Thermostatbandbreite weiter unten:

- Solltemperatur bezieht sich auf den oberen Sensor während des Gangs.
  Bereitschaftsziel = Solltemperatur + einstellbarer Aufschlag (Standard 5 °C).
  Erst beim Erreichen dieses Ziels wird Bereitschaft gemeldet. Ausschalten am
  Bereitschaftsziel, Wiedereinschalten um die einstellbare Hysterese darunter
  (Standard 3 °C), unter Beachtung des separaten Thermostat-Cooldowns.
- Tatsächliches Heizbudget zunächst standardmäßig 90 Minuten. Nach dem ersten
  abgeschlossenen Kühlvorgang derselben Session wird der eingestellte Wert
  einmalig um standardmäßig 30 Minuten reduziert. Danach bleibt er konstant;
  eine neue Session beginnt wieder mit dem ursprünglichen Wert. Die verbrauchte
  Heizzeit des nächsten Intervalls beginnt bei null. Eine reine lokale
  Rücksetzung nach Ofen-Auszeit gilt nicht als abgeschlossene Kühlung.
- Zwangskühlung dauert standardmäßig 15 Minuten. Nachlauf hält den Ofen aus
  und sperrt alle neuen Gangstarts. Nachlaufanrechnung bleibt vollständig und
  einmalig, sein Endzeitpunkt bleibt auch bei Betrieb-Aus unverändert.
- Alle genannten Zahlen sind änderbare Ausgangswerte in der zentralen
  Parameterverwaltung. Die Grundkonfiguration bleibt während einer gesamten
  Session einschließlich kurzer Betriebsunterbrechungen gesperrt.
- Fehlende Schutzwerte werden nicht durch vermeintlich sichere Defaults ersetzt.
  Ohne gültige obere Temperatur und konfigurierte Abschalttemperatur bleibt
  die Heizentscheidung aus; Ein-Sensor-Erkennung ist davon getrennt.


Stand: 18.09.2026. Fachliche Vorgabe für die eigene Integration; die
Konfigurationsoberfläche ist noch nicht implementiert.

## Vereinbart: Einstellungen in der Integration

Notwendige anpassbare Größen werden direkt in Home Assistant konfigurierbar,
über einheitliche eigene Parameterentitäten. Änderungen benötigen keine
Bearbeitung von Python-Code oder YAML. Eingaben werden validiert und dauerhaft
über die gemeinsame Parameterverwaltung gespeichert.

**Pro einstellbarer Größe gibt es genau eine konsumierte Quelle.** Erkennung,
Ablaufsteuerung und Timer erhalten ihre Eingaben daraus. Werden dieselben Werte
in einem Konfigurationsdialog und als Entitäten angezeigt, bearbeiten beide
Zugänge denselben Eintrag; sie erzeugen keine zweite Parametrierung.

Die Bedienung gliedert sich in Entitätszuordnung, Gangablauf, Heizregelung und
Erkennung. Kalibrierungsdetails bleiben in einem erweiterten Bereich zugänglich.
Namen, Einheiten, zulässige Bereiche und Schrittweiten werden bei der Umsetzung
verbindlich festgelegt, ohne notwendige Größen im Code zu verstecken.

## Verknüpfte Entitäten auswählen

**Alle externen Entitätsverknüpfungen werden im Konfigurationsbereich ausgewählt,
nicht als konkrete Entity-IDs im Programm hinterlegt.** Die Zuordnung erfolgt
bei der Einrichtung und bleibt später im selben Konfigurationsbereich änderbar.
Dafür werden passende Auswahlfelder verwendet, keine vorausgefüllten privaten
Entity-Namen aus der Referenzinstallation.

| Funktion | Auszuwählende Quelle beziehungsweise Ziel |
|---|---|
| Obere Messposition | Je eine Temperatur- und Luftfeuchteentität. |
| Untere Messposition | Je eine Temperatur- und Luftfeuchteentität. |
| Heizung | Schaltbare Entität des Heizaktors; zusätzliche Rückmeldungsquelle, sofern die Geräteanbindung sie verwendet. |
| Physische Bedienung | Eingangs-/Ereignisquelle des Schalters oder Tasters, getrennt vom Heizaktor. |
| Saunalicht | Dimmbare Lichtentität für die vereinbarte Zustandsanzeige. |
| Ergänzende Anbindungen | Statusquellen, Medien- und Benachrichtigungsziele ebenfalls auswählbar, soweit die jeweiligen Funktionen angebunden werden. |

Die obere und untere Position sind fachliche Rollen. K3 und K6 bezeichnen die
Sensoren des festgehaltenen Kalibrierungsbestands, keine im Produkt fest
verdrahteten Kanalnummern. Der HA-Adapter ordnet die ausgewählten Quellen den
Rollen zu; der Ablaufkern verarbeitet Rollen und Messdaten. Eine andere
Entitätsauswahl verändert nicht automatisch die Erkennungsparameter oder den
Temperaturbezug. Der eingefrorene Replay samt Referenznamen bleibt unverändert.

Die Auswahl wird nach benötigtem Entitätstyp und Geräteklasse eingegrenzt.
Zusätzlich sind Messgröße, Einheit und benötigte Funktionen zu prüfen, etwa
Dimmbarkeit beim Saunalicht. Ein passender Anzeigename allein belegt keine
Eignung. Dieselbe Temperatur- oder Feuchtequelle darf nicht beide Messpositionen
als scheinbar unabhängige Sensoren belegen. Beide Positionen werden für den
Normalbetrieb konfiguriert; die vereinbarte Ausfallbehandlung ist kein Ersatz
für eine fehlende Zuordnung.

Die gespeicherte Zuordnung ist die einzige aktuelle Quelle für das Lesen und
Schalten externer Entitäten. Bei fehlender oder ungültiger Zuordnung wird keine
Entität anhand ihres Namens erraten. Der bekannte Ausfall eines konfigurierten
Sensors führt zum vereinbarten Ein-Sensor-Betrieb mit Fehlermeldung. Technische
Umbenennung und Geräteersatz sind bei der späteren Adapterimplementierung
nachvollziehbar zu behandeln; Messhistorien verschiedener Quellen dürfen nicht
unbemerkt vermischt werden.

Eigene Ausgaben der Integration – Thermostat, Phasenanzeige, Gangdauer und
Zähler – werden aus dem eigenen Modell erzeugt. Dafür müssen nicht die alten
Helfer und Automationen als zweite Steuerungsquelle verknüpft werden. Auch eine
eigene Oberfläche darf keine konkreten Entity-IDs der Referenzinstallation
voraussetzen. Die historische Herkunft einer Messung bleibt beim Archivdatum
und wird nicht durch eine spätere Neuzuordnung umgeschrieben.

Technische Grundlage der vorgesehenen Auswahloberfläche, eingesehen am 18.09.2026:

- https://www.home-assistant.io/docs/blueprint/selectors/#entity-selector
- https://developers.home-assistant.io/docs/core/integration/config_flow/
- https://developers.home-assistant.io/docs/core/integration/options_flow/

Diese Quellen beschreiben HA-Oberflächen und Konfigurationsmechanismen. Die
Zuordnungsregeln sind Anforderungen dieser Integration, noch kein vorhandener
Config Flow oder bereits getesteter Live-Adapter.

## Grundwerte, Relationen und Ergebnisse

| Art | Behandlung |
|---|---|
| Fachlicher Grundparameter, etwa Bestätigungsfrist oder Rücksetz-Auszeit | Einmal einstellen und an alle Verbraucher übergeben. |
| Begründeter Relationsparameter, etwa ein Verhältnis zusammenhängender Messfenster | Konfigurierbar halten; der Code bildet die Beziehung ab. |
| Abgeleitete Größe, etwa wirksame Heizdauer, Erkennungsschwelle oder Restzeit | Berechnen und lesbar anzeigen; nicht nochmals unabhängig einstellen. |
| Parameterschnappschuss für Replay oder Sessionarchiv | Herkunft und Auswertung dokumentieren; keine konkurrierende aktuelle Laufzeitquelle. |

Es sollen möglichst wenige voneinander unabhängige Absolutwerte erforderlich
sein. Eine Relation wird nur eingeführt, wenn sie einen sachlichen Zusammenhang
abbildet. Einen festen Zahlenwert durch einen ebenso unbegründeten Faktor zu
ersetzen, genügt diesem Ziel nicht. Sicherheitsgrenzen werden nicht ungeprüft
von adaptiven Messgrenzen abgeleitet.

## Aufgussbestätigungsfrist

Die übliche Gangdauer beträgt nach Nutzerangabe ungefähr 15 Minuten. Für die
Bestätigung eines vorläufigen Gangs wurden **12 oder 13 Minuten ab der
zugeordneten Türschließung** als angemessen benannt. Die Frist wird einstellbar;
es ist noch nicht zwischen diesen Ausgangswerten entschieden.

`Fristende = zugeordneter Gangbeginn + eingestellte Bestätigungsfrist`

Die spätere Personenfrüherkennung verschiebt den Bezugspunkt nicht. Eine kurze
Türbetätigung desselben Gangs ändert dessen Beginn und damit das Fristende nicht.
Die ungefähre Gangdauer von 15 Minuten ist kein automatisches Ende eines durch
Aufguss bestätigten Gangs. Eine Kopplung der Frist an einen festen Anteil der
Gangdauer wurde nicht festgelegt. Folgen eines unbestätigten Fristablaufs stehen
mit ihrem Entscheidungsstatus im [Gangmodell](gangmodell.md).

## Diskutierter Ansatz: relative Erkennungsgrenzen

Als mögliche Überarbeitung wurde besprochen, aktuelle Änderungen gegenüber dem
lokalen Vorverlauf und dessen üblicher Schwankungsbreite zu bewerten. Kanal 3
und Kanal 6 behalten dafür eigene Bezugswerte. Wenige gemeinsame
Empfindlichkeitsparameter könnten mehrere absolute Einzelgrenzen ersetzen.

Ebenfalls zu prüfen ist die Zusammenfassung von Messfenstern auf eine kurze
Zeitskala für Tür- und Aufgussereignisse sowie eine längere für Personenmuster.
Die Fenster müssen zur tatsächlichen Messaktualisierung und zum Ereignis passen;
sie werden nicht aus der ungefähren Saunagangdauer abgeleitet.

Ein möglicher robuster Vergleichsmaßstab ist die mediane absolute Abweichung.
Ein dafür verwendeter Hintergrund müsste aus geeigneten ereignisarmen Abschnitten
stammen und bei einem möglichen Ereignis zunächst festgehalten werden. Eine
technische Untergrenze wäre nötig, damit nahezu rauschfreie Werte keine beliebig
großen relativen Signalstärken erzeugen.

**Diese relative Auswertung ist ein noch ungeprüfter Änderungsvorschlag.** Sie
wird nicht durch die Zustimmung zur Konfigurierbarkeit automatisch zum
vereinbarten Detektor. Konfigurierbarkeit funktioniert auch mit den bisherigen
empirisch kalibrierten Prüfwerten. Der eingefrorene Kandidat und seine
Reproduktionsdaten bleiben unverändert, bis eine geänderte Auswertung besprochen
und gesondert gegen die vorhandenen Referenzereignisse geprüft wurde.

## Änderungssperre während einer Session

Nutzerklarstellung vom 19.09.2026: Änderungen der Grundkonfiguration während
einer Session sind nicht vorgesehen. Dialoge und Parameterentitäten verwenden
dieselbe Sperre. Eine kurze Betriebsunterbrechung hebt sie nicht auf; erst das
Ende der Session-Unterbrechungsfrist ermöglicht Änderungen. Deshalb werden
laufende Fristen nicht aufgrund geänderter Grundparameter neu berechnet.

Nicht vereinbarte sichere Ausgangswerte werden weiterhin ausdrücklich
eingegeben und nicht vom Programm erfunden.
