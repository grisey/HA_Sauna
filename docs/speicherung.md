# Sessionarchiv, HA-Backup und Export

Stand: 18.09.2026. Anforderungen an Auflösung, Aufbewahrung, Backup, Export und
Neustartverhalten sind festgelegt. Mit der Zustimmung zu den Ausführungen sind
auch SQLite unter dem HA-Konfigurationsverzeichnis und der ZIP-Export als
technische Richtung angenommen. Die Umsetzung und Backup-Abnahme stehen aus.

## Verbindliche Anforderungen

**Sessiondaten bleiben langfristig vollständig aufgelöst erhalten, unabhängig
vom Recorder.** Maßgeblich ist die tatsächlich empfangene Messauflösung beider
Sensoren. Originalmessungen werden nicht nachträglich durch Minutenmittelwerte
oder langfristige Statistiken ersetzt. Eine automatische altersbedingte Löschung
oder verlustbehaftete Verdichtung ist nicht vorgesehen.

Zu den Sessiondaten gehören Temperatur und Feuchte beider Höhen, Mess-/Empfangszeiten,
Sensorstatus, erkannte Tür-, Personen- und Aufgussereignisse, Gangzuordnungen,
Heizintervalle, Nachlauf und Kühlvorgänge sowie Entscheidungsgründe und die
jeweils verwendeten Parameterstände. Parameterschnappschüsse belegen vergangene
Entscheidungen; sie sind keine zweite aktuelle Parameterquelle.
Die Zuordnung der jeweils verwendeten Entitäten zu Messpositionen und Aktoren
gehört zur Herkunft dieser Daten. Eine spätere Neuzuordnung in der Konfiguration
verändert nicht die Herkunft bereits archivierter Messungen oder Schaltungen.

Beginn, erste Erkennung und Aufgussbestätigung bleiben getrennt erhalten und
über Ereignisreferenzen verbunden. Eine spätere Zuordnung überschreibt nicht
den ursprünglichen Erkenntnisstand. Messlücken werden gekennzeichnet; künstlich
interpolierte oder mehrfach auf einem Prüfraster verwendete Werte werden nicht
als zusätzlich empfangene Originalmessungen ausgegeben.

Die Erfassung schreibt während der Session fortlaufend, nicht erst an ihrem
Ende. Das Archiv muss unabhängig vom flüchtigen Zustand des Ablaufkerns lesbar
bleiben. Schutz vor Datenverlust und automatische Betriebsfortsetzung sind
verschiedene Anforderungen.

## HA-Backup ist Bestandteil der Speicherung

Das vollständige Sessionarchiv einschließlich seiner Zuordnungen und der für
das Lesen erforderlichen Metadaten muss von einem HA-Backup erfasst werden,
das den Home-Assistant-Konfigurationsbereich enthält. Ein reines App-Backup
oder ein Backup ohne diesen Bereich ist kein Archivbackup dieser Integration.

Die HA-Dokumentation nennt `config` als Bestandteil einer vollständigen
Sicherung [1]. Integrationen können über `async_pre_backup` und
`async_post_backup` Daten für eine konsistente Sicherung vorbereiten [2].
Daraus folgt für die Umsetzung: Ablage und Sicherung müssen gemeinsam entworfen
werden. Das bloße Kopieren einer gleichzeitig beschriebenen Datenbankdatei
wird nicht als ausreichender Konsistenznachweis behandelt.

Vor Freigabe wird mit einem tatsächlich erzeugten HA-Backup geprüft, dass das
Archiv vorhanden ist und nach Wiederherstellung dieselben Messungen und
Ereigniszuordnungen enthält. Das ist eine noch auszuführende Abnahmeprüfung,
kein bereits bestandenes Ergebnis. Ausschluss der Recorderhistorie darf nicht
versehentlich auch das unabhängige Saunaarchiv ausschließen.

## Neustart

