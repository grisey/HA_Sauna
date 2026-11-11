"""LZ6: Übersicht (M4) und volle Werte der gespeicherten Verlaufsansicht.

Die Übersicht ist exakt, wenn das Panel aus ihr dieselben Kurvenpunkte bildet wie aus
allen Werten. Als Orakel dient eine Nachbildung von ``historyIndex`` (Reihen, Ersetzen
des Feuchtepunkts derselben Zeit) und ``historyDisplayValues`` (Abschnitte mit
Zweierpotenz-Breite, Randabschnitte mit je einem Nachbarn außerhalb) aus panel.js.
"""

import random
import unittest
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite, log2

from custom_components.ha_sauna import history_view
from custom_components.ha_sauna.history_view import (
    OverviewSeries,
    bin_width,
    epoch_ms,
    join_columns,
    merge_columns,
    public_columns,
    requested_width,
    select_rows,
)

ORIGIN = datetime(2032, 1, 1, 12, tzinfo=UTC)


def iso(milliseconds, micro=0):
    return (ORIGIN + timedelta(milliseconds=milliseconds, microseconds=micro)).isoformat()


def expand(columns):
    """Datensätze wie ``historyPageRecords`` im Panel."""
    times = dict(map(tuple, columns.get("record_received_at", ())))
    records = []
    for row, identity in enumerate(columns["id"]):
        role = dict(columns["roles"][columns["role"][row]])
        kind = role.pop("kind")
        received = columns["received_at"][row]
        records.append({
            "id": identity, "kind": kind, "received_at": times.get(identity, received),
            "payload": {**role, "value": columns["value"][row], "received_at": received},
            "display": {"absolute_humidity": {
                "value": columns["absolute_humidity"][row], "received_at": received,
                "valid_until": columns["absolute_humidity_valid_until"][row],
            }},
        })
    return records


def panel_series(records):
    """``historyIndex``: Kurvenreihen (nur Werte) je Position und Größe."""
    series = {}
    for record in sorted(records, key=lambda record: record["id"]):
        if record["kind"] not in {"measurement", "source_snapshot"}:
            continue
        payload = record["payload"]
        time = epoch_ms(payload.get("received_at"))
        if time is None:
            continue
        value = payload.get("value")
        value = value if type(value) in (int, float) and isfinite(value) else None
        observations = [(payload["quantity"], time, value, (record["id"], "v"))]
        absolute = (record.get("display") or {}).get("absolute_humidity")
        absolute_time = epoch_ms(absolute.get("received_at")) if absolute else None
        if absolute_time is not None:
            number = absolute.get("value")
            observations.append((
                "absolute_humidity", absolute_time,
                number if type(number) in (int, float) and isfinite(number) else None,
                (record["id"], "a"),
            ))
        for quantity, at, number, source in observations:
            points = series.setdefault((payload["position"], quantity), [])
            if quantity == "absolute_humidity":
                index = bisect_left([point[0] for point in points], at)
                if index < len(points) and points[index][0] == at:
                    del points[index]
                if number is not None:
                    points.insert(bisect_left([point[0] for point in points], at),
                                  (at, number, source))
                else:
                    # Der Rohwert ohne Zahl ersetzt den früheren Kurvenpunkt nur.
                    pass
            elif number is not None:
                points.append((at, number, source))
    for key, points in series.items():
        if key[1] != "absolute_humidity":
            points.sort(key=lambda point: point[0])
    return series


