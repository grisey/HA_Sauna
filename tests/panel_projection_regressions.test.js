"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const panelPath = process.env.PANEL_PATH || "custom_components/ha_sauna/panel.js";
let Panel;
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_name, value) => (Panel = value) },
});

const start = "2026-09-22T15:00:00+00:00";
const end = "2026-09-22T16:00:00+00:00";
const session = {
  ended_at: end,
  configuration: { parameters: { sensor_timeout_seconds: 30 } },
  heating: { intervals: [] },
  timeline: {
    completed: [],
    active: null,
    processed: [
      { kind: "ventilation_confirmed", effective_at: "2026-09-22T15:10:00+00:00" },
      { kind: "door_close", effective_at: "2026-09-22T15:20:00+00:00" },
    ],
  },
};
const annotationModel = () => {
  const domainStart = Date.parse(start),
    domainEnd = Date.parse(end),
    left = 65,
    right = 1135,
    top = 18,
    bottom = 435;
  return {
    start: domainStart,
    end: domainEnd,
    left,
    right,
    top,
    bottom,
    x: (time) =>
      left + ((time - domainStart) / (domainEnd - domainStart)) * (right - left),
  };
};

function panel(projection) {
  const value = Object.create(Panel.prototype);
  value.state = { now: end, configuration: session.configuration };
  value.shown = { records: [], phase_projection: projection };
  value.historyRecords = () => [];
  return value;
}

test("ventilation remains visible beside an exclusive phase projection", () => {
  const projection = {
    intervals: [
      { started_at: start, ended_at: end, phase: "aufheizen", complete: true },
    ],
  };
  const svg = panel(projection).historyAnnotations(annotationModel(), session, []);
  assert.match(svg, /class="vent"/);
  assert.match(svg, /class="heat"/);
});

test("projection supplies the gang rectangle without a duplicate fallback gang", () => {
  const gang = {
    gang_id: "g-1",
    started_at: "2026-09-22T15:30:00+00:00",
    ended_at: "2026-09-22T15:40:00+00:00",
    infusion_events: [],
  };
  const svg = panel({
    intervals: [
      {
        started_at: gang.started_at,
        ended_at: gang.ended_at,
        phase: "saunagang",
        source_id: "g-1",
        complete: true,
      },
    ],
  }).historyAnnotations(annotationModel(), session, [gang]);
  assert.equal((svg.match(/class="gang provisional"/g) || []).length, 1);
});

test("details render includes the reachable presence and rule card", () => {
  const nodes = {};
  const defaults = JSON.parse(fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"));
  const state = {
    frontend_defaults: defaults.frontend,
    appearance_catalog: defaults.appearance,
    now: end,
    phase: "bereit",
    operation_enabled: true,
    session: {
      timeline: {
        active: null,
        door: "closed",
        completed: [],
        session_started_at: start,
      },
      heating: { elapsed_seconds: 0 },
      deadlines: [],
      energy: {},
    },
    configuration: {
      parameters: { preset_count: 0, preset_start_c: 80, preset_step_c: 1 },
      control_mode: "automatic",
      program_mode: "constant",
    },
    parameters: [{ key: "target_temperature_c", minimum: 60, maximum: 100 }],
    measurements: [],
    measurement_status: {},
    mechanical_timer: { state: "idle" },
    permissions: {
      admin: true,
      control: true,
      temperature: true,
      program: true,
      light: true,
      heater: true,
    },
    start_errors: [],
    issues: [],
    target_temperature: 80,
    heating_limit_seconds: 0,
    manual_controls: { light: {}, heater: {} },
    start_availability: {},
    phase_timer: null,
    presence: {
      configured_source: "ha_presence",
      current: {
        available: true,
        occupancy: "unknown",
        assertion: "proxy_retraction",
        effective_at: start,
        received_at: start,
      },
      external: {},
    },
    rule_inputs: { gang_heat_demand: true, temporary_door_heat: false },
  };
  const value = Object.assign(Object.create(Panel.prototype), {
    state,
    hass: { user: { is_admin: true } },
    $: (selector) => (nodes[selector] ||= {}),
    updateMarkup: (selector, markup) => ((nodes[selector] ||= {}).innerHTML = markup),
    temperatureBounds: () => ({ minimum: 60, maximum: 100 }),
    programChoice: () => "constant",
    programMode: () => "constant",
    programBounds: () => ({
      minimum: 60,
      maximum: 100,
      gangMinimum: 1,
      gangMaximum: 8,
    }),
    programApplyButton: () => "",
    temperatureTickMarks: () => "",
    temperatureArcPoint: () => ({ x: 0, y: 0 }),
    clampTemperature: (number) => number,
  });
  value.drawCurrent();
  assert.match(nodes["#details"].innerHTML, /Präsenz und Regelursache/);
  assert.match(nodes["#details"].innerHTML, /Externe Präsenz \(vorbereitet\)/);
  assert.match(
    nodes["#details"].innerHTML,
    /Proxy zurückgenommen; keine beobachtete Abwesenheit/,
  );

  state.measurement_positions = ["lower"];
  state.regulation_temperature_position = "lower";
  state.measurements = [
    { position: "lower", quantity: "temperature", value: 67 },
    { position: "lower", quantity: "humidity", value: 38 },
  ];
  state.measurement_status = {
    lower_temperature: { state: "current" },
    lower_humidity: { state: "current" },
  };
  value.drawCurrent();
  assert.match(nodes["#current"].innerHTML, /67 °C/);
  assert.match(nodes["#current"].innerHTML, /38 %/);
  assert.doesNotMatch(nodes["#current"].innerHTML, /Messung unten/);

  state.measurement_positions = ["upper", "lower"];
  state.issues = [{ message: "Temperatur oben ausgefallen" }];
  value.drawCurrent();
  assert.doesNotMatch(nodes["#current"].innerHTML, /Ersatzmessung unten/);
  assert.match(nodes["#current"].innerHTML, /Temperatur oben ausgefallen/);
});
