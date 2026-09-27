# Korrekturen zur Gesamtprüfung vom 27.09.2026

## Nachprüfung des Stands e580a47

Der vierte Gesamtbericht bestätigt die vier vorherigen Gegenfälle und benennt
zwei weitere Fehler. Die bisherige Prüfung der thermischen Freigabeverluste
und der abgeleiteten Textflächen war damit unvollständig.

- **Betriebsfreigabe und thermischer Nachweis:** Der gemeinsame bestehende
  Freigabeausdruck wird auch unmittelbar nach gebuchten Betriebsübergängen
  angewendet. AUS verwirft den thermischen Nachweis; bei weiter bestätigtem
  Heizbetrieb beginnt die erneute Freigabe an ihrer gebuchten Zeit. Das gilt
  auch für mehrere Übergänge im selben Eingangsblock, unabhängig von der
  unveränderten physischen Heizspur.
  Physische Heizintervalle und deren Rasterauswertung bleiben maßgeblich;
  es entsteht keine zusätzliche Historie oder historische Aktorausgabe.
- **Kontrast auf wirklichen Flächen:** Textfarben werden zentral gegen die
  fertig gemischten Hinweis-, Fehler-, Status- und Entwurfsflächen abgeleitet.
  Der Hoverzustand dunkelt die gesamte Schaltfläche nicht mehr nachträglich
  ab. Die Korrektur umfasst auch gleichartige Verbraucher neben den beiden
  belegten Fehlerstellen; Messkurvenfarben bleiben unverändert.

Die gezielten Gegenfälle prüfen reguläre Betriebsübergänge einschließlich
gemeinsam zugestelltem AUS/EIN und die tatsächlichen Textverbraucher nach
Flächenmischung beziehungsweise Hover. Alle Tests laufen ausschließlich in
Linux-CI. Der commitbezogene Abschlussbericht trennt Implementierung,
ausgeführte Prüfungen und verbleibende Nachweisgrenzen. Eine allgemeine
Fehlerfreiheit oder erneute Vollprüfung des gesamten Repositorys wird daraus
nicht abgeleitet. Der Merge bleibt angehalten.

## Nachprüfung des Stands 7a3966c

Der dritte Gesamtbericht bestätigt die sieben vorangegangenen Korrekturen
im geprüften Umfang und benennt vier weitere Befunde. Die Korrekturen:

- **Wartende Bedienung:** Empfangene Eingänge werden vor der Bedienung
  einmal ohne Aktorausgabe gebucht. Neue Meldungen während langsamer Ausgaben
  können die Bedienung nicht mehr durch eine Wiederholungsschleife verdrängen.
  Ein abschließender Zyklus versorgt auch Konfigurations- und Fehlerpfade.
- **Thermischer Türnachweis:** Der Detektor verwendet die vorhandenen
  tatsächlichen Heizintervalle der Sitzung pro Abtastzeitpunkt. Eine gebuchte
  Unterbrechung setzt den Nachweis auch dann zurück, wenn danach im selben
  Eingangsblock wieder EIN gemeldet wurde. Keine zweite Heizhistorie.
- **Lesbare Diagrammbedienung:** Abgewählte, weiterhin bedienbare Optionen
  bleiben vollständig deckend. Schriftgewicht und Unterstreichung kennzeichnen
  die Auswahl; Kurvenfarben bleiben unverändert.
- **Diagnoselegenden:** Farbmuster verwenden dieselben zentralen Rollen wie
  die Kurven. Die Beschriftung nennt Messhöhe und Linienart statt fester Farbnamen.

Gezielte Gegenfälle ergänzen vorhandene Core-, HA- und Browsertests.
Die Prüfung erfolgt ausschließlich in Linux-CI. Der Abschlussbericht nennt
den geprüften Commit, Ergebnisse und Grenzen. Erfolgreiche Tests belegen
keine allgemeine Widerspruchsfreiheit; die unabhängige Nachprüfung umfasst
weiterhin das gesamte Repository. Kein Merge und keine Hardwareprüfung.

## Nachprüfung des Stands b81294f

Die unabhängige Gesamtprüfung hat die frühere Aussage „alle 29 korrigiert“
nicht bestätigt: 27 Altbefunde waren im belegten Umfang korrigiert, HA-06
(Eingangsreihenfolge) und F-FE-04 (Textkontrast) nur teilweise. Insgesamt
wurden sieben aktive Befunde benannt. Die folgenden Korrekturen betreffen
diese sieben Fälle; die älteren Abschnitte sind ein historisches Protokoll.

- **NB-HA-01 – Timer überholt empfangene Eingänge:** Schreibende Runtime-Wege
  übernehmen vor ihrem eigenen Zeitfortschritt die gemeinsame Eingangsqueue.
  Die Gestenerkennung verwendet die ursprüngliche Empfangszeit; die
  Controllerzeit bleibt monoton. Es entsteht keine zweite Gestenerkennung.
- **NB-CORE-01 – Gleichmäßige Wahl behält Einzelstufen:** Ein ausdrücklich
  gewähltes neues skalares Programm löscht die individuelle Stufenliste auch
  bei unveränderten Eckwerten. Eine unveränderte Einzelwertübermittlung bleibt
  dagegen wirkungslos.
- **NB-ARCH-02 – Abschluss beim Entladen fehlt:** Beim Schließen werden die
  neu abgeschlossenen Sitzungen über den bestehenden Persistenzpfad
  gespeichert. Archivfehler verhindern weiterhin nicht die AUS-Anforderung.
- **NB-FE-01 – Rechtegefilterte Seite blockiert Verlauf:** Der Loader verwendet
  den validierten Servercursor auch für leere sichtbare Seiten. Deduplizierung
  sichtbarer Records, Fehlerwiederholung und Abschlussstatus bleiben getrennt.
- **NB-FE-02 – Unlesbare Tooltiptexte und Links:** Die tatsächlichen
  Textverbraucher verwenden die gegen den Kartenhintergrund korrigierte
  Textfarbe. Die gewählten Messkurvenfarben bleiben unverändert.
- **NB-ARCH-01 – Exportrest beim Shutdown:** Die Verantwortung für einen
  aufgegebenen Export reicht bis zum tatsächlichen Ende des Threadschreibers,
  unabhängig vom Abbruch eines asyncio-Proxys.
- **NB-DOC-01 – Falsche Kühlhöchstdauerhilfe:** Der zentrale Hilfetext und
  beide HA-Formulare nennen bestätigte Schütz-AUS-Zeit als Zeitgrundlage.
  Unbestätigte Abschnitte zählen nicht mit und verlängern die verstrichene Zeit.

Die Regressionen bilden die konkreten Gegenbeispiele über bestehende
Produktionswege ab. Tests werden ausschließlich in Linux-CI ausgeführt.
Der commitbezogene Abschlussbericht weist deren Ergebnis und Grenzen aus;
dieser Abschnitt behauptet keinen bereits erfolgten Testlauf und keine
allgemeine Fehlerfreiheit. Keine Hardwareprüfung und kein Merge.

## Erstes Korrekturprotokoll

Ausgangspunkt ist der geprüfte Commit `1cc1eae`. Die Befunde werden gegen
fachliche Anforderungen und tatsächliche Aufrufpfade geprüft. Bestandstests
allein belegen weder Fehlerfreiheit noch Widerspruchsfreiheit. Neue Prüfungen
sichern konkrete Gegenbeispiele mit den bestehenden Produktionspfaden ab;
sie implementieren keine zweite Regelung.

Der PR bleibt bis zur ausdrücklichen Freigabe ungemergt. Reale Aktoren werden
für die Korrekturprüfung nicht verwendet. Ein synthetischer Dienstabschluss
ist kein Nachweis eines tatsächlichen Schaltvorgangs.

## Archiv und Backup

- **A-01:** Der Backuphook behält die konkreten Archive bis zum Posthook,
  auch wenn ihre Runtime schon entladen wird. Schließen nimmt keine eigene
  Backupfreigabe mehr vor. Eine vor dem Erreichen des Pausenauftrags erfolgte
  Freigabe geht nicht verloren. Zwei Reihenfolgen im bestehenden Backuptest
  prüfen Erreichbarkeit, unveränderte Daten während der Pause und Abschluss.
- **A-03:** Ein abgebrochener Export behält seinen Erzeugungstask bis zum
  Threadabschluss; die fertige Datei wird dann gelöscht. Der aufrufende Task
  bleibt abgebrochen. Ein gezielter Exporttest prüft diesen Besitzwechsel.
- **A-04:** Sitzungssnapshot, Records und Legacy-Projektion werden innerhalb
  derselben kurzen SQLite-Lesetransaktion gelesen. Eine Verbindung allein
  genügt dafür nicht. Der gezielte Test schreibt zwischen den beiden SELECTs
  eine neue Abschlussrevision und prüft, dass die Antwort einen einzigen Stand
  enthält; der nächste Abruf sieht die neue Revision.

