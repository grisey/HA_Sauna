"""B4f (L-B4-4): Verhältnis zur nächstbesten Erklärung für übernommene Ereignisse.

Die Erkennung entscheidet unverändert; jede ihrer Entscheidungen über Tür auf,
Tür zu und Aufguss steht mit Zustand, nächstbester Erklärung und Verhältnis im
Entscheidungsprotokoll. ``recognition_archive.decision`` ordnet sie der
gelieferten Erkennung zu, das Archiv speichert sie in der Erkennungsmeldung und
die Erkennungskontrolle zeigt sie wie für verworfene Kandidaten.
"""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np
from test_foundation import bindings, parameters
from test_recognition_model import T0
from test_recognition_model import parameters as model_parameters

from custom_components.ha_sauna.core.recognition import (
    ClimateRecognizer,
    Detection,
    InfusionUpdate,
)
from custom_components.ha_sauna.core.recognition_archive import decision
from custom_components.ha_sauna.core.recognition_model import (
    CLOSED,
    INFUSION,
    NM,
    OPEN,
    RECOVERY,
)
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def t(seconds):
    return T0 + timedelta(seconds=seconds)


def explain(event, effective, decided, ratio, state="open", against="closed"):
    return {"event": event, "effective_at": t(effective).isoformat(), "decided_at": t(decided).isoformat(),
            "state": state, "against": against, "ratio": ratio, "steps_to_decision": 0,
            "difference_factor": 1.0}


def detection(kind, effective, detected):
    return Detection(kind, t(effective), t(detected), ("Tu",))


