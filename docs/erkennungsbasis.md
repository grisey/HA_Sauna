# Fortlaufende probabilistische Erkennungsbasis

`core/sequential_evidence.py` bildet die gemeinsame mathematische Basis. Die
bisherige Ereignisbestätigung wird getrennt abgelöst; bis zur vollständigen
Umstellung ist diese Seite keine Beschreibung des bereits aktiven Detektors.

## Einmalige Beobachtungen und konkurrierende Erklärungen

Jeder empfangene Originalwert aktualisiert konkurrierende Hypothesen genau
einmal. Seine Identität enthält Quelle, Quellenabschnitt, Messgröße, Zeitbasis
und Originalzeit. Wiederholte Meldungen und Auswertungen erhöhen keine
Wahrscheinlichkeit. Fehlende Werte liefern keine Evidenz. Korrekturen ersetzen
ihren ursprünglichen Beitrag, ihre Empfangszeit bleibt separat erhalten.

Für Hypothese `h`, neue Beobachtung `y` und bisherige Originale `D` gilt:

```text
P(h | D, y) proportional P(h | D) * p(y | h, D)
```

Die Vorhersagedichte wird VOR Aufnahme des neuen Werts berechnet. Derselbe
Wert darf nicht erst eingepasst und anschließend als unabhängige Bestätigung
des angepassten Modells verwendet werden. Alle Alternativen erklären dieselben
Beobachtungen. Mehrere mögliche Beginne desselben Ereignisses teilen dessen
Priorwahrscheinlichkeit; ihre Anzahl erzeugt keine zusätzliche Evidenz.

## Modell- und Rauschunsicherheit

Die erwartete Kurve ist linear in kalibrierbaren Koeffizienten:
`y = x(t)^T b + epsilon`. Basisfunktionen können Polynome, Steigungs- oder
Krümmungsknicke enthalten. Ihre Zeitkoordinaten bleiben fest; eine längere
Beobachtung verändert nicht nachträglich den Prior.

Es gilt `b | sigma² ~ N(m, sigma² C)` und `sigma² ~ InvGamma(a, r)`.
Mit `q = 1 + x^T C x` ist die Vorhersage eine Student-t-Verteilung mit `2a`
Freiheitsgraden, Lage `x^T m` und Skalenquadrat `r/a * q`. Sowohl die
Unsicherheit der Koeffizienten als auch der Streuung gehen damit ein.
Ein Original aktualisiert:

```text
e  = y - x^T m
m' = m + C x * e/q
C' = C - C x x^T C/q
a' = a + 1/2
r' = r + e²/(2q)
```

