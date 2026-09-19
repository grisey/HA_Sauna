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

Ohne GitHub-Release lädt HACS den Standardbranch des Repositories. Solange der
geprüfte Arbeitszweig noch nicht in `main` übernommen wurde, bietet HACS daher
noch den alten Stand an. Die Vorbereitung erzeugt keinen Release und erhöht
keine Versionsnummer. [Offizielle HACS-Regel](https://www.hacs.dev/docs/publish/integration/).

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

Die vereinbarten Defaults stehen in [Parameter](parameter.md). Folgende Werte
haben ausdrücklich keinen angenommenen Default und müssen gesetzt werden:

- Session-Unterbrechungsfrist, Aufgussbestätigungsfrist,
  Heizzeit-Rücksetz-Auszeit und Nachlaufdauer.
- Solltemperatur oben; Messwert-Gültigkeitsdauer;
  Rückmeldungsfrist für den Schütz und Bestätigungsfrist zentraler Ausfälle.
- Gewünschte Lichthelligkeit während Kühlung.
- Nur bei Leistungsmesser: Watt-Schwelle. Optional: Timer-Vorwarnzeit.

Die Ofenleistung für die Energieschätzung steht standardmäßig auf **4,5 kW**.
Bei vorhandenem Leistungsmesser wird stattdessen dessen Verlauf integriert.
Gemischte Mess-/Schätzwerte sind in der Anzeige gekennzeichnet.

Die Messgültigkeit muss die reale Aktualisierung der Quellen abdecken. Die
untere Temperatur wird nicht als pauschaler Ersatz für den oberen Sensor benutzt.
Parameter bleiben während der gesamten Session gesperrt, auch bei kurzem Aus/Ein.

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
