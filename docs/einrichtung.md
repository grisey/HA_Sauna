# Einrichtung

Dieser Leitfaden richtet sich an Home-Assistant-Nutzer mit Administratorrechten.
Diese Rechte reichen für die Einrichtung aus.

1. Fügen Sie in HACS das Repository `https://github.com/grisey/HA_Sauna` als
   benutzerdefiniertes Repository vom Typ **Integration** hinzu und laden Sie es
   herunter.
2. Starten Sie Home Assistant neu und fügen Sie unter **Einstellungen → Geräte
   & Dienste** die Integration **HA Sauna** hinzu.
3. Ordnen Sie die vorhandenen Geräte nach ihrer Aufgabe zu: Temperatur und
   Luftfeuchte oben sowie unten, Heizaktor, Bedienquelle und dimmbares Licht.
   Optional können Sie einen Leistungssensor oder eine separate Heizrückmeldung
   auswählen.
4. Verwenden Sie bei einem Taster einen entkoppelten Eingang als Bedienquelle
   und ordnen Sie den tatsächlichen Heizaktor getrennt zu. Die Integration
   ändert die Geräteeinstellung des Tasters nicht.
5. Prüfen Sie vor der ersten Sitzung die voreingestellten Temperatur-, Licht-,
   Heizzeit- und Kühlwerte. Sie sind anpassbare Standards; vorhandene
   Einstellungen bleiben erhalten. Wählen Sie insbesondere Messwerte und
   Rückmeldungen, die zur tatsächlichen Anlage passen.

Die Bedienung beginnt in der **Übersicht**. Zusätzliche Einstellungen,
Gerätezuordnungen nach Sitzungsende, Protokollierung und Archiv finden Sie unter
**Details**. Für das Protokoll wählen Sie bei Bedarf **INFO** für
Betriebsereignisse, **ERROR** für Fehler oder **DEBUG** für die Diagnose.

Mit **Standardwerte wiederherstellen** setzen Sie nach Sitzungsende die
Einstellungen einschließlich Temperaturprogrammen und Protokollstufe zurück.
Die Zuordnung Ihrer Sensoren, Geräte und des Bedieneingangs bleibt erhalten.

Berücksichtigen Sie die vorhandenen Sicherheitsfunktionen der Anlage. Diese
Anleitung beschreibt die Konfiguration in Home Assistant; sie ersetzt keine
Prüfung der konkreten Installation.

Weiterführende technische Hinweise stehen in der [Betriebsdokumentation](betrieb.md)
und im [Abnahmebericht](abnahme.md).