## Oberfläche und API

- **F-FE-01:** Der zweite Schritt des manuellen Heizstarts bleibt an Sauna,
  Panelgeneration und Vorgang gebunden. Nach einem Wechsel darf weder ein
  Heizbefehl noch eine Fehlermeldung in der neu gewählten Sauna ankommen.
- **F-FE-02:** Programmwerte aus Eingabefeldern werden vor der Verteilung als
  Zahlen validiert; Textverkettung kann keine Temperaturstufe erzeugen.
- **F-FE-03:** Ereignissprünge öffnen die jeweilige Zielansicht und führen den
  Fokus zum zugehörigen Diagrammmarker beziehungsweise Listeneintrag.
- **F-FE-04:** Bedienelemente, Diagrammbeschriftungen und Fokusmarkierungen
  verwenden die auf ihrer tatsächlichen Hintergrundfläche lesbaren Farben.
  Datenkurven behalten ihre gewählte Farbe. Auch Phasentönung und ausgewählte
  Ereigniszeilen werden bei der Textableitung berücksichtigt.
- **F-FE-05:** Überlaufende Skalenintervalle werden für Integer- und Floatwerte
  gleichermaßen als ungültige Eingabe behandelt.
- **HA-01:** Technische Number-Entitäten prüfen den HA-Benutzerkontext vor einer
  Änderung. Normale Benutzer behalten die freigegebenen Temperaturänderungen.
- **HA-02:** Instanzliste, Status und Archiv beachten die Leseberechtigung der
  jeweiligen Betriebsentität. Normale Benutzer bekommen die für Bedienung und
  Verlauf erforderlichen Werte ohne Gerätezuordnungen und Erkennungsdiagnosen.
  Die Messgültigkeit wird dafür ausdrücklich im Anzeigevertrag übertragen.
- **HA-03:** Gemeinsame Panelregistrierung ist serialisiert; eine bereits
  registrierte statische Route wird bei einem späteren Teilschrittfehler nicht
  erneut registriert.
- **HA-04:** Fehlgeschlagenes Setup räumt gestartete Plattformen und Runtime auf.
  Der ursprüngliche Fehler bleibt erhalten; zusätzliche Aufräumfehler werden
  daran vermerkt.
- **HA-08:** Fehlerhaftes JSON wird im gemeinsamen Einlesepfad der Schreib-APIs
  mit HTTP 400 beantwortet.

## Laufzeit und Geräte

- **CORE-1:** Endet eine Sitzung bereits beim Vorziehen ihrer Fristen, beendet
  der ausdrückliche Abschluss sie nicht ein zweites Mal.
- **CORE-2:** Eine unveränderte skalare Eingabe löscht keine explizite
  Temperaturfolge. Tatsächliche Formänderungen erreichen denselben Controller.
- **A-02:** Abgeschlossene Sitzungen werden vor dem Wechsel auf die neue
  Tasterprogramm-Konfiguration mit ihrer bisherigen Konfiguration gespeichert.
- **D-01:** Nicht endliche abgeleitete Steigungen und Differenzen gelten als
  fehlender Nachweis. Eine ungültige Erkennungsdiagnose darf den Regelzyklus
  nicht vor der Ausgabe abbrechen. Rohmessungen bleiben erhalten.
- **D-02:** Der Live-Status überträgt nicht mehr die vollständige, dort ungenutzte
  externe Beobachtungshistorie. Das entfernt deren wiederholte Sortierung und
  Serialisierung; es begrenzt nicht den internen Speicher aller Beobachtungen.
- **D-03:** Die zusätzliche thermische Türerkennung ist auch mit einer
  verfügbaren Messposition freigegeben. Die widersprechenden Hilfetexte und
  der irreführende Testname sind korrigiert. Der gesamte Betrieb unterstützt
  eine konfigurierte Messposition. Der obere Sensor bleibt Hauptsensor der
  Solltemperaturregelung; nur bei fehlendem gültigem oberen Wert dient unten
  als Ersatz. Ein konfigurierter ausgefallener Sensor wird weiterhin als
  Störung angezeigt. Konfiguration, Regelung und Anzeige verwenden diese
  Hierarchie ohne Mittelung oder Höhenoffset.
- **EXT-01:** Unabhängig bestätigtes Heizen wird auch bei ausgeschaltetem Betrieb
  und ausgeschaltetem Schütz durch die bestehende Fehlerbestätigung erfasst.
- **EXT-02:** Die erwartete Lichtantwort berücksichtigt die zentral konfigurierte
  native Helligkeitsskala, standardmäßig 255. Die lineare Hin-/Rückumrechnung
  entspricht dem HA-MQTT-Basisschema; sie ist keine automatische Erkennung
  beliebiger Geräte- oder Integrationsquantisierung und keine Fehlertoleranz,
  die benachbarte manuelle Einstellungen verschluckt.
- **EXT-03:** Ein dauerhafter Betriebseingang, der nach unbekanntem Zustand als
  AUS zurückkehrt, bewirkt AUS. Ein zurückkehrendes EIN startet nicht neu.
- **EXT-04 / HA-07:** Ein erfolgreicher Lichtdienstaufruf ersetzt keine beobachtete
  AUS-Rückmeldung. Der fällige Lichtnachlauf bleibt bis dahin offen; vorhandene
  Befehlsverfolgung verhindert Wiederholungen vor Ablauf der Rückmeldefrist.
  Ein Gerätewechsel behält bei ausbleibender Bestätigung die alte Zuordnung
  und Frist. Zwischenzeitlich überholte Konfigurationsvorgänge schreiben den
  neueren Stand nicht zurück.
- **HA-05:** Das Schließen besitzt einen gemeinsamen Abschlusstask. Abbruch
  eines Wartenden bricht die Bereinigung nicht ab; erneutes Schließen wartet
  auf denselben Abschluss.
- **HA-06:** Bereits wartende Geräteeingänge werden in einer gemeinsamen FIFO
  nach Empfangsreihenfolge verarbeitet, bevor der aktuelle Regelzyklus läuft.
  Rückmeldungen und physische Bedienereignisse verwenden dabei den gebuchten
  Zeitpunkt. Geprüft sind zwei wartende Heizflanken, eine dazwischen liegende
  Ausschaltbedienung und der Kühlstart bei verspäteter Schützrückmeldung.
  Hat ein anderer abgeschlossener Zyklus eine Empfangszeit bereits überholt,
  bleibt die Controllerzeit monoton; eine historische Neuberechnung ist
  ausdrücklich nicht implementiert.

## Dokumentation und Bereinigung

- **DOC-01:** Parameterhilfen beschreiben den eingestellten Kühlfaktor,
  Einkanal-Lüftungsdauer, Türkontext schwacher Personensignale und die
  tatsächliche Kurz-/Langdruckfolge. HA-Texte folgen denselben Definitionen.
- **DOC-02:** README, Einrichtung und Speicheranleitung verwenden die vier
  Hauptansichten und die aktuellen Orte für Übersteuerung und Export.
- **DOC-03:** Die Abnahme beschreibt die geltende Ofenkühlung und ihre
  Nachweisgrenzen. Aufgehobene Heizbudget-/Kühlregeln gelten nicht als heutige
  Abnahmebedingungen.
- Hinweise zur Live-Darstellung und zu Ausgabepfaden der Verlaufsskripte sind
  vereinheitlicht. Frühere Teilaufgaben in der Schnittstellendokumentation
  sind als historischer Arbeitsplan gekennzeichnet.
- Der ungenutzte Lichtparameter `phase_started_at`, der nur von Tests
  verwendete Archivwrapper `post_backup` und der ungenutzte Controllerwrapper
  `oven_cooling_duration_seconds` sind entfernt. Alte Frontend-Aktionswege
  ohne Bedienelemente und deren redundante Tests entfallen. Der bestehende
  Kühltest benennt die tatsächlich geprüfte Unterbrechung statt Fristerhalt.
  Ungenutzte Testimporte und vom Linter gemeldete Mehrfachanweisungen wurden
  ohne Änderung der geprüften Fachregeln bereinigt.
- Historische Archivfelder, Kühlphasen und die eingefrorene Kandidatenreferenz
  bleiben erhalten. Test-/Lesekompatibilität ist von ungenutzter aktueller
  Steuerungslogik zu unterscheiden.

## Prüfung und offene Grenze

Eine erneute Gegenprüfung durch drei neue Sol-Agenten fand eine weitere
inkonsistente Zeitübergabe bei manuellen Lichtereignissen. Der Geräteadapter
verwendet nun ebenfalls den gebuchten Zeitpunkt aus dem gemeinsamen
Runtime-Eingang für Phase und Übersteuerungsfrist. Ein vor Fristablauf
empfangenes, erst danach verarbeitetes Dimmereignis verdrängt damit nicht
irrtümlich den fälligen AUS-Befehl.

