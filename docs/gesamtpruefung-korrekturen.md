# Korrekturen zur Gesamtprüfung vom 27.09.2026

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

Die Korrekturen wurden zusätzlich zu den Tests an ihren Eingangswegen,
Berechtigungen, gemeinsamen Datenquellen und asynchronen Übergängen geprüft.
Die Tests bilden gezielte Gegenbeispiele, keine zweite Implementierung.

Lokaler Stand nach Korrektur:

- JavaScript: 78 bestanden.
- Synthetischer Kern: 491 ausgeführt, 2 übersprungen, Prozess Exit 0.
- HA-Adapter: 24 bestanden, Prozess Exit 0.
- HA-Integration: 94 bestanden, Prozess Exit 0; die anschließende Änderung
  der gebuchten physischen Bedienzeit wird zusätzlich in CI geprüft.
- HA/Chromium: alle 8 Fälle melden `OK`, danach endet der lokale
  Python-3.14.7-Prozess mit Exit 139. Das ist **kein erfolgreicher Gesamtlauf**.
  Einzelne gezielte Browserfälle liefen mit Exit 0. Der vollständige Linuxlauf
  in GitHub Actions muss separat bewertet werden.
- Ruff und Formatprüfung der geänderten JavaScript-Dateien bestanden.

**D-03 bleibt fachlich offen:** Darf die zusätzliche thermische Türerkennung
mit nur einem verfügbaren Sensor auslösen oder verlangt diese Zusatzregel
beide Messhöhen? Die Frage ist dem Benutzer gestellt. Bis zur Antwort bleibt
sie unverändert; die allgemeine Freigabe des Ein-Sensor-Betriebs wird dadurch
nicht eingeschränkt. Der Prüfbericht ist deshalb nicht vollständig erledigt.

Keine reale Heizinstallation, private Recorderdaten, Shelly-Firmware oder
Companion-WebView wurden hierfür geprüft. Die gezielten Kontrastfälle sind
keine vollständige Prüfung sämtlicher wählbarer Farbkombinationen. Grüne
Tests begründen keine pauschale Aussage, der Gesamtumfang sei fehlerfrei.
