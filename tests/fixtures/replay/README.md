# Reale Sitzungsaufzeichnungen

Die vier vollständigen Aufzeichnungen sind mit allen benötigten Eingaben im
Repository enthalten. `tests/replay_manifest.json` bindet Metadaten, Messreihen
und historische Rückmeldungen über SHA-256. Fehlende oder veränderte Dateien
sind ein Fehler; eine private Datei oder Umgebungsvariable ist nicht erforderlich.

| Lauf | Auswahl | Unabhängige Grundlage der Bewertung |
|---|---|---|
| R01 | Kaltstart, zwei Aufenthalte, Programmwechsel, Ausklang; indirekte Erkennung | Zwei Aufenthalte mit geschlossener Tür, Verwirbelungen und anschließenden Lüftungsausgängen wurden vom Nutzer bestätigt. |
| R02 | Automatik mit höherem Soll nach Bereitschaft, zwei bestätigten Ausgängen und vollständigem Lichtnachlauf | Bedienversuche, verspätete Abwesenheit nach Türschluss, weitere kurze Türöffnung ohne Aufenthalt und zweiter beendeter Gang wurden während der Beobachtung zugeordnet. |
| R03 | Manuelle Testsitzung, kurzes AUS/EIN, Wechsel zur Automatik und Ausschalten per Langdruck mit gemeinsamem Nachlauf | Manuelle Sitzung und anschließende Bedienfolge wurden vom Nutzer bestätigt. Einzelne Tür- oder Aufgusszeitpunkte sind nicht unabhängig beschriftet und werden deshalb nicht aus alten Erkennungen übernommen. |
| R04 | Zusammenhängender Saunatag mit zwei Regleränderungen, wiederholten Türfolgen, kurzen AUS/EIN-Pausen und gemeinsamem Abschluss | Der Nutzer ordnet beide Regleränderungen ohne Konstantwahl sowie die Türvorgänge und Betriebspausen zu. Tatsächliche Messungen und direkte Präsenz begrenzen die Bewertungsfenster; eine genaue Gesamtzahl menschlicher Aufenthalte wird nicht behauptet. |

Zeitangaben sind Sekunden seit Aufzeichnungsbeginn. Rollen ersetzen Geräte-
und Entitätskennungen. Messwerte und Meldungsabstände sind unverändert; es gibt
keine Glättung, Interpolation oder erfundene Präsenzmeldungen. `initial_state`
enthält den zuvor vorliegenden Zustand samt tatsächlichem Meldungsalter.

`time_groups` in `tests/replay_manifest.json` führt den belegten Zeitbezug zwischen
den Aufzeichnungen. R02 und R03 gehören zu einer gemeinsamen Folge mit R02 als
Ursprung; R01 und R04 besitzen eigene Zeitgruppen. Der gleiche Quellhash von R01
und R02 bleibt Herkunftsmetadatum und belegt keinen Zeitanschluss.
`origin_offset_s` verschiebt lokale Zeiten auf die jeweilige Gruppenachse;
`gap_from_previous_end_s` erhält die tatsächliche unbeobachtete Lücke zwischen
dem Ende von R02 und dem Ursprung von R03. Sie ist keine lückenlose Messreihe und
wird nicht interpoliert. Die zentrale reine Transformation
`tests/analysis/replay_time_axis.py` stellt diese Zuordnung für Analysen und die
private Vorschau bereit. Lokale Fixturezeiten, Meldungsalter und Messdateien
bleiben unverändert. Ursprüngliche Kalenderzeitpunkte bleiben privat; das
Manifest enthält ausschließlich relative Abstände und Quellhashes.

R02 beginnt mit eingeschaltetem Licht; dessen nicht gespeicherte Helligkeit
bleibt unbekannt und wird nicht als ausgeschaltetes Licht interpretiert.
`samples` enthalten ausschließlich Klima- und direkte Präsenzmeldungen.
Bedienhandlungen stehen getrennt in `actions`; erwartetes Verhalten in
`expectations`. `at_s` bezeichnet einen Prüfzeitpunkt, `between_s` ein durchgehend
zu prüfendes Intervall; `eventually: true` verlangt mindestens einen passenden
Zustand innerhalb dieses Fensters. Die Texte unter `reason` begründen jede
Bewertung fachlich, ohne damalige Algorithmusausgaben als Wahrheit zu verwenden.