**Automatische Wiederaufnahme eines laufenden Saunabetriebs nach HA-Neustart
ist nicht erforderlich, außer sie lässt sich sehr einfach umsetzen.** Dafür
wird keine zusätzliche komplexe Wiederanlaufmaschine vorausgesetzt. Für die
Erstfassung ist keine zusätzliche automatische Betriebsfortsetzung vorgesehen.

Das Archiv und die Konfiguration bleiben trotzdem dauerhaft erhalten. Ein
abgebrochener Verlauf muss als unterbrochen nachvollziehbar sein; unbeobachtete
Zeiten werden nicht zu gemessenen Heiz- oder Nutzungszeiten erklärt. Aus der
Wiederherstellung historischer Daten folgt keine automatische Heizfreigabe.
Die im laufenden HA vereinbarte kurze Betriebsunterbrechung mit unverändertem
Nachlauf ist davon getrennt und bleibt wie im [Betriebsmodell](betrieb.md) bestehen.

## Export

Ein **Downloadbutton im Einstellungsbereich** stellt die Sessiondaten als ZIP
mit Messreihen und maschinenlesbaren Session-/Ereignisdaten bereit.
Der Download muss vollständig sein und vorhandene Zeitauflösung und Zuordnungen
erhalten. Ein authentifizierter Abruf aus der eigenen Oberfläche ist vorgesehen;
private Archivdaten werden nicht als frei zugängliche Dateien unter `www` abgelegt.

Export und HA-Backup haben verschiedene Aufgaben: Der Export ermöglicht die
Datenauswertung, das HA-Backup sichert das Archiv zusammen mit der Installation.
Beide benötigen einen konsistenten Datenstand. Ein Export startet oder verändert
keine Session und ist keine zweite Speicherung laufender Parameter.
Die genaue Dateiaufteilung und das Schema innerhalb des ZIP werden bei der
Umsetzung beschrieben; die Zustimmung legt noch keine einzelnen CSV-/JSON-Felder fest.

## Angenommener technischer Aufbau

Eine lokale SQLite-Datenbank im eigenen Datenverzeichnis unter dem
HA-Konfigurationspfad nimmt Sessions, Originalmessungen und Ereignisbezüge
auf, beispielsweise unter `ha_sauna/sessions.sqlite`. Der Pfad wird aus dem
HA-Konfigurationsverzeichnis abgeleitet, nicht aus einem fest angenommenen
SSH-Containerpfad. Daten werden außerhalb von `custom_components` und `www` gehalten.
Die Ablage benötigt keinen zusätzlich zu betreibenden Datenbankdienst.

Für laufende SQLite-Datenbanken bietet die SQLite-Backup-API konsistente Kopien;
Python stellt sie als `Connection.backup()` bereit [3]. Ein solcher stabiler
Sicherungsstand soll über die HA-Backup-Hooks vorbereitet werden. Der eigentliche
Archivschreiber, die Behandlung gleichzeitig eintreffender Messungen und das
Wiederherstellungsverfahren müssen dazu gemeinsam implementiert und getestet
werden. Ein Checkpoint allein ersetzt keine Absicherung gegen spätere Schreibzugriffe.

Die historische Oberfläche liest die eigenen Sessionintervalle und Messreihen.
Eine vereinfachte Darstellung bei großen Zeitbereichen darf die archivierten
Originalwerte nicht verdichten oder ersetzen.

## Stand und Quellen

Der vorhandene Python-Kern enthält Ereignis- und Zeitreferenzen, aber noch
keinen Archivschreiber, Backup-Hook, Downloadendpunkt oder historische Oberfläche.
Die `processed`-Liste des Offline-Modells ist kein persistentes Archiv.
Roh-Recorderdaten, Zugangsdaten und das HA-Inventar bleiben außerhalb des
öffentlichen Repositorys.

Technische Primärquellen, eingesehen am 18.09.2026; sie belegen Schnittstellen,
nicht die fachlichen Saunaregeln:

1. https://www.home-assistant.io/common-tasks/general/#backups
2. https://developers.home-assistant.io/docs/core/platform/backup/#pre--and-post-operations
3. https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup
