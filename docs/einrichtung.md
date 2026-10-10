# Sauna einrichten

## Installation vorbereiten

Voraussetzung ist Home Assistant ab **2026.9.0**. Das HACS-Repository
`https://github.com/grisey/HA_Sauna` wird als Typ **Integration** eingebunden.
Installation und Updates erfordern einen Home-Assistant-Neustart.

HA Sauna übernimmt Ofen und Licht; parallele Steuerungsautomationen müssen
deaktiviert sein. Externe Lichttaster werden als manuelle Eingriffe ausgewertet.

## Sensoren und Geräte zuordnen

Die Home-Assistant-Integrationskonfiguration enthält **Geräte und Erkennungsverfahren**
einschließlich Bedieneingang. Eine Änderung der Zuordnung
erhält die gespeicherten Einstellungen, Programme und Darstellung.

Erforderlich sind mindestens ein vollständiges Temperatur-/Feuchtepaar,
Heizschütz, Bedieneingang und dimmbares Licht. Temperatur und Feuchte eines
Paars müssen denselben Messort abbilden. Die
[Entitätsrollen](parameter.md#entitätsrollen) beschreiben unterstützte Typen,
Einheiten und optionale Quellen.

Den Vorrang der Messpositionen beschreibt
[Aufheizen und Bereitschaft](betrieb.md#aufheizen-und-bereitschaft), den Umgang
mit fehlenden oder veralteten Messwerten
[Ausfälle und Schutzabschaltung](betrieb.md#bei-sensorausfall-oder-schutzabschaltung).

## Name und Entitäten verwalten

**Saunaname** ändert den Namen des Geräts und der ausgewählten Sauna im Panel.
Bei mehreren Saunen heißt der gemeinsame Seitenleisteneintrag weiterhin „Sauna“.

**Entität umbenennen** umfasst eigene und zugeordnete Entitäten. Änderungen der
Entitäts-ID führen die Zuordnungen aller betroffenen Sauna-Instanzen nach und
laden diese neu; Ofen und Licht werden dabei ausgeschaltet. Anzeigenamen allein
erfordern keinen Neustart. Außerhalb dieses Dialogs geänderte IDs werden nicht
nachgeführt. Verweise in anderen Automationen und Dashboards bleiben außerhalb
dieses Ablaufs.

**Entitäten bereinigen** zeigt nicht mehr bereitgestellte Sauna-Entitäten vor
dem Entfernen an. Erst die Bestätigung löscht deren Registereinträge.
Weiterhin gültige, auch deaktivierte Entitäten und gespeicherte Verläufe bleiben
erhalten.

## Gangerkennung festlegen

Gänge werden entweder aus Temperatur und Feuchte oder mit einem direkten
Präsenzsensor erkannt. Die Türerkennung stammt in beiden Verfahren weiterhin
aus Temperatur und Feuchte. Der Präsenzsensor ersetzt ausschließlich den
indirekten Anwesenheitsnachweis. Beim FP300 gehört dazu die Präsenzentität;
der PIR-Bewegungsausgang belegt keine dauerhafte Anwesenheit.

Beide Verfahren benötigen zum Beginn eine erkannte
Türöffnung mit anschließendem Türschluss. Zum Ende genügen eine erneute
Öffnung und der passende Abwesenheits- oder Lüftungsnachweis. Die [Gangerkennung](gangmodell.md) beschreibt die
Bedingungen beider Verfahren.

## Betrieb, Erkennung und Licht einstellen

Die Integrationskonfiguration gliedert die dauerhaften Einstellungen in
**Betrieb und Ofen**, **Sensoren und Erkennung** sowie **Licht**. Die jeweiligen
Untergruppen enthalten Regelung, Erkennung und Lichtautomatik.
Änderungen erfordern Administratorrechte und eine vollständig beendete Sitzung.

Programme, Taster- und Startvorgaben sowie Darstellung bleiben unter
**Einstellungen** im Sauna-Panel. Die aktuelle Temperaturwahl und vorübergehende
Ofen- oder Lichtwahl werden in der Steuerungsansicht bedient.

[Einstellungen und Rücksetzungen](parameter.md) ·
[Sitzung starten und bedienen](bedienung.md)
