# Vollständige Sitzungsprüfungen

Die Manifeste [Szenarien](../scenario_manifest.json) und
[Replays](../replay_manifest.json) binden Eingaben und Bewertungen mit SHA-256.
Ein fehlender Bestandteil ist ein Fehler. Die Prüfung benötigt keine private
Arbeitsablage, keinen HA-Host und keine Zugangsdaten.

Konstruierte Szenarien beziehen Anlagenparameter und gespeicherte Programme
direkt aus dem zentralen `defaults.json`. `configuration.json` enthält dafür
nur den gebundenen Quellenverweis, keinen zweiten Wertekatalog. Ändert sich
diese Quelle, scheitert der Lauf vor Beginn mit dem Hinweis auf veraltete
Messkurven. Die Materialisierung mit `build_fixtures.py` und die fachliche
Prüfung der daraus entstandenen Kurven erfolgen ausdrücklich vor dem nächsten
Produktlauf. Historische Replays behalten ihre damalige Anlagenkonfiguration.

## Sitzungen und Iterationen

Jeder Lauf umfasst Einschalten, Nutzung oder bewussten Abbruch, Ausschalten und
den vollständigen Nachlauf. Bedienungen liegen außerhalb ununterbrochener
Aufenthalte; die physischen Taster befinden sich außerhalb der Kabine.

| Lauf | Zusammenhängender Ablauf |
|---|---|
| G00 | Automatisches bzw. manuelles Aufheizen ohne Saunagang; Ausschalten und Verwerfen der Aufzeichnung. |
| G01 | Gespeichertes Programm durch alle Stufen, zusätzlicher Aufenthalt bei der letzten Stufe. |
| G02 | Individuelles gleichmäßiges Programm mit absteigenden Temperaturen. |
| G03 | Einzelne Stufen: höher, gleich, niedriger; letzte Stufe bleibt erhalten. |
| G04 | Konstant mit zentralem Soll, Thermostatzyklen und mehreren Aufenthalten. |
| G05 | Manuell über Kurzdruck, Thermostatzyklen, erkannte Aufenthalte und Ausschalten während einer Heizpause. |
| I01 | Höheres Soll nach Bereitschaft, niedrigeres Programm, kurze Türöffnung ohne Aufenthalt, nächste Gangtemperatur, Programmwechsel, einzelne Stufen und Konstant. |
| I02 | Ofen- und Lichtübersteuerung, ausdrückliche und zeitabhängige Rückgabe, wiederholte Auswahl, verspätete Abwesenheit, Kühlende per Bedienung und erneuter Eintritt während Kühlung. |
| I03 | Kurzdruck startet Manuell, kurzes Aus/Ein setzt dieselbe Aufzeichnung fort; Gerätegeste übernimmt sie in Automatik; früher Eintritt, mehrere Personen, Übersteuerung und Rückgabe, abschließende Gerätegeste. Varianten: Lang-/Doppel-/Dreifachdruck und gespeichertes Programm/Konstant. |
| I04 | Eintritt unter Mindesttemperatur, abgeschlossener Gang trotz später niedriger Temperatur, Aus und Wiederaufnahme im Nachlauf, endgültiges Ausschalten nach Verlassen. |
| I05 | Proxybetrieb: Rückkehr durch die noch offene Tür vor erfüllter Mindestkühlzeit, wiederholte Personenbelege, unveränderter Kühlbeginn und anschließende Heizanforderung. |

Die Grundsitzungen führen beide Erkennungsquellen durch ihre jeweils möglichen
Abläufe. Varianten verwenden gemeinsame Kurvenabschnitte, solange deren
Heizvorgeschichte übereinstimmt. Abweichende Heizvorgänge benötigen passende
Folgeabschnitte. Die Iterationen kombinieren Bedienungen mit unterschiedlichen
Phasen und Temperaturverhältnissen: darunter, erreicht, darüber; steigende,
gleiche und fallende Programmstufen; frühe, laufende und beendete Aufenthalte.
Damit werden die wesentlichen Übergänge des beschriebenen Gebrauchs gemeinsam
geprüft. Eine vollständige Aufzählung aller zeitlichen Kombinationen ist das
nicht. Transportfehler, Berechtigungen und genaue Ereignisreihenfolgen bleiben
eigenständige Schnittstellenprüfungen.

