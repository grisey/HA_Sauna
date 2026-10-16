"""Individual infusion end evidence stays separate from gang and control state."""

import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_cooling import at, controller
from test_foundation import T0, bindings, event

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.detector import (
    Detection, InfusionEpisodeUpdate,
)
from custom_components.ha_sauna.core.timeline import (
    Infusion, InfusionEnd, Kind, Timeline, apply, update_infusion_end,
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
                original = replace(state, active=replace(gang, infusions=()) if legacy else gang)
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
                    [item for item in updated.active.infusions if item.source_event_id == "second"],
                    [] if legacy else [Infusion("second")],
                )

        # A matching string in a foreign or non-infusion source is no ownership.
        for invalid in (
            replace(gang.infusion_events[0], session_id="other"),
            replace(gang.infusion_events[0], kind=Kind.PERSON_WEAK),
        ):
            with self.subTest(invalid=invalid):
                foreign = replace(state, active=replace(gang, infusion_events=(invalid,)))
                self.assertIs(update_infusion_end(foreign, "first", end), foreign)
        with self.assertRaises(ValueError):
            update_infusion_end(state, "first", replace(end, effective_at=at(1)))

    def test_controller_updates_current_and_completed_session_without_control_transition(self):
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
                decision, inputs, clock = c.last_decision, c.regulation_inputs, c._last_at
                self.assertTrue(c.record_infusion_end("s", "water", end if not completed else None))
                after = c.completed_sessions[0] if completed else c.session
                self.assertEqual(replace(after, timeline=before.timeline), before)
                old_gang = before.timeline.completed[0] if completed else before.timeline.active
                new_gang = after.timeline.completed[0] if completed else after.timeline.active
                self.assertEqual(replace(new_gang, infusions=old_gang.infusions), old_gang)
                self.assertEqual(after.timeline, replace(
                    before.timeline,
                    **({"completed": (new_gang,)} if completed else {"active": new_gang}),
                ))
                self.assertEqual(new_gang.infusions[0].end, None if completed else end)
                self.assertEqual(tuple(c.decisions), decisions)
                self.assertEqual(tuple(c.consumer_events), consumers)
                self.assertIs(c.last_decision, decision)
                self.assertEqual(c.regulation_inputs, inputs)
                self.assertEqual(c._last_at, clock)
                self.assertFalse(c.record_infusion_end("missing", "water", end))
                self.assertFalse(c.record_infusion_end("s", "close", end))
                self.assertFalse(c.record_infusion_end("s", "water", None if completed else end))
                if completed:
                    self.assertIs(c.session, current)
                else:
                    self.assertIsNotNone(c.session.timeline.active)
                    self.assertEqual(c.session.timeline.completed, ())
                    self.assertIsNone(c.session.after_run)


class InfusionEndRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_only_packets_keep_older_source_across_calls_and_archive_retraction(self):
        """Replace only detector transport; timeline, controller and SQLite are real."""
        c = controller()
        p = c.parameters
        grid = p.values["grid_seconds"]
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
            at(6 * grid), at(7 * grid), at(6 * grid),
            "original_background", "evidence_grid",
        )
        second_end = InfusionEnd(
            at(11 * grid), at(13 * grid), at(12 * grid),
            "door_open", "physical_effective",
        )
        packets = iter((
            ((first,), ()),
            ((), (InfusionEpisodeUpdate(first, first_end),)),
            ((second,), ()),
            ((), (
                InfusionEpisodeUpdate(replace(first), second_end),
                InfusionEpisodeUpdate(first, None),
                InfusionEpisodeUpdate(first, first_end),
                InfusionEpisodeUpdate(second, second_end),
                InfusionEpisodeUpdate(second, second_end),
            )),
        ))

        def transport_only_advance(through, **callbacks):
            detections, updates = next(packets)
            self.assertTrue(callbacks["before_sample"](through))
            for detection in detections:
                callbacks["on_detection"](detection)
            for update in updates:
                callbacks["on_infusion_update"](update)
            callbacks["after_sample"](through)
            detector.index = int((through - detector.origin).total_seconds() / grid)
            return list(detections)

        with TemporaryDirectory() as directory:
            await runtime.start_archive(Path(directory) / "infusion.sqlite", "entry")
            try:
                with patch.object(detector, "advance", side_effect=transport_only_advance):
                    for second_at in (4, 7, 10, 14):
                        now[0] = at(second_at * grid)
                        runtime._deliver_detection(now[0])
                        runtime.persist()
                await runtime.archive.flush()
                stored = await asyncio.to_thread(runtime.archive.read, "s")
                gang = runtime.session.timeline.active
                first_id, second_id = (item.event_id for item in gang.infusion_events)
                self.assertNotEqual(first_id, second_id)
                self.assertEqual(gang.infusions, (
                    Infusion(first_id, first_end), Infusion(second_id, second_end),
                ))
                self.assertIsNone(gang.ended_at)
                self.assertEqual(runtime.session.timeline.completed, ())
                self.assertIsNone(runtime.session.after_run)
                self.assertFalse(any(item.kind == "gang_ended" for item in runtime.consumer_events))
                self.assertEqual(
                    stored["session"]["timeline"]["active"]["infusions"],
                    plain(gang.infusions),
                )
                updates = [row for row in stored["records"] if row["kind"] == "infusion_update"]
                self.assertEqual([row["payload"] for row in updates], [
                    {"source_event_id": first_id, "end": plain(first_end)},
                    {"source_event_id": first_id, "end": None},
                    {"source_event_id": first_id, "end": plain(first_end)},
                    {"source_event_id": second_id, "end": plain(second_end)},
                ])
                self.assertEqual(updates[-1]["received_at"], at(14 * grid).isoformat())
                self.assertEqual(updates[-1]["payload"]["end"]["confirmed_at"],
                                 at(13 * grid).isoformat())
            finally:
                await runtime.archive.close()