Der Praxishinweis zum gehaltenen Saunataster war ebenfalls berechtigt: Code,
Parameter und Test verlangten noch 1 % Helligkeit. Nach Erkennen des langen
Drucks wird jetzt ausdrücklich AUS angefordert. Loslassen startet weiterhin
den Lichtnachlauf. Der vorhandene Integrationstest prüft AUS-Zustand und
AUS-Dienst sowie das anschließende Hochdimmen. Die alte Haltehelligkeit ist
nicht mehr einstellbar; gespeicherte Werte werden kompatibel eingelesen und
als überholt verworfen, ohne den Ablauf wieder zu verändern.

Der Registrierungsgegenfall benötigt kein vollständiges HA-Frontendsetup.
Er erzwingt die konkurrierenden Awaitpunkte, verwendet aber weiterhin den
echten HA-Router. Mit der ursprünglichen Produktionsfunktion aus `1cc1eae`
scheitert derselbe Test an der doppelten GET-Route; mit der Korrektur endet
er erfolgreich. Es gibt dafür keine nachgebildete Registrierungslogik.

Die Korrekturen wurden zusätzlich zu den Tests an ihren Eingangswegen,
Berechtigungen, gemeinsamen Datenquellen und asynchronen Übergängen geprüft.
Die Tests bilden gezielte Gegenbeispiele, keine zweite Implementierung.

Lokaler Stand mit einer neu isolierten Python-3.14.2-Laufzeit passend zur CI;
jeder Testprozess wurde einzeln gestartet und sein Exitcode erfasst:

- JavaScript: 78 bestanden (JavaScript seit diesem Lauf unverändert).
- Synthetischer Kern: 491 ausgeführt, 2 übersprungen, Prozess Exit 0.
- HA-Adapter: 25 bestanden, Prozess Exit 0.
- HA-Integration: 94 bestanden, Prozess Exit 0.
- HA/Chromium: ein gezielter echter Browserfall endet mit Exit 0. Der volle
  Lauf meldet acht Fälle als `OK`, endet aber anschließend auch mit Python
  3.14.2 unter macOS mit Exit 139. Das ist **kein erfolgreicher Gesamtlauf**.
- Ruff und Formatprüfung der geänderten JavaScript-Dateien bestanden.

Die früher verwendete Homebrew-Python-3.14.7-Umgebung erzeugte wiederholt
Abstürze bei der Interpreter-Finalisierung. Der Wechsel auf eine isolierte
3.14.2-Laufzeit allein beseitigte das Problem des gesamten Browserprozesses
nicht. Die genaue Ursache bleibt ungeklärt; der Finalisierungsstack beweist
keinen bestimmten Python- oder C-Erweiterungsfehler. Weitere lokale
Browserwiederholungen sind ausgesetzt. Weder `os._exit` noch Fehlerunterdrückung
werden zum Verbergen des fehlgeschlagenen Prozessendes verwendet.

Der Linux-CI-Lauf `36318733249` zum ersten Korrekturcommit `ef5475d` bestand
Core und Browser; die Integration scheiterte am inzwischen entfernten,
unnötigen vollständigen Frontendsetup des Registrierungsgegenfalls.
Der vollständige Linux-CI-Lauf zum Folgestand `13a3935` ist erfolgreich:
https://github.com/grisey/HA_Sauna/actions/runs/36319682144

Nach ausdrücklicher Nutzeranweisung finden keine lokalen Tests auf dem Mac
und keine weitere Untersuchung ihrer Abstürze statt. Linux ist das
Testzielsystem. Der macOS-Absturz ist kein offener Arbeitsauftrag.

Die Sensorregel ist durch den Benutzer abschließend klargestellt: vollständiger
Ein-Sensor-Betrieb, bei zwei konfigurierten Sensoren sichtbare Ausfallmeldung;
oben bleibt Hauptsensor, unten nur Ersatz. Dazu besteht keine offene
fachliche Rückfrage. Nachprüfung und Merge-Freigabe sind Verfahrensschritte
und keine offenen Codebefunde.

Keine reale Heizinstallation, private Recorderdaten, Shelly-Firmware oder
Companion-WebView wurden hierfür geprüft. Die gezielten Kontrastfälle sind
keine vollständige Prüfung sämtlicher wählbarer Farbkombinationen. Grüne
Tests begründen keine pauschale Aussage, der Gesamtumfang sei fehlerfrei.
