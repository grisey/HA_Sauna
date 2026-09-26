"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(
  fs.readFileSync(
    path.join(__dirname, "../custom_components/ha_sauna/panel.js"),
    "utf8",
  ),
  {
    HTMLElement: class {},
    customElements: {
      get: () => undefined,
      define: (_name, value) => {
        Panel = value;
      },
    },
    Date,
    Map,
    Set,
    Math,
    Number,
    String,
    Object,
    Array,
    Infinity,
  },
);

function state(admin) {
  return {
    now: "2026-01-01T00:00:05Z",
    operation_enabled: true,
    phase: "nachlauf",
    session: {
      timeline: { active: null, door: "closed", completed: [] },
      heating: { elapsed_seconds: 0 },
      deadlines: [],
      after_run: {
        phase_id: "pending-token",
        pending_start: true,
        ends_at: null,
        duration_seconds: 30,
        elapsed_seconds: 0,
      },
    },
    configuration: {
      control_mode: "automatic",
      program_mode: "constant",
      selected_program_id: null,
      temperature_programs: [],
      parameters: {
        preset_count: 0,
        preset_start_c: 70,
        preset_step_c: 5,
        target_temperature_c: 80,
        final_temperature_c: 90,
        temperature_gangs: 3,
        session_light_brightness_percent: 50,
        readiness_hysteresis_c: 3,
      },
    },
    parameters: [{ key: "target_temperature_c", minimum: 60, maximum: 100 }],
    measurements: [],
    measurement_status: {},
    mechanical_timer: { state: "idle", remaining_seconds: 0 },
    manual_controls: { light: {}, heater: {} },
    permissions: {
      admin,
      control: true,
      heater: true,
      light: true,
      temperature: true,
      program: true,
    },
    issues: [],
    start_errors: [],
    detection_channels: [],
    target_temperature: 80,
    thermostat_target: 80,
    heating_feedback: false,
    heating_observation: { source: "unknown" },
    energy_kwh: 0,
    energy_source: "estimated",
    phase_timer: {
      kind: "after_run",
      label: "Ofenkühlung wartet auf Schütz-Aus",
      seconds: null,
      mode: "pending",
    },
    start_availability: { blocker: { kind: "after_run_pending" } },
    decision_text: "Die Ofenkühlung läuft; der Ofen bleibt aus.",
  };
}

function render(admin) {
  const nodes = new Map();
  const panel = Object.assign(Object.create(Panel.prototype), {
    state: state(admin),
    hass: { user: { name: "Admin", is_admin: admin } },
    progressionDraft: null,
    manualLightDraft: null,
    $: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
      return nodes.get(selector);
    },
  });
  panel.drawCurrent();
  return nodes.get("#details").innerHTML;
}

const admin = render(true);
assert.match(admin, /Ofenkühlung wartet auf Schütz-Aus/);
assert.match(admin, /data-action="end-phase:after_run:pending-token"/);
assert.doesNotMatch(admin, /noch 0:00 Minuten/);

const readOnly = render(false);
assert.match(readOnly, /data-action="end-phase:after_run:pending-token"[^>]*disabled/);

(async () => {
  const calls = [];
  const panel = Object.assign(Object.create(Panel.prototype), {
    entry: "entry-1",
    state: { permissions: { admin: true } },
    api: async (...args) => calls.push(args),
    refresh: async () => {},
    message: () => {},
  });
  await panel.action("end-phase:after_run:pending-token");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/finish_phase", "POST", { purpose: "after_run", token: "pending-token" }],
  ]);
})();
