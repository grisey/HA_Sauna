# HA Sauna

HA Sauna steuert Ofen und Licht in Home Assistant und zeichnet Saunasitzungen auf.

## Voraussetzungen

Für die Installation werden Home Assistant in der
[unterstützten Version](hacs.json) und eingerichtetes HACS benötigt. Die
[Gerätezuordnung](docs/einrichtung.md#sensoren-und-geräte-zuordnen) beschreibt die
erforderlichen Sensoren und Geräte.

## Installation über HACS

1. In HACS öffnet das Drei-Punkte-Menü den Dialog **Custom repositories**.
   Dort wird `https://github.com/grisey/HA_Sauna` mit dem Typ **Integration** über
   **ADD** hinzugefügt.
2. **HA Sauna** wird in HACS gesucht und geöffnet. **Download** installiert die
   Integration.
3. Anschließend erfolgt ein Neustart von Home Assistant.
4. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** wird
   **HA Sauna** ausgewählt.
5. Gerätezuordnung und weitere Einstellungen erfolgen nach
   [Sauna einrichten](docs/einrichtung.md).

Die HACS-Dokumentation beschreibt das
[Hinzufügen eigener Repositorys](https://www.hacs.dev/docs/faq/custom_repositories/)
und den [Download](https://www.hacs.dev/docs/use/repositories/dashboard/).

## Dokumentation

- [Sauna einrichten](docs/einrichtung.md): Voraussetzungen, Gerätezuordnung und
  dauerhafte Anlagenwerte in der Integrationskonfiguration.
- [Sitzung starten, ändern und beenden](docs/bedienung.md): Temperaturprogramm,
  Taster, manuelle Bedienung und Wiederaufnahme.
- [Automatischen Betrieb verstehen](docs/betrieb.md): Heizen, Bereitschaft,
  Licht, Timer und Schutzabschaltung.
- [Sitzungen aufbewahren und exportieren](docs/speicherung.md): Archiv,
  Löschung und Backup.

Ergänzende Regeln: [Gangerkennung](docs/gangmodell.md),
[Tür- und Feuchteerkennung](docs/erkennung.md), [Ofenkühlung](docs/ofenkuehlung.md)
und [Einstellungen und Entitäten](docs/parameter.md).
