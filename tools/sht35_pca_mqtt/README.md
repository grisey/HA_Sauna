# SHT35 via PCA9548A → MQTT, Version 1.2

Die bestehenden Temperatur-, Feuchte-, Status- und Discovery-Themen bleiben
erhalten. Das zusätzliche Thema `<mqtt_base_topic>/ch<kanal>/sample` enthält
ein gemeinsames JSON-Paket je Ausleseversuch. Für die Standardkonfiguration
sind dies `sensors/sht35_pca/ch3/sample` und `sensors/sht35_pca/ch6/sample`.

| Feld | Bedeutung |
| --- | --- |
| `schema_version` | Version des Paketformats, derzeit `1` |
| `sensor_id` | MQTT-Basisthema und physischer Kanal |
| `boot_id` | Zufällige UUID für diesen Start des App-Prozesses |
| `sample_id` | Aufsteigende Nummer je Ausleseversuch, über beide Kanäle hinweg |
| `measured_at` | UTC-Zeit unmittelbar nach erfolgreicher Auslesung einschließlich CRC-Prüfung; bei Fehler `null` |
| `monotonic_ns` | Monotone Zeit am Ende des Versuchs; innerhalb desselben App-Starts vergleichbar |
| `status` | `ok` oder `read_error` |
| `temperature` | Temperatur in °C, wie bisher zwei Nachkommastellen; bei Fehler `null` |
| `humidity` | Relative Feuchte in %, wie bisher zwei Nachkommastellen; bei Fehler `null` |

`(boot_id, sample_id)` identifiziert eine Probe. Temperatur und Feuchte im Paket
stammen aus derselben Sensorlesung. Auch zwei Proben mit gleichen Zahlenwerten
haben unterschiedliche IDs. Ein erneuter Transport desselben Pakets behält
seine ID; ein App-Neustart erzeugt eine neue `boot_id`. MQTT-Neuverbindungen
und der GPIO-Reset setzen den Zähler nicht zurück. Fehler verbrauchen eine ID,
behaupten aber keine Messung. Die alte Option `publish_nan_on_error` betrifft
weiterhin nur die bisherigen skalaren Wertthemen.

Die Messzeit bezeichnet den Abschluss der Auslesung, nicht einen exakt
bestimmbaren physikalischen Augenblick innerhalb der Sensorwandlung. Die
Empfangszeit wird beim Verbraucher separat erfasst. Eine UTC-Uhrkorrektur
ändert weder Probenidentität noch die monotone Reihenfolge.

Die Pakete verwenden wie die bisherigen Messwertthemen QoS 0 und `retain=false`.
Sie garantieren keine lückenlose Zustellung; die IDs ermöglichen die Erkennung
von Wiederholungen und fehlenden Versuchen. Die wechselnden Metadaten werden
nicht an jede Home-Assistant-Wertentität angehängt. Ein Verbraucher der neuen
Pakete muss Zeit und Identität ausdrücklich übernehmen.

Der Messabstand bleibt konfigurierbar. `interval_seconds: 1` ergibt eine
Sekunde Pause nach beiden Sensorlesungen, zuzüglich Wandlungs-, Bus- und
Sendezeit. Die Erweiterung fügt weder Glättung noch Bestätigungsfenster hinzu.