R01 unterscheidet die zwei menschlich bestätigten Aufenthalte von der
unbestätigten Proxyvermutung während der anschließenden Feuchterholung.
P05 verlangt keinen dritten Gang und keine behauptete Abwesenheitsgewissheit:
Ein noch vorläufiger Verdacht ist zulässig, ein bestätigter dritter Gang nicht.
Die neue Nutzerregel für jedes Proxy-Personensignal bindet hier die laufende
Kühlung an ihre Mindestgesamtdauer; bis zu deren Ablauf bleibt der Ofen aus.
Die Meldungen enthalten keinen unabhängigen direkten Präsenznachweis.

R03 enthält die vollständige anschließende Tasterfolge einschließlich aller
nativen Druck-, Langdruck- und Loslassmeldungen. Die kurze manuelle Pause und
der Wechsel zur Automatik müssen dieselbe Aufzeichnung fortsetzen. Echte
Recorder-Meldungen reichen über die gemeinsame 15-minütige Sitzungsfrist hinaus;
auch dieser Abschnitt enthält keine erzeugten Messwerte. Erst danach darf eine
einzige Sitzung gespeichert sein. `session_started_s` prüft den unveränderten
Beginn; `archived_preheat_roles: 4` verlangt den vollständigen Erhalt aller vier
Messkanäle vom ursprünglichen Beginn bis zum Automatikwechsel unter derselben
Archiv-ID. Empfangszeit, Wert und Anzahl jedes Messwerts werden mit den rohen
Archivzeilen verglichen.

R04 verbindet die drei früher getrennt gespeicherten Abschnitte. Der echte
Einfachdruck am Ende bleibt genau eine native Druck-/Loslass-/Einfachdruckfolge;
ältere gespeicherte Tasterzustände sind keine weiteren Drücke. Betrieb-AUS,
anschließendes manuelles Beenden des Lichtnachlaufs und damaliger Archivabschluss
bleiben getrennt. Die gemeinsame Frist beginnt beim letzten belegten Betrieb-AUS
und wird durch das Lichtnachlauf-Ende nicht verkürzt. Echte Messwerte reichen über
diese Frist hinaus. `archived_preheat_roles: 4` prüft hier trotz des historischen
Feldnamens sämtliche vier Messkanäle vom ursprünglichen Beginn bis zur gemeinsamen
Frist, einschließlich der beiden kurzen Betriebspausen, vollständig unter einer ID.

Die zweite Regleränderung in R04 ist durch die nachträgliche ausdrückliche
Nutzerangabe als Temperaturänderung belegt. Beide Regleraktionen müssen das
steigende Programm erhalten und nur die nächste Gangtemperatur verändern.
Wiederholte kurze Türvorgänge, die spätere Rückkehr und der anschließend leere Raum
werden aus Klima und direkter Präsenz bewertet. Die fehlerhafte historische
Ofenkühlung wird dabei nicht als notwendige Voraussetzung vorgeschrieben.

Der Aktor im Prüflauf bestätigt die neuen Befehle. Die ursprünglichen
Schütz- und Lichtmeldungen liegen separat in `observed-outputs` und dienen dem
Abweichungsbericht. Sie bestätigen keine geänderten Befehle. Insbesondere enthält
R02 nach dem damals übersehenen Gangende weiterhin Messwerte bei irrtümlich
eingeschalteter Heizung. Ein Replay dieser unveränderten Werte ist eine Prüfung
der Verarbeitung tatsächlicher Eingaben, keine physikalische Vorhersage der
korrigierten Regelung. Die passend zur Sollsteuerung zusammengesetzten
Messkurven werden in den gesonderten Sitzungsszenarien geprüft.

Die damalige sichtbare Lampenüberblendung lässt sich aus HA-Rückmeldungen allein
nicht bewerten. Historische Sonnenhöhen fehlen; genaue automatische
Helligkeitsprozente sind deshalb kein Replay-Orakel. Nicht genau benannte
menschliche Ereignisse erhalten keine
nachträglich aus dem Produktcode gewonnenen exakten Zeitpunkte.
