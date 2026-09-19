# Erste Testinstallation

Ziel: Home Assistant Core **2026.9.2**. Diese Anleitung beschreibt einen
Teststand, keinen Release und keine Hardwarefreigabe. Die laufende bestehende
Saunasteuerung bleibt bis zum bewusst vorgenommenen Wechsel maßgeblich.

## Installation durch den Nutzer über HACS

1. Eine aktuelle HA-Sicherung erstellen und deren Abschluss kontrollieren.
2. In HACS **Benutzerdefinierte Repositories** öffnen, das Repository
   `https://github.com/grisey/HA_Sauna` mit Typ **Integration** hinzufügen.
3. **HA Sauna** herunterladen. Vorher den im Abnahmebericht benannten Stand
   mit der von HACS angebotenen Commitkennung vergleichen.
4. Home Assistant selbst neu starten. Unter **Einstellungen → Geräte & Dienste →
   Integration hinzufügen → HA Sauna** einrichten. Noch nicht einschalten.

Einrichtung, Reload und Unload senden **Aus** an den ausgewählten Heizaktor.
Die Zuordnung des echten Schützes daher bei ausgeschaltetem Saunabetrieb
vornehmen; für die erste Prüfung können stattdessen Testentitäten gewählt werden.
Vor dessen Übernahme bestehende Regelungen wie unten beschrieben umstellen.

