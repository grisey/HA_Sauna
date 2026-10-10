"""Lokale Vorschau des echten Panels und Ablaufkerns mit synthetischen Eingängen.

Start: python3 tools/local_preview.py. Keine HA-Verbindung, keine Geräteausgabe.
"""
# Imports follow the script-local repository path bootstrap.
# ruff: noqa: E402
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.ha_sauna.appearance import APPEARANCE_CATALOG
from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.history import HISTORY_CONTEXT_SECONDS, measurement_window
from custom_components.ha_sauna.core.light import normal_brightness
from custom_components.ha_sauna.core.light_control import light_plan, select_light
from custom_components.ha_sauna.core.light_output import LightOutput, LightQuantizer
from custom_components.ha_sauna.core.parameters import (
    EDITABLE_DEFINITIONS, LIVE_TEMPERATURE_KEYS, Parameters,
)
from custom_components.ha_sauna.core.phases import project_session
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.presentation import (
    decision_message, public_measurement, public_phase_projection, public_session, public_state,
    resolve_cooling_token,
)
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import (
    async_set_appearance, async_set_button_gesture, async_set_control_mode,
    async_set_parameters, async_set_program,
    async_set_temperature_steps,
)


class Preview:
    def __init__(self):
        self.admin = True
        self.reset("verlauf")

    def reset(self, scenario):
        self.archive_revision = getattr(self, "archive_revision", 0) + 1
        self.scenario = scenario
        self.now = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
        self.records = []
        self.sessions = {}
        self.decision_cursor = self.consumer_cursor = 0
        self.light = 0
        self.sun_elevation = None
        self.humidity = 24.5
        self.follow_feedback = True
        self.button_pressed = False
        self.runtime = SaunaRuntime(Configuration(
            Bindings({"upper_temperature": "sensor.demo_temperatur",
                      "upper_humidity": "sensor.demo_feuchte",
                      "heater": "switch.demo_ofen", "light": "light.demo_sauna",
                      "control_input": "event.demo_taster",
                      "presence": "binary_sensor.demo_fp300"}),
            Parameters({"target_temperature_c": 80}), presence_source="ha_presence",
            control_input_mode="button",
            control_mode="manual" if scenario == "manuell" else "automatic",
        ), clock=lambda: self.now)
        self.entry = SimpleNamespace(
            runtime_data=self.runtime, options=self.runtime.configuration.as_options(),
        )
        self.hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=self.save_options,
        ))
        self.c = self.runtime.controller
        self.light_output = LightOutput(self.c.parameters)
        self.light_quantizer = LightQuantizer()
        if scenario == "manuell":
            self.select_light(0)
        self.now -= timedelta(seconds=HISTORY_CONTEXT_SECONDS)
        self.c.set_temperature(58, self.now)
        self.sample()
        self.step(HISTORY_CONTEXT_SECONDS, temperature=62)
        if scenario != "manuell":
            self.c.set_operation(True, self.now, session_id="lokale-vorschau")
        self.record("presence_source", {"configured_source":"ha_presence", "effective_source":"ha_presence", "entity_id":"binary_sensor.demo_fp300"})
        self.presence("off")
        self.sample()
        if scenario in ("bereit", "gang", "verlauf", "archiv", "ausfall"):
            self.step(600, temperature=80)
        if scenario in ("gang", "verlauf", "archiv", "ausfall"):
            self.step(120)
            self.door(Kind.DOOR_OPEN)
            self.step(3)
            self.presence("on")
            self.step(4)
            self.door(Kind.DOOR_CLOSE)
            self.step(240, temperature=82)
        if scenario in ("verlauf", "archiv"):
            self.door(Kind.INFUSION)
            self.humidity = 39
            self.step(180, temperature=83)
            self.door(Kind.DOOR_OPEN)
            self.step(4)
            self.presence("off")
            self.step(1)
            self.door(Kind.VENTILATION)
            self.step(6)
            self.door(Kind.DOOR_CLOSE)
            self.step(160, temperature=79)
        if scenario == "archiv":
            self.c.finish_session(self.now)
            self.sample()
            self.step(HISTORY_CONTEXT_SECONDS, temperature=65)
        if scenario == "ausfall":
            self.presence("unavailable")
        self.sample()

    @staticmethod
    def save_options(entry, *, options):
        changed = entry.options != options
        entry.options = options
        return changed

    def record(self, kind, payload):
        payload = plain(payload)
        session_id = payload.get("session_id") or (
            self.c.session.session_id if self.c.session else None
        )
        self.records.append({"id": len(self.records)+1, "kind": kind,
                             "session_id": session_id,
                             "received_at": self.now.isoformat(), "payload": payload})

    def save_sessions(self):
        for session in self.c.completed_sessions:
            previous = self.sessions.get(session.session_id)
            if previous and previous["session"].get("ended_at"):
                continue
            configuration = (
                previous["session"]["configuration"] if previous
                else self.runtime.configuration.as_options()
            )
            self.save_session(session, configuration)
        if self.c.session:
            self.save_session(self.c.session, self.runtime.configuration.as_options())

    def save_session(self, session, configuration):
        saved = plain(session)
        saved["configuration"] = configuration
        saved["measurement_ttl_seconds"] = configuration["parameters"]["sensor_timeout_seconds"]
        self.sessions[session.session_id] = {
            "session": saved,
            "phase_projection": plain(project_session(session, self.now)),
        }

    def archive(self, session_id=None, after=0):
        if session_id is None:
            return [
                {"session_id": item["session"]["timeline"]["session_id"],
                 "started_at": item["session"]["timeline"]["session_started_at"],
                 "ended_at": item["session"].get("ended_at")}
                for item in reversed(self.sessions.values())
            ]
        stored = self.sessions.get(session_id)
        if stored is None:
            return None
        session = dict(stored["session"])
        projection = stored["phase_projection"]
        window = measurement_window(
            datetime.fromisoformat(session["timeline"]["session_started_at"]),
            datetime.fromisoformat(session["ended_at"]) if session.get("ended_at") else None,
            self.now,
        )
        records = [r for r in self.records
                   if r["id"] > after and (
                       r["session_id"] == session_id or (
                           r["kind"] == "measurement"
                           and window["started_at"] <= r["received_at"] <= window["ended_at"]
                       )
                   )]
        if not self.admin:
            session = public_session(session)
            projection = public_phase_projection(projection)
            records = [
                {**r, "payload": public_measurement(r["payload"])}
                for r in records if r["kind"] in ("measurement", "source_snapshot", "phase")
            ]
        return {"session": session, "phase_projection": projection, "measurement_window": window,
                "records": records, "next_after": None}

    def sample(self):
        if self.follow_feedback:
            demand = bool(self.c.last_decision and self.c.last_decision.heat)
            self.c.report_contactor(demand, self.now)
            self.c.report_heating(demand, self.now)
        self.sample_light()
        self.runtime.persist()
        for d in self.c.decisions[self.decision_cursor:]:
            self.record("decision", d)
        self.decision_cursor = len(self.c.decisions)
        for event in self.c.consumer_events[self.consumer_cursor:]:
            self.record("consumer_event", event)
        self.consumer_cursor = len(self.c.consumer_events)
        for quantity, value in (("temperature", self.c.temperature), ("humidity", self.humidity)):
            self.record("measurement", {"position":"upper", "quantity":quantity,
                "value":value, "raw_value":str(value), "received_at":self.now,
                "measured_at":self.now, "available":True})
        self.save_sessions()

    def step(self, seconds, temperature=None):
        # Refresh synthetic samples along the simulated time axis.
        end = self.now + timedelta(seconds=seconds)
        initial = self.c.temperature
        origin = self.now
        while self.now < end:
            self.now = min(end, self.now + timedelta(seconds=10))
            value = initial if temperature is None else initial + (temperature-initial)*(self.now-origin).total_seconds()/seconds
            self.c.set_temperature(value, self.now)
            asyncio.run(self.runtime.tick())
            self.sample()

    async def button_event(self, *events):
        async with self.runtime.serialized():
            for event in events:
                await self.runtime._handle_button_event(event, self.now)
            await self.runtime._cycle()

    def button(self, action):
        previous_mode = self.c.control_mode
        if action == "button_release":
            asyncio.run(self.button_event("release"))
            self.button_pressed = False
        else:
            if self.button_pressed:
                raise ValueError("Der Taster ist noch gedrückt. Zuerst loslassen.")
            if action in ("button_short", "button_double", "button_triple"):
                asyncio.run(self.button_event("press", action.removeprefix("button_"), "release"))
            else:
                asyncio.run(self.button_event("press"))
                self.button_pressed = True
                self.step(self.c.parameters.values["button_hold_seconds"])
                asyncio.run(self.button_event("long"))
        if previous_mode != self.c.control_mode:
            self.select_light(0 if self.c.control_mode == "manual" else None)
        self.sample()

    def door(self, kind):
        if self.c.session is None:
            raise ValueError("Ohne laufende Sitzung wird kein Tür- oder Aufgussereignis zugeordnet.")
        self.runtime._process_event(Event(f"demo:{kind}:{self.now.isoformat()}",
            self.c.session.session_id, kind, self.now, self.now))
        self.sample()

    def presence(self, value):
        report = binary_presence("binary_sensor.demo_fp300", value, self.now, self.now)
        asyncio.run(self.runtime.accept_presence(report))
        self.record("presence", report)
        self.sample()

    @property
    def light_manual(self):
        return self.light_output.manual_brightness

    def normal_light(self):
        return normal_brightness(self.sun_elevation, self.c.parameters)

    def select_light(self, value):
        self.light_output.parameters = self.c.parameters
        select_light(self.light_output, self.c, value, self.now, self.normal_light())

    def sample_light(self):
        output = self.light_output
        output.parameters = self.c.parameters
        if self.c.control_mode == "manual":
            output.keep_manual()
        else:
            output.expire_manual(self.now)
        if (self.runtime.button_start_hold_active
                or self.runtime._button_hold_session_id is not None):
            output.return_to_automatic()
            self.light = self.observed_light()
            return
        after_run = self.c.light_after_run
        if (self.c.control_mode == "manual"
                and (after_run is None or self.now >= after_run.ends_at)
                and output.manual_brightness is None):
            return
        plan = light_plan(output, self.c, self.now, self.light, self.normal_light())
        self.light = self.light_quantizer.quantize(
            plan, self.c.parameters.values["light_output_hysteresis_percent"],
        )

    def observed_light(self):
        if self.runtime.button_start_hold_active:
            return self.c.parameters.values["session_light_brightness_percent"]
        if self.runtime._button_hold_session_id is not None:
            return 0
        return self.light

    def state(self):
        c, session = self.c, self.c.session
        configuration = self.runtime.configuration
        active = session.timeline.active if session else None
        light_observation = self.observed_light()
        measurements = [{"position":"upper", "quantity":q, "value":v,
                         "received_at":self.now, "measured_at":self.now}
                        for q,v in (("temperature", c.temperature), ("humidity",self.humidity))]
        result = plain({
            "now": self.now, "phase":c.phase, "session":session,
            "presence":self.runtime.presence_status, "rule_inputs":c.regulation_inputs,
            "phase_projection":c.phase_projection(self.now), "configuration":configuration.as_options(),
            "appearance":configuration.appearance, "appearance_catalog":APPEARANCE_CATALOG,
            "frontend_defaults":section("frontend"),
            "button_session_gestures":configuration.available_button_session_gestures,
            "environment": {
                "configured": True,
                "station": {"name": "Beispielstation", "id": "demo"},
                "condition": "partlycloudy",
                "values": [
                    {"key": key, "label": label, "state": "current", "value": value,
                     "unit": unit, "available": True, "last_updated": self.now}
                    for key, label, value, unit in (
                        ("temperature", "Temperatur", 16.4, "°C"),
                        ("humidity", "Relative Luftfeuchte", 64, "%"),
                        ("absolute_humidity", "Absolute Luftfeuchte", 8.9, "g/m³"),
                        ("dew_point", "Taupunkt", 9.6, "°C"),
                        ("pressure", "Luftdruck", 1016.2, "hPa"),
                        ("wind_speed", "Windgeschwindigkeit", 12.6, "km/h"),
                        ("wind_direction", "Windrichtung", "W", None),
                        ("precipitation", "Niederschlag", 0, "mm/h"),
                    )
                ],
                "measurement_time": self.now - timedelta(minutes=10),
                "forecast_time": self.now,
                "source": {"mode": "mixed_data", "interpolated": True},
                "updated_at": self.now,
            },
            "last_session":next((s for s in reversed(c.completed_sessions) if s.timeline.gang_count), None),
            "archive_revision":self.archive_revision, "preview_scenario":self.scenario,
            "preview_button":{"pressed":self.button_pressed,
                              "hold_seconds":c.parameters.values["button_hold_seconds"],
                              "start_hold":self.runtime.button_start_hold_active},
            "measurement_ttl_seconds":configuration.parameters.values["sensor_timeout_seconds"],
            "parameters":[{**asdict(d), "minimum":configuration.parameters.minimum_for(d.key),
                           "live_editable":d.key in LIVE_TEMPERATURE_KEYS} for d in EDITABLE_DEFINITIONS],
            "configuration_locked":session is not None,
            "operation_enabled":bool(session and session.operation_enabled),
            "heating_feedback":c.feedback, "heating_observation":{"source":"contactor"},
            "energy_kwh":session.energy.total_kwh if session else 0,
            "energy_source":session.energy.source if session else "estimated",
            "thermostat_target":c.thermostat_target, "target_temperature":c.target_temperature,
            "next_gang_temperature":c.next_gang_temperature,
            "thermostat_restart_temperature":c.thermostat_restart_temperature,
            "phase_timer":phase_timer(c,self.now),
            "start_availability":start_availability(c,self.now), "light_after_run":c.light_after_run,
            "start_errors":[], "issues":[], "decision_text":decision_message(c.last_decision),
            "measurement_status":{f"upper_{q}":{"state":"current"} for q in ("temperature","humidity")},
            "regulation_temperature_position":"upper", "measurement_positions":["upper"],
            "gang_count":session.timeline.gang_count if session else 0,
            "gang_confirmation":active.confirmation if active else None,
            "gang_duration_seconds":active.elapsed_seconds(self.now) if active else None,
            "decision":c.last_decision, "measurements":measurements, "faults":{}, "protection":[],
            "inhibits":[], "detection_channels":[], "detector_trace":None,
            "manual_controls":{"heater":{"manual":c.heater_override,
                "commanded":c.last_decision.heat if c.last_decision else None,
                "blocked_on_reason":self.runtime.heater_on_blocked_reason,
                "observation":{"available":c.contactor is not None,"on":c.contactor},
                "override_ends_at":c.heater_override_ends_at,
                "automatic":c.automatic_decision.heat if c.automatic_decision else None},
                "light":{"observation":{"available":True,"brightness_percent":light_observation},
                         "manual":self.light_manual,
                         "manual_feedback_target":round(self.light_manual)
                         if self.light_manual is not None else None,
                         "override_ends_at":self.light_output.manual_ends_at,
                         "automatic":self.light_output.last_automatic_brightness,
                         "normal":self.normal_light()}},
            "permissions":{k:True for k in ("admin","control","temperature","program","light","heater")},
        })
        result["permissions"]["admin"] = self.admin
        result = result if self.admin else public_state(result)
        result["preview_feedback"] = {"following": self.follow_feedback, "on": c.contactor}
        return result

    def action(self, path, body):
        if path == "/simulate":
            action = body["action"]
            if action == "role":
                self.admin = body["value"] == "admin"
                return {}
            if action.startswith("scenario:"):
                self.reset(action.split(":")[1])
                return {}
            if action in ("button_short", "button_double", "button_triple", "button_hold", "button_release"):
                self.button(action)
                return {}
            if action != "step":
                self.step(1)
            if action in ("door_open", "door_close", "infusion", "ventilation_confirmed"):
                self.door(Kind(action))
            elif action in ("on", "off", "unavailable"):
                self.presence(action)
            elif action == "step":
                self.step(60)
            elif action == "temperature":
                self.c.set_temperature(float(body["value"]), self.now)
            elif action == "feedback":
                value = body["value"]
                if value == "follow":
                    self.follow_feedback = True
                elif value is None or isinstance(value, bool):
                    self.follow_feedback = False
                    self.c.report_contactor(value, self.now)
                    self.c.report_heating(value, self.now)
                else:
                    raise ValueError("Ungültige Ofenrückmeldung")
            else:
                raise ValueError("Unbekannter Simulationseingang")
        elif path.endswith("/control"):
            asyncio.run(self.runtime.set_operation(body["enabled"]))
        elif path.endswith("/control-mode"):
            previous_mode = self.c.control_mode
            asyncio.run(async_set_control_mode(self.hass, self.entry, body["mode"]))
            if previous_mode != self.c.control_mode:
                self.select_light(0 if self.c.control_mode == "manual" else None)
            self.sample()
            return {"configuration":self.runtime.configuration.as_options()}
        elif path.endswith("/finish_phase"):
            if (
                not isinstance(body, dict)
                or set(body) != {"purpose", "token"}
                or body["purpose"] != "after_run"
                or not isinstance(body["token"], str)
                or not body["token"]
            ):
                raise ValueError("Bitte eine laufende Ofenkühlung auswählen.")
            self.c.finish_phase(
                body["purpose"], resolve_cooling_token(self.c.session, body["token"]),
                self.now,
            )
        elif path.endswith("/finish-session"):
            self.c.finish_session_gap(body["token"], self.now)
        elif path.endswith("/heater"):
            asyncio.run(self.runtime.set_heater_override(body["value"]))
        elif path.endswith("/light"):
            value = body["value"]
            actual = self.observed_light()
            unchanged = (
                (value is False and actual == 0)
                or (value is True and actual > 0)
                or (not isinstance(value, bool) and isinstance(value, (int, float))
                    and value == round(actual))
            )
            if not unchanged:
                self.select_light(value)
        elif path.endswith("/button-gesture"):
            if not isinstance(body, dict) or set(body) != {"gesture"}:
                raise ValueError("Tastergeste fehlt oder ist ungültig")
            configuration = asyncio.run(async_set_button_gesture(self.hass, self.entry, body["gesture"]))
            self.button_pressed = False
            return {"success":True, "button_session_gesture":configuration.button_session_gesture}
        elif path.endswith("/appearance"):
            appearance = asyncio.run(async_set_appearance(self.hass, self.entry, body))
            return {"appearance": appearance}
        elif path.endswith("/parameters"):
            if not self.admin:
                raise ValueError("Administratorrechte erforderlich")
            display_keys = {
                definition.key for definition in EDITABLE_DEFINITIONS
                if definition.settings_group == "appearance"
            }
            if not isinstance(body, dict) or not body or set(body) - display_keys:
                raise ValueError("Ungültige Darstellungseinstellungen")
            asyncio.run(async_set_parameters(self.hass, self.entry, body, partial=True))
            # The preview has no HA options listener. Only display choices are
            # accepted here; publish the saved configuration without a reload.
            self.runtime.configuration = Configuration.from_options(self.entry.options)
            self.c.parameters = self.runtime.configuration.parameters
            self.runtime.reconfiguring = False
        elif path.endswith("/temperature"):
            if not body or set(body) - LIVE_TEMPERATURE_KEYS:
                raise ValueError("Ungültige Temperatureinstellung")
            asyncio.run(async_set_parameters(self.hass, self.entry, body, partial=True))
        elif path.endswith("/program"):
            if set(body) == {"profile"} and isinstance(body["profile"], str):
                asyncio.run(async_set_program(self.hass, self.entry, body["profile"]))
            elif set(body) == {"target_temperature_c"}:
                asyncio.run(async_set_parameters(
                    self.hass, self.entry, body, partial=True, explicit_target=True,
                    program_mode="constant", new_program=True,
                ))
            elif set(body) == {"temperature_steps"}:
                asyncio.run(async_set_temperature_steps(self.hass, self.entry, body["temperature_steps"]))
            elif set(body) == {"target_temperature_c", "final_temperature_c", "temperature_gangs"}:
                asyncio.run(async_set_parameters(
                    self.hass, self.entry, body, partial=True, explicit_target=False,
                    program_mode="progressive", new_program=True,
                ))
            else:
                raise ValueError("Ungültiges Temperaturprogramm")
        else:
            raise ValueError("Diese Einstellungsänderung ist in der lokalen Vorschau nicht angebunden.")
        self.sample()
        return {"parameters":self.c.parameters.as_dict(),
                "target_temperature":self.c.target_temperature,
                "next_gang_temperature":self.c.next_gang_temperature,
                "program_mode":self.runtime.configuration.program_mode,
                "selected_program_id":self.runtime.configuration.selected_program_id,
                "temperature_steps":self.runtime.configuration.temperature_steps}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, payload, content_type="application/json"):
        data = json.dumps(plain(payload), ensure_ascii=False).encode() if content_type=="application/json" else payload
        self.send_response(200)
        self.send_header("Content-Type",content_type)
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/panel.js", "/preview.js"):
            file = ROOT / ("custom_components/ha_sauna/panel.js" if path=="/panel.js" else "tools/preview/"+("index.html" if path=="/" else "preview.js"))
            self.send(file.read_bytes(), "text/html; charset=utf-8" if path=="/" else "text/javascript; charset=utf-8")
        elif path.endswith("/state"):
            self.send(preview.state())
        elif path.endswith("/archive"):
            query = parse_qs(parsed.query)
            result = preview.archive(query.get("session_id", [None])[0],
                                     int(query.get("after", [0])[0]))
            if result is None:
                self.send_error(404)
                return
            self.send(result)
        else:
            self.send_error(404)

    def do_PATCH(self):
        if urlparse(self.path).path != "/preview/parameters":
            self.send_error(405)
            return
        self.do_POST()

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            self.send(preview.action(urlparse(self.path).path,body))
        except (ValueError, KeyError, TypeError) as error:
            self.send_response(400)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error":str(error)}).encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv)>1 else 8765
    preview = Preview()
    print(f"Lokale Vorschau: http://127.0.0.1:{port} · synthetische Daten", flush=True)
    HTTPServer(("127.0.0.1",port), Handler).serve_forever()