def display_values(points, start, end, pixels):
    """``historyDisplayValues``: Kurvenpunkte eines Fensters (Zeit, Wert, Quelle)."""
    if not points:
        return []
    width = max(1, 2 ** ceil(log2((end - start) / pixels or 1)))
    times = [point[0] for point in points]
    first_index = max(0, bisect_left(times, start) - 1)
    after_index = min(len(points), bisect_left(times, end + 1) + 1)
    nodes, keys = {}, []
    for index, point in enumerate(points):
        key = point[0] // width
        node = nodes.get(key)
        if node is None:
            nodes[key] = {"first_index": index, "last_index": index, "first": point,
                          "last": point, "minimum": point, "maximum": point}
            keys.append(key)
            continue
        node["last_index"] = index
        node["last"] = point
        if point[1] < node["minimum"][1]:
            node["minimum"] = point
        if point[1] > node["maximum"][1]:
            node["maximum"] = point
    first_key = points[first_index][0] // width
    after_key = points[after_index - 1][0] // width + 1
    output = []
    for key in keys[bisect_left(keys, first_key):bisect_left(keys, after_key)]:
        node = nodes[key]
        if node["first_index"] < first_index or node["last_index"] >= after_index:
            left = max(first_index, node["first_index"])
            right = min(after_index, node["last_index"] + 1)
            minimum = maximum = points[left]
            for point in points[left + 1:right]:
                if point[1] < minimum[1]:
                    minimum = point
                if point[1] > maximum[1]:
                    maximum = point
            node = {"first": points[left], "last": points[right - 1],
                    "minimum": minimum, "maximum": maximum}
        selected = sorted((node["first"], node["minimum"], node["maximum"], node["last"]),
                          key=lambda point: point[0])
        chosen = []
        for point in selected:
            if not chosen or chosen[-1][2] != point[2]:
                chosen.append(point)
        for point in chosen:
            if not output or output[-1][2] != point[2]:
                output.append(point)
    return output


def curves(records, start, end, pixels):
    return {key: display_values(points, start, end, pixels)
            for key, points in panel_series(records).items()}


def random_view(seed, rows=3000):
    """Spalten und übrige Datensätze einer Sitzung mit Lücken, Nullwerten, gleichen
    Zeiten, Feuchtepaaren, Spitzen und Werten vor und nach dem Messfenster."""
    rng = random.Random(seed)
    # Fenstergrenzen nicht auf Abschnittsgrenzen (Randabschnitte des Panels).
    start = rng.randint(1, 900_000)
    end = start + 3_600_000 + rng.randint(0, 99_999)
    roles = [
        {"kind": "measurement", "position": position, "quantity": quantity,
         "source": f"sensor.{position}_{quantity}"}
        for position in ("upper", "lower") for quantity in ("temperature", "humidity")
    ] + [{"kind": "source_snapshot", "position": "upper", "quantity": "temperature",
          "source": "sensor.upper_temperature"}]
    columns = {key: [] for key in (
        "role", "id", "received_at", "value", "absolute_humidity",
        "absolute_humidity_valid_until",
    )}
    columns["roles"], columns["record_received_at"] = roles, []
    identity, time = 0, start - rng.randint(0, 200_000)
    level = {role: 60.0 for role in range(len(roles))}
    remaining = []
    while len(columns["id"]) < rows:
        time += rng.choice((0, 0, 1, 7, 250, 997, 1000, 1000, 1500, 4000, 20_000))
        role = rng.choice((0, 0, 1, 1, 2, 3, 4)) if rng.random() < 0.98 else 4
        # Stufen, Plateaus und Spitzen; gleiche Werte prüfen die Regel „der erste“.
        level[role] += rng.choice((-1.5, -0.5, 0.0, 0.0, 0.0, 0.5, 1.0, 12.0, -9.0))
        value = None if rng.random() < 0.05 else round(level[role] * 2) / 2
        identity += rng.randint(1, 3)
        if rng.random() < 0.02:
            # Messung in anderer Form: bleibt ein einzelner Datensatz.
            remaining.append({
                "id": identity, "kind": "measurement", "received_at": iso(time),
                "payload": {"position": roles[role]["position"],
                            "quantity": roles[role]["quantity"], "value": value,
                            "received_at": iso(time), "source": None},
                "display": {"absolute_humidity": {"value": rng.choice((None, 9.5)),
                                                  "received_at": iso(time),
                                                  "valid_until": None}},
            })
            continue
        if rng.random() < 0.03:
            remaining.append({"id": identity, "kind": "phase", "received_at": iso(time),
                              "payload": {"phase": "bereit"}})
            identity += 1
        absolute = None if rng.random() < 0.2 else round(rng.uniform(5, 40), 3)
        columns["role"].append(role)
        columns["id"].append(identity)
        columns["received_at"].append(iso(time, rng.choice((0, 123, 999))))
        columns["value"].append(value)
        columns["absolute_humidity"].append(absolute)
        columns["absolute_humidity_valid_until"].append(
            None if absolute is None else iso(time + 60_000)
        )
        if roles[role]["kind"] == "source_snapshot":
            columns["record_received_at"].append([identity, iso(time + 5)])
        if time > end + 200_000:
            break
    base = epoch_ms(iso(0))
    return columns, remaining, base + start, base + end


