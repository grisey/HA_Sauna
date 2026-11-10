"use strict";
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const {
  parameterValues: fixtureValues,
  parameterDefinition: fixtureDefinition,
} = require("./catalog_fixture.cjs");
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

// GE1 (Nutzer 10.11.2026): Gang von Hand beenden auf der Steuerungsseite.
function state({
  mode = "automatic",
  control = true,
  active = true,
  afterRun = false,
  calibration = null,
} = {}) {
  return {
    calibration: calibration ? { active: calibration } : {},
    now: "2026-01-01T00:20:00Z",
    operation_enabled: true,
    phase: active ? "saunagang" : "bereit",
    session: {
      timeline: {
        active: active
          ? {
              gang_id: "s:gang/1",
              started_at: "2026-01-01T00:10:00Z",
              confirmation: "confirmed",
              infusion_events: [{ event_id: "w" }],
            }
          : null,
        door: "closed",
        completed: [],
      },
      heating: { elapsed_seconds: 0 },
      deadlines: [],
      after_run: afterRun
        ? {
            phase_id: "cool-token",
            pending_start: true,
            ends_at: null,
            duration_seconds: 30,
            elapsed_seconds: 0,
          }
        : null,
    },
    configuration: {
      control_mode: mode,
      program_mode: "constant",
      selected_program_id: null,
      temperature_programs: [],
      parameters: fixtureValues(),
    },
    parameters: [fixtureDefinition("target_temperature_c")],
    measurements: [],
    measurement_status: {},
    manual_controls: { light: {}, heater: {} },
    permissions: {
      admin: false,
      control,
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
    thermostat_restart_temperature: 77,
    heating_feedback: true,
    heating_observation: { source: "unknown" },
    energy_kwh: 0,
    energy_source: "estimated",
    phase_timer: { kind: "gang", seconds: 600 },
    start_availability: {},
    decision_text: "",
  };
}

function render(options) {
  const nodes = new Map();
  const panel = Object.assign(fixturePanel(Panel), {
    state: state(options),
    hass: { user: { name: "Nutzer", is_admin: false } },
    progressionDraft: null,
    manualLightDraft: null,
    $: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
      return nodes.get(selector);
    },
  });
  panel.drawCurrent();
  return nodes.get("#current").innerHTML;
}

// Automatik: neben Ein/Aus wie „Kühlung beenden“ (G16).
const automatic = render();
assert.match(
  automatic,
  /class="operation-control split"[\s\S]*Ausschalten<\/button><button class="tile operation" data-action="end-gang:s%3Agang%2F1" >Gang beenden<\/button><\/div>/,
);
assert.match(
  render({ afterRun: true }),
  /Gang beenden<\/button><button[^>]*data-action="end-phase:after_run:cool-token"[^>]*>Kühlung beenden/,
);
// Ohne laufenden Gang keine Taste.
assert.doesNotMatch(render({ active: false }), /end-gang:/);
assert.doesNotMatch(render({ active: false }), /class="operation-control split"/);
// Handbetrieb: eigene Aktionszeile nur während eines Gangs (GE1-4).
const manual = render({ mode: "manual" });
assert.match(
  manual,
  /<div class="tiles"><div class="operation-control"><button class="tile operation" data-action="end-gang:s%3Agang%2F1" >Gang beenden<\/button><\/div><\/div>/,
);
assert.doesNotMatch(manual, /data-action="(?:operation|finish-session:|end-phase:)/);
assert.doesNotMatch(
  render({ mode: "manual", active: false }),
  /end-gang:|class="tiles"/,
);
// Ohne Steuerrecht gesperrt.
assert.match(
  render({ control: false }),
  /data-action="end-gang:s%3Agang%2F1" disabled>Gang beenden/,
);

// GE1a: während einer Kalibrierung gesperrt, mit Grund in der Zustandszeile.
const calibrating = render({
  calibration: { kind: "module", module: "A1", step: 1, steps: 4, waiting: "reply" },
});
assert.match(calibrating, /data-action="end-gang:s%3Agang%2F1" disabled>Gang beenden/);
assert.match(calibrating, /„Gang beenden“ ist währenddessen gesperrt/);
assert.doesNotMatch(render(), /währenddessen gesperrt/);

(async () => {
  const calls = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    state: { permissions: { admin: false, control: true } },
    api: async (...args) => calls.push(args),
    refresh: async () => {},
    message: () => {},
  });
  await panel.action("end-gang:s%3Agang%2F1");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/end_gang", "POST", { gang_id: "s:gang/1" }],
  ]);
  panel.state.calibration = { active: { kind: "module" } };
  await panel.action("end-gang:s%3Agang%2F1");
  assert.equal(calls.length, 1);
  panel.state.calibration = {};
  panel.state.permissions.control = false;
  await panel.action("end-gang:s%3Agang%2F1");
  assert.equal(calls.length, 1);
})();
