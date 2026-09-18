# Erkennungskandidat „Tür–Gang“
Stand: 18.09.2026. **Festgehaltener Offline-Erprobungskandidat.**
Gegenstand: schnelle Türereignisse, davon getrennte Lüftungseinordnung und Zwei-Sensor-Personen-/Aufgusserkennung.
Es wurden keine Änderungen an Home Assistant vorgenommen.

## 1. Grundlage und verbindliche fachliche Trennung

Datengrundlage ist ausschließlich der Recorderexport vom 17.09.2026, 18:00–24:00 Uhr MESZ, zusammen mit den Angaben des Nutzers.
Quelle: `sauna-recorder-2026-09-17.zip`.
SHA-256: `0a99e8421674561620ce9c278664822a85c896f125a4c82edc5b6564d5cc791d`.

Kanal 3 befindet sich auf Kopfhöhe der obersten Bank; Kanal 6 etwa 20–30 cm darunter.
Beide Kanäle sind im Normalbetrieb fest eingebunden. Ihre Temperaturen werden nicht gemittelt und nicht über einen festen Offset ineinander umgerechnet.
Bei einem verfügbaren Kanal werden dessen eigene Kriterien verwendet; eine sichtbare Fehlermeldung und Kennzeichnung im Sessionprotokoll gehören zum vorgesehenen Integrationsverhalten.

Aktualisierte Referenz: **15 bekannte Öffnungsepisoden**. Zu den 14 bisher gespeicherten Episoden kommt die vom Nutzer bestätigte kurze Gefäßentnahme um ungefähr 22:39:40 hinzu. Das Gefäß wurde entnommen und die Tür sofort geschlossen; dieses Ereignis ist kein gezieltes Zwischenlüften. Es fand zwischen Gang 3 und Gang 4 statt. Die alte Behauptung einer vollständigen Erkennung aller Türereignisse gilt nicht mehr.

Eine Türöffnung beendet keinen Gang. Kurzes Herausgehen einzelner Personen lässt einen bereits laufenden Gang und seine Aufgussbestätigung bestehen.
Erst hinreichend ausgeprägtes Durchlüften nach einem Aufguss qualifiziert den Gangabschluss. Aus Türkurven wird weder eine Personenzahl noch der konkrete Anlass einer Türbetätigung abgeleitet.

## 2. Messaufbereitung und Zeitbezug

Ein kausales Ein-Sekunden-Raster übernimmt jeweils den letzten bereits eingegangenen Recorderwert. Fehlende Werte werden nicht durch zukünftige Werte interpoliert. Ein rückwärtsgerichteter Fünf-Sekunden-Median glättet die Messwerte.

Für Trends wird der Median aller paarweisen Steigungen des jeweiligen rückwärtsgerichteten Fensters berechnet. Die Türtrends werden sekündlich, die Personentrends alle fünf Sekunden geprüft.

`detected_at` ist stets der tatsächliche Zeitpunkt der Offline-Entscheidung. Weder Ereignisse noch Freigaben werden zum Zwecke einer scheinbar schnelleren Reaktion rückdatiert. Die im Ergebnis zusätzlich angegebenen Temperaturminima sind nachträgliche Kurvenmerkmale und keine Eingaben aus der Zukunft.

**Es gibt keine separat gemessenen mechanischen Öffnungs- oder Schließzeiten.** Insbesondere dauerte die Gefäßentnahme nicht nachweislich 40 Sekunden: 40 Sekunden liegen zwischen den zwei Sensorauswertungsentscheidungen. Die physische Tür war nach Nutzerangabe sofort wieder geschlossen. Die Reaktion der Raumluft an den beiden Messpositionen kann länger dauern.

## 3. Schnelle Türerkennung

### Öffnung

Je verfügbarer Kanal müssen gleichzeitig gelten:

