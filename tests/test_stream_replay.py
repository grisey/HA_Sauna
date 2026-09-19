"""Privater Vergleich jedes Rasterpunkts mit dem eingefrorenen Kandidaten.

Nur lokal mit SAUNA_RECORDER_ARCHIVE. Keine Veröffentlichung der Rohdaten.
Keine Home-Assistant-Imports; NumPy/Pandas nur für die eingefrorene Referenz.
"""
from datetime import datetime, UTC
import importlib.util
import json
import os
from pathlib import Path
import unittest

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind
from test_detector import detection_parameters

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("SAUNA_RECORDER_ARCHIVE"), "privater Recorderexport nicht angegeben")
class StreamReplayTests(unittest.TestCase):
    def test_all_stream_decisions_match_frozen_candidate(self):
        spec = importlib.util.spec_from_file_location("frozen_candidate", ROOT / "candidate/replay.py")
        ref = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ref)
        params = json.loads((ROOT / "candidate/parameter.json").read_text())
        source, raw, meta = ref.load(Path(os.environ["SAUNA_RECORDER_ARCHIVE"]))
        frame, personframe = ref.prepare(source, params)
        origin = frame.index[0].to_pydatetime()
        # Replay untersucht Detektorgleichheit, nicht eine örtliche Ausfallfrist.
        # Der Fristwert wird für diesen Vergleich explizit auf Exportdauer gesetzt.
        timeout = (frame.index[-1] - frame.index[0]).total_seconds() + 1
        for positions, channels in (((Position.UPPER, Position.LOWER), (3, 6)),
                                    ((Position.UPPER,), (3,)), ((Position.LOWER,), (6,))):
            with self.subTest(channels=channels):
                expected_doors, doors = ref.door(frame, params, channels)
                ctx, episodes = ref.ventilation(frame, params, expected_doors, channels)
                paths, infusions, eligible = ref.signals(frame, personframe, params, doors, ctx, channels)
                detector = Detector(detection_parameters(sensor_timeout_seconds=timeout), origin, positions)
                inputs = []
                for c, pos in zip(channels, positions):
                    for letter, quantity in (("T", Quantity.TEMPERATURE), ("H", Quantity.HUMIDITY)):
                        for row in raw[f"{letter}{c}"].itertuples():
                            try:
                                value = float(row.state)
                            except ValueError:
                                value = None
                            received = datetime.fromtimestamp(row.ts, UTC)
                            inputs.append(Measurement(pos, quantity, value, row.state,
                                f"sensor.synthetic_{pos}_{quantity}", received, received))
                inputs.sort(key=lambda m: m.received_at)
                cursor = 0
                received_doors, received_vents = [], []
                for i, now in enumerate(frame.index):
                    while cursor < len(inputs) and inputs[cursor].received_at <= now:
                        detector.accept(inputs[cursor])
                        cursor += 1
                    events = detector.advance(now.to_pydatetime(), enabled=frame.session.iloc[i] == "on")
                    for e in events:
                        if e.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
                            received_doors.append((i, "open" if e.kind == Kind.DOOR_OPEN else "close"))
                        if e.kind == Kind.VENTILATION:
                            received_vents.append(i)
                    self.assertEqual(detector.open, bool(doors[i]), f"Tür bei Raster {i}")
                    self.assertEqual(detector.levels.get("infusion", False), bool(infusions.iloc[i]), f"Aufguss bei Raster {i}")
                    if i % 5 == 0:
                        for route, path in (("strong", "stark"), ("weak", "schwach")):
                            self.assertEqual(detector.levels.get(route, False), bool(paths[path].iloc[i // 5]), f"{path} bei Raster {i}")
                self.assertEqual(received_doors, expected_doors)
                self.assertEqual(len([x for x in received_doors if x[1] == "open"]), 15)
                expected_vents = [frame.index.get_loc(ref.pd.Timestamp(e["vent_detected_at"])) for e in episodes if e["vent_detected_at"]]
                self.assertEqual(received_vents, expected_vents)
