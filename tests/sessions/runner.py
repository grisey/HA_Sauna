"""Feed received values through Runtime and HADevice; never inject detections.

Only the HA transport is replaced. Input curves and expected outcomes are
immutable fixture data. The current product output cannot select a curve or
change an expectation. Both kinds use a declared acknowledging actuator.
Historical feedback is separate diagnostic evidence, never an acknowledgement
of a new command or an oracle for the corrected controller.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from bisect import bisect_right
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ha_sauna.archive import plain, session_has_gangs
from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.configuration_model import temperature_control
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.device import HADevice
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import (
    apply_temperature_parameters,
    async_set_control_mode,
    async_set_parameters,
    async_set_program,
    async_set_temperature_steps,
)
from homeassistant.components.light import LightEntityFeature
from homeassistant.core import Context

from fixture_configuration import bound_parameters

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = datetime(2000, 1, 1, 12, tzinfo=UTC)
CLIMATE = ("upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity")
BINDINGS = {
    **{role: f"sensor.fixture_{role}" for role in CLIMATE},
    "heater": "switch.fixture_heater",
    "light": "light.fixture_light",
    "presence": "binary_sensor.fixture_presence",
    "control_input": "event.fixture_button",
}


def read_json(path):
    # Missing/unreadable data is an error, never an optional test.
    return json.loads(Path(path).read_text(encoding="utf-8"))


def substitute(value, values):
    if isinstance(value, str) and value.startswith("$"):
        return values[value[1:]]
    if isinstance(value, dict):
        return {key: substitute(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, values) for item in value]
    return value


def load_manifest(name):
    path = ROOT / name
    manifest = read_json(path)
    parameters = bound_parameters() if "configuration_file" in manifest else None
    for file_key, hash_key in (("source_curves", "source_curves_sha256"),
                               ("configuration_file", "configuration_sha256")):
        if file_key in manifest:
            source = ROOT / manifest[file_key]
            if hashlib.sha256(source.read_bytes()).hexdigest() != manifest[hash_key]:
                raise ValueError(f"Source checksum mismatch: {source}")
    entries = manifest.get("scenarios", manifest.get("replays", manifest.get("sessions")))
    if not entries:
        raise ValueError(f"{path}: no required sessions")
    cases = []
    for entry in entries:
        fixture = ROOT / entry.get("path", entry.get("file"))
        data = fixture.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Fixture checksum mismatch: {fixture}")
        case = json.loads(data)
        if case["id"] != entry["id"]:
            raise ValueError(f"Fixture identity mismatch: {fixture}")
        case["_path"] = fixture
        for source in entry.get("presence_sources", [case.get("configuration", {}).get("presence_source", "ha_presence")]):
            for choice in entry.get("variants", [{}]):
                variant = deepcopy(case)
                variant["base_id"] = case["id"]
                if "presence_sources" in entry and not variant["id"].endswith("-" + source):
                    variant["id"] += "-" + source
                if choice:
                    variant["id"] += "-" + choice["id"]
                config = variant.setdefault("configuration", {})
                if parameters is not None:
                    if config.get("parameter_source") != "configuration.json" or "parameters" in config:
                        raise ValueError(f"Derived fixture duplicates or lacks the canonical parameter binding: {fixture}")
                    config["parameters"] = dict(parameters)
                config.update(choice.get("configuration", {}), presence_source=source)
                gesture = config.get("button_session_gesture", "long")
                variant["actions"] = substitute([
                    action for action in variant["actions"]
                    if gesture in action.get("gestures", [gesture])
                ], choice.get("action_substitutions", {}))
                variant["expectations"] = substitute([
                    exp for exp in variant["expectations"]
                    if source in exp.get("presence_sources", [source])
                    and gesture in exp.get("gestures", [gesture])
                ], choice.get("expected_substitutions", {}))
                cases.append(variant)
    return cases


def bound_jsonl(case, key):
    path = case["_path"].parent / case[f"{key}_file"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != case[f"{key}_sha256"]:
        raise ValueError(f"Evidence checksum mismatch: {path}")
    if path.suffix == ".gz":
        payload = gzip.decompress(payload)
    rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if f"{key}_count" in case and len(rows) != case[f"{key}_count"]:
        raise ValueError(f"Evidence row count mismatch: {path}")
    return rows


def load_samples(case):
    source = case.get("samples_file", case.get("measurements_file", case.get("measurement_file")))
    if source is None:
        return list(case.get("samples", []))
    path = case["_path"].parent / source
    expected = case.get("samples_sha256", case.get("measurements_sha256", case.get("measurement_sha256")))
    if expected is None:
        raise ValueError(f"{case['id']}: unbound measurement file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Measurement checksum mismatch: {path}")
    if path.suffix == ".json":
        curve = read_json(path)
        rows = curve["rows"]
        result = []
        # The derived fixture declares a linear curve and original channel
        # offsets. Its one-second sampling does not alter any controller limit.
        times = sorted({r[0] for r in rows} | set(range(math.floor(rows[-1][0]) + 1)))
        cursor = 0
        for t in times:
            while cursor + 1 < len(rows) - 1 and rows[cursor + 1][0] < t:
                cursor += 1
            left, right = rows[cursor:cursor + 2]
            ratio = (t - left[0]) / (right[0] - left[0])
            for col, role in enumerate(curve["roles"], 1):
                time = round(t + curve["channel_offsets_s"][role], 6)
                if time <= case["duration_s"]:
                    result.append({"t": time, "role": role,
                                   "value": left[col] + ratio * (right[col] - left[col])})
        case.setdefault("initial_state", {}).update({
            role: {"value": rows[0][col]} for col, role in enumerate(curve["roles"], 1)
        })
        return result
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def matching(actual, expected):
    if isinstance(expected, dict):
        if not expected or set(expected) - {"min", "max", "one_of"}:
            raise ValueError(f"Unknown value predicate: {expected}")
        if actual is None and ("min" in expected or "max" in expected):
            return False
        return (all(actual >= expected[k] for k in ("min",) if k in expected)
                and all(actual <= expected[k] for k in ("max",) if k in expected)
                and ("one_of" not in expected or actual in expected["one_of"]))
    return actual == expected


class SessionRun:
    """A deterministic virtual clock with real production input/output owners."""

    def __init__(self, case):
        self.case = case
        self.now = ORIGIN
        self.states = {}
        self.calls = []
        self.snapshots = []
        self.action_results = []
        self.notifications = []
        self.observed_outputs = (bound_jsonl(case, "observed_outputs")
                                 if case.get("kind") == "real_session" else [])
        self.feedback_mode = case.get("feedback_mode", "simulated_acknowledgement")
        self.pending_feedback = []
        self.first_session_id = None
        self.first_session_started_at = None
        self.recording_history_checks = any(
            "recording_history_intact" in check["values"] for check in case["expectations"]
        )
        self.recording_history = {}
        self.recording_history_errors = []
        self.transition_checks = [
            check for check in case["expectations"] if "input_transition" in check
        ]
        self.input_transitions = {check["id"]: [] for check in self.transition_checks}
        spec = case.get("configuration", {})
        bindings = dict(BINDINGS)
        if spec.get("button_input_kind") == "binary":
            bindings["control_input"] = "binary_sensor.fixture_button"
        if spec.get("presence_source", "ha_presence") == "proxy":
            bindings.pop("presence")
        program = spec.get("program", {})
        parameters = dict(spec.get("parameters", {}))
        for key, parameter in (("start_c", "target_temperature_c"),
                               ("end_c", "final_temperature_c"),
                               ("gangs", "temperature_gangs")):
            if key in program:
                parameters[parameter] = program[key]
        if program.get("targets"):
            program = {**program, "steps": program["targets"]}
        if program.get("steps"):
            parameters.update(target_temperature_c=program["steps"][0],
                              final_temperature_c=program["steps"][-1],
                              temperature_gangs=len(program["steps"]))
        configuration = Configuration(
            Bindings(bindings), Parameters(parameters),
            control_input_mode=spec.get("control_input_mode", "button"),
            control_mode=spec.get("control_mode", "automatic"),
            presence_source=spec.get("presence_source", "ha_presence"),
            program_mode=program.get("mode", "constant"),
            temperature_steps=tuple(program["steps"]) if program.get("steps") else None,
            selected_program_id=program.get("profile"),
            button_program=spec.get("button_program", "constant"),
            button_session_gesture=spec.get("button_session_gesture", "long"),
        )
        self.runtime = SaunaRuntime(configuration, lambda: self.now)
        self.hass = SimpleNamespace(
            states=SimpleNamespace(get=self.states.get),
            services=SimpleNamespace(async_call=self.service),
            config_entries=SimpleNamespace(async_update_entry=self.save_options,
                                            async_get_entry=lambda _id: self.entry),
        )
        self.entry = SimpleNamespace(runtime_data=self.runtime, options=configuration.as_options(),
                                     title="Fixture sauna")
        self.device = HADevice(self.hass, self.runtime)
        self.runtime.device = self.device
        self.device.ingest("control_input", self.state("control_input", "idle", {
            "event_types": spec.get("button_event_types", [
                "btn_down", "btn_up", "single_push", "long_push", "double_push", "triple_push",
            ]),
        }), self.now, initial=True)
        self.runtime._reset_button_gestures()
        self.state("light", spec.get("initial_light_percent", 0))
        self.state("heater", False)
        self.states["sun.sun"] = SimpleNamespace(attributes={
            "elevation": spec.get("sun_elevation", 10 if spec.get("daylight") else -10),
        })
        self._temporary = TemporaryDirectory(prefix="sauna-session-")

    @staticmethod
    def save_options(entry, *, options):
        entry.options = options
        return False

    def state(self, role, value, attributes=None, *, context=None, age=0):
        entity_id = self.runtime.configuration.bindings.values[role]
        attrs = dict(attributes or {})
        if role in CLIMATE:
            attrs["unit_of_measurement"] = "°C" if role.endswith("temperature") else "%"
        if role == "light":
            attrs.update(supported_features=int(LightEntityFeature.TRANSITION),
                         supported_color_modes=["brightness"], color_mode="brightness")
            if isinstance(value, dict):
                if value.get("brightness") is not None:
                    attrs["brightness"] = value["brightness"]
                value = "on" if value["on"] else "off"
            else:
                attrs["brightness"] = round(float(value) * 255 / 100)
                value = "on" if float(value) > 0 else "off"
        elif isinstance(value, bool):
            value = "on" if value else "off"
        time = self.now - timedelta(seconds=age)
        state = SimpleNamespace(
            state=str(value) if value is not None else "unavailable", attributes=attrs,
            entity_id=entity_id, domain=entity_id.split(".")[0],
            last_changed=time, last_updated=time, last_reported=time,
            context=context or Context(),
        )
        self.states[entity_id] = state
        return state

    async def service(self, domain, service, data, **kwargs):
        """Transport only; no thermostat, gesture or phase calculation here."""
        self.calls.append({"t": self.seconds, "domain": domain, "service": service, "data": data})
        if self.feedback_mode != "simulated_acknowledgement":
            return
        if domain == "switch":
            role, value = "heater", service == "turn_on"
        elif domain == "light":
            role = "light"
            value = (data.get("brightness_pct", data.get("brightness", 255) * 100 / 255)
                     if service == "turn_on" else 0)
        else:
            raise AssertionError(f"Unexpected real service: {domain}.{service}")
        # Feedback is received after the service, through the same runtime FIFO.
        self.pending_feedback.append((role, value, kwargs.get("context")))

    @property
    def seconds(self):
        return (self.now - ORIGIN).total_seconds()

    async def feedback(self):
        while self.pending_feedback:
            batch, self.pending_feedback = self.pending_feedback, []
            for role, value, context in batch:
                await self.input({"role": role, "value": value}, context=context)

    async def input(self, sample, *, initial=False, context=None):
        role = sample["role"]
        role = {"heater_feedback": "heater", "light_feedback": "light"}.get(role, role)
        if role == "presence":
            if self.runtime.configuration.presence_source == "ha_presence":
                value = sample["value"]
                value = "on" if value is True else "off" if value is False else value
                age = sample.get("reported_age_s", 0)
                await self.runtime.accept_presence(binary_presence(
                    BINDINGS["presence"], str(value),
                    self.now - timedelta(seconds=age), self.now,
                ))
            return
        if role not in self.runtime.configuration.bindings.values:
            raise ValueError(f"Unknown fixture role: {role}")
        entity_id = self.runtime.configuration.bindings.values[role]
        old = self.states.get(entity_id)
        new = self.state(role, sample["value"], sample.get("attributes"),
                         context=context, age=sample.get("reported_age_s", 0))
        if initial:
            self.device.ingest(role, new, new.last_reported, initial=True)
        else:
            await self.runtime.device_input(SimpleNamespace(event_type="state_changed", data={
                "entity_id": entity_id, "old_state": old, "new_state": new,
            }))

    async def action(self, item):
        action, args = item["name"], item.get("args", {})
        r = self.runtime
        if action in ("start", "stop"):
            await r.set_operation(action == "start")
        elif action == "set_control_mode":
            await async_set_control_mode(self.hass, self.entry, args["value"])
        elif action == "set_heater":
            await r.set_heater_override(args["value"])
        elif action == "set_light":
            await r.set_light_override(args.get("value", args.get("brightness_pct")))
        elif action == "set_temperature":
            await async_set_parameters(self.hass, self.entry,
                                       {"target_temperature_c": args["value"]}, partial=True)
        elif action == "select_program":
            if "profile" in args:
                await async_set_program(self.hass, self.entry, args["profile"])
            else:
                values = dict(r.configuration.parameters.as_dict())
                for key, parameter in (("start_c", "target_temperature_c"),
                                       ("end_c", "final_temperature_c"),
                                       ("gangs", "temperature_gangs")):
                    if key in args:
                        values[parameter] = args[key]
                if args["mode"] == "constant":
                    await async_set_program(self.hass, self.entry, "constant")
                    if "start_c" in args:
                        await async_set_parameters(self.hass, self.entry,
                                                   {"target_temperature_c": args["start_c"]}, partial=True)
                elif args.get("steps"):
                    await async_set_temperature_steps(self.hass, self.entry, args["steps"])
                else:
                    async with r.serialized():
                        await apply_temperature_parameters(
                            r, Parameters(values), program_mode=args["mode"],
                            new_program=True, temperature_steps=None,
                        )
        elif action == "finish_cooling":
            if r.session is None or r.session.after_run is None:
                if self.case.get("kind") == "real_session":
                    return "Historical phase action has no current cooling to finish"
                raise AssertionError("No cooling to finish at the planned outside action")
            await r.finish_phase("after_run", r.session.after_run.phase_id)
        elif action == "finish_session":
            deadline = next(d for d in r.session.deadlines if d.purpose == "session_gap")
            await r.finish_session_gap(deadline.token)
        elif action == "button":
            event = args["event"]
            if r.configuration_model.button.local_hold_timing:
                await self.input({"role": "control_input", "value": event})
            else:
                await self.input({"role": "control_input", "value": self.now.isoformat(),
                                  "attributes": {"event_type": event,
                                                 "event_types": self.device.button_event_types}})
        elif action == "draft_cancel":
            # A cancelled panel draft sends no runtime request.
            pass
        elif action == "presence":
            await self.input({"role": "presence", "value": args["value"]})
        else:
            raise ValueError(f"Unknown fixture action: {action}")

    def snapshot(self):
        r, c = self.runtime, self.runtime.controller
        session = c.session
        cooling = session.after_run if session else None
        all_sessions = (*c.completed_sessions, *((session,) if session else ()))
        if self.recording_history_checks:
            self._observe_recording_history(all_sessions)
        recorded = session or (c.completed_sessions[-1] if c.completed_sessions else None)
        if recorded is not None and self.first_session_id is None:
            self.first_session_id = recorded.session_id
            self.first_session_started_at = recorded.timeline.session_started_at
        return {
            "t": self.seconds, "phase": c.phase, "control_mode": c.control_mode,
            "program_mode": c.program_mode, "target_temperature_c": c.target_temperature,
            "selected_program_id": r.configuration.selected_program_id,
            "next_target_temperature_c": c.next_gang_temperature,
            "temperature_c": c.temperature, "heater_override": c.heater_override,
            "heater_request": bool(c.last_decision and c.last_decision.heat),
            "automatic_heat": bool(c.automatic_decision and c.automatic_decision.heat),
            "heater_command": self.device.command, "heater_feedback": c.contactor,
            "active_gang": bool(session and session.timeline.active),
            "gang_confirmation": (session.timeline.active.confirmation.value
                                  if session and session.timeline.active else None),
            "gang_count": sum(s.timeline.gang_count for s in all_sessions),
            "proxy_person_signal_count": sum(
                event.kind in (Kind.PERSON_WEAK, Kind.PERSON_STRONG, Kind.INFUSION)
                for s in all_sessions for event in s.timeline.processed
            ) if c.presence_source == "proxy" else 0,
            "cooling_active": bool(session and session.after_run),
            "cooling_duration_s": cooling.required_seconds if cooling else None,
            "cooling_elapsed_s": cooling.elapsed_seconds if cooling else None,
            "cooling_remaining_s": cooling.remaining_seconds if cooling else None,
            "cooling_minimum_s": (cooling.cooling_calculation.base_minutes * 60
                                  if cooling and cooling.cooling_calculation else None),
            "operation_enabled": bool(session and session.operation_enabled),
            "recording": session is not None,
            "session_started_s": (
                (recorded.timeline.session_started_at - ORIGIN).total_seconds()
                if recorded else None
            ),
            "archived_preheat_roles": None,
            "recording_history_intact": not self.recording_history_errors,
            "ready": bool(session and session.ready_at),
            "light_override": self.device.light_output.manual_brightness,
            "light_percent": self.device.light_observation["brightness_percent"],
            "light_after_run": bool(c.light_after_run and self.now < c.light_after_run.ends_at),
            "completed_sessions": len(c.completed_sessions),
            "retained_sessions": sum(session_has_gangs(plain(s)) for s in c.completed_sessions),
            "fault_count": len(self.device.faults),
            "gang_heat_demand": c.regulation_inputs.gang_heat_demand,
            "temporary_door_heat": c.regulation_inputs.temporary_door_heat,
            "temperature_scope": temperature_control(c).scope,
        }

    async def run(self):
        # Notification delivery is another external HA transport. Keep the
        # production decision and notice archive; capture only its delivery.
        with patch("custom_components.ha_sauna.device.persistent_notification.async_create",
                   side_effect=lambda _hass, message, **kwargs: self.notifications.append(
                       {"t": self.seconds, "action": "create", "message": message, **kwargs})), \
             patch("custom_components.ha_sauna.device.persistent_notification.async_dismiss",
                   side_effect=lambda _hass, notification_id: self.notifications.append(
                       {"t": self.seconds, "action": "dismiss", "notification_id": notification_id})):
            return await self._run()

    async def _run(self):
        samples = load_samples(self.case)
        events = [(float(s["t"]), 0, i, "sample", s) for i, s in enumerate(samples)]
        events += [(float(a["t"]), 1, i, "action", a)
                   for i, a in enumerate(self.case["actions"])]
        duration = float(self.case["duration_s"])
        checkpoints = {float(x) for x in range(math.ceil(duration) + 1)} | {duration}
        for exp in self.case["expectations"]:
            checkpoints.update(exp.get("between_s", [exp.get("at_s", exp.get("t", 0))]))
            checkpoints.update(value["since_s"] for value in exp["values"].values()
                               if isinstance(value, dict) and "since_s" in value)
        events += [(t, 2, 0, "tick", None) for t in checkpoints if 0 <= t <= duration]
        events.sort(key=lambda row: row[:3])
        try:
            initial = self.case.get("initial_state", {})
            for role, spec in initial.items():
                spec = spec if isinstance(spec, dict) else {"value": spec}
                await self.input({"role": role, **spec}, initial=True)
            for role in ("heater", "light"):
                self.device.ingest(role, self.states[BINDINGS[role]], self.now, initial=True)
            self.device.refresh(self.now)
            await self.runtime.start_archive(Path(self._temporary.name) / "sessions.sqlite", "fixture")
            for t, _, _, kind, payload in events:
                if t < 0:
                    raise ValueError("Use initial_state for pre-session received values")
                self.now = ORIGIN + timedelta(seconds=t)
                transition_checks = [
                    check for check in self.transition_checks
                    if check["between_s"][0] <= t <= check["between_s"][1]
                ]
                before = self.snapshot() if transition_checks else None
                if kind == "sample":
                    await self.input(payload)
                elif kind == "action":
                    try:
                        outcome = await self.action(payload)
                    except (ValueError, AssertionError) as error:
                        self.action_results.append({"t": t, "name": payload["name"], "error": str(error)})
                        if not payload.get("expect_rejection", False):
                            raise
                    else:
                        self.action_results.append({"t": t, "name": payload["name"],
                                                    "error": None, "note": outcome})
                        if payload.get("expect_rejection", False):
                            raise AssertionError(f"Expected rejection at {t}: {payload['name']}")
                else:
                    await self.runtime.tick()
                if transition_checks:
                    # Observe the completed input handler before its simulated
                    # actuator acknowledgement or any subsequent timer step.
                    after = self.snapshot()
                    for check in transition_checks:
                        trigger = check["input_transition"]
                        counter = trigger["counter_increases"]
                        if after[counter] <= before[counter] or not all(
                            matching(before[key], value)
                            for key, value in trigger["before"].items()
                        ):
                            continue
                        self.input_transitions[check["id"]].append({
                            "t": t, "input_kind": kind, "input": payload,
                            "before": {key: before[key] for key in (*trigger["before"], counter)},
                            "after": {"t": t, **{key: after[key] for key in check["values"]}},
                            "counter_after": after[counter],
                        })
                await self.feedback()
                if kind == "tick":
                    self.snapshots.append(self.snapshot())
            self.runtime.persist()
            await self.runtime.archive.flush()
            stored = self.runtime.archive.read(now=self.now)
            self.archive_session_count = len(stored)
            self.archived_sessions = stored
            if self.recording_history_checks:
                self._verify_archived_histories(stored)
            preservation_before = self.case.get("preserve_measurements_before_s")
            if preservation_before is not None:
                self._verify_preserved_measurements(samples, stored, preservation_before)
        finally:
            # Preserve the state at failure/end before adapter teardown can
            # close an unfinished session and obscure the original failure.
            self.final_sessions = [plain(s) for s in self.runtime.controller.completed_sessions]
            self.consumer_events = [plain(e) for e in self.runtime.controller.consumer_events]
            sessions = (*self.runtime.controller.completed_sessions,
                        *((self.runtime.session,) if self.runtime.session else ()))
            self.detections = [plain(e) for s in sessions for e in s.timeline.processed]
            try:
                await self.runtime.close()
            finally:
                self._temporary.cleanup()
        return self

    def _observe_recording_history(self, sessions):
        """Preserve accumulated evidence; do not prescribe a switching schedule."""
        for session in sessions:
            current = {
                "heating": plain(session.heating.intervals),
                "contactor": plain(session.contactor_history),
                "energy": plain(session.energy),
            }
            previous = self.recording_history.get(session.session_id)
            if previous is not None:
                failures = []
                old_heat, new_heat = previous["heating"], current["heating"]
                if len(new_heat) < len(old_heat):
                    failures.append("heating intervals removed")
                else:
                    for old, new in zip(old_heat, new_heat):
                        if (old["started_at"] != new["started_at"]
                                or (old["ended_at"] is not None and old != new)):
                            failures.append("previous heating interval changed")
                            break
                if current["contactor"][:len(previous["contactor"])] != previous["contactor"]:
                    failures.append("previous contactor evidence changed")
                for key, value in previous["energy"].items():
                    if key != "accounted_at" and current["energy"][key] < value:
                        failures.append(f"accumulated energy field decreased: {key}")
                if failures:
                    self.recording_history_errors.append({
                        "t": self.seconds, "session_id": session.session_id, "errors": failures,
                    })
            self.recording_history[session.session_id] = current

    def _verify_archived_histories(self, stored):
        """The completed SQLite state must retain the same verified histories."""
        with closing(sqlite3.connect(
            f"file:{self.runtime.archive.path}?mode=ro", uri=True
        )) as db:
            for item in stored:
                identity = item["session_id"]
                row = db.execute(
                    "SELECT payload FROM sessions WHERE entry_id=? AND session_id=?",
                    ("fixture", identity),
                ).fetchone()
                payload = json.loads(row[0])
                archived = {"heating": payload["heating"]["intervals"],
                            "contactor": payload["contactor_history"],
                            "energy": payload["energy"]}
                if archived != self.recording_history.get(identity):
                    self.recording_history_errors.append({
                        "t": self.seconds, "session_id": identity,
                        "errors": ["persisted heating, contactor or energy history differs"],
                    })
        self.snapshots[-1]["recording_history_intact"] = not self.recording_history_errors

    def _verify_preserved_measurements(self, samples, stored, before_s):
        """Compare every bound input with raw rows under its original session ID."""
        start = self.first_session_started_at
        before = ORIGIN + timedelta(seconds=before_s)
        if start is None or start >= before:
            raise ValueError("The bound preservation interval needs an original session start")
        retained = self.first_session_id in {row["session_id"] for row in stored}
        expected = {role: Counter() for role in CLIMATE}
        actual = {role: Counter() for role in CLIMATE}
        unexpected_roles = Counter()
        for sample in samples:
            role = sample["role"]
            received = ORIGIN + timedelta(seconds=sample["t"])
            if role in expected and start <= received < before:
                expected[role][(received, received, sample["value"])] += 1
        with closing(sqlite3.connect(
            f"file:{self.runtime.archive.path}?mode=ro", uri=True
        )) as db:
            rows = db.execute(
                "SELECT received_at,payload FROM records "
                "WHERE entry_id=? AND session_id=? AND kind='measurement' ORDER BY id",
                ("fixture", self.first_session_id),
            )
            for received_at, payload in rows:
                received = datetime.fromisoformat(received_at)
                if not start <= received < before:
                    continue
                data = json.loads(payload)
                role = f"{data.get('position')}_{data.get('quantity')}"
                if role in actual:
                    payload_received = datetime.fromisoformat(data["received_at"])
                    actual[role][(received, payload_received, data["value"])] += 1
                else:
                    unexpected_roles[role] += 1

        def examples(difference):
            return [
                {"received_at": received.isoformat(),
                 "payload_received_at": payload_received.isoformat(),
                 "value": value, "count": count}
                for (received, payload_received, value), count in list(difference.items())[:10]
            ]

        report = {}
        for role in CLIMATE:
            missing = expected[role] - actual[role]
            extra = actual[role] - expected[role]
            report[role] = {
                "complete": (bool(expected[role]) and retained and not unexpected_roles
                             and not missing and not extra),
                "expected_count": expected[role].total(),
                "actual_count": actual[role].total(),
                "expected_duplicate_count": expected[role].total() - len(expected[role]),
                "actual_duplicate_count": actual[role].total() - len(actual[role]),
                "missing_count": missing.total(),
                "unexpected_count": extra.total(),
                "missing_examples": examples(missing),
                "unexpected_examples": examples(extra),
            }
        self.archived_preheat_comparison = {
            "session_id": self.first_session_id, "session_retained": retained,
            "from_inclusive": start.isoformat(), "until_exclusive": before.isoformat(),
            "comparison": "Bound inputs versus raw SQLite rows, including duplicate multiplicity",
            "unexpected_roles": dict(sorted(unexpected_roles.items())),
            "examples_limit_per_difference": 10, "roles": report,
        }
        self.archived_preheat_roles = sorted(role for role, result in report.items()
                                             if result["complete"])
        self.snapshots[-1]["archived_preheat_roles"] = len(self.archived_preheat_roles)

    def output_comparison(self):
        """Historical outputs describe divergence; they do not decide success."""
        times = [s["t"] for s in self.snapshots]
        comparisons = []
        for item in self.observed_outputs:
            index = bisect_right(times, item["t"]) - 1
            if index < 0:
                continue
            role = item["role"]
            field = {"heater_feedback": "heater_feedback", "light_feedback": "light_percent"}[role]
            comparisons.append({"t": item["t"], "role": role,
                                "historical": item["value"],
                                "replayed": self.snapshots[index][field],
                                "replayed_at": times[index]})
        return comparisons

    def evaluate(self):
        results = []
        times = [s["t"] for s in self.snapshots]
        for expected in self.case["expectations"]:
            start, end = expected.get("between_s", [expected.get("at_s", 0)] * 2)
            transitions = self.input_transitions.get(expected["id"])
            rows = ([item["after"] for item in transitions] if transitions is not None
                    else [s for s in self.snapshots if start <= s["t"] <= end])
            if not rows:
                error = ("No input handler reached the bound transition preconditions"
                         if transitions is not None else "No sampled checkpoint")
                results.append({"id": expected["id"], "passed": False, "error": error})
                continue
            fields = expected["values"]
            unknown = set(fields) - set(rows[0])
            if unknown:
                raise ValueError(f"Unknown evaluation fields: {unknown}")
            baselines, predicates = {}, {}
            for key, predicate in fields.items():
                if isinstance(predicate, dict) and "since_s" in predicate:
                    since = predicate["since_s"]
                    index = bisect_right(times, since) - 1
                    if index < 0 or since > start:
                        raise ValueError(f"Invalid comparison origin: {since}")
                    baselines[key] = self.snapshots[index][key]
                    predicate = {k: v for k, v in predicate.items() if k != "since_s"}
                predicates[key] = predicate
            compared = [{"t": row["t"], **{
                key: row[key] - baselines[key] if key in baselines else row[key]
                for key in fields
            }} for row in rows]
            failures = [row for row in compared
                        if not all(matching(row[k], v) for k, v in predicates.items())]
            passed = len(failures) < len(rows) if expected.get("eventually") else not failures
            results.append({"id": expected["id"], "passed": passed,
                            "expected": fields, "reason": expected["reason"],
                            "window": [start, end],
                            **({"input_transition_count": len(transitions)}
                               if transitions is not None else {}),
                            "first_mismatch": ({k: failures[0][k] for k in ("t", *fields)}
                                               if failures else None)})
        retention = [e for e in self.case["expectations"]
                     if "retained_sessions" in e["values"]]
        if retention:
            final = max(retention, key=lambda e: e.get("between_s", [e.get("at_s", 0)])[-1])
            expected = final["values"]["retained_sessions"]
            results.append({
                "id": final["id"] + ":sqlite", "passed": matching(self.archive_session_count, expected),
                "expected": {"archive_session_count": expected},
                "actual": {"archive_session_count": self.archive_session_count},
                "reason": "The final bound retention expectation also applies to the persisted SQLite archive.",
            })
        return results