- Robuster Temperaturtrend über die letzten **8 Sekunden < −1,8 °C/min**.
- Abnahme der geglätteten relativen Feuchte innerhalb von **10 Sekunden** um mindestens **0,45 Prozentpunkte an K3** beziehungsweise **0,30 Prozentpunkte an K6**.
- Die gesamte Bedingung gilt **2 Sekunden** durchgehend.

Bei zwei Kanälen gilt die Schnittmenge; drei aufeinanderfolgende Ein-Sekunden-Prüfungen belegen zwei verstrichene Sekunden.
Es wird **keine große Mindest-Gesamtabkühlung** mehr abgewartet. Das ersetzt die alten Grenzwerte 1,50/1,20 °C aus dem konservativen Türkandidaten.

### Schließung

Je verfügbarer Kanal gilt der robuste **8-Sekunden-Temperaturtrend > +0,15 °C/min**, gemeinsam **3 Sekunden** durchgehend.
Die Feuchte erhält hier keine vorgegebene Richtung. Eine reine Abflachung noch negativer Temperaturtrends reicht nicht.
Der Türzustandsautomat erwartet abwechselnd Öffnung und Schließung. Seine Entscheidungen benötigen weder Gangstatus noch eine bestimmte Heizphase.

Drei Sekunden bleiben als kurze Messwertbestätigung bestehen: In der gezielten Vergleichsprüfung führen zwei Sekunden im K3-Alleinbetrieb zu einer zusätzlichen Aufspaltung der letzten Lüftungsepisode. Ebenso ist +0,10 °C/min in diesem Alleinbetrieb zu empfindlich.
Die viel längere Frist zur Einordnung des Durchlüftens beeinflusst die Türmeldung nicht.

## 4. Gesonderte Erkennung „Durchlüften bestätigt“

**Neue Kalibrierungsregel dieses Kandidaten, keine unabhängig gemessene Türöffnungsdauer:**

Nach einem Öffnungssignal wird pro Kanal das bisherige Temperaturmaximum aus den unmittelbar zurückliegenden **60 Sekunden** als feste Episodenbasis gespeichert. Die laufende Episode wird als ausgeprägtes Durchlüften bestätigt, sobald gleichzeitig

1. seit dem Öffnungssignal mindestens **60 Sekunden** verstrichen sind und die Episode noch nicht geschlossen wurde;
2. die Temperatur gegenüber ihrer gespeicherten Basis an jedem verfügbaren Kanal um mindestens **3,0 °C** gefallen ist.

Die Bestätigung entsteht im laufenden Verlauf, nicht rückblickend aus dem späteren Maximumverlust.
Sie wird innerhalb der Episode gespeichert. Bei der anschließenden Schließung wird diese abgeschlossene Lüftungsepisode als Kontext übernommen. Die nächste Öffnung verwirft den alten Vorbereitungskontext und beginnt eine neue Episode. Ein separates, willkürlich ablaufendes Vorbereitungszeitfenster wurde nicht eingeführt.

**Durchlüften allein aktiviert keinen Gang.** Ein ausbleibendes Merkmal bedeutet nur „Durchlüften nicht bestätigt“, nicht automatisch „Tür war nur ganz kurz geöffnet“. Die kurzen und die ausgeprägten Signale dienen verschiedenen Entscheidungen.

Für einen laufenden Gang gilt: Ein Öffnungssignal allein verändert weder Gangstatus noch Aufgussbestätigung. Eine nicht als Durchlüften bestätigte, wieder geschlossene Episode lässt ihn weiterlaufen. Gangabschluss und Zählererhöhung dürfen erst durch bestätigtes Durchlüften bei bereits bestätigtem Aufguss ausgelöst werden und müssen im späteren Ablaufkern einmalig verarbeitet werden.
Dieser Replay implementiert den Erkennungsteil, nicht den gesamten Heiz- und Sessionautomaten.

## 5. Personenfrüherkennung mit beiden Sensoren