Ohne GitHub-Release lädt HACS den Standardbranch `main` des Repositories.
Der angebotene Stand muss den im [Abnahmebericht](abnahme.md) benannten
Implementierungscommit enthalten. Die Vorbereitung erzeugt keinen Release und
erhöht keine Versionsnummer. [Offizielle HACS-Regel](https://www.hacs.dev/docs/publish/integration/).

Alle Laufzeitdateien liegen unter `custom_components/ha_sauna`; `hacs.json`
legt Anzeigenamen und Mindestversion fest. Das Archiv liegt außerhalb des
Integrationsordners und wird bei HACS-Updates nicht ersetzt.

Installation, Neustart und Umstellung bestehender Automationen übernimmt der
Nutzer. SSH-Zugriff durch den Assistenten dient ausschließlich dem Lesen von
Status und Logs. Keine Dateiänderungen, Installation oder Schaltbefehle darüber.

## Entitäten zuordnen

| Rolle | Auswahl |
|---|---|
| Temperatur / Luftfeuchte oben | Obere Messposition, °C und % |
| Temperatur / Luftfeuchte unten | Untere Messposition, °C und % |
| Heizaktor | Switch mit der Schützstellung |
| Physische Bedienquelle | Binary Sensor für An/Aus oder Event-Entität für Umschaltimpulse |
| Licht | Dimmbares Saunalicht |
| Leistungsmessung | Optionaler Sensor mit Geräteklasse Leistung, W oder kW |
| Binärer Heiznachweis | Nur optional, wenn unabhängig vom Schütz vorhanden |

Ohne Leistungsmesser und unabhängigen Heiznachweis beide Felder leer lassen.
Der Heizzähler zählt dann bei Schütz EIN; die Details kennzeichnen die Schätzung.
Eine einfache Temperaturanstiegsgrenze wird nicht verwendet. Die weitergehende
Krümmungserkennung ist für diesen ersten Teststand zurückgestellt.

Bei Leistungsmesser dessen Watt-Schwelle ausdrücklich einstellen; der Wert
trennt Standby und Heizen. Bei Ausfall der optionalen Messung erscheint eine
Diagnose und die Zählung fällt auf die verbleibende Rückmeldung zurück.

## Werte vor dem ersten Heizversuch

Alle notwendigen Einstellungen besitzen [Standardwerte](parameter.md). Besonders prüfen: Solltemperatur 80 °C, Messwertalter 180 Sekunden, Schützrückmeldung 10 Sekunden, Störungsbestätigung 60 Sekunden; Sitzungsende nach 15 Minuten Betrieb-Aus, Aufgussbestätigung 12 Minuten, Heizzeitrücksetzung nach 10 Minuten Ofen-Aus und Nachlauf 8 Minuten. Gespeicherte örtliche Werte werden beim Update nicht überschrieben.

Die Lichthelligkeiten sind 35 % beim Einschalten, 15 % im Nachlauf und 5 % bei Zwangskühlung. Nach dem endgültigen Sitzungsende folgen 10 Minuten Lichtnachlauf bei 50 %, dann Licht aus. Alle Lichtwerte sind einstellbar. Optional bleiben Endtemperatur der Steigerung und Timer-Vorwarnzeit.

Die Ofenleistung für die Energieschätzung steht standardmäßig auf **4,5 kW**.
Bei vorhandenem Leistungsmesser wird stattdessen dessen Verlauf integriert.
Gemischte Mess-/Schätzwerte sind in der Anzeige gekennzeichnet.

Die Messgültigkeit muss die reale Aktualisierung der Quellen abdecken. Die
untere Temperatur wird nicht als pauschaler Ersatz für den oberen Sensor benutzt.
Während der Sitzung sind Solltemperatur, Erhöhung je Gang und Endtemperatur änderbar. Laufende Fristen und Heizsperren bleiben erhalten. Andere Parameter und Gerätezuordnungen bleiben gesperrt.

## Kurzer Testablauf

1. **Betrieb aus:** Nach Neustart steht die Integration auf Aus; alte Sessions
   starten nicht selbständig. Zuordnungen, Temperatur-/Feuchtewerte und Diagnosen
   in **Sauna → Details** kontrollieren. Einstellungen und ZIP-Export öffnen.
2. **Vor der Übernahme des echten Aktors:** Vorhandene Automationen und Thermostate,
   die denselben Schütz bedienen, identifizieren. Es darf nur eine aktive Regelung
   diesen Aktor steuern. Der Wechsel wird bewusst vor Ort vorgenommen.
3. **Beaufsichtigter Betrieb:** Start über Oberfläche, Schützstellung prüfen,
   Zählerzuwachs beobachten und ausdrücklich wieder ausschalten. Der physische
   Eingang und die Oberfläche müssen denselben Betrieb bedienen.
4. **Session:** Temperaturverlauf prüfen, Türöffnung und Aufguss beobachten.
   Vorläufiger und bestätigter Gang behalten ID und Beginn. Ohne Aufguss erfolgt
   keine Gangzählung; Nachlauf hält den Ofen aus. Kühlung und Licht prüfen.
5. **Archiv:** Betrieb beenden, Session-Unterbrechungsfrist abwarten, alte Session
   auswählen, ZIP herunterladen. Darin müssen Messwerte und Ereignisse enthalten sein.

Lange Fristen lassen sich für einen späteren gesonderten Test vor Sessionstart
verkürzen. Solche Testwerte anschließend im ausgeschalteten Zustand nach Ende
der Session wieder auf die gewünschten Betriebswerte setzen.

Der mechanische Timer wird zu Beginn weiterhin von Hand eingestellt. Die
Vier-Stunden-Anzeige ist eine Erinnerung; sie löst keine Steuerung aus.

## Rückweg

Betrieb ausschalten und reale Schütz-Aus-Stellung prüfen. Integration in
**Geräte & Dienste** deaktivieren. Falls nötig die zuvor erstellte HA-Sicherung
selbst wiederherstellen. Die bisherige Steuerung bewusst und ohne parallel aktive
zweite Regelung wieder aktivieren. Archiv und Export vorher sichern; zur
Rückkehr ist keine Archivlöschung nötig.


### Aktualisierung dieser Testfassung

In HACS den Branch **main** auswählen. Eine Commit-Kurznummer ist kein Branchname;
sie führt bei HACS zum falschen Downloadpfad und zu HTTP 404. Die Installation
und der erforderliche HA-Neustart erfolgen ausschließlich durch den Benutzer.
SSH wird nur lesend für die Diagnose verwendet.

Gerätezuordnungen lassen sich unter Geräte & Dienste → HA Sauna → Konfigurieren
→ Sensoren und Geräte ändern, sobald die Saunasitzung beendet ist. Den entkoppelten
Shelly-Eingang als Bedienquelle auswählen, die Art auf Taster stellen und den
wirklichen Schaltausgang getrennt als Heizschütz zuordnen. Der Shelly-Eingang muss
auf Geräteebene entkoppelt bleiben; die Integration ändert diese Geräteeinstellung nicht.

### Start abgewiesen oder weiterhin die alte Oberfläche sichtbar

Nach einem Update HA neu starten und die Oberfläche in der App vollständig neu
laden. Eine bereits geöffnete Seite kann noch den zuvor geladenen JavaScript-Code
verwenden. Der aktuelle Reiter heißt **Übersicht**, nicht **Normal**. Die Integration
vergibt anhand des Dateiinhalts eine neue Moduladresse, damit die nächste geladene
Seite nach einem Update die passende Oberfläche erhält.

Eine Startantwort mit HTTP 409 bedeutet eine abgewiesene Betriebsanforderung.
Die Oberfläche zeigt die konkrete Begründung der Integration an und behält diese
auch bei den automatischen Messwertaktualisierungen. Bei noch fehlenden Werten
unter **Details → Einstellungen & Export → Messwerte und Störungsüberwachung**
folgende Felder ergänzen und speichern:

- **Höchstalter eines Messwerts**: wie lange nach der letzten Meldung ein
  Sensorwert für die Regelung gültig bleibt; passend zum realen Meldeabstand.
- **Wartezeit auf die Schützrückmeldung**: Frist für die Bestätigung eines
  Schaltbefehls durch die zurückgemeldete Schützstellung.
- **Dauer bis zur bestätigten Störung**: wie lange ein zentraler Fehler
  durchgehend bestehen muss, bevor die Schutzabschaltung verriegelt.

Diese Felder werden bei fehlendem Eintrag mit 180, 10 und 60 Sekunden ergänzt. Vorhandene Einstellungen bleiben erhalten. Ein sehr knapp eingestelltes Messwertalter kann bereits bei gewöhnlichen Meldepausen zu kurzen Heizsperren führen. Bei gültigen Messwerten und Rückmeldungen wird die Starttaste freigegeben.
