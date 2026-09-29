"""Personensignale dürfen nur zum aktuellen Türschluss einen Gang beginnen."""

import asyncio
import json
import unittest
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Door, Event, Kind

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def event(event_id, kind, detected, *, effective=None):
    return Event(
        event_id,
        "s",
        kind,
        at(detected if effective is None else effective),
        at(detected),
    )


def controller(**parameters):
    result = Controller(Parameters(parameters))
    result.begin_session("s", T0)
    result.set_temperature(99, T0)
    return result


class EntryContextTests(unittest.TestCase):
    def test_expired_strong_signal_is_processed_without_inventing_a_gang(self):
        sauna = controller()
        sauna.process(event("close", Kind.DOOR_CLOSE, 0))
        before = (
            sauna.session.timeline.gang_count,
            sauna.session.timeline.retracted,
            sauna.session.cooling,
            tuple(sauna.consumer_events),
        )

        result = sauna.process(
            event("old-strong", Kind.PERSON_STRONG, 721, effective=1)
        )

        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "entry_context_expired")
        self.assertIsNone(sauna.session.timeline.active)
        self.assertEqual(sauna.session.timeline.gang_count, before[0])
        self.assertEqual(sauna.session.timeline.retracted, before[1])
        self.assertIsNone(sauna.session.cooling)
        self.assertEqual(tuple(sauna.consumer_events), before[3])
        self.assertEqual(
            [item.event_id for item in sauna.session.timeline.processed],
            ["close", "old-strong"],
        )
        self.assertFalse(sauna.regulation_inputs.gang_heat_demand)

    def test_old_strong_signal_cannot_attach_to_replacement_close_or_heat(self):
        sauna = controller()
        sauna.process(event("close-old", Kind.DOOR_CLOSE, 0))
        sauna.process(event("open-new", Kind.DOOR_OPEN, 100))
        sauna.set_heater_override(False, at(105))
        sauna.process(event("close-new", Kind.DOOR_CLOSE, 110))
        sauna.set_heater_override(None, at(120))
        self.assertEqual(sauna.last_decision.reason, "temperature_reached")

        result = sauna.process(
            event("old-strong", Kind.PERSON_STRONG, 200, effective=1)
        )

        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "entry_context_changed")
        self.assertIsNone(sauna.session.timeline.active)
        self.assertEqual(sauna.session.timeline.anchor.event_id, "close-new")
        self.assertFalse(sauna.regulation_inputs.gang_heat_demand)
        self.assertFalse(sauna.last_decision.heat)
        self.assertEqual(sauna.last_decision.reason, "temperature_reached")
        self.assertFalse(
            any(
                deadline.purpose == "confirmation"
                for deadline in sauna.session.deadlines
            )
        )

    def test_current_strong_signal_and_infusion_remain_admissible(self):
        sauna = controller()
        sauna.process(event("close", Kind.DOOR_CLOSE, 110))

        result = sauna.process(
            event("current-strong", Kind.PERSON_STRONG, 200, effective=111)
        )

        self.assertTrue(result.changed)
        self.assertEqual(sauna.session.timeline.active.started_at, at(110))
        self.assertEqual(sauna.session.timeline.active.start_source_event_id, "close")
        self.assertTrue(sauna.regulation_inputs.gang_heat_demand)

        sauna = controller()
        sauna.process(event("close", Kind.DOOR_CLOSE, 0))
        result = sauna.process(event("old-infusion", Kind.INFUSION, 721, effective=1))
        self.assertTrue(result.changed)
        self.assertTrue(sauna.session.timeline.active.infusion_events)

    def test_recognition_only_strong_and_weak_rules_are_unchanged(self):
        sauna = controller()
        sauna._session = replace(
            sauna.session,
            timeline=replace(sauna.session.timeline, door=Door.CLOSED),
        )
        self.assertTrue(sauna.process(event("strong", Kind.PERSON_STRONG, 1)).changed)
        self.assertEqual(sauna.session.timeline.active.start_basis, "recognition_only")

        sauna = controller()
        self.assertEqual(
            sauna.process(event("weak", Kind.PERSON_WEAK, 1)).reason,
            "entry_context_missing",
        )
        sauna._session = replace(
            sauna.session,
            timeline=replace(sauna.session.timeline, door=Door.CLOSED),
        )
        self.assertEqual(
            sauna.process(event("weak-closed", Kind.PERSON_WEAK, 2)).reason,
            "entry_context_missing",
        )

        sauna = controller()
        sauna.process(event("close", Kind.DOOR_CLOSE, 0))
        self.assertTrue(sauna.process(event("weak", Kind.PERSON_WEAK, 1)).changed)

    def test_confirmation_boundary_keeps_existing_event_order(self):
        for detected, expected_changed in ((719, True), (720, True), (721, False)):
            with self.subTest(detected=detected):
                sauna = controller()
                sauna.process(event("close", Kind.DOOR_CLOSE, 0))
                result = sauna.process(
                    event("strong", Kind.PERSON_STRONG, detected, effective=1)
                )

                self.assertEqual(result.changed, expected_changed)
                if detected < 720:
                    self.assertIsNotNone(sauna.session.timeline.active)
                elif detected == 720:
                    # The signal is admitted first; the existing inclusive
                    # deadline pass then retracts that still-unconfirmed gang.
                    self.assertIsNone(sauna.session.timeline.active)
                    self.assertEqual(len(sauna.session.timeline.retracted), 1)
                else:
                    self.assertEqual(result.reason, "entry_context_expired")
                    self.assertFalse(sauna.session.timeline.retracted)

    def test_rejected_signal_keeps_identity_and_duplicate_rules(self):
        sauna = controller()
        sauna.process(event("close", Kind.DOOR_CLOSE, 0))
        old = event("old", Kind.PERSON_STRONG, 721, effective=1)
        self.assertEqual(sauna.process(old).reason, "entry_context_expired")
        self.assertEqual(sauna.process(old).reason, "duplicate")
        with self.assertRaises(ValueError):
            sauna.process(event("old", Kind.PERSON_STRONG, 722, effective=1))


class EntryContextArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_signal_is_kept_without_a_fabricated_gang_in_read_and_export(
        self,
    ):
        with TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "sessions.sqlite", "entry")
            await archive.start()
            try:
                sauna = controller()
                sauna.process(event("close", Kind.DOOR_CLOSE, 0))
                sauna.process(event("old-strong", Kind.PERSON_STRONG, 721, effective=1))
                archive.save_session(sauna.session, at(721), {})
                await archive.flush()

                stored = await asyncio.to_thread(archive.read, "s")
                timeline = stored["session"]["timeline"]
                self.assertIsNone(timeline["active"])
                self.assertEqual(timeline["retracted"], [])
                self.assertEqual(
                    [item["event_id"] for item in timeline["processed"]],
                    ["close", "old-strong"],
                )

                export_path = await archive.export()
                try:
                    with zipfile.ZipFile(export_path) as export:
                        exported = json.loads(
                            export.read("sessions.jsonl").decode().strip()
                        )
                    timeline = exported["timeline"]
                    self.assertIsNone(timeline["active"])
                    self.assertEqual(timeline["retracted"], [])
                    self.assertEqual(
                        [item["event_id"] for item in timeline["processed"]],
                        ["close", "old-strong"],
                    )
                finally:
                    export_path.unlink()
            finally:
                await archive.close()


if __name__ == "__main__":
    unittest.main()
