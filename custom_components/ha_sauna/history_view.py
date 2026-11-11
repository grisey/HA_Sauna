"""LZ6: Gespeicherte Verlaufsansicht abgeschlossener Sitzungen.

Die Übersicht enthält je Messreihe und Spalte den ersten, letzten, kleinsten und
größten Wert (M4, Jugel u. a., VLDB 2014). Die Spalten sind Zeitabschnitte mit
einer Zweierpotenz an Millisekunden (``bin_ms``), ausgerichtet an der Epoche. Das
Panel fasst seine Kurven ebenso in Zweierpotenz-Abschnitten zusammen
(``historyDisplayValues``); jeder seiner Abschnitte besteht damit aus ganzen
Übersichtsspalten, solange seine Breite nicht kleiner als ``bin_ms`` ist. Erster,
letzter, kleinster und größter Wert sind dann dieselben Originalpunkte wie mit
allen Werten, die gezeichnete Kurve ist dieselbe.

Die Übersicht ist eine Teilmenge der Originalzeilen (``measurement_columns``):
Werte und Zeiten bleiben Originalwerte vorhandener Messungen. Dazu kommen alle
Zeilen der beiden Spalten, in denen Anfang und Ende des Messfensters liegen; mit
dem letzten Punkt jeder Spalte davor und dem ersten danach hat das Panel damit
die Originale seiner Randabschnitte. Und für jede Zeile die letzte Zeile derselben
Position und Zeit (sie ersetzt im Panel den Feuchtepunkt der früheren).

Die vollen Werte liegen in Zeitabschnitten von ``CHUNK_MS`` und werden für einen
Ausschnitt geliefert (Zoom, Details).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite, log2

from .presentation import public_measurement

# Rechenversion: eine Änderung macht alle gespeicherten Ansichten ungültig.
VERSION = 1
# Spalten der Übersicht: mindestens die breiteste Kurvenfläche des Panels in
# CSS-Pixeln (Seitenbreite höchstens 1280 px). Das Panel fasst je CSS-Pixel zusammen;
# das Geräte-Pixelverhältnis ändert die Abschnitte nicht.
OVERVIEW_COLUMNS = 2048
# Zeitabschnitt der vollen Werte: 2^20 ms (rund 17,5 min).
CHUNK_MS = 1 << 20
# Gröbere Stufen der Übersicht (je doppelte Abschnittsbreite) für schmalere Kurven.
LEVELS = 6

# Arten der Verlaufsprojektion: Administratoren alle, sonst nur Messungen und Phasen.
HISTORY_KINDS = (
    "measurement", "source_snapshot", "phase", "diagnostic",
    "detector_trace", "detection", "presence", "presence_source",
    "decision", "command", "light_command", "consumer_event",
    "source_state", "heating_observation", "illuminance", "notice",
    "manual_light", "manual_heater", "heater_override_rejected",
    "manual_phase_end", "manual_gang_end", "manual_session_light_end",
    "interruption", "session", "calibration", "notification",
)
PUBLIC_HISTORY_KINDS = ("measurement", "source_snapshot", "phase")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND = timedelta(milliseconds=1)
_ROW_KEYS = (
    "role", "id", "received_at", "value", "absolute_humidity",
    "absolute_humidity_valid_until",
)


def epoch_ms(value):
    """Millisekunden wie ``new Date(value).getTime()`` im Panel, sonst ``None``.

    Nachkommastellen über Millisekunden fallen weg (abgerundet). Zeiten ohne
    Zeitzone liest das Panel als Ortszeit; sie gelten hier als unbekannt.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return (parsed - _EPOCH) // _MILLISECOND


def bin_width(start_ms, end_ms, columns=OVERVIEW_COLUMNS):
    """Größte Zweierpotenz in ms, die höchstens (Ende − Anfang) / Spalten ist."""
    per_column = (end_ms - start_ms) // columns
    return 1 << max(0, per_column.bit_length() - 1) if per_column >= 1 else 1


def requested_width(start_ms, end_ms, columns):
    """Abschnittsbreite des Panels für ``columns`` Pixel: kleinste Zweierpotenz ≥ Spanne/Pixel.

    Mit der Breite der Kurvenfläche oder mehr Pixeln ist sie nie größer als die
    Breite, die das Panel selbst wählt.
    """
    per_column = (end_ms - start_ms) / max(1, columns)
    return 1 << max(0, ceil(log2(per_column))) if per_column > 1 else 1