Allgemeine Voraussetzung: Session offen und durch den **neuen** Türdetektor als geschlossen geführt.
Der alte Türhelfer wird nicht als Freigabe verwendet.

| Pfad | Trendfenster | Feuchtetrend K3/K6 | Temperaturtrend K3/K6 | Bestätigung | Lüftungskontext |
|---|---:|---:|---:|---:|---|
| Stark | 60 s | ≥0,50 / ≥0,50 Prozentpunkte/min | ≥0 / ≥0 °C/min | 10 s | nicht erforderlich |
| Schwach | 120 s | ≥0,14 / ≥0,14 Prozentpunkte/min | ≥1,00 / ≥0,80 °C/min | 30 s | letzte Öffnungsepisode als Durchlüften bestätigt |

Die bereits akzeptierten K3-Prüfwerte bleiben bestehen. Für den fest eingebundenen tieferen Kanal 6 wird beim schwachen Pfad **0,80 °C/min** verwendet; ein unverändertes Übertragen der K3-Grenze 1,00 °C/min hätte den schwachen Gang in der Zwei-Kanal-Kombination nicht vor dem Aufguss bestätigt.

Der Lüftungskontext ist in dieser Fassung ein zusätzliches Zulassungskriterium **nur für den weniger spezifischen schwachen Pfad**. Er ersetzt dessen Feuchte- und Temperaturbedingungen nicht. Ein ausgeprägtes Personensignal und ein Aufguss bleiben auch ohne bestätigte Vorgängerlüftung verwertbar.

Die Entscheidung ist nicht an den Moment der Türschließung gekoppelt. Sie kann später entstehen, wenn die Kombination tatsächlich bestätigt ist.
Ein erfolgreiches Startsignal wird im späteren Ablaufkern gehalten; sinkende Steigungen oder weitere Aufgussimpulse dürfen keinen neuen Gang erzeugen.

In dieser einen Session erzeugten die schon zuvor akzeptierten K3-Regeln ebenfalls keine zusätzlichen Personenmeldungen in den Vergleichsabschnitten. Der neue Kontext ist daher eine explizite sachliche Zuordnung des schwachen Signals; eine darüber hinausgehende allgemeine Verringerung der Fehlalarmquote ist damit nicht statistisch gemessen.

## 6. Aufguss als unabhängige, spezifischere Bestätigung

An jedem verfügbaren Kanal gelten, bezogen auf die Rohwerte:

- Feuchteanstieg in **10 Sekunden ≥2,00 Prozentpunkte**;
- Temperaturänderung im selben Zeitraum **≥−0,30 °C**;
- Kombination **3 Sekunden** bestätigt.

Session offen und Tür geschlossen bleiben Kontextvoraussetzungen.
Eine vorherige Personenerkennung oder bestätigte Vorgängerlüftung sind **keine Voraussetzung**.
Damit kann ein Aufguss einen zuvor nicht erkannten Gang nachträglich aktivieren und gleichzeitig bestätigen. Weitere Aufgüsse innerhalb desselben Gangs werden als Aufgussereignisse erfasst, nicht als weitere Gänge.

## 7. Ergebnisse des gemeinsamen Replay

### Vier Gänge, Normalbetrieb mit K3 und K6

| Gang | Durchlüften bestätigt | Türschließung erkannt | Personenfrüherkennung | Pfad | Erster Aufguss bestätigt |
|---|---|---|---|---|---|
| 1 | 20:44:49 | 20:45:15 | **20:46:05** | stark | 20:56:11 |
| 2 | 21:15:20 | 21:15:51 | **21:19:15** | schwach | 21:22:52 |
| 3 | 21:56:52 | 21:57:33 | **21:58:25** | stark | 22:03:07 |
| 4 | 22:49:33 | 22:50:13 | **22:51:00** | stark | 22:57:34 |

