# Architektur und Implementierungsstand

## Übergeordnete Struktur

```text
Sauna-Instanz / ConfigEntry.runtime_data
├── Konfiguration: Entitätsrollen und Parameter
├── Geräte- und Messanbindung
├── Aktuelle Session
│   ├── Tür-/Lüftungsepisoden und Gänge mit Aufgüssen
│   ├── Heizzeitführung
│   └── Nachlauf, Zwangskühlung und Fristen
└── Archivzugriff
```

Konfiguration und Archiv leben außerhalb einer einzelnen Session. Sämtliche
sessionbezogenen Ablaufobjekte gehören dagegen zu deren gemeinsamem Lebenszyklus.
Ein Sessionwechsel darf keine gültige Geräte-/Sensorstörung beseitigen.

## Paket 1

Das einrichtbare Grundgerüst besitzt Config Flow und Optionsdialog, geprüfte
Entitätsrollen, einen zentralen Parameterkatalog und ein Laufzeitobjekt. Der
neue Sessionrahmen verwendet den vorhandenen `core/timeline.py` unverändert.
Der Controller verarbeitet dessen bereits definierte Ereignisse; technische
Fristidentitäten verhindern Aufrufe für falsche Sessions oder ersetzte Fristen.

Es gibt noch keine Live-Messlistener, Betriebsaktionen, tatsächliche Heizregelung,
Aktorzugriffe oder Archivschreiber. Setup startet keine Session. Die Session-
und Fristmethoden sind derzeit Kern-/Testaufrufe, keine Bedienaktionen in HA.
Dateien, Konfigurationsumfang und Tests: [Umsetzungspaket 1](umsetzung.md).

## Zielaufteilung

| Baustein | Verantwortung |
|---|---|
| Messadapter | Originalmessungen beider Rollen mit Mess-/Empfangszeit und Gültigkeit übernehmen. |
| Erkennung | Kausale Aufbereitung, Türereignisse, Durchlüften, Personenmuster und Aufguss. |
| Ablaufkern | Session, Gang, Bestätigungsstand, Fristen und zeitliche Zuordnung verwalten. |
| Thermostatkern | Heizentscheidungen aus Temperatur, Parametern und Ablauf-/Schutzbedingungen berechnen. |
| HA-Adapter | Eingänge geordnet weitergeben, Entitäten bereitstellen und später zulässige Gerätebefehle ausführen. |
| Archiv | SQLite unter dem Konfigurationsverzeichnis, Originalauflösung, HA-Backup und ZIP-Export. |
| Darstellung | Gegenwart und historische Intervalle aus demselben Datenmodell lesen. |

Nur der Ablaufkern schreibt den fachlichen Sessionzustand. Detektoren melden
Ereignisse, setzen aber weder GUI-Phasen noch Heizaktoren. Schaltentscheidung und
Rückmeldung sind getrennt. Rohmessungen werden später bereits am Eingang dem
Archiv zugeführt; geglättete Rechenwerte ersetzen keine Originalmessung.

## Eine Quelle je Zustand und Parameter

`ConfigEntry.options` hält Entitätszuordnung und veränderliche Parameter. Der
Kern bekommt einen unveränderlichen validierten Stand. Oberfläche und Kern
verwenden denselben Katalog; spätere Number-Entitäten denselben Schreibweg.
Abgeleitete Zustände, Restzeiten und Bestätigungsstand sind keine unabhängig
schreibbaren Einstellungen. Konkrete Entity-IDs gehören nur zur HA-Zuordnung.

Die GUI leitet Saunagang und Bestätigungsstand aus den Gang-/Aufgussobjekten ab.
Rückwirkend zugeordnete Intervalle ändern keine vergangenen Schaltbefehle und
keine alten HA-Zustandswechsel. Die genaue Gangbedeutung steht im
[Gangmodell](gangmodell.md), die Zeitbasis im [Zeitmodell](zeitmodell.md).

## Weiteres Vorgehen

Die in [Umsetzung](umsetzung.md) abgegrenzten Pakete werden nacheinander gebaut
und getestet. Der eingefrorene Kandidat bleibt als Referenz erhalten; eine
adaptive Neufassung wird nicht nebenbei eingeführt. Die lokale Prüfung des
Gerüsts ersetzt keine HA-Systemabnahme, keinen Backup-/Restore-Test und keine
Freigabe der Heizungssteuerung.