class EpochTests(unittest.TestCase):
    def test_milliseconds_like_javascript_date(self):
        # Werte aus ``new Date(text).getTime()`` (V8): Bruchteile abgeschnitten.
        for text, expected in (
            ("2026-11-04T17:11:05.534521+00:00", 1793812265534),
            ("2026-11-04T17:11:05.5349+00:00", 1793812265534),
            ("2026-11-04T17:11:05+01:00", 1793808665000),
            ("2026-11-04T17:11:05.999999+00:00", 1793812265999),
            ("1969-12-31T23:59:59.9999+00:00", -1),
        ):
            with self.subTest(text=text):
                self.assertEqual(epoch_ms(text), expected)
        for text in (None, "", "kein Datum", "2026-11-04T17:11:05", 17):
            self.assertIsNone(epoch_ms(text))

    def test_widths_are_powers_of_two_on_the_safe_side(self):
        self.assertEqual(bin_width(0, 3_600_000, 2048), 1024)
        self.assertEqual(bin_width(0, 1000, 2048), 1)
        for span in (999, 3_600_000, 22_605_000):
            for pixels in (1, 330, 1295.4, 2048):
                wanted = requested_width(0, span, ceil(pixels))
                panel = max(1, 2 ** ceil(log2(span / pixels or 1)))
                self.assertLessEqual(wanted, panel)
                self.assertEqual(wanted & (wanted - 1), 0)


