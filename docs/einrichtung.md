# Einrichtungsrunbook

Dieses Runbook richtet sich an Home-Assistant-Administratoren. HA Sauna setzt
Home Assistant ab **2026.9.0** voraus; geprüft wurde der aktuelle Stand mit
**2026.9.2**.

## 1. Integration installieren

1. Fügen Sie in HACS `https://github.com/grisey/HA_Sauna` als
   benutzerdefiniertes Repository vom Typ **Integration** hinzu und laden Sie
   die Integration herunter.
2. Starten Sie Home Assistant neu.
3. Öffnen Sie **Einstellungen → Geräte & Dienste**, wählen Sie **Integration
   hinzufügen** und richten Sie **HA Sauna** ein.

Für ein Update wählen Sie die gewünschte Version in HACS und starten Home
Assistant anschließend neu. Bei einer Vorabversion wie `1.0.0-rc2` aktivieren
Sie den HACS-[Schalter für Vorabversionen](https://hacs.dev/docs/use/entities/switch/)
von HA Sauna. HACS legt diesen Schalter zunächst deaktiviert an; aktivieren Sie
gegebenenfalls zuerst die Entität. Solange nur Vorabversionen vorliegen, kann
HACS mit ausgeschaltetem Schalter einen Commit-Code als Update anbieten. Für die
RC-Reihe sollte der Schalter daher eingeschaltet bleiben und die HACS-
Versionsprüfung aktualisiert werden.

## 2. Rollen zuordnen

Wählen Sie die vorhandenen Entitäten nach ihrer Aufgabe aus, nicht nach ihrem
Namen oder Gerät. Erforderlich sind die obere und untere Messposition mit jeweils
Temperatur und Luftfeuchte, der Schalter des Heizschützes, ein Bedieneingang und
ein dimmbares Saunalicht. Optional lassen sich ein Leistungssensor sowie eine
unabhängige Heizrückmeldung ergänzen.

Die obere Messposition ist für die Temperaturregelung maßgeblich. Die untere
Messposition ergänzt die Erkennung und bleibt eine eigene Messhöhe.

## 3. Taster und Heizschütz getrennt einrichten

Ordnen Sie den **Saunataster oder Betriebsschalter** als Bedieneingang zu und
den **Heizschütz** separat als Heizaktor. Ein Taster steuert die Saunasitzung;
er schaltet den Schütz nicht unmittelbar. Bei einem Ereignistaster wählen Sie
den passenden Ereignistyp. Ein binärer Taster meldet Drücken und Loslassen;
HA Sauna erkennt das lange Halten aus der eingestellten Dauer. Ein dauerhaft
eingerichteter Betriebsschalter folgt seiner Ein-/Ausstellung.

## 4. Vor dem ersten Einsatz prüfen

Kontrollieren Sie unter **Details → Einstellungen & Export** die voreingestellten
Temperatur-, Licht-, Heizzeit- und Kühlwerte. Diese Werte sind einstellbare
Standards; bereits gespeicherte örtliche Einstellungen bleiben erhalten.
Prüfen Sie insbesondere, ob Messabstände, Schützrückmeldung und optionale
Leistungsmessung zur Anlage passen.

Unter **Temperaturprogramme** können Sie Namen, Start, Ende und Verteilung
ändern oder Programme hinzufügen. Wählen Sie außerdem die Vorgabe für den
Saunataster: die aktuelle Auswahl, eine konstante Temperatur oder eines dieser
Programme. Diese Wahl ist ebenfalls unter **Einstellungen & Export** verfügbar.

**Standardwerte wiederherstellen** ist für Administratoren nach Ende einer
Saunasitzung verfügbar. Es setzt Einstellungswerte, Temperaturprogramme und
Protokollstufe zurück. Sensoren, Geräte und die Taster-/Schalterkonfiguration
bleiben zugeordnet.

Die tägliche Nutzung beschreibt die [Bedienungsanleitung](bedienung.md).
Technische Zusammenhänge und alle Parameter stehen in [Betrieb](betrieb.md) und
[Parameter](parameter.md).