Bei Proxy-Personenbelegen gilt die Mindestdauer für die gesamte bereits laufende
Kühlung. Die Bewertung prüft deshalb Gesamtdauer, gezählte AUS-Zeit und Restzeit
gemeinsam. Nach bereits erfülltem Minimum müssen Kühlung und Heizentscheidung
im selben Verarbeitungsschritt wechseln; dies prüfen die Proxyvarianten G01–G04.
Weitere Signale dürfen keine neue
Frist beginnen. Ein unbestätigter Proxyhinweis zählt keinen zusätzlichen Gang.

## Messwerte und Bewertung

Die abgeleiteten Kurven bestehen aus gemeinsam verwendeten, real gemessenen
Abschnitten. Temperatur und Feuchte beider Messhöhen bleiben gekoppelt;
Übergänge berücksichtigen die vorherige Erwärmung oder Abkühlung. Stützstellen
werden linear abgetastet. Die Messkurven stehen vor dem Produktlauf fest:
Weder aktuelle Schaltbefehle noch Erkennungen wählen nachträglich andere Werte.

`actions` enthalten Bedienungen und direkte Sensormeldungen. Tür-, Aufguss- und
Gangereignisse werden nicht eingespeist. `expectations` enthalten unabhängig
begründete Sollzustände: `at_s` prüft einen Zeitpunkt, `between_s` das gesamte
Intervall, `eventually` mindestens einen Zeitpunkt im ausdrücklich genannten
Fenster. Gleichzeitige Eingaben werden vor der Bewertung verarbeitet.
Bei Zählerwerten bezeichnet `since_s` die Bezugszeit einer Differenz, etwa zwei
neue Personenbelege während derselben Kühlung. Ein früherer Gang kann diese
Erwartung deshalb nicht bereits erfüllen.
Die durchgängige Aufzeichnung wird über ihren ursprünglichen Beginn geprüft.
`archived_preheat_roles` zählt nur vollständig erhaltene Messkanäle: Alle
gebundenen Eingänge ab diesem Beginn bis ausschließlich
`preserve_measurements_before_s` werden nach Empfangszeit und Wert mit den rohen
SQLite-Zeilen derselben ursprünglichen Session-ID verglichen. Auch die
Empfangszeit im gespeicherten Messwert und die Anzahl identischer Zeilen müssen
übereinstimmen. Fehlende, zusätzliche oder veränderte Werte lassen den Kanal
durchfallen. Der Nachweis enthält die Anzahlen und konkrete Differenzbeispiele;
ein nachträglich angezeigter Messkontext ersetzt keine archivierten Eingänge.

`recording_history_intact` sichert bei Wiederaufnahme und Betriebsartwechseln
die bereits erfassten Heizintervalle, Schützmeldungen und Energieanteile.
Geschlossene Intervalle und vorhandene Schützmeldungen müssen als unverändertes
Präfix erhalten bleiben; Energieanteile dürfen innerhalb derselben Sitzung
nicht zurückgehen. Der gespeicherte Abschluss muss dieselben Verläufe enthalten.
Diese Erhaltungsprüfung gibt keinen zweiten Heizalgorithmus vor.

Der Lauf verwendet den produktiven `HADevice`, `SaunaRuntime`, Detektor,
Controller und SQLite-Archiv. Ersetzt werden HA-Zustandsbereitstellung,
Diensttransport und Benachrichtigungszustellung. Ein Aktor bestätigt die neuen
Befehle über den normalen Rückmeldeweg. Die
[gebundenen Replays](../fixtures/replay/README.md) liefern unveränderte reale
Messmeldungen; historische Aktorwerte stehen separat im Diagnosebericht.

## Linux-Aufruf und Nachweise

```sh
SAUNA_SESSION_EVIDENCE=/tmp/sauna-session-evidence python -m unittest discover -s tests/sessions -v
```

`SAUNA_SESSION_CASE` wählt für die Fehlersuche eine vollständige Lauf-ID oder
Grund-ID. Unbekannte IDs sind Fehler. In der CI wird jede Grund-ID ausgeführt,
einschließlich sämtlicher gebundener Varianten. Pro Lauf entstehen Bewertungen,
Bedien- und Dienstprotokoll, Archivnachweis und eine komprimierte Zustandsfolge.
SQLite kann auf einem Linux-RAM-Dateisystem liegen; Erkennungs- und Regelzeiten
werden dafür nicht verkürzt.

Die [Zuständigkeitstabelle](../coverage_ownership.json) nennt die ersetzten
Fachtests mit ihrer jeweils führenden Sitzungsbewertung. Der zugehörige Metatest
prüft die Verweise und verhindert die Rückkehr bereits entfernter Duplikate.