class OverviewExactnessTests(unittest.TestCase):
    """Kurvenpunkte des Panels aus Übersicht und Ausschnitten gleich wie aus allen Werten."""

    def test_random_sessions_give_the_same_curves_at_every_wide_enough_width(self):
        for seed in range(40):
            columns, remaining, start, end = random_view(seed)
            records = expand(columns) + remaining
            width = bin_width(start, end, 64)
            series = OverviewSeries(columns, remaining)
            rows = series.rows(start, end, width)
            self.assertLess(len(rows), len(columns["id"]))
            overview = expand(select_rows(columns, rows)) + remaining
            coarse = series.subset(rows)
            levels = [(width, overview)] + [
                (width << step, expand(select_rows(
                    select_rows(columns, rows), coarse.rows(start, end, width << step),
                )) + remaining)
                for step in range(1, 4)
            ]
            for pixels in (8, 13, 30, 64):
                panel = max(1, 2 ** ceil(log2((end - start) / pixels)))
                expected = curves(records, start, end, pixels)
                for level, shown in levels:
                    if level > panel:
                        continue
                    with self.subTest(seed=seed, pixels=pixels, level=level):
                        self.assertEqual(curves(shown, start, end, pixels), expected)

    def test_zoom_with_a_loaded_range_and_back_to_the_whole_session(self):
        for seed in range(40, 70):
            columns, remaining, start, end = random_view(seed)
            records = expand(columns) + remaining
            series = OverviewSeries(columns, remaining)
            width = bin_width(start, end, 64)
            rows = series.rows(start, end, width)
            # Wie das Panel: Ausschnitt an der Stufe der Übersicht ausgerichtet.
            window_start = start + (end - start) // 3
            window_end = window_start + (end - start) // 5
            left = (window_start // width - 1) * width
            right = (window_end // width + 2) * width
            times = [epoch_ms(value) for value in columns["received_at"]]
            loaded = [row for row, time in enumerate(times) if left <= time < right]
            merged = {
                record["id"]: record for record in
                expand(select_rows(columns, sorted(set(rows) | set(loaded)))) + remaining
            }
            shown = list(merged.values())
            for pixels in (6, 17, 40, 400):
                with self.subTest(seed=seed, pixels=pixels):
                    self.assertEqual(curves(shown, window_start, window_end, pixels),
                                     curves(records, window_start, window_end, pixels))
            panel = max(1, 2 ** ceil(log2((end - start) / 64)))
            if panel >= width:
                self.assertEqual(curves(shown, start, end, 64), curves(records, start, end, 64))

    def test_peaks_are_kept_and_values_stay_originals(self):
        columns, remaining, start, end = random_view(7, rows=3000)
        series = OverviewSeries(columns, remaining)
        rows = series.rows(start, end, bin_width(start, end, 32))
        kept = select_rows(columns, rows)
        for quantity in ("temperature", "humidity"):
            values = [value for role, value in zip(columns["role"], columns["value"], strict=True)
                      if value is not None and columns["roles"][role]["quantity"] == quantity
                      and columns["roles"][role]["position"] == "upper"]
            shown = [value for role, value in zip(kept["role"], kept["value"], strict=True)
                     if value is not None and kept["roles"][role]["quantity"] == quantity
                     and kept["roles"][role]["position"] == "upper"]
            self.assertEqual(max(shown), max(values))
            self.assertEqual(min(shown), min(values))
        original = {identity: row for row, identity in enumerate(columns["id"])}
        for row, identity in enumerate(kept["id"]):
            source = original[identity]
            for key in ("value", "received_at", "absolute_humidity"):
                self.assertEqual(kept[key][row], columns[key][source])


class ColumnTests(unittest.TestCase):
    def test_join_merge_and_public_roles(self):
        first = {"roles": [{"kind": "measurement", "position": "upper",
                            "quantity": "temperature", "source": "sensor.a"}],
                 "role": [0], "id": [5], "received_at": [iso(5)], "value": [70.0],
                 "absolute_humidity": [None], "absolute_humidity_valid_until": [None],
                 "record_received_at": []}
        second = {"roles": [{"kind": "measurement", "position": "upper",
                             "quantity": "temperature", "source": "sensor.b"},
                            first["roles"][0]],
                  "role": [0, 1], "id": [7, 9], "received_at": [iso(7), iso(9)],
                  "value": [71.0, 72.0], "absolute_humidity": [None, 8.0],
                  "absolute_humidity_valid_until": [None, iso(60_009)],
                  "record_received_at": [[9, iso(10)]]}
        joined = join_columns([first, second])
        self.assertEqual(len(joined["roles"]), 2)
        self.assertEqual(joined["role"], [0, 1, 0])
        self.assertEqual(joined["id"], [5, 7, 9])
        merged = merge_columns(
            [select_rows(joined, [2]), select_rows(joined, [0, 1])], joined["roles"]
        )
        self.assertEqual(merged["id"], [5, 7, 9])
        self.assertEqual(merged["record_received_at"], [[9, iso(10)]])
        shown = public_columns(joined, {"bindings": {"upper_temperature": "sensor.a"}})
        self.assertEqual(shown["roles"], [
            {"kind": "measurement", "position": "upper", "quantity": "temperature",
             "source_matches_binding": True},
            {"kind": "measurement", "position": "upper", "quantity": "temperature",
             "source_matches_binding": False},
        ])
        self.assertEqual(shown["role"], [0, 1, 0])
        self.assertNotIn("sensor.a", repr(shown))
        self.assertEqual(history_view.VERSION, 1)