Uhrzeiten: 17.09.2026, MESZ. Die Personenentscheidungen fallen 50, 204, 52 und 47 Sekunden nach der jeweiligen neuen Schließungsmeldung.
Der schwache zweite Gang wird **217 Sekunden vor seinem ersten erkannten Aufguss** bestätigt.
Die Temperaturverluste der vier Vorlüftungen betragen K3/K6: **6,39/5,68; 5,79/5,01; 8,24/7,18; 7,68/6,78 °C**.

### Tür- und Ein-Sensor-Prüfung

| Modus | Bekannten Episoden zugeordnete Öffnungen | Schließungsmeldungen | Gänge vor Aufguss erkannt | Personen-/Aufgussmeldungen in Vergleichszeiten |
|---|---:|---:|---:|---:|
| K3 und K6 | 15/15 | 15 | 4/4 | 0 |
| nur K3 | 15/15 | 15 | 4/4 | 0 |
| nur K6 | 15/15 | 15 | 4/4 | 0 |

Die Gefäßentnahme wird um **22:39:47** als Öffnung und um **22:40:27** als Schließung verarbeitet; **keine Durchlüftungsbestätigung, keine Personenfrüherkennung und keine Aufgussbestätigung**. Ihre Maximalabkühlungen betragen 1,27/0,99 °C.

Gegenüber den 14 alten Meldungspaaren liegen die neuen Öffnungen im Median **18,6 Sekunden früher** (Spanne 2,9–66,1 Sekunden).
Die Schließungen liegen im Median **60,4 Sekunden früher** (Spanne 29,8–126,8 Sekunden).
Sie liegen **10–20 Sekunden nach dem späteren Temperaturminimum beider Kanäle**, statt 25–40 Sekunden beim vorherigen konservativen Kandidaten. Dies quantifiziert Auswertungsverzögerung gegenüber Kurvenmerkmalen, nicht einen Fehler gegenüber unabhängigen Kontaktzeiten.

### Alle Tür-/Lüftungsepisoden

| Nr. | Öffnung erkannt | Schließung erkannt | Durchlüften bestätigt | maximaler Abfall K3, °C | maximaler Abfall K6, °C |
|---|---|---|---|---:|---:|
| 1 | 19:37:54 | 19:38:43 | — | 1.80 | 1.41 |
| 2 | 19:57:29 | 19:58:21 | — | 3.56 | 2.85 |
| 3 | 20:30:31 | 20:31:39 | 20:31:31 | 4.21 | 3.24 |
| 4 | 20:32:54 | 20:34:03 | 20:33:54 | 4.41 | 3.55 |
| 5 | 20:43:49 | 20:45:15 | 20:44:49 | 6.39 | 5.68 |
| 6 | 20:59:10 | 21:03:01 | 21:00:10 | 8.79 | 6.94 |
| 7 | 21:14:20 | 21:15:51 | 21:15:20 | 5.79 | 5.01 |
| 8 | 21:27:15 | 21:30:07 | 21:28:25 | 7.74 | 6.47 |
| 9 | 21:33:50 | 21:34:40 | — | 5.21 | 4.30 |
| 10 | 21:55:52 | 21:57:33 | 21:56:52 | 8.24 | 7.18 |
| 11 | 22:11:09 | 22:15:28 | 22:12:27 | 9.12 | 7.04 |
| 12 | 22:39:47 | 22:40:27 | — | 1.27 | 0.99 |
| 13 | 22:48:33 | 22:50:13 | 22:49:33 | 7.68 | 6.78 |
| 14 | 23:02:23 | 23:15:40 | 23:03:53 | 18.50 | 15.10 |
| 15 | 23:35:35 | 23:37:13 | 23:36:35 | 3.49 | 3.68 |

### Parameter- und Rasterprüfung

