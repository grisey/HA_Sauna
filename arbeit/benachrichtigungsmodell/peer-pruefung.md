# Unabhängige Peerprüfung der materialisierten Grundformen

Stand: 15. Oktober 2026. Statische Quellprüfung der beiden neuen Coremodule und ihrer beiden Testdateien auf dem Branch `codex/notification-model-preparation`. AGENTS.md und grundformen.md, anschlusspunkte.md, nachweise.md wurden selbst gelesen; bestehende HTTP-, Entitäts-, Sitzungs- und notify-Anschlüsse wurden quergelesen. Keine Tests oder Remotezugriffe ausgeführt. Die folgenden Eingänge sind aus dem Quelltext nachvollzogene Konstruktionen, keine behaupteten Prozessbeobachtungen.

## Erforderliche Korrekturen der Erstprüfung

1. **P2 – Eine gemeinsame Eignungsprüfung für Zuordnungen fehlt.** `custom_components/ha_sauna/core/notification_recipients.py:108` nimmt jede Referenzmenge entgegen, ohne Auflösung und Fähigkeiten einzubeziehen. Ein `RecipientAssignment("u", {ref})` mit `RecipientResolution(ref)` oder einer aufgelösten Referenz ohne Rückkanalbeleg wird regulär konstruiert. `SessionRecipients.accumulate` (Zeilen 152–158) lässt dieses Ziel lediglich aus. Die Grundlagenseite verlangt dieselbe nach Fähigkeiten begründete Eignung für Auswahl und serverseitiges Speichern; stilles Wegfiltern ist keine verständliche Ablehnung einer ungeeigneten Zuordnung. Erforderlich ist ein allgemeiner, vom künftigen Adapter aufrufbarer Prüfpfad, der Referenz, Auflösung und beide Fähigkeitsbelege gemeinsam bewertet und fehlende/ungeeignete Auflösungen ausdrücklich unterscheidet. Dafür braucht es weder eine HA-Konfigurationsansicht noch einen Runtimeanschluss. Die rohe Zuordnungsform darf für Altbestand/unaufgelöste Daten bestehen bleiben, muss dann von einer geprüften Zuordnung unterscheidbar sein.

2. **P2 – Ungültige zusammengesetzte Formen umgehen die vorhandene Identitätsvalidierung.** `SessionRecipients.__post_init__` (Zeile 127) friert eine Menge ein, kontrolliert ihre Elemente aber nicht: `SessionRecipients("e", "s", {"kein Ziel"})` enthält dauerhaft einen unadressierbaren Eintrag. Entsprechend erlaubt `RecipientAssignment("u", {"kein Verweis"})` einen ungültigen Zuordnungszustand. `ResolvedRecipient` prüft nur Transportgleichheit und akzeptiert etwa `capability=None`; dies führt erst bei Akkumulation zu einem Attributfehler. Die Typannotationen schützen den tatsächlich aufrufbaren Python-Eingang nicht. Erforderlich sind konsistente Typprüfungen für die tragenden Unterformen und Mengen, damit ungültige Daten an der Grundformgrenze ausdrücklich abgewiesen werden.

3. **P2 – Rückmeldungen ohne Herkunft können als zugeordnet akzeptiert werden.** `custom_components/ha_sauna/core/notification_exchange.py:29` und `:46` prüfen nur Zeichenketten. `NotificationEnvelope("m", "c", "e", None, None)` sowie `ReplyEnvelope("r", "m", "c", "e", None, None, None)` werden konstruiert. Nach Registrierung der ersten Hülle berechnet `_key` für beide `(e, None, c, None)`; `receive` erreicht daher `ACCEPTED`, obwohl weder Ziel noch Herkunft noch ActorRef existieren. Auch `TransportHandoff(..., "nein", "Beleg")` akzeptiert einen wahrheitswertigen Text als Annahmefeld. Erforderlich sind Typprüfungen der verpflichtenden Ziel-/Herkunfts-/Actor- und Beobachtungsformen einschließlich des booleschen Annahmefeldes. Die externe Authentifizierung eines echten Clients bleibt ausdrücklich Adapterpflicht; sie wird durch diese Korrektur nicht vorgetäuscht.

## Positive Quellbefunde und Grenze

Die Grundformen erfinden keine Benachrichtigungsanlässe, Bedienungen, Gerätearten oder Benutzerzahlen und aktivieren keine Laufzeit. Benutzer-ID, Eingangsherkunft und Antwortbenutzer werden getrennt getragen. Ein unbekannter Benutzer führt nicht zur Auswahl aller Empfänger. Die Akkumulation vereinigt kanonische Transportidentitäten, hält vorherige Ziele und bindet den Zustand an Instanz/Sitzung. Die Korrelation prüft Instanz, Sitzung, Korrelationsreferenz, Transportziel und Nachrichten-ID vor der Duplikatbuchung. Doppelte Registrierung löscht keine Duplikatkenntnis. Übergabe, Endgerätebeobachtung und Rückmeldung sind getrennte Formen.

Ein realer Resolver und dessen Anbieter-/Protokollvertrag, die Herkunftsprüfung des Adapters, tatsächlicher Versand und tatsächlicher Rückkanal sind nicht implementiert oder beobachtet. Nichtleere Belegtexte beweisen deren Wahrheit nicht; sie sind hier ausdrücklich adaptergelieferte Angaben. Die Tests sind synthetische Vertragsbeispiele und wurden nur gelesen. Eine positive Freigabe der gegenwärtigen Materialisierung wird wegen der drei Korrekturpunkte nicht erteilt.

