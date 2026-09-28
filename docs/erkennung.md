# Produktive Erkennung

HA Sauna erkennt Türbewegungen, Personen, Aufgüsse und Durchlüften aus den
zugeordneten Temperatur- und Feuchtesensoren. Zunächst liefern Veränderungen
einen Hinweis; die passende Ereignisprüfung sucht anschließend nach einem
zusammenhängenden Nachweis und berücksichtigt Gegenzeichen. Die Messungen
werden in Empfangsreihenfolge ausgewertet. Fehlende Werte werden nicht ergänzt,
und die Originalmessungen bleiben unverändert im Archiv.

## Tür, Person und Aufguss

Eine Türöffnung kann sich durch fallende Temperatur und Feuchte zeigen.
Die Sensoren müssen diese Veränderungen innerhalb der eingestellten
Beobachtungsfenster bestätigen; sie müssen nicht im selben Moment melden.
Während eines durchgehend bestätigten Heizvorgangs kann auch ein deutlicher
Temperaturabfall an den verfügbaren Messpositionen die Öffnung belegen. Eine
verfügbare Messposition genügt auch für diese zusätzliche Regel. Sind beide
verfügbar, müssen beide den Abfall bestätigen. Der Ausfall einer konfigurierten
zweiten Messposition wird als Störung angezeigt und sperrt den Ein-Sensor-Betrieb
nicht. Diese Regel gilt ebenfalls in der heißen Sauna. Eine Schließung wird erst nach einer
erkannten Öffnung geprüft. Sie folgt der Temperaturerholung an den verfügbaren
Messpositionen. Fällt dabei ein Feuchtesensor aus, kann die Tür weiterhin als
geschlossen erkannt werden; der Sensorfehler bleibt sichtbar.

Personen und Aufgüsse erhöhen die relative Luftfeuchte. Da diese aber auch
allein durch Abkühlung steigen kann, prüft HA Sauna zusätzlich, ob der daraus
berechnete absolute Wassergehalt tatsächlich zunimmt. Ein Temperaturanstieg
ist dafür nicht erforderlich. So kann ein Aufguss trotz des anschließenden
Temperatureinbruchs beim Wedeln erkannt werden.

Ein ausgeprägtes Personensignal benötigt ein vollständiges Messfenster und die
eingestellte Bestätigungsdauer. Das schwächere Signal benötigt zusätzlich den
Zusammenhang mit einer Türöffnung und anschließender Schließung. Ein bereits
vor der Türöffnung bestehender Feuchteanstieg zählt dabei nicht als neuer
Eintritt. In einem laufenden Gang wird nicht erneut nach Personen gesucht;
weitere Aufgüsse bleiben erkennbar. Die Wirkung auf einen vorläufigen oder
bestätigten Gang beschreibt das [Gangmodell](gangmodell.md).

## Absoluter Wassergehalt und Lüften

Aus jedem verbundenen Temperatur-/Feuchte-Paar entsteht eine zusätzliche
diagnostische Entität für absoluten Wassergehalt oben bzw. unten. Sie ist nur
verfügbar, wenn beide Quellen frisch und gültig sind. Für die Lüftungsprüfung
merkt sich die Erkennung bereits beim ersten Öffnungshinweis eine vollständige
Messung aus der Zeit davor. Mit dieser Referenz vergleicht sie anschließend
den Temperaturverlust und den relativen Verlust des absoluten Wassergehalts.

Bei zwei von Beginn an gültigen Kanälen gilt Lüften, sobald **beide** mindestens
3 °C Temperaturverlust und 30 % relativen Verlust an absolutem Wassergehalt
zeigen. Dafür gibt es keine feste Haltezeit. Beginnt die Öffnung dagegen mit
nur einem gültigen Kanal, gelten dieselben beiden Schwellen und zusätzlich
60 s seit dem Öffnen. Fällt bei einem ursprünglich zweikanaligen Vorgang ein
Kanal aus oder wechselt die Quelle, wird er nicht zu einem leichteren
Einkanalvorgang herabgestuft.

Eine kurze Messlücke pausiert den Vergleich; sie löscht die Referenz nicht.
Werte einer ausgetauschten Quelle werden dagegen nicht mit der alten Quelle
vermengt. Beginnen beide Temperaturen bereits wieder zu steigen, spricht das
gegen noch anhaltendes Durchlüften. Diese Erholung verhindert eine verspätete
Lüftungsbestätigung schon während der Schließprüfung. Eine kurze Öffnung soll
dadurch den bestehenden Saunagang erhalten.

Die konkreten Expertenwerte bleiben zentral validierte Einstellungen. Sie
verändern den einen produktiven Detektor; die Erkennungskontrolle zeigt dessen
archivierte Merkmale und berechnet im Browser keine zweite Regel.

Die Haltezähler in dieser Ansicht zählen erfüllte Prüfpunkte, keine Sekunden.
Ein Wechsel oder Ausfall der vollständigen Messpositionen sowie das Ende einer
Türöffnungsepisode beendet deren zusammenhängenden Bestätigungsnachweis.
Die gesonderte Lüftungsreferenz behält die oben beschriebene Pausenregel.
Ein vollständig und fortlaufend belegter Öffnungsweg kann auch eine länger als
sein Merkmalsfenster eingestellte Bestätigung erreichen. Das Fenster begrenzt
das Alter der Hinweise, nicht eine weiterhin gültige Haltezeit.

Bei nachgeholten Messungen liefert der Controller die zu jedem Raster bereits
gebuchte Betriebs-/Kühlfreigabe. Signale aus gesperrten oder inzwischen beendeten
Freigabeabschnitten dürfen keinen heutigen Gang oder Türheizbedarf auslösen.
Nach einer neuen Freigabe muss das beitragende Merkmals-/Medianfenster zu diesem
Abschnitt gehören. Türbeobachtung und Archivierung bleiben davon getrennt.
Neue Aktorbefehle entstehen ausschließlich zur tatsächlichen Verarbeitungszeit.

Neue Erkennungsdatensätze enthalten neben dem fachlichen Ereignis und dessen
Empfangszeit den Rasterzeitpunkt `trace_at`. Darüber verbindet das Panel ein
Ereignis mit genau dem Diagnosepunkt, der es ausgelöst hat. Das gilt auch,
wenn der fachliche Beginn vor dem Haltenachweis liegt oder Messungen verspätet
nachgeholt werden. Ältere Archive ohne diese Referenz behalten die bisherige
Zuordnung anhand des fachlichen Ereignisbeginns; eine exakte nachträgliche
Rekonstruktion wird nicht behauptet.

Der ältere [Offline-Kandidat](kandidat.md) bleibt als unveränderte
Kalibrierreferenz erhalten. Er beschreibt einen historischen Erprobungsstand;
die aktuellen Regeln sind auf dieser Seite zusammengefasst.
