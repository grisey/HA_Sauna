"""Individual infusion end evidence stays separate from gang and control state."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_cooling import at, controller
from test_foundation import T0, bindings, event
import test_recognition_model as synthetic

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.recognition import (
    ClimateRecognizer,
    Detection,
    InfusionUpdate,
)
from custom_components.ha_sauna.core.timeline import (
    Infusion,
    InfusionEnd,
    Kind,
    Timeline,
    apply,
    update_infusion_end,
)
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


class InfusionEndOwnershipTests(unittest.TestCase):
    def test_source_owned_update_is_idempotent_retractable_and_supports_old_gangs(self):
        state = Timeline("s", T0)
        for source in (
            event("entry", Kind.DOOR_OPEN, 0),
            event("close", Kind.DOOR_CLOSE, 1),
            event("first", Kind.INFUSION, 2),
            event("second", Kind.INFUSION, 3),
        ):
            state = apply(state, source)
        end = InfusionEnd(at(4), at(7), at(6), "door_open", "physical_effective")
        gang = state.active

        # Older stored gang structures have leading events without metadata.
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                original = replace(
                    state, active=replace(gang, infusions=()) if legacy else gang
                )
                self.assertIs(update_infusion_end(original, "first", None), original)
                updated = update_infusion_end(original, "first", end)
                self.assertEqual(updated.active.infusions[0], Infusion("first", end))
                self.assertEqual(
                    replace(updated.active, infusions=original.active.infusions),
                    original.active,
                )
                self.assertEqual(replace(updated, active=original.active), original)
                self.assertIs(update_infusion_end(updated, "first", end), updated)
                self.assertIs(update_infusion_end(updated, "missing", end), updated)
                self.assertIs(update_infusion_end(updated, "close", end), updated)
                withdrawn = update_infusion_end(updated, "first", None)
                self.assertEqual(withdrawn.active.infusions[0], Infusion("first"))
                self.assertIs(update_infusion_end(withdrawn, "first", None), withdrawn)
                self.assertEqual(withdrawn.active.infusion_events, gang.infusion_events)
                self.assertEqual(
                    [
                        item
                        for item in updated.active.infusions
                        if item.source_event_id == "second"
                    ],
                    [] if legacy else [Infusion("second")],
                )

        # A matching string in a foreign or non-infusion source is no ownership.
        for invalid in (
            replace(gang.infusion_events[0], session_id="other"),
            replace(gang.infusion_events[0], kind=Kind.PERSON_WEAK),
        ):
            with self.subTest(invalid=invalid):
                foreign = replace(
                    state, active=replace(gang, infusion_events=(invalid,))
                )
                self.assertIs(update_infusion_end(foreign, "first", end), foreign)
        with self.assertRaises(ValueError):
            update_infusion_end(state, "first", replace(end, effective_at=at(1)))

    def test_controller_updates_current_and_completed_session_without_control_transition(
        self,
    ):
        c = controller()
        for source in (
            event("entry", Kind.DOOR_OPEN, 0),
            event("close", Kind.DOOR_CLOSE, 1),
            event("water", Kind.INFUSION, 2),
        ):
            c.process(source)
        end = InfusionEnd(at(3), at(5), at(4), "door_open", "physical_effective")

        for completed in (False, True):
            with self.subTest(completed=completed):
                if completed:
                    c.finish_session(at(6), light_after_run=False)
                    c.begin_session("next", at(7))
                before = c.completed_sessions[0] if completed else c.session
                current = c.session
                decisions, consumers = tuple(c.decisions), tuple(c.consumer_events)
                decision, inputs, clock = (
                    c.last_decision,
                    c.regulation_inputs,
                    c._last_at,
                )
                self.assertTrue(
                    c.record_infusion_end("s", "water", end if not completed else None)
                )
                after = c.completed_sessions[0] if completed else c.session
                self.assertEqual(replace(after, timeline=before.timeline), before)
                old_gang = (
                    before.timeline.completed[0]
                    if completed
                    else before.timeline.active
                )
                new_gang = (
                    after.timeline.completed[0] if completed else after.timeline.active
                )
                self.assertEqual(
                    replace(new_gang, infusions=old_gang.infusions), old_gang
                )
                self.assertEqual(
                    after.timeline,
                    replace(
                        before.timeline,
                        **(
                            {"completed": (new_gang,)}
                            if completed
                            else {"active": new_gang}
                        ),
                    ),
                )
                self.assertEqual(new_gang.infusions[0].end, None if completed else end)
                self.assertEqual(tuple(c.decisions), decisions)
                self.assertEqual(tuple(c.consumer_events), consumers)
                self.assertIs(c.last_decision, decision)
                self.assertEqual(c.regulation_inputs, inputs)
                self.assertEqual(c._last_at, clock)
                self.assertFalse(c.record_infusion_end("missing", "water", end))
                self.assertFalse(c.record_infusion_end("s", "close", end))
                self.assertFalse(
                    c.record_infusion_end("s", "water", None if completed else end)
                )
                if completed:
                    self.assertIs(c.session, current)
                else:
                    self.assertIsNotNone(c.session.timeline.active)
                    self.assertEqual(c.session.timeline.completed, ())
                    self.assertIsNone(c.session.after_run)


class InfusionEndRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_only_packets_keep_older_source_across_calls_and_archive_retraction(
        self,
    ):
        """Replace only detector transport; timeline, controller and SQLite are real."""
        c = controller()
        p = c.parameters
        grid = 2
        now = [T0]
        runtime = SaunaRuntime(Configuration(bindings(), p), lambda: now[0])
        runtime.controller = c
        runtime._sync_detector()
        runtime.controller.process(event("entry", Kind.DOOR_OPEN, 0))
        runtime.controller.process(event("close", Kind.DOOR_CLOSE, grid))
        detector = runtime.detector
        first = Detection(Kind.INFUSION, at(2 * grid), at(4 * grid), (), at(3 * grid))
        second = Detection(Kind.INFUSION, at(8 * grid), at(10 * grid), (), at(9 * grid))
        first_end = InfusionEnd(
            at(6 * grid),
            at(7 * grid),
            at(6 * grid),
            "steam_decayed",
            "physical_effective",
        )
        second_end = InfusionEnd(
            at(11 * grid),
            at(13 * grid),
            at(12 * grid),
            "door_open",
            "physical_effective",
        )
        packets = iter(
            (
                ((first,), ()),
                ((), (InfusionUpdate(first, first_end),)),
                ((second,), ()),
                (
                    (),
                    (
                        # an equal but different detection is not the delivered source
                        InfusionUpdate(replace(first), second_end),
                        InfusionUpdate(first, None),
                        InfusionUpdate(first, first_end),
                        InfusionUpdate(second, second_end),
                        InfusionUpdate(second, second_end),
                    ),
                ),
            )
        )

        def transport_only_advance(through, **callbacks):
            detections, updates = next(packets)
            for detection in detections:
                callbacks["on_detection"](detection)
            for update in updates:
                callbacks["on_infusion_update"](update)
            return list(detections)

        with TemporaryDirectory() as directory:
            await runtime.start_archive(Path(directory) / "infusion.sqlite", "entry")
            try:
                with patch.object(
                    detector, "advance", side_effect=transport_only_advance
                ):
                    for second_at in (4, 7, 10, 14):
                        now[0] = at(second_at * grid)
                        runtime._deliver_detection(now[0])
                        runtime.persist()
                await runtime.archive.flush()
                stored = await asyncio.to_thread(runtime.archive.read, "s")
                gang = runtime.session.timeline.active
                first_id, second_id = (item.event_id for item in gang.infusion_events)
                self.assertNotEqual(first_id, second_id)
                self.assertEqual(
                    gang.infusions,
                    (
                        Infusion(first_id, first_end),
                        Infusion(second_id, second_end),
                    ),
                )
                self.assertIsNone(gang.ended_at)
                self.assertEqual(runtime.session.timeline.completed, ())
                self.assertIsNone(runtime.session.after_run)
                self.assertFalse(
                    any(item.kind == "gang_ended" for item in runtime.consumer_events)
                )
                self.assertEqual(
                    stored["session"]["timeline"]["active"]["infusions"],
                    plain(gang.infusions),
                )
                updates = [
                    row for row in stored["records"] if row["kind"] == "infusion_update"
                ]
                self.assertEqual(
                    [row["payload"] for row in updates],
                    [
                        {"source_event_id": first_id, "end": plain(first_end)},
                        {"source_event_id": first_id, "end": None},
                        {"source_event_id": first_id, "end": plain(first_end)},
                        {"source_event_id": second_id, "end": plain(second_end)},
                    ],
                )
                self.assertEqual(updates[-1]["received_at"], at(14 * grid).isoformat())
                self.assertEqual(
                    updates[-1]["payload"]["end"]["confirmed_at"],
                    at(13 * grid).isoformat(),
                )
            finally:
                await runtime.archive.close()


class InfusionEndRecognitionTests(unittest.TestCase):
    def run_cabin(self, door):
        t, y = synthetic.cabin(n=600, infusion=560.0, door=door, seed=3)
        recognizer = ClimateRecognizer(synthetic.parameters(), synthetic.T0,
                                       outside_reference=synthetic.ROOM)
        delivered, updates = [], []
        for m in synthetic.measurements(t, y):
            recognizer.accept(m)
            recognizer.advance(m.received_at - timedelta.resolution, on_detection=delivered.append,
                               on_infusion_update=updates.append)
        recognizer.advance(synthetic.T0 + timedelta(seconds=float(t[-1])), on_detection=delivered.append,
                           on_infusion_update=updates.append)
        return delivered, updates

    def test_door_opening_during_the_steam_ends_the_infusion_at_the_opening(self):
        delivered, updates = self.run_cabin((630.0, 800.0))
        infusions = [d for d in delivered if d.kind == Kind.INFUSION]
        openings = [d for d in delivered if d.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(infusions), 1)
        self.assertEqual(len(openings), 1)
        self.assertEqual(len(updates), 1)
        end = updates[0].end
        self.assertIs(updates[0].confirmed_detection, infusions[0])
        self.assertEqual(end.basis, "door_open")
        self.assertEqual(end.time_basis, "physical_effective")
        self.assertEqual(end.effective_at, openings[0].effective_at)
        self.assertLessEqual(end.effective_at, end.trace_at)
        self.assertLessEqual(end.trace_at, end.confirmed_at)
        self.assertGreater(end.effective_at, infusions[0].effective_at)

    def test_steam_decaying_before_the_opening_ends_by_itself(self):
        delivered, updates = self.run_cabin((700.0, 860.0))
        opening = next(d for d in delivered if d.kind == Kind.DOOR_OPEN)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].end.basis, "steam_decayed")
        self.assertLess(updates[0].end.effective_at, opening.effective_at)