def _number(value):
    return value if type(value) in (int, float) and isfinite(value) else None


def _m4(points, width, start, end, keep):
    """Erster, letzter, kleinster und größter Punkt je Abschnitt (Panelregeln).

    ``points`` ist nach (Zeit, ID) geordnet: (Zeit, ID, Wert, Zeile). Bei gleichen
    Werten gilt wie im Panel der erste. Zeilen ``None`` (Datensätze außerhalb der
    Spalten) werden ohnehin vollständig geliefert.
    """
    if not points:
        return
    edges = {start // width, end // width}
    key = None
    first = last = minimum = maximum = None
    for point in points:
        current = point[0] // width
        if current in edges:
            keep.add(point[3])
        if current != key:
            if key is not None:
                keep.update((first[3], last[3], minimum[3], maximum[3]))
            key = current
            first = last = minimum = maximum = point
            continue
        last = point
        if point[2] < minimum[2]:
            minimum = point
        if point[2] > maximum[2]:
            maximum = point
    keep.update((first[3], last[3], minimum[3], maximum[3]))


class OverviewSeries:
    """Messreihen der Spaltenzeilen wie im Panelindex, für die Auswahl je Breite.

    ``times`` sind die Millisekunden der Zeilen (``epoch_ms``), sonst werden sie
    gelesen. Datensätze außerhalb der Spalten (``records``) zählen nur für die
    Feuchteersetzung; sie werden ohnehin vollständig geliefert.
    """

    def __init__(self, columns, records=(), times=None):
        roles = columns["roles"]
        self.times = times if times is not None else [
            epoch_ms(value) for value in columns["received_at"]
        ]
        self.untimed = set()
        self.positions = []
        series = {}
        # Feuchtepunkt je (Position, Zeit): die letzte Zeile ersetzt frühere (Panelindex).
        humidity = {}
        # (Position, Zeit) der Feuchtepunkte aus Datensätzen außerhalb der Spalten.
        self.records = set()
        for row, time in enumerate(self.times):
            position = roles[columns["role"][row]]["position"]
            self.positions.append(position)
            if time is None:
                self.untimed.add(row)
                continue
            identity = columns["id"][row]
            value = _number(columns["value"][row])
            if value is not None:
                series.setdefault(
                    (position, roles[columns["role"][row]]["quantity"]), []
                ).append((time, identity, value, row))
            previous = humidity.get((position, time))
            if previous is None or previous[0] < identity:
                humidity[(position, time)] = (
                    identity, _number(columns["absolute_humidity"][row]), row,
                )
        for record in records:
            if record.get("kind") not in {"measurement", "source_snapshot"}:
                continue
            display = record.get("display")
            absolute = (
                display.get("absolute_humidity") if isinstance(display, Mapping) else None
            )
            payload = record.get("payload")
            if not isinstance(absolute, Mapping) or not isinstance(payload, Mapping):
                continue
            time = epoch_ms(absolute.get("received_at"))
            identity = record.get("id")
            if time is None or type(identity) is not int:
                continue
            key = (payload.get("position"), time)
            self.records.add(key)
            previous = humidity.get(key)
            if previous is None or previous[0] < identity:
                humidity[key] = (identity, _number(absolute.get("value")), None)
        for (position, time), (identity, value, row) in humidity.items():
            if value is not None:
                series.setdefault((position, "absolute_humidity"), []).append(
                    (time, identity, value, row)
                )
        for points in series.values():
            points.sort(key=lambda point: (point[0], point[1]))
        self.series = series
        self.humidity = humidity

    def subset(self, rows):
        """Dieselben Reihen nur mit den Zeilen ``rows`` (aufsteigend), neu nummeriert.

        ``rows`` enthält zu jeder Zeile die ersetzende letzte Zeile derselben
        Position und Zeit (``rows`` liefert das so); die Feuchteersetzung bleibt gleich.
        """
        number = {row: index for index, row in enumerate(rows)}
        result = object.__new__(OverviewSeries)
        result.times = [self.times[row] for row in rows]
        result.positions = [self.positions[row] for row in rows]
        result.untimed = {number[row] for row in self.untimed if row in number}
        result.series = {
            key: [
                (time, identity, value, None if row is None else number[row])
                for time, identity, value, row in points
                if row is None or row in number
            ]
            for key, points in self.series.items()
        }
        result.humidity = {
            key: (identity, value, None if row is None else number[row])
            for key, (identity, value, row) in self.humidity.items()
            if row is None or row in number
        }
        result.records = self.records
        return result

    def rows(self, start_ms, end_ms, width):
        """Zeilen der Übersicht mit Abschnittsbreite ``width``, aufsteigend."""
        keep = set(self.untimed)
        for points in self.series.values():
            _m4(points, width, start_ms, end_ms, keep)
        keep.discard(None)
        # Eine frühere Zeile oder ein übriger Datensatz derselben Position und Zeit
        # würde im Panel einen anderen Feuchtepunkt setzen, bis die letzte Zeile ihn
        # ersetzt: diese gehört dazu.
        keys = {
            (self.positions[row], self.times[row]) for row in keep
            if self.times[row] is not None
        } | self.records
        for key in keys:
            winner = self.humidity.get(key)
            if winner is not None and winner[2] is not None:
                keep.add(winner[2])
        return sorted(keep)


def select_rows(columns, rows):
    """Spalten mit den angegebenen Zeilen; Rollen bleiben unverändert."""
    selected = {key: [columns[key][row] for row in rows] for key in _ROW_KEYS}
    identities = set(selected["id"])
    return {
        "roles": columns["roles"],
        **selected,
        "record_received_at": [
            pair for pair in columns.get("record_received_at", ()) if pair[0] in identities
        ],
    }


def chunk_rows(columns, times):
    """Zeilen je Zeitabschnitt ``CHUNK_MS``; Zeilen ohne lesbare Zeit unter ``None``."""
    chunks = {}
    for row, time in enumerate(times):
        chunks.setdefault(None if time is None else time // CHUNK_MS, []).append(row)
    return chunks


def public_columns(columns, configuration):
    """Öffentliche Rollen: Quelle nur als Übereinstimmung mit der Zuordnung.

    Rollen, die öffentlich gleich aussehen, werden wie in
    ``history_measurement_columns`` zusammengelegt.
    """
    public, mapping = [], []
    for role in columns["roles"]:
        shown = {"kind": role["kind"], "position": role["position"],
                 "quantity": role["quantity"]}
        shown.update({
            name: value for name, value in public_measurement(
                {"position": role["position"], "quantity": role["quantity"],
                 "source": role.get("source")},
                configuration=configuration,
            ).items() if name == "source_matches_binding"
        })
        number = next((known for known, other in enumerate(public) if other == shown), None)
        if number is None:
            number = len(public)
            public.append(shown)
        mapping.append(number)
    return {
        **columns, "roles": public, "role": [mapping[index] for index in columns["role"]],
    }


def join_columns(blocks):
    """Spalten aufeinanderfolgender Seiten zu einem Block mit gemeinsamen Rollen."""
    roles, joined = [], {key: [] for key in _ROW_KEYS}
    joined["record_received_at"] = []
    for block in blocks:
        mapping = []
        for role in block["roles"]:
            number = next((known for known, other in enumerate(roles) if other == role), None)
            if number is None:
                number = len(roles)
                roles.append(role)
            mapping.append(number)
        joined["role"].extend(mapping[index] for index in block["role"])
        for key in _ROW_KEYS[1:]:
            joined[key].extend(block[key])
        joined["record_received_at"].extend(block.get("record_received_at", ()))
    return {"roles": roles, **joined}


def merge_columns(blocks, roles):
    """Spaltenblöcke mit denselben Rollen zu einem, geordnet nach ID."""
    rows = []
    received = []
    for block in blocks:
        rows.extend(zip(*(block[key] for key in _ROW_KEYS), strict=True))
        received.extend(block.get("record_received_at", ()))
    rows.sort(key=lambda row: row[1])
    merged = {"roles": roles}
    for index, key in enumerate(_ROW_KEYS):
        merged[key] = [row[index] for row in rows]
    merged["record_received_at"] = sorted(received)
    return merged
