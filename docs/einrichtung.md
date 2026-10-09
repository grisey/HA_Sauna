# Sauna einrichten

## Installation vorbereiten

Voraussetzung ist Home Assistant ab **2026.9.0**. Das HACS-Repository
`https://github.com/grisey/HA_Sauna` wird als Typ **Integration** eingebunden.
Installation und Updates erfordern einen Home-Assistant-Neustart.

HA Sauna übernimmt Ofen und Licht; parallele Steuerungsautomationen müssen
deaktiviert sein. Externe Lichttaster werden als manuelle Eingriffe ausgewertet.

## Sensoren und Geräte zuordnen

Erforderlich sind mindestens ein vollständiges Temperatur-/Feuchtepaar,
Heizschütz, Bedieneingang und dimmbares Licht. Temperatur und Feuchte eines
Paars müssen denselben Messort abbilden. Die
[Entitätsrollen](parameter.md#entitätsrollen) beschreiben unterstützte Typen,
Einheiten und optionale Quellen.

Den Vorrang der Messpositionen beschreibt
[Aufheizen und Bereitschaft](betrieb.md#aufheizen-und-bereitschaft), den Umgang
mit fehlenden oder veralteten Messwerten
[Ausfälle und Schutzabschaltung](betrieb.md#bei-sensorausfall-oder-schutzabschaltung).

## Gangerkennung festlegen

Gänge werden entweder aus Temperatur und Feuchte oder mit einem direkten
Präsenzsensor erkannt. Beide Verfahren benötigen zum Beginn eine erkannte
Türöffnung mit anschließendem Türschluss. Zum Ende genügen eine erneute
Öffnung und der passende Abwesenheits- oder Lüftungsnachweis. Die [Gangerkennung](gangmodell.md) beschreibt die
Bedingungen beider Verfahren.

[Einstellungen und Rücksetzungen](parameter.md) ·
[Sitzung starten und bedienen](bedienung.md)
