import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import zipfile

from custom_components.ha_sauna.archive import Archive, encoded, plain
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.energy import advance
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from test_foundation import T0, event, parameters, bindings
from test_detector import measurement
from test_settings import async_timeout


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "ha_sauna" / "sessions.sqlite", "entry")
        await self.archive.start()
        self.c = Controller(parameters())
        self.c.begin_session("s", T0)
        self.config = {"parameters": parameters().as_dict(), "bindings": bindings().as_dict()}
        self.archive.save_session(self.c.session, T0, self.config)
        await self.archive.flush()

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    def record(self, n):
        m = measurement(Position.UPPER, Quantity.TEMPERATURE, 70 + n / 1000, n / 1000)
        self.archive.append("measurement", m.received_at, m, "s")

    async def test_energy_tail_and_metadata_cursor_preserve_original_boundaries(self):
        p = parameters()
        seconds = p.values["sensor_timeout_seconds"]
        step = timedelta(seconds=seconds)
        nominal_kw = p.values["nominal_power_kw"]
        start = T0 + timedelta(microseconds=123456)
        owner = Controller(p)
        owner.begin_session("Sitzung-ä-♨", start)
        energy = owner.session.energy
        rates = (nominal_kw / 2, nominal_kw / 2, nominal_kw / 4, None, None)
        for n, rate in enumerate(rates, 1):
            at = start + step * n
            energy = advance(
                energy, at, power_w=None if rate is None else rate * 1000,
                valid_until=at if rate is not None else None,
                heating=True if n == 4 else None, nominal_kw=nominal_kw,
            )
            self.archive.save_session(
                replace(owner.session, energy=energy), at, self.config,
            )
        await self.archive.flush()
        last = start + step * 5
        expected = [
            {"started_at": start.isoformat(), "ended_at": (start + step * 2).isoformat(),
             "source": "measured", "power_kw": nominal_kw / 2},
            {"started_at": (start + step * 2).isoformat(),
             "ended_at": (start + step * 3).isoformat(),
             "source": "measured", "power_kw": nominal_kw / 4},
            {"started_at": (start + step * 3).isoformat(),
             "ended_at": (start + step * 4).isoformat(),
             "source": "estimated", "power_kw": nominal_kw},
            {"started_at": (start + step * 4).isoformat(), "ended_at": last.isoformat(),
             "source": "unknown", "power_kw": None},
        ]
        saved = self.archive.read(owner.session.session_id)
        saved_energy = saved["session"]["energy"]
        self.assertEqual(saved_energy["interval_delta"], {
            "from": None, "to": last.isoformat(), "intervals": expected,
        })
        self.assertAlmostEqual(saved_energy["measured_kwh"], nominal_kw * seconds * 1.25 / 3600)
        self.assertAlmostEqual(saved_energy["estimated_kwh"], nominal_kw * seconds / 3600)
        self.assertEqual(
            [saved_energy[key] for key in
             ("measured_seconds", "estimated_seconds", "unknown_seconds")],
            [seconds * 3, seconds, seconds],
        )
        originals = [record["payload"]["energy"]["interval_delta"] for record in saved["records"]]
        self.assertEqual(
            [delta["from"] for delta in originals],
            [None] + [(start + step * n).isoformat() for n in range(1, 5)],
        )
        self.assertEqual([delta["intervals"][0]["started_at"] for delta in originals], [
            (start + step * n).isoformat() for n in range(5)
        ])
        self.assertEqual([delta["intervals"][0]["ended_at"] for delta in originals], [
            (start + step * n).isoformat() for n in range(1, 6)
        ])
        cursor = start + step * 1.5
        offset_cursor = cursor.astimezone(timezone(timedelta(hours=2))).isoformat()
        page = self.archive.read(owner.session.session_id, limit=1, energy_after=offset_cursor)
        self.assertEqual(page["session"]["energy"]["interval_delta"], {
            "from": cursor.isoformat(), "to": last.isoformat(),
            "intervals": [{**expected[0], "started_at": cursor.isoformat()}, *expected[1:]],
        })
        next_page = self.archive.read(
            owner.session.session_id, after=page["next_after"], limit=1,
            energy_after=last.isoformat(),
        )
        self.assertGreater(next_page["records"][0]["id"], page["records"][0]["id"])
        self.assertEqual(next_page["session"]["energy"]["interval_delta"], {
            "from": last.isoformat(), "to": last.isoformat(), "intervals": [],
        })
        for invalid in ("keine-Zeit", last.replace(tzinfo=None).isoformat(),
                        (start - step).isoformat(),
                        (last + timedelta(microseconds=1)).isoformat()):
            with self.subTest(cursor=invalid):
                result = self.archive.read(owner.session.session_id, energy_after=invalid)
                self.assertEqual(result["session"]["energy"]["interval_delta"], {
                    "from": None, "to": last.isoformat(), "intervals": expected,
                })
        self.archive.save_session(replace(owner.session, energy=energy), last, self.config)
        await self.archive.flush()
        unchanged = self.archive.read(owner.session.session_id)["records"][-1]["payload"]
        self.assertEqual(unchanged["energy"]["interval_delta"], {
            "from": last.isoformat(), "to": last.isoformat(), "intervals": [],
        })
        with closing(sqlite3.connect(self.archive.path)) as db:
            raw = db.execute(
                "SELECT payload FROM records WHERE session_id=? ORDER BY id LIMIT 1",
                (owner.session.session_id,),
            ).fetchone()[0]
        self.assertIn("Sitzung-ä-♨", raw)
        self.assertNotIn("\\u00e4", raw)
        self.assertIn(start.astimezone(UTC).isoformat(), raw)

    async def test_energy_enqueue_and_encoding_failure_keep_the_unsaved_tail(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"]
        first, last = T0 + step, T0 + step * 2
        energy = advance(
            self.c.session.energy, first, power_w=rate * 1000,
            valid_until=last, heating=True, nominal_kw=rate,
        )
        snapshot = replace(self.c.session, energy=energy)
        with self.assertRaises(ValueError):
            self.archive.save_session(snapshot, first, {"invalid": float("nan")})
        with patch.object(self.archive.queue, "put_nowait", side_effect=asyncio.QueueFull):
            with self.assertRaises(asyncio.QueueFull):
                self.archive.save_session(snapshot, first, self.config)
        energy = advance(
            energy, last, power_w=rate * 1000, valid_until=last,
            heating=True, nominal_kw=rate,
        )
        self.archive.save_session(replace(self.c.session, energy=energy), last, self.config)
        await self.archive.flush()
        with self.assertRaisesRegex(ValueError, "rückwärts"):
            self.archive.save_session(snapshot, first, self.config)
        saved = self.archive.read("s")
        originals = [record["payload"] for record in saved["records"] if record["kind"] == "session"]
        self.assertEqual(len(originals), 2)
        self.assertEqual(originals[-1]["energy"]["interval_delta"], {
            "from": T0.isoformat(), "to": last.isoformat(), "intervals": [{
                "started_at": T0.isoformat(), "ended_at": last.isoformat(),
                "source": "measured", "power_kw": rate,
            }],
        })
        self.assertAlmostEqual(
            saved["session"]["energy"]["measured_kwh"], rate * step.total_seconds() * 2 / 3600,
        )

    async def test_energy_metadata_distinguishes_legacy_zero_and_unknown(self):
        zero = self.archive.read("s")["session"]["energy"]
        self.assertEqual(zero["interval_delta"], {
            "from": None, "to": T0.isoformat(), "intervals": [],
        })
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"]
        first, last = T0 + step, T0 + step * 2
        measured = advance(
            self.c.session.energy, first, power_w=rate * 1000,
            valid_until=first, heating=True, nominal_kw=rate,
        )
        sessions = {}
        for identity in ("legacy", "unproven", "zero-then-unknown"):
            owner = Controller(p)
            sessions[identity] = owner.begin_session(identity, T0)
        legacy = plain(replace(sessions["legacy"], energy=measured))
        legacy["configuration"] = self.config
        self.archive.append("session", first, legacy, "legacy")
        self.archive.save_session(
            replace(sessions["unproven"], energy=replace(measured, intervals=())),
            first, self.config,
        )
        measured_zero = advance(
            self.c.session.energy, first, power_w=0, valid_until=first,
            heating=False, nominal_kw=rate,
        )
        unknown = advance(
            measured_zero, last, power_w=None, valid_until=None,
            heating=None, nominal_kw=rate,
        )
        self.archive.save_session(
            replace(sessions["zero-then-unknown"], energy=unknown),
            last, self.config,
        )
        await self.archive.flush()
        for identity in ("legacy", "unproven"):
            with self.subTest(session=identity):
                energy = self.archive.read(identity, energy_after=first.isoformat())["session"]["energy"]
                self.assertAlmostEqual(energy["measured_kwh"], rate * step.total_seconds() / 3600)
                self.assertNotIn("interval_delta", energy)
                self.assertNotIn("intervals_available", energy)
        energy = self.archive.read("zero-then-unknown")["session"]["energy"]
        self.assertEqual(energy["measured_kwh"], 0)
        self.assertEqual(energy["measured_seconds"], step.total_seconds())
        self.assertEqual(energy["unknown_seconds"], step.total_seconds())
        self.assertEqual(energy["interval_delta"]["intervals"], [
            {"started_at": T0.isoformat(), "ended_at": first.isoformat(),
             "source": "measured", "power_kw": 0},
            {"started_at": first.isoformat(), "ended_at": last.isoformat(),
             "source": "unknown", "power_kw": None},
        ])

    async def test_energy_follow_page_reads_and_encodes_only_the_new_suffix(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        nominal_kw = p.values["nominal_power_kw"]
        horizon = T0 + timedelta(microseconds=123456)
        last = horizon + step
        expected = {
            "from": horizon.isoformat(), "to": last.isoformat(), "intervals": [{
                "started_at": horizon.isoformat(), "ended_at": last.isoformat(),
                "source": "measured", "power_kw": nominal_kw / 2,
            }],
        }
        selected, decoded, queries = [], [], []
        connect, loads = sqlite3.connect, json.loads

        class DeltaRows:
            def __init__(self, rows):
                self.rows = rows

            def __iter__(self):
                for row in self.rows:
                    selected.append(row[0])
                    yield row

        class TracedConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                rows = super().execute(sql, *args)
                if sql.startswith("SELECT json_extract(payload,'$.energy.interval_delta')"):
                    queries.append(sql)
                    return DeltaRows(rows)
                return rows

        def decode(raw):
            value = loads(raw)
            if isinstance(value, dict) and set(value) == {"from", "to", "intervals"}:
                decoded.append(raw)
            return value

        sizes = {}
        observations = []
        for changing in (False, True):
            for count in (16, 128):
                with self.subTest(changing=changing, prefix=count):
                    owner = Controller(p)
                    owner.begin_session(f"energy-size-{changing}-{count}", horizon - step * count)
                    energy = owner.session.energy
                    for n in range(1, count + 1):
                        at = horizon - step * (count - n)
                        rate = nominal_kw / (4 if changing and n % 2 == 0 else 2)
                        energy = advance(
                            energy, at, power_w=rate * 1000, valid_until=at,
                            heating=True, nominal_kw=nominal_kw,
                        )
                        self.archive.save_session(replace(owner.session, energy=energy), at, self.config)
                    await self.archive.flush()
                    bootstrap = self.archive.read(owner.session.session_id, limit=1)
                    self.assertEqual(bootstrap["session"]["energy"]["interval_delta"]["to"], horizon.isoformat())
                    self.assertEqual(
                        len(bootstrap["session"]["energy"]["interval_delta"]["intervals"]),
                        count if changing else 1,
                    )
                    energy = advance(
                        energy, last, power_w=(nominal_kw / 2) * 1000, valid_until=last,
                        heating=True, nominal_kw=nominal_kw,
                    )
                    self.archive.save_session(replace(owner.session, energy=energy), last, self.config)
                    await self.archive.flush()
                    with closing(connect(self.archive.path)) as db:
                        originals = [loads(row[0])["energy"]["interval_delta"] for row in db.execute(
                            "SELECT payload FROM records WHERE session_id=? AND kind='session' ORDER BY id",
                            (owner.session.session_id,),
                        )]
                        scalar = loads(db.execute(
                            "SELECT payload FROM sessions WHERE session_id=?",
                            (owner.session.session_id,),
                        ).fetchone()[0])["energy"]
                    self.assertNotIn("intervals", scalar)
                    self.assertNotIn("interval_delta", scalar)
                    self.assertTrue(scalar["intervals_available"])
                    original_pieces = sum(len(delta["intervals"]) for delta in originals)
                    self.assertEqual(original_pieces, count + 1)
                    self.assertEqual(originals[-1], expected)
                    byte_sizes = [len(json.dumps(
                        delta, ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")) for delta in originals]
                    # Every saved contribution remains one small envelope,
                    # including a coalesced owner tail and many true switches.
                    self.assertLessEqual(max(byte_sizes), byte_sizes[-1] + 4)
                    sizes[changing, count] = sum(byte_sizes), byte_sizes[-1]
                    selected.clear()
                    decoded.clear()
                    queries.clear()
                    with (
                        patch("custom_components.ha_sauna.archive.sqlite3.connect",
                              side_effect=lambda *args, **kwargs: connect(
                                  *args, **kwargs, factory=TracedConnection,
                              )),
                        patch("custom_components.ha_sauna.archive.json.loads", side_effect=decode),
                    ):
                        page = self.archive.read(
                            owner.session.session_id, after=bootstrap["next_after"],
                            limit=1, energy_after=horizon.isoformat(),
                        )
                        self.assertEqual(page["session"]["energy"]["interval_delta"], expected)
                        suffix_bytes = len(json.dumps(
                            page["session"]["energy"]["interval_delta"],
                            ensure_ascii=False, separators=(",", ":"),
                        ).encode("utf-8"))
                        self.assertEqual(suffix_bytes, byte_sizes[-1])
                        self.assertEqual(len(selected), 1)
                        self.assertEqual(decoded, selected)
                        self.assertEqual(len(queries), 1)
                        self.assertIn("json_extract(payload,'$.energy.interval_delta.to')>?", queries[0])
                        suffix_counts = {
                            "sql_queries": len(queries), "sql_rows": len(selected),
                            "delta_decodes": len(decoded),
                        }
                        selected.clear()
                        decoded.clear()
                        queries.clear()
                        unchanged = self.archive.read(
                            owner.session.session_id, after=page["next_after"],
                            limit=1, energy_after=last.isoformat(),
                        )
                        self.assertEqual(unchanged["session"]["energy"]["interval_delta"], {
                            "from": last.isoformat(), "to": last.isoformat(), "intervals": [],
                        })
                        self.assertEqual((selected, decoded, queries), ([], [], []))
                        observations.append({
                            "changing": changing, "prefix": count,
                            "original_records": len(originals),
                            "original_pieces": original_pieces,
                            "stored_delta_bytes": sum(byte_sizes),
                            "max_stored_delta_bytes": max(byte_sizes),
                            "suffix_bytes": suffix_bytes,
                            "suffix": suffix_counts,
                            "unchanged": {
                                "sql_queries": len(queries), "sql_rows": len(selected),
                                "delta_decodes": len(decoded),
                            },
                        })
        for changing in (False, True):
            self.assertEqual(sizes[changing, 16][1], sizes[changing, 128][1])
            self.assertLess(sizes[changing, 128][0], sizes[changing, 16][0] * 8)
        print(json.dumps(
            {"archive_energy_delta_metrics": observations},
            ensure_ascii=False, separators=(",", ":"),
        ))

    async def test_energy_restart_cleanup_and_erase_preserve_only_saved_coverage(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"]
        self.c.set_temperature(p.values["target_temperature_c"], T0)
        for kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION):
            self.c.process(event(kind.value, kind, 0))
        saved_at = T0 + step
        energy = advance(
            self.c.session.energy, saved_at, power_w=rate * 1000,
            valid_until=saved_at, heating=True, nominal_kw=rate,
        )
        self.archive.save_session(replace(self.c.session, energy=energy), saved_at, self.config)
        empty = Controller(p)
        empty.begin_session("empty-energy", T0)
        empty_energy = advance(
            empty.session.energy, saved_at, power_w=None, valid_until=None,
            heating=None, nominal_kw=rate,
        )
        self.archive.save_session(replace(empty.session, energy=empty_energy), saved_at, self.config)
        await self.archive.flush()
        original = self.archive.read("s")
        late_payload = original["records"][-1]["payload"]
        # A hard stop retains the existing last-save horizon; an owner advance
        # that never reached save_session is deliberately not durable evidence.
        unsaved = advance(
            energy, saved_at + step, power_w=rate * 1000,
            valid_until=saved_at + step, heating=True, nominal_kw=rate,
        )
        await self.archive.close()
        self.archive = Archive(self.archive.path, "entry", sessions_only=True)
        await self.archive.start()
        restored = self.archive.read("s", now=saved_at + step * 10)
        self.assertEqual(restored["session"]["energy"], original["session"]["energy"])
        self.assertEqual(restored["session"]["energy"]["accounted_at"], saved_at.isoformat())
        self.assertNotEqual(restored["session"]["energy"]["accounted_at"], unsaved.accounted_at.isoformat())
        self.assertIsNone(restored["session"]["ended_at"])
        self.assertIsNone(self.archive.read("empty-energy"))
        with closing(sqlite3.connect(self.archive.path)) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM records WHERE session_id='empty-energy'",
            ).fetchone()[0], 0)
        self.assertEqual(await self.archive.erase("s"), 1)
        self.archive.append("session", saved_at + step, late_payload, "s")
        self.archive.save_session(replace(self.c.session, energy=unsaved), saved_at + step, self.config)
        await self.archive.flush()
        self.assertIsNone(self.archive.read("s"))
        fresh_owner = Controller(p)
        fresh = replace(fresh_owner.begin_session("fresh-energy", T0), energy=energy)
        self.archive.save_session(fresh, saved_at, self.config)
        await self.archive.flush()
        fresh_payload = self.archive.read("fresh-energy")["records"][-1]["payload"]
        self.assertEqual(await self.archive.erase(reset=True), 1)
        self.archive.append("session", saved_at + step, fresh_payload, "fresh-energy")
        await self.archive.flush()
        self.assertEqual(self.archive.read(), [])
        with closing(sqlite3.connect(self.archive.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM records WHERE kind='session'").fetchone()[0], 0)

    async def test_runtime_close_persists_the_final_off_decision_once(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        nominal_kw = p.values["nominal_power_kw"]
        now = T0
        runtime = SaunaRuntime(Configuration(bindings(), p), lambda: now)
        runtime.archive = self.archive
        runtime.controller.set_temperature(
            p.values["target_temperature_c"] - p.values["readiness_hysteresis_c"], T0,
        )
        runtime.controller.begin_session("closing", T0)
        runtime.controller.report_power(nominal_kw * 1000, T0 + step * 2, T0)
        runtime.persist()
        self.assertTrue(runtime.controller.last_decision.heat)
        now = T0 + step / 2
        runtime.controller.advance(now)
        runtime.persist()
        now = T0 + step

        async def close_device():
            return None

        runtime.device = SimpleNamespace(
            close=close_device, prepare_light_handoff=AsyncMock(return_value=True),
        )
        await runtime.close()
        await runtime.close()
        stored = await asyncio.to_thread(self.archive.read, "closing")
        off = [record for record in stored["records"]
               if record["kind"] == "decision" and not record["payload"]["heat"]]
        self.assertEqual(len(off), 1)
        self.assertEqual(off[0]["payload"]["reason"], "operation_off")
        self.assertEqual(off[0]["session_id"], "closing")
        self.assertFalse(stored["session"]["operation_enabled"])
        energy = stored["session"]["energy"]
        self.assertEqual(stored["session"]["ended_at"], now.isoformat())
        self.assertEqual(energy["accounted_at"], now.isoformat())
        self.assertAlmostEqual(
            energy["measured_kwh"], nominal_kw * step.total_seconds() / 3600,
        )
        self.assertEqual(energy["interval_delta"], {
            "from": None, "to": now.isoformat(), "intervals": [{
                "started_at": T0.isoformat(), "ended_at": now.isoformat(),
                "source": "measured", "power_kw": nominal_kw,
            }],
        })

    async def test_decision_append_failure_during_close_still_attempts_device_off(self):
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
        runtime.archive = self.archive
        runtime.controller.set_temperature(60, T0)
        runtime.controller.begin_session("closing-error", T0)
        runtime.persist()
        calls = []

        async def close_device():
            calls.append("off")

        runtime.device = SimpleNamespace(
            close=close_device, prepare_light_handoff=AsyncMock(return_value=True),
        )
        original_append = self.archive.append

        def append(kind, *args, **kwargs):
            if kind == "decision":
                raise OSError("decision append")
            return original_append(kind, *args, **kwargs)

        self.archive.append = append
        with self.assertRaises(ExceptionGroup) as caught:
            await runtime.close()
        self.assertEqual(calls, ["off"])
        self.assertTrue(any(str(error) == "decision append" for error in caught.exception.exceptions))
        self.assertFalse(runtime.controller.last_decision.heat)
        self.assertLess(runtime._saved_decisions, len(runtime.controller.decisions))

    async def test_decision_receipt_uses_creation_clock_before_a_later_persist(self):
        runtime = SaunaRuntime(Configuration(bindings(), parameters()),
                               lambda: T0 + timedelta(seconds=30))
        runtime.archive = self.archive
        runtime.controller.set_temperature(60, T0)
        runtime.controller.begin_session("decision-clock", T0)
        runtime._clock = lambda: T0 + timedelta(seconds=31)
        runtime.controller.set_operation(False, T0 + timedelta(seconds=28))
        runtime._clock = lambda: T0 + timedelta(seconds=50)
        runtime.persist()
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, "decision-clock")
        decisions = [r for r in stored["records"] if r["kind"] == "decision"]
        self.assertEqual([(r["payload"]["at"], r["payload"]["created_at"], r["received_at"])
                          for r in decisions], [
            (T0.isoformat(), (T0 + timedelta(seconds=30)).isoformat(),
             (T0 + timedelta(seconds=30)).isoformat()),
            ((T0 + timedelta(seconds=28)).isoformat(),
             (T0 + timedelta(seconds=31)).isoformat(),
             (T0 + timedelta(seconds=31)).isoformat()),
        ])

    async def test_full_resolution_references_and_earlier_assignments_survive(self):
        self.c.set_temperature(80, T0)
        for n in range(30):
            self.record(n)
        self.c.process(event("open", Kind.DOOR_OPEN, 0))
        self.c.process(event("close", Kind.DOOR_CLOSE, 1))
        self.c.process(event("person", Kind.PERSON_STRONG, 2))
        provisional = self.c.session.timeline.active
        self.archive.save_session(self.c.session, T0 + timedelta(seconds=2), self.config)
        self.c.process(event("infusion", Kind.INFUSION, 3))
        self.archive.save_session(self.c.session, T0 + timedelta(seconds=3), self.config)
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, "s")
        values = [r for r in stored["records"] if r["kind"] == "measurement"]
        self.assertEqual(len(values), 30)
        self.assertEqual(len({v["received_at"] for v in values}), 30)
        gang = stored["session"]["timeline"]["active"]
        self.assertEqual(gang["gang_id"], provisional.gang_id)
        self.assertEqual(gang["start_source_event_id"], "close")
        self.assertEqual(gang["infusion_events"][0]["event_id"], "infusion")
        earlier = [r["payload"] for r in stored["records"] if r["kind"] == "session"]
        self.assertEqual(earlier[-2]["timeline"]["active"]["infusion_events"], [])
        self.assertEqual(stored["session"]["configuration"], self.config)
        self.assertFalse(str(self.archive.path).endswith("www"))

    async def test_historical_cooling_cycles_remain_readable(self):
        from custom_components.ha_sauna.core.models import CoolingCycle
        cycle = CoolingCycle("legacy", T0, 900, credited_seconds=480,
                             started_at=T0, ends_at=T0 + timedelta(seconds=420))
        historical = replace(self.c.session, cooling_history=(cycle,))
        self.archive.save_session(historical, T0, self.config)
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, "s")
        saved = stored["session"]["cooling_history"][0]
        self.assertEqual(saved["cycle_id"], "legacy")
        self.assertEqual(saved["duration_seconds"], 900)
        self.assertEqual(saved["credited_seconds"], 480)

    async def test_backup_pause_buffers_without_blocking_received_measurements(self):
        self.record(1)
        await self.archive.pre_backup()
        for n in range(2, 12):
            self.record(n)
        frozen = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in frozen["records"]), 1)
        self.archive.release_backup()
        await self.archive.flush()
        final = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in final["records"]), 11)

    async def test_export_during_capture_is_consistent_and_contains_originals(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"] / 2
        energy = advance(
            self.c.session.energy, T0 + step, power_w=rate * 1000,
            valid_until=T0 + step * 2, heating=False,
            nominal_kw=p.values["nominal_power_kw"],
        )
        self.archive.save_session(
            replace(self.c.session, energy=energy), T0 + step, self.config,
        )
        for n in range(30):
            self.record(n)
        task = asyncio.create_task(self.archive.export())
        energy = advance(
            energy, T0 + step * 2, power_w=rate * 1000,
            valid_until=T0 + step * 2, heating=False,
            nominal_kw=p.values["nominal_power_kw"],
        )
        self.archive.save_session(
            replace(self.c.session, energy=energy), T0 + step * 2, self.config,
        )
        for n in range(30, 60):
            self.record(n)
        path = await task
        try:
            with zipfile.ZipFile(path) as export:
                self.assertEqual(set(export.namelist()), {"manifest.json", "records.jsonl", "sessions.jsonl", "measurements.csv"})
                rows = [json.loads(line) for line in export.read("records.jsonl").decode().splitlines()]
                count = sum(r["kind"] == "measurement" for r in rows)
                self.assertGreaterEqual(count, 30)
                self.assertLessEqual(count, 60)
                csv_rows = export.read("measurements.csv").decode().splitlines()
                self.assertEqual(len(csv_rows), count + 1)
                self.assertEqual(len({r["id"] for r in rows}), len(rows))
                sessions = [
                    json.loads(line)
                    for line in export.read("sessions.jsonl").decode().splitlines()
                ]
                saved_energy = sessions[0]["energy"]
                self.assertEqual(
                    saved_energy["accounted_at"], (T0 + step * 2).isoformat(),
                )
                self.assertAlmostEqual(
                    saved_energy["measured_kwh"],
                    rate * step.total_seconds() * 2 / 3600,
                )
                self.assertEqual(saved_energy["intervals"], [{
                    "started_at": T0.isoformat(),
                    "ended_at": (T0 + step * 2).isoformat(),
                    "source": "measured", "power_kw": rate,
                }])
                originals = [
                    row["payload"]["energy"] for row in rows
                    if row["kind"] == "session"
                ]
                self.assertTrue(all("intervals" not in item for item in originals))
                self.assertEqual(
                    [item["interval_delta"]["from"] for item in originals],
                    [None, T0.isoformat(), (T0 + step).isoformat()],
                )
                self.assertEqual(
                    [len(item["interval_delta"]["intervals"]) for item in originals],
                    [0, 1, 1],
                )
        finally:
            path.unlink()

    async def test_session_and_records_share_one_read_snapshot(self):
        # The production journal permits a second connection to commit while
        # the reader retains a consistent session/record snapshot.
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"]
        first = T0 + step
        at = first + step
        energy = advance(
            self.c.session.energy, first, power_w=rate * 1000,
            valid_until=at, heating=True, nominal_kw=rate,
        )
        self.archive.save_session(
            replace(self.c.session, energy=energy), first, self.config,
        )
        await self.archive.flush()
        energy = advance(
            energy, at, power_w=rate * 1000, valid_until=at,
            heating=True, nominal_kw=rate,
        )
        finished = plain(replace(self.c.session, ended_at=at, energy=energy))
        finished["configuration"] = self.config
        finished["energy"]["interval_delta"] = {
            "from": first.isoformat(), "to": at.isoformat(), "intervals": [{
                "started_at": first.isoformat(), "ended_at": at.isoformat(),
                "source": "measured", "power_kw": rate,
            }],
        }
        archive = self.archive
        connect = sqlite3.connect
        committed = False

        class InterleavedConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                nonlocal committed
                # The scalar row was fetched before this first energy lookup;
                # hydration and history must retain its established snapshot.
                if not committed and sql.startswith(
                    "SELECT MAX(id) FROM records INDEXED BY records_energy_delta"
                ):
                    committed = True
                    archive._write(("session", at.isoformat(), encoded(finished), "s"))
                return super().execute(sql, *args)

        with patch(
            "custom_components.ha_sauna.archive.sqlite3.connect",
            side_effect=lambda *args, **kwargs: connect(
                *args, **kwargs, factory=InterleavedConnection
            ),
        ):
            result = archive.read("s")
        latest_record = next(
            record["payload"] for record in reversed(result["records"])
            if record["kind"] == "session"
        )
        self.assertEqual(result["session"]["ended_at"], latest_record["ended_at"])
        self.assertIsNone(result["session"]["ended_at"])
        saved_energy = result["session"]["energy"]
        self.assertEqual(saved_energy["accounted_at"], first.isoformat())
        self.assertEqual(latest_record["energy"]["accounted_at"], first.isoformat())
        self.assertEqual(saved_energy["interval_delta"], {
            "from": None, "to": first.isoformat(), "intervals": [{
                "started_at": T0.isoformat(), "ended_at": first.isoformat(),
                "source": "measured", "power_kw": rate,
            }],
        })
        self.assertAlmostEqual(
            saved_energy["measured_kwh"], rate * step.total_seconds() / 3600,
        )
        latest = archive.read("s")["session"]
        self.assertEqual(latest["ended_at"], at.isoformat())
        self.assertEqual(latest["energy"]["accounted_at"], at.isoformat())
        self.assertEqual(latest["energy"]["interval_delta"]["intervals"], [{
            "started_at": T0.isoformat(), "ended_at": at.isoformat(),
            "source": "measured", "power_kw": rate,
        }])

    async def test_long_history_snapshot_does_not_block_queued_writer(self):
        with closing(sqlite3.connect(self.archive.path)) as reader:
            reader.execute("BEGIN")
            before = reader.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            self.record(1)
            await asyncio.wait_for(self.archive.flush(), async_timeout("writer"))
            self.assertIsNone(self.archive.failure)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], before)
        with closing(sqlite3.connect(self.archive.path)) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], before + 1)

    async def test_backup_pause_checkpoints_committed_wal_into_standalone_file(self):
        # Keep another connection open so SQLite cannot auto-checkpoint on the
        # writer connection's close, as it can when there are no history readers.
        with closing(sqlite3.connect(self.archive.path)) as reader:
            reader.execute("SELECT COUNT(*) FROM records").fetchone()
            self.record(1)
            await self.archive.flush()
            wal = Path(str(self.archive.path) + "-wal")
            self.assertGreater(wal.stat().st_size, 0)
            await self.archive.pre_backup()
            try:
                snapshot = Path(self.temp.name) / "standalone.sqlite"
                shutil.copyfile(self.archive.path, snapshot)
                with closing(sqlite3.connect(snapshot)) as restored:
                    count = restored.execute("SELECT COUNT(*) FROM records WHERE kind='measurement'").fetchone()[0]
                self.assertEqual(count, 1)
                self.assertEqual(wal.stat().st_size, 0)
            finally:
                self.archive.release_backup()

    async def test_writer_logs_original_exception_once_until_recovery(self):
        original = self.archive._write
        with patch.object(self.archive, "_write", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertLogs("custom_components.ha_sauna.archive", level="ERROR") as captured:
                self.record(1)
                await self.archive.queue.join()
                with self.assertRaises(sqlite3.OperationalError):
                    await self.archive.flush()
            self.assertEqual(len(captured.records), 1)
            self.assertIn("database is locked", captured.output[0])
            self.assertIsNotNone(captured.records[0].exc_info)
        self.archive._write = original
        await self.archive.flush()

    async def test_backup_rejects_busy_checkpoint_and_releases_writer(self):
        connect = sqlite3.connect
        with closing(connect(self.archive.path)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM records").fetchone()
            self.record(1)
            await self.archive.flush()
            with patch(
                "custom_components.ha_sauna.archive.sqlite3.connect",
                side_effect=lambda *args, **kwargs: connect(*args, **kwargs, timeout=0.01),
            ):
                with self.assertRaisesRegex(sqlite3.OperationalError, "aktiven Leser"):
                    await self.archive.pre_backup()
            self.assertTrue(self.archive.resume.is_set())
        self.record(2)
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_cancelled_reader_does_not_poison_archive_writer(self):
        await self.archive.pre_backup()
        waiter = asyncio.create_task(self.archive.flush())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.record(2)
        self.archive.release_backup()
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)
        saved = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in saved["records"]), 1)

    async def test_transient_sqlite_lock_retries_oldest_record_before_newer_records(self):
        original_write = self.archive._write
        attempts = []

        def write(record):
            attempts.append(record)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            original_write(record)

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        self.assertIsInstance(self.archive.failure, sqlite3.OperationalError)
        self.record(2)
        await self.archive.flush()

        saved = await asyncio.to_thread(self.archive.read, "s")
        measurements = [
            r["payload"]["received_at"]
            for r in saved["records"]
            if r["kind"] == "measurement"
        ]
        self.assertEqual(
            measurements,
            [
                (T0 + timedelta(milliseconds=1)).isoformat(),
                (T0 + timedelta(milliseconds=2)).isoformat(),
            ],
        )
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], attempts[1])
        self.assertIsNone(self.archive.failure)
        self.assertFalse(self.archive.failed_records)

    async def test_permanent_writer_failure_remains_reported_until_recovered(self):
        original_write = self.archive._write

        def write(record):
            raise sqlite3.DatabaseError("disk failure")

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "disk failure"):
            await self.archive.flush()
        self.assertIsInstance(self.archive.failure, sqlite3.DatabaseError)
        self.assertEqual(len(self.archive.failed_records), 1)

        self.archive._write = original_write
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_recovery_does_not_clear_fault_before_current_record_is_saved(self):
        original_write = self.archive._write
        failure_visible_during_new_write = []
        calls = 0

        def write(record):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database is locked")
            if calls == 3:
                failure_visible_during_new_write.append(self.archive.failure is not None)
                raise sqlite3.OperationalError("database is locked again")
            original_write(record)

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        self.record(2)
        await self.archive.queue.join()

        self.assertEqual(failure_visible_during_new_write, [True])
        self.assertIsNotNone(self.archive.failure)
        self.archive._write = original_write
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_recovered_session_snapshot_does_not_overwrite_newer_snapshot(self):
        original_write = self.archive._write
        connect = sqlite3.connect
        attempts = []

        class FailedScalarConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("INSERT INTO sessions"):
                    raise sqlite3.OperationalError("database is busy")
                return super().execute(sql, *args)

        def write(record):
            attempts.append(record)
            if len(attempts) == 1:
                # Fail after the original INSERT, before its scalar UPSERT.
                with patch(
                    "custom_components.ha_sauna.archive.sqlite3.connect",
                    side_effect=lambda *args, **kwargs: connect(
                        *args, **kwargs, factory=FailedScalarConnection,
                    ),
                ):
                    original_write(record)
            else:
                original_write(record)

        self.archive._write = write
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"]
        first = T0 + step
        last = first + step
        energy = advance(
            self.c.session.energy, first, power_w=rate * 1000,
            valid_until=last, heating=True, nominal_kw=rate,
        )
        self.archive.save_session(
            replace(self.c.session, energy=energy), first, self.config,
        )
        await self.archive.queue.join()
        before_retry = self.archive.read("s")
        self.assertEqual(before_retry["session"]["energy"]["accounted_at"], T0.isoformat())
        self.assertEqual(len(before_retry["records"]), 1)
        self.assertIsInstance(self.archive.failure, sqlite3.OperationalError)
        energy = advance(
            energy, last, power_w=None, valid_until=None,
            heating=None, nominal_kw=rate,
        )
        self.archive.save_session(
            replace(self.c.session, energy=energy), last, self.config,
        )
        await self.archive.flush()

        saved = await asyncio.to_thread(self.archive.read, "s")
        saved_energy = saved["session"]["energy"]
        self.assertEqual(saved_energy["accounted_at"], last.isoformat())
        self.assertAlmostEqual(
            saved_energy["measured_kwh"], rate * step.total_seconds() / 3600,
        )
        self.assertEqual(saved_energy["unknown_seconds"], step.total_seconds())
        self.assertEqual(saved_energy["interval_delta"]["intervals"], [
            {
                "started_at": T0.isoformat(), "ended_at": first.isoformat(),
                "source": "measured", "power_kw": rate,
            },
            {
                "started_at": first.isoformat(), "ended_at": last.isoformat(),
                "source": "unknown", "power_kw": None,
            },
        ])
        self.assertEqual(
            [record["payload"]["energy"]["interval_delta"]["to"]
             for record in saved["records"] if record["kind"] == "session"],
            [T0.isoformat(), first.isoformat(), last.isoformat()],
        )
        self.assertEqual(
            [record["payload"]["energy"]["interval_delta"]["from"]
             for record in saved["records"] if record["kind"] == "session"],
            [None, T0.isoformat(), first.isoformat()],
        )
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], attempts[1])
        self.assertIsNone(self.archive.failure)
        self.assertFalse(self.archive.failed_records)

    async def test_failed_record_burst_retries_once_while_catching_up(self):
        original_write = self.archive._write
        attempts = []

        def write(record):
            attempts.append(record)
            raise sqlite3.OperationalError("database is locked")

        self.archive._write = write
        for number in range(100):
            self.record(number)
        await self.archive.queue.join()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.archive.failed_records), 100)
        self.assertIsInstance(self.archive.failure, sqlite3.OperationalError)
        self.archive._write = original_write

    async def test_cancelled_pause_request_does_not_pause_the_writer(self):
        future = asyncio.get_running_loop().create_future()
        self.archive.queue.put_nowait(("pause", future))
        future.cancel()
        await self.archive.flush()
        self.assertTrue(self.archive.resume.is_set())
        self.record(1)
        await self.archive.flush()
        saved = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in saved["records"]), 1)

    async def test_latest_completed_warmup_reads_only_initial_heating_measurements(self):
        for second, phase in ((0, "aufheizen"), (181, "bereit"), (220, "kuehlung")):
            self.archive.append("phase", T0 + timedelta(seconds=second), {"phase": phase}, "s")
        for second, value in ((0, 20), (60, 26), (120, 32), (180, 38), (240, 25)):
            m = measurement(Position.UPPER, Quantity.TEMPERATURE, value, second)
            self.archive.append("measurement", m.received_at, m, "s")
        self.archive.save_session(
            replace(self.c.session, ended_at=T0 + timedelta(seconds=300)),
            T0 + timedelta(seconds=300),
            self.config,
        )
        await self.archive.flush()

        history = await asyncio.to_thread(
            self.archive.latest_completed_warmup, "sensor.upper_temperature", 60
        )

        self.assertEqual(history["session_id"], "s")
        self.assertEqual(
            history["measurements"],
            tuple((T0 + timedelta(seconds=second), value) for second, value in ((0, 20), (60, 26), (120, 32), (180, 38))),
        )

    async def response_history_session(
        self, identity, marks, samples, *, legacy=False, doors=(),
        source="sensor.upper_temperature", heater="switch.heater", position="upper",
        completed=True, sample_source=None, feedback_source=None,
    ):
        snapshot = plain(self.c.session)
        snapshot["timeline"]["session_id"] = identity
        snapshot["ended_at"] = (T0 + timedelta(seconds=300)).isoformat() if completed else None
        snapshot["configuration"] = {
            "bindings": {f"{position}_temperature": source, "heater": heater},
        }
        if legacy:
            snapshot.pop("contactor_history", None)
            for second, state in marks:
                self.archive.append("source_state", T0 + timedelta(seconds=second), {
                    "role": "heater", "source": feedback_source or heater,
                    "state": state if isinstance(state, str) else (
                        "on" if state is True else "off" if state is False else "unavailable"
                    ),
                }, identity)
        else:
            snapshot["contactor_history"] = [
                {"at": (T0 + timedelta(seconds=second)).isoformat(), "state": state}
                for second, state in marks
            ]
        self.archive.append("session", T0 + timedelta(seconds=300), snapshot, identity)
        for second, value in samples:
            m = replace(
                measurement(Position(position), Quantity.TEMPERATURE, value, second),
                source=sample_source or source,
            )
            self.archive.append("measurement", m.received_at, m, identity)
        for second, kind in doors:
            self.archive.append("detection", T0 + timedelta(seconds=second + 1), {
                "event": {
                    "kind": kind, "effective_at": (T0 + timedelta(seconds=second)).isoformat(),
                },
            }, identity)
        await self.archive.flush()

    async def test_heating_response_history_preserves_real_edges_and_original_samples(self):
        samples = ((5, 20), (20, 20), (40, 21), (60, 23))
        for legacy in (False, True):
            await self.response_history_session(
                f"response-{legacy}", ((0, False), (10, True), (70, False)), samples,
                legacy=legacy,
            )
        history = await asyncio.to_thread(
            self.archive.heating_response_history,
            "sensor.upper_temperature", "upper", "switch.heater", 30,
        )
        self.assertEqual(len(history), 2)
        for episode in history:
            self.assertEqual(episode["started_at"], T0 + timedelta(seconds=10))
            self.assertEqual(episode["ended_at"], T0 + timedelta(seconds=70))
            self.assertEqual(episode["measurements"], tuple(
                (T0 + timedelta(seconds=second), value) for second, value in samples
            ))

    async def test_heating_response_history_rejects_unproven_and_interrupted_episodes(self):
        marks = ((0, False), (10, True), (70, False))
        samples = ((10, 20), (30, 21), (50, 22), (70, 23))
        cases = (
            ("initial-on", ((0, True), (70, False)), samples, {}),
            ("unknown-on", ((0, None), (10, True), (70, False)), samples, {}),
            ("unknown-end", ((0, False), (10, True), (70, None)), samples, {}),
            ("no-off", ((0, False), (10, True)), samples, {}),
            ("invalid-value", marks, ((10, 20), (30, None), (50, 22), (70, 23)), {}),
            ("sample-gap", marks, ((10, 20), (70, 23)), {}),
            ("late-first", marks, ((50, 22), (70, 23)), {}),
            ("early-last", marks, ((10, 20), (30, 21)), {}),
            ("door-during", marks, samples, {"doors": ((30, "door_open"),)}),
            ("door-before", marks, samples, {"doors": ((5, "door_open"),)}),
            ("other-source", marks, samples, {"source": "sensor.changed"}),
            ("other-height", marks, samples, {"position": "lower"}),
            ("other-heater", marks, samples, {"heater": "switch.changed"}),
            ("ongoing", marks, samples, {"completed": False}),
        )
        for identity, contact_marks, values, options in cases:
            await self.response_history_session(identity, contact_marks, values, **options)
        history = await asyncio.to_thread(
            self.archive.heating_response_history,
            "sensor.upper_temperature", "upper", "switch.heater", 30,
        )
        self.assertEqual(history, ())

    async def test_heating_response_history_keeps_later_complete_episode_after_unknown(self):
        await self.response_history_session(
            "later", ((0, True), (20, None), (30, False), (40, True), (100, False)),
            ((40, 20), (60, 21), (80, 22), (100, 23)),
            doors=((5, "door_open"), (25, "door_close")),
        )
        history = await asyncio.to_thread(
            self.archive.heating_response_history,
            "sensor.upper_temperature", "upper", "switch.heater", 30,
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["started_at"], T0 + timedelta(seconds=40))

    async def test_heating_response_history_uses_runtime_received_door_events(self):
        now = T0
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: now)
        runtime.archive = self.archive
        runtime.controller.report_contactor(False, now)
        await runtime.begin_session("received-door")
        now = T0 + timedelta(seconds=10)
        runtime.controller.report_contactor(True, now)
        source = runtime.configuration.bindings.values["upper_temperature"]
        heater = runtime.configuration.bindings.values["heater"]
        for second, value in ((10, 20), (30, 21), (50, 22), (70, 23)):
            m = measurement(Position.UPPER, Quantity.TEMPERATURE, value, second, source=source)
            self.archive.append("measurement", m.received_at, m, "received-door")
        now = T0 + timedelta(seconds=30)
        await runtime.receive(event("door", Kind.DOOR_OPEN, 30, "received-door"))
        now = T0 + timedelta(seconds=70)
        runtime.controller.report_contactor(False, now)
        now = T0 + timedelta(seconds=100)
        runtime.controller.finish_session(now)
        runtime.persist()
        await self.archive.flush()

        stored = await asyncio.to_thread(self.archive.read, "received-door")
        self.assertTrue(any(
            item["kind"] == "door_open"
            for item in stored["session"]["timeline"]["processed"]
        ))
        self.assertFalse(any(record["kind"] == "detection" for record in stored["records"]))
        history = await asyncio.to_thread(
            self.archive.heating_response_history, source, "upper", heater, 30,
        )
        self.assertEqual(history, ())

    async def test_heating_response_history_rejects_invalid_legacy_evidence_and_foreign_reports(self):
        samples = ((10, 20), (30, 21), (50, 22), (70, 23))
        marks = ((0, False), (10, True), (70, False))
        cases = (
            ("legacy-initial-on", ((0, True), (70, False)), {}),
            ("legacy-unknown-start", ((0, None), (10, True), (70, False)), {}),
            ("legacy-unknown-end", ((0, False), (10, True), (70, None)), {}),
            ("legacy-invalid-start", ((0, "invalid"), (10, True), (70, False)), {}),
            ("legacy-invalid-during", ((0, False), (10, True), (40, "invalid"), (70, False)), {}),
            ("legacy-foreign-feedback", marks, {"feedback_source": "switch.foreign"}),
            ("legacy-foreign-measurement", marks, {"sample_source": "sensor.foreign"}),
        )
        for identity, contact_marks, options in cases:
            await self.response_history_session(
                identity, contact_marks, samples, legacy=True, **options,
            )
        await self.response_history_session(
            "native-foreign-measurement", marks, samples, sample_source="sensor.foreign",
        )
        history = await asyncio.to_thread(
            self.archive.heating_response_history,
            "sensor.upper_temperature", "upper", "switch.heater", 30,
        )
        self.assertEqual(history, ())

    async def test_temperature_change_at_session_expiry_keeps_original_archive_configuration(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
        from custom_components.ha_sauna.core.parameters import Parameters
        from custom_components.ha_sauna.settings import apply_temperature_parameters
        now = T0
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: now)
        runtime.archive = self.archive
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        old_target = runtime.configuration.parameters.values["target_temperature_c"]
        await runtime.set_operation(False)
        now += timedelta(seconds=runtime.configuration.parameters.seconds("session_gap_minutes"))
        updated = Parameters({**runtime.configuration.parameters.as_dict(), "target_temperature_c": 91})
        async with runtime._lock:
            await apply_temperature_parameters(runtime, updated, explicit_target=True)
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, session_id)
        self.assertEqual(stored["session"]["configuration"]["parameters"]["target_temperature_c"], old_target)
        self.assertEqual(runtime.controller.target_temperature, 91)
        self.assertIsNone(runtime.session)

    async def test_projection_uses_full_evidence_even_on_paginated_read(self):
        self.archive.append("phase", T0, {"phase": "aufheizen"}, "s")
        self.archive.append("phase", T0 + timedelta(seconds=10), {"phase": "bereit"}, "s")
        self.archive.append("source_state", T0, {"role": "heater", "state": "off"}, "s")
        legacy = plain(self.c.session)
        legacy.pop("base_phases", None)
        legacy.pop("contactor_history", None)
        self.archive.append("session", T0 + timedelta(seconds=20), legacy, "s")
        await self.archive.flush()
        full = self.archive.read("s")
        page = self.archive.read("s", after=2, limit=1)
        self.assertEqual(full["phase_projection"], page["phase_projection"])
        self.assertEqual(full["phase_projection"]["intervals"][-1]["phase"], "bereit")
        self.assertEqual(full["phase_projection"]["readiness_pauses"][0]["started_at"], (T0 + timedelta(seconds=10)).isoformat())
        self.assertFalse(full["phase_projection"]["complete"])

    async def test_consumer_identities_survive_reload_and_are_scoped_to_entry(self):
        self.archive.append("consumer_event", T0, {"event_id": "present-1"}, "s")
        self.archive.append("consumer_event", T0, {"event_id": "present-1"}, "s")
        self.archive.append("event", T0, {"event_id": "other"}, "s")
        await self.archive.flush()
        self.assertEqual(self.archive.consumer_event_ids(), {"present-1"})
        other = Archive(self.archive.path, "different-entry")
        self.assertEqual(other.consumer_event_ids(), set())


