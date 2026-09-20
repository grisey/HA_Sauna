# Produktive Erkennung

Der Detektor wertet nur frische, zeitlich geordnete Temperatur- und
Feuchtemessungen aus. Er arbeitet kausal in begrenzten Arbeitsfenstern, bewahrt
aber jedes empfangene Original separat im Archiv. Quellenwechsel, Messlücken,
alte Werte und unplausible Messungen brechen die jeweilige Prüfung ab; es gibt
keine Interpolation und keine Sonderregel für einzelne Aufzeichnungen.

## Tür, Person und Aufguss

Eine Türöffnung setzt Temperaturtrend und Feuchteabfall an beiden verfügbaren
Messpositionen voraus; das Schließen prüft nur bei bereits offener Tür. Beim
durchgehend bestätigten Heizen gibt es zusätzlich die niedrige,
zweikanalige Temperaturregel für eine Türöffnung. Personen- und
Aufgusszeichen verwenden eigene Fenster und Bestätigungszeiten. Der Detektor
meldet nur Signale: Die Zuordnung zum vorläufigen oder bestätigten Gang sowie
deren Wirkung übernimmt der führende Controller. Die Ablaufregeln stehen in
[Gangmodell](gangmodell.md).

## Absoluter Wassergehalt und Lüften

Aus jedem verbundenen Temperatur-/Feuchte-Paar entsteht eine zusätzliche
diagnostische Entität für absoluten Wassergehalt oben bzw. unten. Sie ist nur
verfügbar, wenn beide Quellen frisch und gültig sind. Für die Lüftungsprüfung
merkt der Detektor beim Öffnen je Kanal eine vollständige, quellengebundene
Referenz und vergleicht später Temperatur- und relativen Verlust des absoluten
Wassergehalts damit.

Bei zwei von Beginn an gültigen Kanälen gilt Lüften, sobald **beide** mindestens
3 °C Temperaturverlust und 30 % relativen Verlust an absolutem Wassergehalt
zeigen. Dafür gibt es keine feste Haltezeit. Beginnt die Öffnung dagegen mit
nur einem gültigen Kanal, gelten dieselben beiden Schwellen und zusätzlich
60 s seit dem Öffnen. Fällt bei einem ursprünglich zweikanaligen Vorgang ein
Kanal aus oder wechselt die Quelle, wird er nicht zu einem leichteren
Einkanalvorgang herabgestuft.

Die konkreten Expertenwerte bleiben zentral validierte Einstellungen. Sie
verändern den einen produktiven Detektor; die Erkennungskontrolle zeigt dessen
archivierte Merkmale und berechnet im Browser keine zweite Regel.

Der ältere [Offline-Kandidat](kandidat.md) bleibt als unveränderte
Kalibrierreferenz erhalten. Er beschreibt einen historischen Erprobungsstand;
die aktuellen Regeln sind auf dieser Seite zusammengefasst.
