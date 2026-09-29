"""F4 regression: physical cooling evidence and logical cooling phase differ."""

import asyncio
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.phases import project_session

T0 = datetime(2030, 1, 1, tzinfo=UTC)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(contactor=False):
    value = Controller(
        Parameters(
            {
                "target_temperature_c": 80,
                "after_run_minutes": 5,
                "oven_cooling_max_minutes": 5,
            }
        )
    )
    value.set_temperature(90, at(0))
    value.report_contactor(contactor, at(0))
    value.begin_session("s", at(0))
    value._begin_after_run("g", at(0))
    return value


def interval_seconds(intervals):
    return sum((right - left).total_seconds() for left, right in intervals)


class CoolingEvidenceTests(unittest.TestCase):
    def test_subsecond_evidence_matches_accounting_at_each_transition(self):
        value = controller()
        for state, second in (
            (False, 0),
            (True, 0),
            (False, 0),
            (False, 0.123456),
            (None, 0.123456),
            (False, 0.654321),
            (False, 1.123456),
        ):
            value.report_contactor(state, at(second))
            phase = value.session.after_run
            spans = [
                (start, end or at(second)) for start, end in phase.active_intervals
            ]
            self.assertAlmostEqual(
                interval_seconds(spans), phase.elapsed_seconds, places=6
            )
            self.assertTrue(all(start <= end for start, end in spans))
            self.assertTrue(all(left[1] <= right[0] for left, right in pairwise(spans)))
        phase = value.session.after_run
        self.assertTrue(value.finish_phase("after_run", phase.phase_id, at(1.234567)))
        finished = value.session.after_run_history[-1]
        self.assertAlmostEqual(
            interval_seconds(finished.active_intervals),
            finished.elapsed_seconds,
            places=6,
        )
        self.assertIsNone(value.session.last_completed_oven_cooling_at)

    def test_manual_finish_during_unconfirmed_gap_keeps_logical_cooling(self):
        for state in (True, None):
            with self.subTest(state=state):
                value = controller()
                value.report_contactor(state, at(120))
                phase = value.session.after_run
                self.assertFalse(value.last_decision.heat)
                value.finish_phase("after_run", phase.phase_id, at(180))
                self.assertIsNone(value.session.after_run)
                finished = value.session.after_run_history[-1]
                self.assertEqual(finished.active_intervals, ((at(0), at(120)),))
                self.assertEqual(finished.elapsed_seconds, 120)
                self.assertEqual(
                    [
                        (i.phase, i.started_at, i.ended_at)
                        for i in value.phase_projection(at(180)).intervals
                    ],
                    [("nachlauf", at(0), at(180))],
                )
                self.assertIsNone(value.session.last_completed_oven_cooling_at)

    def test_unknown_gap_splits_physical_evidence_but_keeps_logical_phase(self):
        value = controller()
        value.report_contactor(None, at(120))
        value.report_contactor(False, at(180))
        value.advance(at(360))
        phase = value.session.after_run_history[-1]
        self.assertEqual(phase.active_intervals, ((at(0), at(120)), (at(180), at(360))))
        self.assertEqual(
            interval_seconds(phase.active_intervals), phase.elapsed_seconds
        )
        self.assertEqual(phase.elapsed_seconds, 300)
        projection = value.phase_projection(at(360))
        self.assertEqual(
            [(i.phase, i.started_at, i.ended_at) for i in projection.intervals],
            [("nachlauf", at(0), at(360))],
        )
        self.assertEqual(projection.readiness_pauses, ())

    def test_on_gap_and_same_time_repeats_neither_overlap_nor_add_time(self):
        value = controller()
        value.report_contactor(True, at(120))
        value.report_contactor(True, at(120))
        value.report_contactor(None, at(120))
        value.report_contactor(None, at(120))
        value.report_contactor(False, at(180))
        value.report_contactor(False, at(180))
        value.advance(at(360))
        phase = value.session.after_run_history[-1]
        self.assertEqual(phase.active_intervals, ((at(0), at(120)), (at(180), at(360))))
        self.assertEqual(interval_seconds(phase.active_intervals), 300)

    def test_abort_pending_and_operation_off_close_or_preserve_evidence(self):
        pending = controller(contactor=True)
        pending._abort_after_run(at(5))
        phase = pending.session.after_run_history[-1]
        self.assertEqual(phase.active_intervals, ())
        self.assertIsNone(pending.session.last_completed_oven_cooling_at)

        running = controller()
        running.set_operation(False, at(120))
        phase = running.session.after_run_history[-1]
        self.assertEqual(phase.active_intervals, ((at(0), at(120)),))
        self.assertEqual(phase.elapsed_seconds, 120)
        self.assertIsNone(running.session.last_completed_oven_cooling_at)

    def test_old_archived_format_without_requested_at_still_projects_its_spans(self):
        state = {
            "timeline": {"session_started_at": T0},
            "base_phases": [
                {"at": at(0), "phase": "bereit", "operation_enabled": True}
            ],
            "contactor_history": [{"at": at(0), "state": False}],
            "after_run_history": [
                {
                    "phase_id": "old",
                    "active_intervals": [(at(20), at(25)), (at(35), at(40))],
                }
            ],
        }
        projection = project_session(state, at(50))
        self.assertEqual(
            [
                (i.phase, i.started_at, i.ended_at)
                for i in projection.intervals
                if i.phase == "nachlauf"
            ],
            [("nachlauf", at(20), at(25)), ("nachlauf", at(35), at(40))],
        )

    def test_sqlite_read_and_export_preserve_split_intervals(self):
        async def run():
            value = controller()
            value.report_contactor(None, at(120))
            value.report_contactor(False, at(180))
            value.advance(at(360))
            with tempfile.TemporaryDirectory() as directory:
                archive = Archive(Path(directory) / "archive.sqlite", "entry")
                await archive.start()
                archive.save_session(value.session, at(360), {})
                await archive.flush()
                read = archive.read("s")
                exported = await archive.export()
                with zipfile.ZipFile(exported) as bundle:
                    saved = json.loads(bundle.read("sessions.jsonl").decode().strip())
                exported.unlink()
                await archive.close()
            return read, saved

        read, saved = asyncio.run(run())
        expected = [
            [at(0).isoformat(), at(120).isoformat()],
            [at(180).isoformat(), at(360).isoformat()],
        ]
        self.assertEqual(
            read["session"]["after_run_history"][-1]["active_intervals"], expected
        )
        self.assertEqual(saved["after_run_history"][-1]["active_intervals"], expected)
        phases = read["phase_projection"]["intervals"]
        self.assertEqual(
            [(item["phase"], item["started_at"], item["ended_at"]) for item in phases],
            [("nachlauf", at(0).isoformat(), at(360).isoformat())],
        )


if __name__ == "__main__":
    unittest.main()