45 Einzelfaktor-/Sensormodusvarianten wurden zusätzlich gerechnet. **43** erhalten 15 Türpaare und die vier Früherkennungen ohne Vergleichsauslösungen.
Die beiden abweichenden Varianten sind eine Schließungsgrenze von 0,10 °C/min beziehungsweise eine Schließungsbestätigung von nur zwei Sekunden, jeweils nur mit Kanal 3. Sie erzeugen eine zusätzliche Aufspaltung um 23:03 Uhr. Die festgehaltenen Werte 0,15 °C/min und drei Sekunden vermeiden diese Aufspaltung.

Alle fünf Zeitversätze des Fünf-Sekunden-Personenrasters erkennen weiterhin vier Gänge ohne zusätzliche Vergleichsauslösungen.
Die Zwei-Kanal-Erkennung des schwachen Gangs liegt dabei zwischen **21:18:58 und 21:19:15**.

Das ist eine örtliche Kalibrierungsprüfung derselben Session, keine Prüfung aller beliebigen Parameterkombinationen.

## 8. Grenzen und unverändert offene Punkte

Die mechanischen Türzeiten und unabhängige Eintritts-/Austrittszeitpunkte liegen nicht vor. Das Erkennungstiming kann an den Messkurven beurteilt, aber nicht als sekundengenaues Türkontakt-Timing validiert werden.
Nur ein Einzelpersonengang liegt als schwaches Referenzmuster vor.

Die kurze Gefäßentnahme belegt die Trennung einer kleinen Türreaktion von stärkerem Durchlüften. Ein kurzer Austritt einer Person **innerhalb** eines bestätigten Gangs ist nicht zusätzlich annotiert. Dessen Fortsetzung ist die vereinbarte Verarbeitungsregel, nicht ein hier neu bewiesener Testfall.

Der feste Ausfall eines Kanals über das Replay wurde geprüft. Dynamische Ausfälle, automatische Hardwarediagnose, eingefrorene plausible Messwerte und das tatsächliche Wiederbeitreten eines Sensors wurden mit diesem neuen Kandidaten nicht neu getestet. Die 888 Umschaltprüfungen des vorherigen konservativen Türkandidaten werden nicht auf diese geänderten Regeln übertragen.

Fehleranzeige, Wiederanlaufverhalten und Ersatztemperatur für das eigene Thermostat bleiben Aufgaben der späteren Integration; es wird kein Temperaturoffset erfunden.
Die Regeln wurden anhand derselben Messdaten entwickelt und geprüft. Eine allgemeine Sensitivität oder Spezifität für andere Sessions ist daraus nicht berechnet.

## 9. Einzige Parameterquelle und Reproduktion

`parameter.json` enthält sämtliche numerischen Detektor-Prüfwerte. In der geplanten Integration werden dieselben Größen als einheitliche, veränderliche Entitäten geführt. Dort darf keine zusätzlich konsumierte JSON-/YAML- oder Code-Konfiguration entstehen. Das JSON ist hier der eingefrorene Parameterschnappschuss des Offline-Kandidaten.

Es werden keine Entitätsnamen eines bereits installierten Systems behauptet. Zeitgrößen: Grundraster, Medianfenster, Türtrend-/Feuchtefenster, beide Türbestätigungen, Lüftungsrückblick und -mindestdauer, Personenprüfraster, beide Personentrendfenster und -bestätigungen sowie Aufgussfenster und -bestätigung. Amplituden- und Steigungsgrenzen stehen getrennt von Zeitparametern.

```sh
python3 replay.py /pfad/sauna-recorder-2026-09-17.zip \
  --parameters parameter.json --out ergebnis.json --tests
```

Abhängigkeiten: `numpy`, `pandas`.
Der Originalexport wird nur gelesen. Bewertungsannotationen im Code werden nicht zur Detektion genutzt.
`ergebnis.json` enthält die Ereignisse aller drei Betriebsarten, sämtliche durchgeführten Varianten und je Öffnung konkrete State-IDs und Zeilennummern aus `states.jsonl`.
