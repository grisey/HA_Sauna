# Grundformen für das Benachrichtigungsmodell

Stand: 15. Oktober 2026. Vorbereitung auf dem Branch
`codex/notification-model-preparation`, ausgehend von
`3b31ccb9a1c9e6362d7c58acc9cef417a253117b`.

Das Paket beschreibt mögliche Bausteine für eine spätere Implementierung.
Maßgeblich sind die drei Anforderungen: Austausch mit der Companion-App,
leicht zuordenbare und nach Fähigkeit gefilterte Empfänger sowie die Auswahl
nach startendem Benutzer mit Akkumulation bei Eingriffen weiterer Benutzer.
Die Präzisierung des Auftrags begrenzt die Vorbereitung auf Grundformen.
Benachrichtigungsanlässe, konkrete Interaktionen, Bedienhandlungen und ein
vollständiger Ablauf werden nicht festgelegt. Ebenso wenig werden bestimmte
Geräte, Betriebssysteme oder eine feste Zahl von Benutzern vorausgesetzt.

## Mögliche Bausteine

Die Bezeichnungen und Felder sind Entwurfsvorschläge, kein bereits vorhandenes
API und keine Festlegung der späteren Implementierungssprache.

| Grundform | Mindestinhalt | Aufgabe |
| --- | --- | --- |
| `ActorRef` | HA-Benutzer-ID, sofern am Eingang vorhanden; Herkunft des Eingangs | Die Identität eines Bedienenden über Adaptergrenzen erhalten. |
| `RecipientRef` | Art der Referenz, stabile Entitäts- oder Geräteidentität, Transportzuordnung | Ein adressierbares Ziel unabhängig von seinem Anzeigenamen beschreiben. |
| `RecipientCapability` | Versandfähigkeit, Rückkanalfähigkeit, Beleg der jeweiligen Aussage | Die zulässige Auswahl aus tatsächlichen Fähigkeiten ableiten. |
| `RecipientAssignment` | Beziehung zwischen HA-Benutzer-ID und Empfängerreferenzen | Die konfigurierbare Zuordnung tragen, ohne sie aus Namen oder Anwesenheit zu erraten. |
| `SessionRecipients` | Sauna-Instanz, Sitzungs-ID, bereits zugeordnete Empfänger | Die Empfängermenge einer Sitzung zusammenführen und von anderen Sitzungen trennen. |
| `NotificationEnvelope` und `ReplyEnvelope` | Nachrichtenidentität, Ziel beziehungsweise Herkunft, Zuordnungsreferenz, Nutzdaten | Austausch und Zuordnung ermöglichen, ohne eine fachliche Aktion vorzugeben. |

Eine Empfängerreferenz kann auf eine Entität oder ein Gerät zeigen. Beide sind
keine unabhängigen Empfänger, wenn sie denselben Transportendpunkt bezeichnen.
Ein möglicher Resolver übersetzt sie in eine gemeinsame Zielidentität. Ob die
Oberfläche beide Referenzarten oder nur eine anbietet, bleibt eine
Implementierungsentscheidung; ihre Auswahl muss auf geeignete Ziele begrenzt sein.

## Identität und Zuordnung

Ein HA-Benutzer ist die führende Identität für die Anforderung. `person`-Entitäten,
Gerätenamen und Anwesenheit ersetzen diesen Nachweis nicht. Eine fehlende
Benutzer-ID bleibt ausdrücklich unbekannt. Daraus entsteht weder ein vermuteter
Besitzer noch eine automatische Auswahl aller Empfänger.

Der am Eingang bekannte Benutzer, der konfigurierte Zuordnungsschlüssel und
der Benutzer einer eingehenden Companion-Rückmeldung sind unterschiedliche
Informationen. Die Grundformen dürfen sie nicht stillschweigend gleichsetzen.
Insbesondere ist eine konfigurierte Empfangszuordnung keine Steuerberechtigung.

Die Zuordnungsform enthält Mengen von Empfängerreferenzen und keine feste Zahl
von Zuordnungsfeldern. Eine mögliche Oberfläche zeigt Benutzer und verständliche
Zielnamen aus HA. Freie Eingabe von Dienstnamen ist dafür nicht erforderlich.
Ob die Beziehung je Benutzer oder je Empfänger bearbeitet wird, ist offen.

## Fähigkeiten als Auswahlgrundlage

Die Auswahl benötigt einen Resolver, der eine Referenz anhand der registrierten
Integration, ihres Transportwegs und ihres Fähigkeitsvertrags bewertet. Für den
gewünschten interaktiven Kanal müssen Versand und Rückkanal begründet sein.
Ein `notify`-Domainfilter, ein Namenspräfix oder die bloße Existenz eines
Mobilgeräts belegt das nicht. Der Server prüft dieselbe Eignung beim Speichern;
ein eingeschränktes Auswahlfeld allein ist keine Validierung.

