# Einrichtung

Home Assistant ab **2026.9.0**. HACS-Repository:
`https://github.com/grisey/HA_Sauna`, Typ **Integration**.
Installation und Updates erfordern einen Home-Assistant-Neustart.

## Anbindung

HA Sauna übernimmt Ofen und Licht; parallele Steuerungsautomationen müssen
deaktiviert sein. Externe Lichttaster werden als manuelle Eingriffe ausgewertet.

Eine vollständige Temperatur-/Feuchteposition, Heizschütz, Bedieneingang und
dimmbares Licht sind erforderlich. Die [Entitätsrollen](parameter.md#entitätsrollen)
definieren unterstützte Typen und Einheiten.
[Direkte Präsenzführung](gangmodell.md#direkter-präsenzsensor) verwendet weiterhin
die thermische Türerkennung.