Für `C'` verwendet die Implementierung die algebraisch äquivalente Josephform
zur numerischen Stabilität. Sie ergänzt keine künstliche Rauschuntergrenze.
Grundlage sind die Gleichungen des konjugierten
[Bayesschen linearen Modells](https://arxiv.org/html/2406.01819v2).

Ein properer Prior erlaubt Vorhersagen auch vor vollem Rang der beobachteten
Entwurfsmatrix. Wenige Werte erzeugen entsprechende Unsicherheit. Es gibt
keine zusätzliche Mindestzahl je Flanke und keine K-/2K-Bestätigungsblöcke.
Priormittel, Kovarianz, Varianzverteilung, Ereignispriors und Entscheidungsschwelle
sind explizite Eingaben, keine versteckten Standardwerte.

## Gerichtete Kurvenhypothesen

Eine physikalische Richtung kann direkt zum Modell gehören. `DirectedRegression`
beschränkt den Koeffizientenprior auf einen Halbraum `cᵀb > d`, beispielsweise
einen negativen Steigungsknick. Die Kurve darf beim Anpassen nicht unbemerkt
ihre Richtung und damit die Bedeutung des Ereignisses wechseln.

Die uneingeschränkte konjugierte Verteilung bleibt rechnerisch erhalten.
Mit `M(D) = P(cᵀb > d | D)` lautet die korrekte Vorhersagedichte des
gerichteten Modells:

```text
p(y | D, cᵀb > d) = p(y | D) * M(D,y) / M(D)
```

Der Kontrast hat eine Student-t-Verteilung mit Lage `cᵀm`, Skalenquadrat
`r/a * cᵀCc` und `2a` Freiheitsgraden. Hier wird kein zusätzliches
Messrauschen addiert. Die Student-t-Randwahrscheinlichkeit wird über die
[unvollständige Betafunktion](https://dlmf.nist.gov/8.17) berechnet.
Numerische Nichtkonvergenz ist ein Rechenfehler und kein Gegenbeleg.
Mehrere korrelierte Richtungsbedingungen dürfen nicht durch Multiplikation
einzelner Randwahrscheinlichkeiten ersetzt werden.

Ein negativer Steigungsknick ist zunächst eine Kurveneigenschaft. Seine
Zuordnung zur Türöffnung benötigt ein entsprechendes fachliches Modell;
die Bezeichnung allein liefert keine zusätzliche Wahrscheinlichkeit.

## Fortlaufende Beginnhypothesen

`core/curve_probability.py` vergleicht den unveränderten Hintergrund mit
kalibrierbaren kausalen Antworten: Steigungs- und Krümmungswechsel,
exponentielle Annäherung, Impuls und allmähliche Bremsung. Ihre Zeitmaßstäbe
beschreiben die Kurvenform; sie sind keine Wartezeiten oder Ablaufgrenzen.
Die positive unsichere Amplitude gehört zum gerichteten Modell.

Jeder neue Originalwert kann weitere mögliche Beginne zwischen der bisherigen
und der neuen Messzeit eröffnen. Die bisherige Hintergrundwahrscheinlichkeit
teilt sich gemäß einer expliziten Ereignisrate `lambda` auf:

```text
P(noch kein Beginn über dt) = exp(-lambda * dt)
P(Beginn innerhalb dt)     = 1 - exp(-lambda * dt)
```

Mehrere Profile und Beginnpositionen teilen dieselbe neue Wahrscheinlichkeitsmasse.
Die Hintergrundverteilung VOR dem neuen Wert liefert ihren gemeinsamen
Ausgangszustand. Anschließend erhalten bestehende und neue Alternativen
denselben Originalwert jeweils genau einmal. Wiederholungen und fehlende
Werte eröffnen keine Beginne; reine Zeitfortschreibung ist keine Beobachtung.

Die unbekannte Beginnzeit innerhalb eines Messabstands hat eine ausdrücklich
angegebene endliche Verteilung. Deren Rechnung ist exakt für dieses endliche
Modell. Sie ist keine exakte Integration über alle kontinuierlichen Zeitpunkte.
Eine feinere Verteilung benötigt einen Konvergenznachweis. Beginnhypothese,
zugehöriger Messabstand und tatsächlicher Bestätigungszeitpunkt bleiben
verschiedene Angaben.

Ereignisrate, Kurvenmaßstäbe, Amplitudenstreuungen und Profilgewichte sind
explizite kalibrierbare Eingaben. Es gibt keine übernommenen Katalogdefaults
aus dem bisherigen Detektor. Ebenso gibt es keine verdeckte Kürzung schwacher
Hypothesen: Kontextübergänge und Laufzeitgrenzen sind vor dem produktiven
Einsatz zu qualifizieren.

## Entscheidung und Kalibrierung

Ein Ereignis erreicht die explizite Wahrscheinlichkeitsschwelle `q_event`,
wenn seine Posterior-Odds mindestens `q_event/(1-q_event)` betragen. Der
Abstand wird in Log-Odds ausgewiesen. Ein Prior ohne beobachteten Originalwert
bestätigt kein Ereignis.

Die Wahrscheinlichkeiten gelten innerhalb der angegebenen Modelle. Eine
empirisch belastbare Ereigniswahrscheinlichkeit benötigt zusätzlich geeignete
Alternativen und eine unabhängige Kalibrierungsprüfung. Ein BIC-Score wird
nicht als kalibrierte Wahrscheinlichkeit ausgegeben. R01 sowie die
zusammenhängenden R02/R03 liefern die vorgesehenen Kalibrierungsdaten; sie
ersetzen keinen unabhängigen Qualitätsnachweis. Unsichere Türgrenzen werden
nicht als exakt gelabelte Trainingsdaten verwendet.

Standardmäßig führt der obere physische Temperatur-/Feuchtesensor. Der
untere liefert bei Unklarheit eigene Evidenz und ist keine verpflichtende
zweite Bestätigung. Die mathematische Basis nimmt unabhängiges Messrauschen
je Kanal BEDINGT auf die Kurvenhypothese an. Diese Annahme ist bei der
Kalibrierung zu prüfen. Die beiden Messgrößen werden nicht als unabhängig
abstimmende Ereignisdetektoren behandelt.

Die Fachschicht ordnet die Wahrscheinlichkeiten dem aktuellen Tür-/Aufguss-
vorgang zu. Die natürliche Abklingphase nach einem Aufguss begründet keine
feste Sperrzeit. Eine eigenständig belegte Türöffnung darf einen
softwareseitig noch offenen Aufguss beenden.
