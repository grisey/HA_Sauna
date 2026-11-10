"use strict";
// Steuerung nach Entwurf B (B3a): Aufgüsse im laufenden Gang (S5), Programmstufen
// (S6), Restzeit einer Übersteuerung unter der Taste (S7). Instrumente und
// Knopfrollen bleiben (G15, G16); Details bleibt unverändert.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
const {
  parameterValues: fixtureValues,
  parameterDefinition: fixtureDefinition,
} = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const defaults = structuredClone(require("./catalog_fixture.cjs").catalog);

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
});

const NOW = "2026-11-02T19:31:00Z";
const gangState = () => ({
  frontend_defaults: defaults.frontend,
  appearance_catalog: defaults.appearance,
  now: NOW,
  phase: "saunagang",
  operation_enabled: true,
  session: {
    timeline: {
      active: {
        gang_id: "g2",
        infusion_events: [{ event_id: "i1" }, { event_id: "i2" }],
      },
      door: "closed",
      completed: [{ gang_id: "g1", infusion_events: [{ event_id: "i0" }] }],
    },
    heating: { elapsed_seconds: 0 },
    deadlines: [],
  },
  configuration: {
    parameters: fixtureValues(),
    control_mode: "automatic",
    program_mode: "progressive",
    selected_program_id: "hoehenwanderung",
    temperature_programs: [
      {
        id: "hoehenwanderung",
        name: "Höhenwanderung",
        start_c: 85,
        end_c: 95,
        distribution_gangs: 5,
      },
    ],
  },
  last_session: null,
  measurements: [],
  measurement_status: {},
  parameters: [
    fixtureDefinition("target_temperature_c"),
    fixtureDefinition("temperature_gangs"),
  ],
  heating_feedback: true,
  permissions: {
    control: true,
    temperature: true,
    program: true,
    light: true,
    heater: true,
  },
  start_errors: [],
  issues: [],
  detection_channels: [],
  target_temperature: 88,
  temperature_control: {
    scope: "next_gang",
    value: 90,
    current_target: 88,
    next_target: 90,
    program_mode: "progressive",
    stages: [85, 88, 90, 93, 95],
    stage: 1,
  },
  manual_controls: {
    heater: {
      manual: null,
      observation: { available: true, on: true },
      override_ends_at: null,
    },
    light: {
      manual: null,
      observation: { available: true, brightness_percent: 30 },
      override_ends_at: null,
    },
  },
  start_availability: {
    blocker: { kind: "gang" },
    message: "Ein Saunagang läuft bereits.",
  },
  phase_timer: { kind: "gang", label: "Saunagang seit", seconds: 720, mode: "elapsed" },
});
const render = (state) => {
  const nodes = {};
  const panel = Object.assign(fixturePanel(Panel), {
    state,
    hass: { user: { name: "Test" } },
    $: (selector) =>
      ["#current", "#details", '.main-tabs [data-action="details"]'].includes(selector)
        ? (nodes[selector] ||= {})
        : null,
  });
  panel.drawCurrent();
  return { control: nodes["#current"].innerHTML, details: nodes["#details"].innerHTML };
};

test("state line names the infusions of the running gang only in the control view", () => {
  const state = gangState();
  let { control, details } = render(state);
  assert.match(
    control,
    /Saunagang<\/strong><span class="availability-line wait">seit 12 Minuten <span class="gang-infusions">· 2 Aufgüsse<\/span><\/span>/,
  );
  assert.match(
    control,
    /<span class="badge">1 Saunagang<\/span>/,
    "count of finished gangs stays",
  );
  assert.doesNotMatch(details, /gang-infusions/, "Details keeps its content until B4");
  state.session.timeline.active.infusion_events = [{ event_id: "i1" }];
  assert.match(render(state).control, /· 1 Aufguss</);
  state.session.timeline.active.infusion_events = [];
  assert.match(render(state).control, /· noch kein Aufguss</);
  state.phase = "bereit";
  state.phase_timer = null;
  state.session.timeline.active = null;
  state.start_availability = { blocker: null };
  ({ control } = render(state));
  assert.doesNotMatch(control, /gang-infusions/, "no gang, no infusion line");
});

test("program stages come from the backend with the current one marked", () => {
  const state = gangState();
  const { control } = render(state);
  assert.match(
    control,
    /<div class="program-stages"><span class="program-stages-label" aria-hidden="true">Stufen je Gang<\/span><ol aria-label="Stufen je Gang in °C"><li data-stage="done">85<\/li><li data-stage="current" aria-current="step">88<\/li><li data-stage="next">90<\/li><li data-stage="next">93<\/li><li data-stage="next">95<\/li><\/ol><span aria-hidden="true">°C<\/span><\/div>/,
  );
  assert.match(
    control,
    /<strong class="program-active-label">Höhenwanderung<\/strong><small>85 → 95 °C<\/small>/,
  );
  // Die Stufen stammen aus dem Endpunkt, nicht aus der Programmdefinition.
  state.temperature_control.stages = [86, 91, 95];
  state.temperature_control.stage = 0;
  const changed = render(state).control;
  assert.match(changed, /<small>86 → 95 °C<\/small>/);
  assert.match(
    changed,
    /<li data-stage="current" aria-current="step">86<\/li><li data-stage="next">91<\/li>/,
  );
  for (const stages of [null, [88]]) {
    state.temperature_control.stages = stages;
    assert.doesNotMatch(render(state).control, /program-stages/);
  }
});

test("override remaining time stands under the overridden output", () => {
  const state = gangState();
  assert.doesNotMatch(render(state).control, /override-remaining/);
  state.manual_controls.light.manual = 60;
  state.manual_controls.light.override_ends_at = "2026-11-02T19:55:30Z";
  let { control } = render(state);
  assert.match(
    control,
    /<section class="manual-section manual-light">[\s\S]*<\/div><\/div><p class="override-remaining" data-override="light">Von Hand ein · Automatik wieder in 25 Minuten<\/p><\/section>/,
  );
  state.manual_controls.heater.manual = false;
  state.manual_controls.heater.override_ends_at = "2026-11-02T19:41:00Z";
  ({ control } = render(state));
  assert.match(
    control,
    /data-override="heater">Von Hand aus · Automatik wieder in 10 Minuten</,
  );
  // Abgelaufen oder ohne Wahl: keine Zeile.
  state.manual_controls.heater.override_ends_at = "2026-11-02T19:30:00Z";
  state.manual_controls.light.manual = null;
  assert.doesNotMatch(render(state).control, /override-remaining/);
  // Buttons and their roles stay as before (G16).
  assert.match(control, /data-action="light:auto" aria-pressed="false"/);
  assert.match(control, /data-action="heater:true" aria-pressed="true"/);
});

test("manual mode has no automatic return and no override line", () => {
  const state = gangState();
  state.configuration.control_mode = "manual";
  state.manual_controls.light.manual = 60;
  state.manual_controls.light.override_ends_at = "2026-11-02T19:55:30Z";
  assert.doesNotMatch(render(state).control, /override-remaining|Automatik wieder/);
});