## Verbleibende semantische Entscheidungen

Welche Bedienung als Eingriff zählt, unbekannter Starter, Wirkung später geänderter Zuordnung, Persistenz und Aufbewahrungsdauer bleiben Entscheidungen außerhalb dieser Materialisierung. Die vorhandene explizite snapshotbasierte Vereinigung ist eine Grundoperation und kein festgelegter Produktablauf. Keine dieser offenen Entscheidungen erfordert zusätzliche Produktaktionen in diesem Auftrag.

## Geprüfte Dateihashes (SHA-256)

| Datei | Hash |
| --- | --- |
| `custom_components/ha_sauna/core/notification_recipients.py` | `84d1a15c62e211ec1ff8990b740dd754e23215d4556b3044ce3081bc3349cd50` |
| `custom_components/ha_sauna/core/notification_exchange.py` | `db848191b6a02bbe12d595ec695209e9e2262cf13a8e74814b49239589398925` |
| `tests/test_notification_recipients.py` | `220ed2e16e1280450d8b8cc78a078e1ca538e27f9c1e910e748e077a57f7e1b6` |
| `tests/test_notification_exchange.py` | `9581988746c64605c82bc07f91e2576e0bdfc9db59f2e88e275bed339c4e4655` |

## Unabhängige Nachprüfung nach Korrektur

Nach erneuter Bereitschaftsmeldung wurden beide Coremodule und beide Testdateien vollständig neu gelesen. Die Erstbefunde bleiben oben historisch erhalten. Keine Tests ausgeführt; kein CI-Ergebnis wurde als Voraussetzung der folgenden Quellbewertung angenommen.

1. **Erstbefund 1 geschlossen:** `eligible_recipients` und `validate_assignment` verwenden dieselbe `_eligible`-Regel und denselben konfliktprüfenden Auflösungsindex. Die Speichergrenze unterscheidet fehlende Auflösung, nicht auflösbares Ziel und fehlende Fähigkeitsbelege durch ausdrückliche Fehler. Eine gemischte Zuordnung erhält kein partielles Erfolgsergebnis. `RecipientAssignment` ist nun ausdrücklich als rohe Form dokumentiert; die Prüffunktion ist die allgemeine Grenze vor dem Speichern. Es wird noch kein tatsächliches Speichern in HA behauptet.
2. **Erstbefund 2 geschlossen:** Aufgelöste Empfänger kontrollieren Referenz, Ziel und Fähigkeitsform, Auflösungen ihre Unterform; Mengen kontrollieren vor der Umwandlung ihre Elemente. Akkumulation kontrolliert Actor, Zuordnungen und Auflösungseinträge. Die im Erstbericht genannten Stringziele und `capability=None` scheitern damit bereits an der Konstruktion.
3. **Erstbefund 3 geschlossen:** Ziel und Herkunft müssen `TargetIdentity` sein, der Antwortactor muss `ActorRef` sein. Handoff und Receipt verlangen eine NotificationEnvelope; Annahme muss bool sein. Auch die öffentlichen Register-/Receive-/Forget-Grenzen prüfen den jeweiligen Hüllentyp. Der zuvor beschriebene Weg über zwei None-Ziele erreicht die Korrelation daher nicht mehr.

Bei dieser Nachprüfung ergaben sich keine weiteren erforderlichen Korrekturen für den vereinbarten Umfang. Die gemeinsame Eignungsregel ist mit Referenzbindung und kanonischer Zielidentität verbunden; die Mengen- und Korrelationsregeln bleiben erhalten. Die gelesenen synthetischen Tests decken die korrigierten Grenzen mit direkten Konstruktionen ab. Das ist eine statische Feststellung über deren Inhalt, keine Aussage über ausgeführte oder bestandene Tests.

**Positive Peerbewertung ausschließlich für die Materialisierung der allgemeinen Grundformen:** Der geprüfte Stand bildet Identität, referenzgebundene Eignung, explizite Zuordnung, kanonische sitzungsbezogene Akkumulation und isolierte Austauschzuordnung ohne zusätzliche Produktregeln ab. Er aktiviert keinen HA-Pfad. Weder reale HA-Konfiguration noch Companion-Versand, Endgeräteempfang oder Rückmeldung sind damit nachgewiesen. Externe Resolverwahrheit, Herkunftsauthentifizierung und die oben benannten späteren semantischen Entscheidungen bleiben außerhalb dieser positiven Bewertung.

### Geprüfter korrigierter Stand (SHA-256)

| Datei | Hash |
| --- | --- |
| `custom_components/ha_sauna/core/notification_recipients.py` | `da9203b163b4debe4fcdb5e76ca317ef959f88f4e5d87222898a6befdc47866a` |
| `custom_components/ha_sauna/core/notification_exchange.py` | `f855ac7963062ba3f7765a1cd50a6ad626dcb5620774982144b599b069e1c0c8` |
| `tests/test_notification_recipients.py` | `227a566c2d251c39239e6b2d7db0019be08269f193c292fa6296d6e4c6b88820` |
| `tests/test_notification_exchange.py` | `5ebc6ea3fb1a4e78d87555243b59239feb5d1c63eede55abaf170bb099fc9a14` |
