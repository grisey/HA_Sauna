# Einrichtung

Dieser Leitfaden richtet sich an Home-Assistant-Nutzer mit Administratorrechten.
Diese Rechte reichen für die Einrichtung aus. Voraussetzung ist Home Assistant
ab **2026.9.0**; neuere Versionen sind ebenfalls zugelassen.

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
   ändert die Geräteeinstellung des Tasters nicht. Ein Ereignistaster sollte
   Drücken, langes Drücken und Loslassen melden können, damit das Licht den
   Abschluss bis zum Loslassen bestätigt.
5. Prüfen Sie vor der ersten Sitzung die voreingestellten Temperatur-, Licht-,
   Heizzeit- und Kühlwerte. Sie sind anpassbare Standards; vorhandene
   Einstellungen bleiben erhalten. Wählen Sie insbesondere Messwerte und
   Rückmeldungen, die zur tatsächlichen Anlage passen.

Für ein Update wählen Sie in HACS die gewünschte Veröffentlichung von **HA Sauna**
und starten anschließend Home Assistant neu. Wenn Sie eine Vorabversion wie
`1.0.0-rc1` verwenden, aktivieren Sie in Home Assistant den zu **HA Sauna**
gehörenden HACS-[Schalter für Vorabversionen](https://hacs.dev/docs/use/entities/switch/)
und schalten ihn ein. HACS legt diese Schalter standardmäßig als deaktivierte
Entitäten an; gegebenenfalls müssen Sie die Entität zuerst aktivieren.

Solange ausschließlich Vorabversionen veröffentlicht sind, kann HACS bei
ausgeschaltetem Vorabversionsschalter einen Commit-Code statt einer Versionsnummer
als Update anbieten – auch wenn derselbe Stand bereits installiert ist. Schalten
Sie für die RC-Reihe den Vorabversionsschalter ein und aktualisieren Sie die
HACS-Versionsprüfung.

Die Bedienung beginnt in der **Übersicht**. Zusätzliche Einstellungen,
Gerätezuordnungen nach Sitzungsende, Protokollierung und Archiv finden Sie unter
**Details**. Für das Protokoll wählen Sie bei Bedarf **INFO** für
Betriebsereignisse, **ERROR** für Fehler oder **DEBUG** für die Diagnose.

Unter **Temperaturprogramme** können Sie die benannten Programme ändern oder
eigene hinzufügen. Jedes Programm hat einen Startwert, einen Endwert und die
Anzahl der Temperaturstufen. Danach bleibt die Endtemperatur für beliebig viele
weitere Saunagänge erhalten. Wählen Sie außerdem, welche Temperaturwahl der
Saunataster beim Einschalten verwenden soll.

Mit **Standardwerte wiederherstellen** setzen Sie nach Sitzungsende die
Einstellungen einschließlich Temperaturprogrammen und Protokollstufe zurück.
Die Zuordnung Ihrer Sensoren, Geräte und des Bedieneingangs bleibt erhalten.

Berücksichtigen Sie die vorhandenen Sicherheitsfunktionen der Anlage. Diese
Anleitung beschreibt die Konfiguration in Home Assistant; sie ersetzt keine
Prüfung der konkreten Installation.

Weiterführende technische Hinweise stehen in der [Betriebsdokumentation](betrieb.md)
und im [Abnahmebericht](abnahme.md).