Die untersuchten HA-Versionen liefern für `mobile_app` konkrete Wege für
Geräteauflösung, Pushfähigkeit und Versand mit Zusatzdaten. Eine universelle
Eigenschaft „unterstützt interaktive Benachrichtigungen“ wurde dabei nicht
gefunden. `supports_push` beschreibt Pushregistrierung, nicht den beobachteten
Rückkanal eines konkreten Clients. Der spätere Adapter muss daher offenlegen,
welcher Anbieter- beziehungsweise Protokollvertrag die Rückkanalfähigkeit belegt.
Eine pauschale Zulassung aller `notify`-Entitäten wäre keine Umsetzung dieser
Anforderung. Siehe [Quellenbefunde](nachweise.md#home-assistant-schnittstellen).

Technische Fähigkeit und momentane Erreichbarkeit bleiben getrennte Angaben.
Ein nicht erreichbares, aber kompatibles Gerät verliert dadurch nicht seine
Identität. Für gelöschte oder nicht mehr auflösbare Ziele muss ein Ergebnis
„nicht auflösbar“ darstellbar sein; es darf keinen stillen Ersatzempfänger geben.

## Sitzungsbezogene Akkumulation

Für eine unveränderte Zuordnung bezeichnet `M(u)` die Empfänger eines Benutzers
und `R(s)` die akkumulierten Empfänger einer Sitzung:

```text
Start durch u:                 R(s) = M(u)
Zugeordneter Eingriff durch v:  R(s) = R(s) ∪ M(v)
Wiederholung derselben Aufnahme: unveränderte Menge
Andere Sitzung t:             eigene Menge R(t)
```

Das ist der Mengenvertrag des Bausteins, keine Festlegung eines Bedienablaufs.
Er enthält keine Rangfolge zwischen Benutzern. Mehrfach zugeordnete Ziele werden
anhand ihrer aufgelösten Identität zusammengeführt. Die Sitzungsidentität stammt
aus der vorhandenen Laufzeit; eine zweite Definition des Sitzungsbeginns oder
-endes ist nicht erforderlich. Der vorhandene Unterschied zwischen Ausschalten,
Wiederaufnahme und tatsächlichem Sitzungsende bleibt maßgeblich.

Die fachliche Bedeutung von „Eingriff“ muss ein aufrufender Bedienweg liefern.
Die Grundform erhält dafür eine ausdrücklich zugeordnete Bedienung und soll
diese nicht aus beliebigen Zustandsänderungen ableiten. Welche Bedienungen
hierzu zählen, ist mit diesem Auftrag nicht abschließend festgelegt.

## Austausch ohne konkrete Interaktionen

Ein Transportadapter kann Versand und eingehende Rückmeldungen kapseln. Die
Hüllen ermöglichen die Zuordnung einer Rückmeldung zu Nachricht, Ziel und
gegebenenfalls Sitzung. Nutzdaten bleiben inhaltlich offen. Dadurch wird keine
konkrete Aktion, kein Ereigniskatalog und kein Sauna-Steuerbefehl eingeführt.

Der Baustein muss verschiedene Nachrichten und Sitzungen auseinanderhalten
können. Eine unbekannte Zuordnungsreferenz darf nicht als gültige Antwort einer
anderen Nachricht erscheinen. Ebenso muss sich die mehrfache Zustellung einer
bereits zugeordneten Rückmeldung erkennen lassen. Aufbewahrungsdauer,
Wiederholungsstrategie und konkrete Antwortverarbeitung sind spätere Entscheidungen.

Ein erfolgreicher Versandaufruf bezeichnet lediglich die Übergabe an den
Transport. Empfang auf dem Endgerät und eine tatsächlich eingegangene
Rückmeldung sind eigene Beobachtungen. Diese Trennung gehört bereits in die
möglichen Ergebnisformen; ein einzelnes Feld `success` wäre dafür mehrdeutig.

## Entscheidungen vor der jeweiligen Implementierung

| Noch offene Entscheidung | Auswirkung auf einen Baustein |
| --- | --- |
| Welche vorhandenen Bedienungen als Eingriff zählen | Welche Eingänge `ActorRef` an die Akkumulation weitergeben; insbesondere unveränderte oder abgewiesene Bedienungen. |
| Behandlung eines Starts ohne zuordenbaren Benutzer | Initialisierung ohne bekannte Zuordnung; kein Ersatzverhalten wird vorweggenommen. |
| Wirkung einer geänderten Zuordnung auf eine bestehende Sitzung | Gespeicherte Empfängerreferenzen oder erneute Auflösung einer Beteiligtenmenge; bei unveränderter Zuordnung gilt in beiden Formen der obige Mengenvertrag. |
| Dauerhaftigkeit der Empfängermenge und Nachrichtenreferenzen | Speicherort, Datenschutz und Verhalten bei Reload beziehungsweise Neustart. |
| Konkreter Fähigkeitsvertrag des Companion-Adapters | Welche Ziele nachweisbar auswählbar sind und wie die Zuordnung zum Versandweg erfolgt. |

Diese Punkte benennen die Grenzen der Grundformen. Sie sind kein Auftrag zur
Erfindung weiterer Produktregeln und verhindern nicht den Abschluss dieser
Vorbereitung. [Anschlusspunkte](anschlusspunkte.md) und
[Nachweise und Abschlusskriterien](nachweise.md) ergänzen den Entwurf.
