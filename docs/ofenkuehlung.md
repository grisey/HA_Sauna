# Ofenkühlung – geltender Stand

Diese Seite ersetzt frühere Beschreibungen von Zwangskühlung, Heizbudgets,
Temperatur-Zusatzkühlung, pausierbarer Kühlung und einer Anrechnung von
Nachläufen. Ein live aktiver Gang fordert vorläufig wie bestätigt Heizen an;
nur nach jedem nachlaufberechtigten, bestätigten und beendeten Gang gibt es
genau eine Ofenkühlung. Der technische Name `after_run` bleibt aus Kompatibilitätsgründen
bestehen; in der Oberfläche heißt die Phase **Ofenkühlung**.

## Beginn und Ende

Das Ende eines bestätigten Gangs fordert die Ofenkühlung sofort an und sperrt
jeden weiteren EIN-Befehl. Die Uhr beginnt jedoch erst, wenn die tatsächliche
Schützrückmeldung AUS bestätigt. Genau zu diesem tatsächlichen Start berechnet
und speichert die Steuerung Dauer und Berechnungsbeleg. Spätere Phasen-,
Temperatur- oder Rückmeldungsänderungen ändern diese bereits laufende Dauer
nicht.

Nur bestätigte AUS-Laufzeit zählt. Türereignisse, Personensignale und manuelles
Heizen können die Ofenkühlung nicht unterbrechen. Die regulären Thermostatpausen
bleiben davon unabhängig erhalten. Ein ausdrücklich manuelles Beenden ist möglich. Eine verkürzt beendete
Kühlung gilt nicht als vollständig und setzt den Bemessungszeitraum nicht zurück.
Betrieb-AUS oder ein Sitzungsabbruch setzen ihn ebenfalls nicht zurück.

Das Berechnungsfenster beginnt am Ende der letzten tatsächlich vollständig
abgeschlossenen Ofenkühlung und endet am tatsächlichen Beginn der neuen
Ofenkühlung. Ohne eine vorherige vollständige Kühlung beginnt es am Sitzungsbeginn.

## Bemessung

Die Basisdauer ist `after_run_minutes` (Standard **5 min**). Die berechnete
Dauer liegt zwischen dieser Basis und `oven_cooling_max_minutes` (Standard
**15 min**). Ein schon gespeicherter Basiswert bleibt erhalten; liegt er über
einem später eingeführten Maximum, ist er zugleich das wirksame Minimum und
Maximum.

Für jedes Intervall im Fenster wird die Zeit in Minuten exponentiell gewichtet:

\[
w(Alter)=2^{-Alter / h}, \qquad h=\texttt{oven\_cooling\_half\_life\_minutes}
\]

Der Standard für die Halbwertszeit ist **15 min**. Die Integration erfolgt
analytisch über die tatsächlichen Intervallgrenzen, nicht in einem künstlichen
Sekundenraster. Ein 15 Minuten alter Abschnitt zählt mit halbem Gewicht,
ein 30 Minuten alter mit einem Viertel. Das gilt gleichermaßen für Heizzeiten
und Bereitschaftspausen; sämtliche Gewichte beziehen sich auf den Kühlbeginn.

- **H** ist die gewichtete Zeit mit tatsächlich bestätigtem Schütz **EIN**,
  unabhängig von der gerade angezeigten Betriebsphase.
- **I** ist die gewichtete Schnittmenge aus korrigierter, exklusiver
  Bereitschaftspause, Betrieb EIN und tatsächlich bestätigtem Schütz **AUS**.
- Unbekannte Schützstellung ist weder Heizen noch Idle und erzeugt insbesondere
  keine Idle-Gutschrift.

Mit `oven_cooling_heat_idle_ratio` (Standard **2**) lautet die Dauer in Minuten:

\[
\text{Basis} + \operatorname{clip}(H / \text{Ratio} - I,\,0,\,
\text{wirksames Maximum}-\text{Basis})
\]

Der Idle-Saldo wird nicht vorzeitig auf null gesetzt; nur das Endergebnis wird
auf den erlaubten Bereich begrenzt. Überlappende Pausen und doppelte
Rückmeldungen dürfen keine Zeit doppelt zählen.

Mit den Standardwerten lautet die Formel `5 + clip(H / 2 - I, 0, 10)` Minuten.
Eine gewichtete Minute Bereitschaftspause gleicht zwei gewichtete Heizminuten
aus. Erst ein positiver verbleibender Heizanteil verlängert die Grundkühlung.
Die Formel ist die vereinbarte Betriebsregel; Halbwertszeit und Verhältnis sind
keine aus Ofentemperaturmessungen kalibrierten Material- oder Schutzgrenzen.

## Nachvollziehbarkeit

Die eingefrorene Berechnung speichert Dauer, Basis, wirksames Maximum,
Halbwertszeit, Verhältnis, gewichtete Heiz- und Idle-Minuten, Fenstergrenzen
und die Qualität. `complete` bedeutet vollständige Schütz- und
Phasenprojektion für die benötigte Aussage; `incomplete` macht fehlende oder
unbekannte Belege sichtbar, ohne daraus Idle zu erfinden.

Die Datenquellen bleiben voneinander getrennt: `contactor_history` enthält
reale Rückmeldungen, die Phasenprojektion ist eine rückblickende, exklusive
Darstellung. Ihre Korrektur erzeugt niemals nachträgliche Aktorbefehle.
Historische Kühlzyklen bleiben lesbar, steuern aber nichts mehr.