class ExportShutdownTests(unittest.TestCase):
    def test_cancelled_export_is_removed_even_after_all_loop_tasks_are_cancelled(self):
        for finished in (False, True):
            with self.subTest(writer_already_finished=finished):
                self.cancel_export(finished)

    def cancel_export(self, finished):
        # A dedicated loop lets shutdown cancel every task before the real
        # thread finishes, without cancelling this suite's own test runner.
        with tempfile.TemporaryDirectory() as directory:
            loop = asyncio.new_event_loop()
            executor = ThreadPoolExecutor()
            loop.set_default_executor(executor)
            archive = Archive(Path(directory) / "sessions.sqlite", "entry")
            started, release = threading.Event(), threading.Event()
            paths = []
            original_export = archive._export

            def blocked_export():
                started.set()
                release.wait()
                path = original_export()
                paths.append(path)
                return path

            archive._export = blocked_export

            async def start_request():
                await archive.start()
                request = asyncio.create_task(archive.export())
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await archive.close()
                return request

            try:
                request = loop.run_until_complete(start_request())
                if finished:
                    # Finish the real writer while the loop cannot deliver
                    # its result. Cancellation must still remove that result.
                    release.set()
                    executor.shutdown(wait=True)
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    loop.run_until_complete(request)
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            finally:
                release.set()
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
            try:
                self.assertEqual(len(paths), 1)
                self.assertFalse(paths[0].exists())
            finally:
                for path in paths:
                    path.unlink(missing_ok=True)