class DecisionTests(unittest.TestCase):
    def test_the_recognizer_log_gives_each_delivered_event_its_own_decision(self):
        # As the recognizer tests script it: a door opening, its closing with the
        # recovery and an infusion confirmed while the door was undecided; the
        # infusion follows the closing (its time moves to the closing).
        def row(closed=0.0, opened=0.0, infusion=0.0, recovery=0.0):
            r = np.zeros(NM)
            r[CLOSED], r[OPEN], r[INFUSION], r[RECOVERY] = closed, opened, infusion, recovery
            return r / r.sum()

        def script(k):
            rows = [row(closed=1)] * 3
            rows += [row(closed=0.4, infusion=0.6) if k < 7 else row(closed=0.001, infusion=0.999)]
            rows += [row(closed=0.3, opened=0.7)] * 3
            rows += [row(closed=0.001, opened=0.999)] * 3
            rows += [row(recovery=0.6, opened=0.4)] * 2
            rows += [row(recovery=0.999, opened=0.001)] * 3
            return np.array(rows[:k + 1])

        recognizer = ClimateRecognizer(model_parameters(), T0)
        recognizer._grid_t = [float(i) for i in range(15)]
        for k in range(15):
            recognizer._steps = k + 1
            recognizer._post, recognizer._post_base = script(k), 0
            at = T0 + timedelta(seconds=100 + k)
            for item in [*recognizer._decide_door(at), *recognizer._decide_infusion(at)]:
                if not isinstance(item, InfusionUpdate):
                    recognizer.detections.append(item)        # as ``advance`` delivers them
        delivered = recognizer.detections
        self.assertEqual([(d.kind, (d.effective_at - T0).total_seconds()) for d in delivered],
                         [(Kind.DOOR_OPEN, 4.0), (Kind.DOOR_CLOSE, 10.0), (Kind.INFUSION, 10.0)])
        log = {entry["event"]: entry for entry in recognizer.decisions if "ratio" in entry}
        for item, event in zip(delivered, ("door_open", "door_close", "infusion"), strict=True):
            self.assertEqual(decision(item, delivered, recognizer.decisions),
                             {key: log[event][key] for key in ("state", "against", "ratio")})
        self.assertEqual(log["infusion"]["effective_at"], t(3).isoformat())

    def test_rejected_closings_and_held_infusions_keep_the_order(self):
        decisions = [
            explain("door_open", 10, 12, 300.0),
            explain("door_close", 20, 25, 400.0, "recovery", "open"),
            {"event": "door_open_rejected", "effective_at": t(10).isoformat(),
             "decided_at": t(25).isoformat(), "reason": "opening_form"},
            explain("door_open", 40, 42, 500.0),
            {"event": "door_open_form", "effective_at": t(40).isoformat(),
             "decided_at": t(44).isoformat(), "steps_to_decision": 2},
            explain("infusion", 45, 46, 700.0, "infusion", "open"),
            explain("door_close", 60, 66, 800.0, "recovery", "open"),
            explain("infusion", 70, 70, 900.0, "infusion", "decay"),
        ]
        opened = detection(Kind.DOOR_OPEN, 40, 44)
        closed = Detection(Kind.DOOR_CLOSE, t(58), t(66), ("Tu",))   # timed at the kink
        held = detection(Kind.INFUSION, 58, 66)                     # follows the closing
        later = detection(Kind.INFUSION, 70, 70)
        vented = detection(Kind.VENTILATION, 50, 55)
        delivered = [opened, vented, closed, held, later]
        self.assertEqual(decision(opened, delivered, decisions)["ratio"], 500.0)
        self.assertEqual(decision(closed, delivered, decisions),
                         {"state": "recovery", "against": "open", "ratio": 800.0})
        self.assertEqual(decision(held, delivered, decisions)["ratio"], 700.0)
        self.assertEqual(decision(later, delivered, decisions)["ratio"], 900.0)
        self.assertIsNone(decision(vented, delivered, decisions))
        # A door operation the gang model took from rejected openings is no delivery.
        rescued = detection(Kind.DOOR_OPEN, 10, 30)
        self.assertIsNone(decision(rescued, delivered, decisions))
        # A decision after the event it would explain is not its decision.
        early = detection(Kind.INFUSION, 44, 45)
        self.assertIsNone(decision(early, [opened, early], decisions))


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_detection_records_and_the_control_carry_the_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0 + timedelta(hours=1))
            await runtime.start_archive(Path(directory) / "a.sqlite", "entry")
            try:
                runtime.controller.begin_session("s", T0)
                runtime._sync_detector()
                recognizer = runtime.detector
                recognizer.decisions.extend([explain("door_open", 10, 12, 312.5),
                                             explain("infusion", 30, 31, 24264.3, "infusion", "decay")])
                pending = [detection(Kind.DOOR_OPEN, 10, 12), detection(Kind.INFUSION, 30, 31)]

                def advance(through, *, on_detection=None, **_callbacks):
                    for item in pending:
                        recognizer.detections.append(item)
                        on_detection(item)
                    pending.clear()
                    return []

                recognizer.advance = advance
                runtime._deliver_detection(T0 + timedelta(seconds=40))
                runtime.persist()
                await runtime.archive.flush()
                records = [record["payload"] for record in runtime.archive.read(
                    "s", limit=10000, now=runtime._clock())["records"]
                    if record["kind"] in ("detection", "detection_rejected")]
                self.assertEqual([(r["event"]["kind"], r.get("state"), r.get("against"), r.get("ratio"))
                                  for r in records],
                                 [("door_open", "open", "closed", 312.5),
                                  ("infusion", "infusion", "decay", 24264.3)])
                control = runtime.archive.recognition_control("s", now=runtime._clock())
                shown = {event["kind"]: (event["state"], event["against"], event["ratio"])
                         for event in control["events"]}
                self.assertEqual(shown["door_open"], ("offen", "Ruhe", 312.5))
                self.assertEqual(shown["infusion"], ("Aufguss", "Abklingen", 24264.3))
            finally:
                await runtime.archive.close()


class OlderSessionTests(unittest.TestCase):
    def test_older_records_without_the_ratio_show_the_probability_only(self):
        from custom_components.ha_sauna.core.recognition_control import (
            recognition_control,
        )

        event = {"event_id": "recognition:s:door_open:x", "kind": "door_open",
                 "effective_at": t(10).isoformat(), "detected_at": t(12).isoformat()}
        session = {"timeline": {"session_id": "s", "processed": [event]}}
        control = recognition_control(session, now=t(100), detections=[
            {"event": event, "probability": 0.998}])
        item = control["events"][0]
        self.assertEqual((item["probability"], item["ratio"], item["state"], item["against"]),
                         (0.998, None, None, None))


if __name__ == "__main__":
    unittest.main()
