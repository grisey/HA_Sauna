"""Synthetic regressions for read-only legacy phase reconstruction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "ha_sauna"))

from core.phases import project_archive  # noqa: E402

UTC = timezone.utc


def at(seconds: int) -> datetime:
    return datetime(2026, 9, 22, tzinfo=UTC) + timedelta(seconds=seconds)


def record(kind: str, seconds: int, payload: dict) -> dict:
    return {"kind": kind, "received_at": at(seconds).isoformat(), "payload": payload}


class LegacyPhaseProjectionTests(unittest.TestCase):
    def test_retracted_legacy_gang_exposes_preceding_warmup_mark(self):
        gang_id = "candidate"
        session = {
            "timeline": {
                "session_started_at": at(0).isoformat(),
                "completed": [],
                "active": None,
                "retracted": [
                    {
                        "gang_id": gang_id,
                        "started_at": at(10).isoformat(),
                        "ended_at": at(20).isoformat(),
                    }
                ],
            },
            "ended_at": at(30).isoformat(),
        }
        projection = project_archive(
            session,
            [
                record("phase", 0, {"phase": "aufheizen"}),
                record("phase", 10, {"phase": "saunagang"}),
                # The matching stored revision identifies this phase record as
                # the candidate later present in ``retracted``.  The phase
                # record itself must not erase the warm-up evidence.
                record(
                    "session",
                    10,
                    {
                        "ready_at": None,
                        "operation_enabled": True,
                        "timeline": {"active": {"gang_id": gang_id}},
                    },
                ),
            ],
            at(30),
        )

        self.assertEqual([(item.phase, item.complete) for item in projection.intervals], [("aufheizen", True)])
        self.assertEqual(projection.intervals[0].started_at, at(0))
        self.assertEqual(projection.intervals[0].ended_at, at(30))

    def test_legacy_gang_without_background_evidence_stays_unknown(self):
        session = {
            "timeline": {"session_started_at": at(0).isoformat(), "completed": [], "active": None, "retracted": []},
            "ended_at": at(20).isoformat(),
        }
        projection = project_archive(
            session,
            [record("phase", 5, {"phase": "saunagang"})],
            at(20),
        )

        self.assertEqual([(item.phase, item.complete) for item in projection.intervals], [("unknown", False)])


if __name__ == "__main__":
    unittest.main()
