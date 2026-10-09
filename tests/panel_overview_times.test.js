// Runs without Home Assistant or a browser.  Each assertion renders the
// overview from an availability DTO, so it covers the guest-facing result.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const defaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
);

let Panel;
const sandbox = {
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
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"),
  sandbox,
);

const parameters = {
  preset_count: 6,
  preset_start_c: 95,
  preset_step_c: 2,
  program_1_start_c: 70,
  program_1_end_c: 80,
  program_2_start_c: 80,
  program_2_end_c: 90,
  target_temperature_c: 80,
  final_temperature_c: 90,
  temperature_gangs: 3,
};
const baseState = () => ({
  frontend_defaults: defaults.frontend,
  appearance_catalog: defaults.appearance,
  now: "2026-09-20T10:00:00Z",
  phase: "bereit",
  operation_enabled: true,
  session: {
    timeline: { active: null, door: "closed", completed: [] },
    heating: { elapsed_seconds: 0 },
    deadlines: [],
  },
  configuration: { parameters },
  last_session: null,
  measurements: [],
  measurement_status: {},
  parameters: [
    { key: "target_temperature_c", minimum: 60, maximum: 100 },
    { key: "temperature_gangs", minimum: 1, maximum: 8 },
  ],
  mechanical_timer: { state: "idle" },
  heating_feedback: false,
  permissions: { control: true, temperature: true, program: true, light: true },
  start_errors: [],
  issues: [],
  detection_channels: [],
  target_temperature: 80,
  manual_controls: { light: {} },
  start_availability: {
    until_ready_seconds: 0,
    ready_estimated: false,
    minimum_wait_seconds: null,
    message: "Bereit für einen Saunagang.",
    blocker: null,
    gang_elapsed_seconds: null,
  },
  phase_timer: null,
});
const render = (state, target = "#current") => {
  const nodes = {};
  const panel = Object.assign(Object.create(Panel.prototype), {
    state,
    hass: { user: { name: "Test" } },
    $: (selector) => {
      if (
        selector === "#current" ||
        selector === "#details" ||
        selector === '.main-tabs [data-action="details"]'
      ) {
        return (nodes[selector] ||= {});
      }
      return null;
    },
  });
  panel.drawCurrent();
  return nodes[target].innerHTML;
};

{
  const state = baseState();
  state.appearance = { scales: { temperature: { minimum: 60, maximum: 100 } } };
  state.measurements = [{ position: "upper", quantity: "temperature", value: 80 }];
  state.measurement_status = { upper_temperature: { state: "current" } };
  const instrument = render(state).match(
    /<svg class="dial dial-temperature"[\s\S]*?<\/svg>/,
  )[0];
  assert.match(
    instrument,
    /stroke-dasharray="50 100"/,
    "80 °C is halfway along the 60–100 °C measurement scale",
  );
  assert.match(
    instrument,
    /target-temperature-handle[^>]*cx="150.00" cy="25.00"/,
    "the same measured and selected temperature use the same position",
  );
}

let state = baseState();
state.phase = "aufheizen";
state.start_availability = { until_ready_seconds: 480, ready_estimated: true };
assert.doesNotMatch(
  render(state),
  /availability-card/,
  "readiness no longer occupies a separate overview card",
);
assert.match(
  render(state),
  /Heizen<\/strong><span class="availability-line ">noch 10 Minuten bis bereit/,
  "heating and the estimate share one compact state line without repeating its state",
);
assert.doesNotMatch(
  render(state),
  /8:00 Minuten/,
  "estimated readiness never exposes a changing seconds timer",
);

state = baseState();
assert.match(
  render(state),
  /Bereit<\/strong>/,
  "ready state uses the backend's readiness without a fabricated start deadline",
);
assert.doesNotMatch(
  render(state),
  /availability-line/,
  "readiness without a phase timer has no empty information area",
);
assert.doesNotMatch(
  render(state),
  /Jetzt bereit|Bereitschaft/,
  "ready state is not repeated in a second announcement",
);

state = baseState();
state.start_availability = { minimum_wait_seconds: 300, until_ready_seconds: null };
state.start_availability.blocker = { kind: "after_run" };
assert.match(
  render(state),
  /Start gesperrt – noch 5 Minuten/,
  "a blocker remains distinct from readiness",
);

state = baseState();
state.phase = "aufheizen";
state.start_availability = { until_ready_seconds: 299, ready_estimated: true };
assert.match(
  render(state),
  /noch unter 5 Minuten bis bereit/,
  "an estimate below five minutes never reaches zero before readiness",
);

state = baseState();
state.phase = "aufheizen";
state.start_availability = { until_ready_seconds: 360, ready_estimated: true };
assert.match(
  render(state),
  /noch 5 Minuten bis bereit/,
  "an estimate above five minutes uses the nearest five-minute step",
);

state = baseState();
state.start_availability = {
  until_ready_seconds: null,
  message: "Startzeit noch nicht abschätzbar.",
};
assert.doesNotMatch(
  render(state),
  /Startzeit noch nicht abschätzbar\.|availability-line/,
  "an unavailable estimate leaves no empty-information notice",
);

state = baseState();
state.phase = "saunagang";
state.start_availability = { gang_elapsed_seconds: 61 };
state.phase_timer = { kind: "gang", seconds: 61 };
assert.match(
  render(state),
  /Saunagang<\/strong><span class="availability-line ">seit 1 Minute/,
  "an active round shows its elapsed time beside the state",
);

state = baseState();
state.phase = "nachlauf";
state.start_availability = {
  blocker: { kind: "after_run" },
  minimum_wait_seconds: 600,
};
state.phase_timer = { kind: "after_run", seconds: 300, mode: "remaining" };
assert.match(
  render(state),
  /Ofenkühlung<\/strong><span class="availability-line wait">noch 5 Minuten/,
  "after-run time does not claim readiness",
);

state = baseState();
state.phase = "nachlauf";
state.phase_timer = {
  kind: "after_run",
  label: "Ofenkühlung wartet auf Schütz-Aus",
  seconds: null,
  mode: "pending",
};
let overview = render(state);
assert.match(
  overview,
  /Ofenkühlung wartet auf Schütz-Aus/,
  "a pending cooling phase states what it is waiting for",
);
assert.doesNotMatch(
  overview,
  /noch 0 Minuten/,
  "a pending cooling phase does not invent a zero duration",
);

state = baseState();
state.phase_timer = { kind: "thermostat_pause", label: "Heizpause noch", seconds: 600 };
overview = render(state);
assert.doesNotMatch(
  overview,
  /Heizpause/,
  "technical phase timers stay out of the overview",
);
assert.equal(
  (overview.match(/data-action="preset:/g) || []).length,
  3,
  "presets over the 100 °C maximum are hidden",
);
assert.match(
  overview,
  /data-action="program-mode:program" aria-pressed="false"/,
  "the named-program type remains selectable",
);
assert.match(
  overview,
  /data-action="program-mode:individual" aria-pressed="false"/,
  "the individual type remains selectable",
);
assert.match(
  overview,
  /data-action="program-mode:constant" aria-pressed="true"/,
  "constant is selected for the stored constant configuration",
);
assert.doesNotMatch(
  overview,
  /program-named-list|program-form/,
  "constant mode shows presets without a named or individual form",
);
assert.match(
  overview,
  /role="group" aria-label="Temperatur und Solltemperatur"/,
  "the slider remains exposed within an accessible group",
);

state = baseState();
state.configuration.temperature_programs = [
  { id: "quiet", name: "Ruhige Runde", start_c: 80, end_c: 90, distribution_gangs: 3 },
];
state.configuration.program_mode = "progressive";
state.configuration.selected_program_id = "quiet";
overview = render(state);
assert.match(
  overview,
  /data-action="program-mode:program" aria-pressed="true"/,
  "the named-program type is selected for a saved program",
);
assert.match(
  overview,
  /program-named-list[\s\S]*data-action="program-select:quiet"/,
  "named programs appear only in Program mode",
);
assert.doesNotMatch(
  overview,
  /temperature-presets|program-form/,
  "Program mode does not show constant presets or the individual form",
);

state = baseState();
state.configuration.program_mode = "progressive";
overview = render(state);
assert.match(
  overview,
  /data-action="program-mode:individual" aria-pressed="true"/,
  "the individual type is selected for an unnamed progression",
);
assert.match(
  overview,
  /class="program-form"/,
  "individual mode shows its progression form",
);
assert.doesNotMatch(
  overview,
  /program-named-list|temperature-presets/,
  "individual mode hides named programs and constant presets",
);

state = baseState();
state.phase = "aufheizen";
state.start_availability = { until_ready_seconds: 480, ready_estimated: true };
state.phase_timer = {
  kind: "minimum_heating",
  label: "Mindestheizzeit noch",
  seconds: 600,
};
overview = render(state);
assert.match(
  overview,
  /noch 10 Minuten bis bereit/,
  "minimum heating does not displace the readiness estimate",
);
assert.doesNotMatch(overview, /Mindestheizzeit/, "minimum heating stays in details");

state = baseState();
state.phase = "bereit";
state.start_availability = { until_ready_seconds: 0 };
state.phase_timer = { kind: "thermostat_pause", label: "Heizpause noch", seconds: 600 };
overview = render(state);
assert.match(
  overview,
  /Bereit<\/strong>/,
  "a heating pause preserves the ready state without a countdown",
);
assert.doesNotMatch(
  overview,
  /Heizpause/,
  "heating pauses remain in details when ready",
);

state = baseState();
state.operation_enabled = false;
state.session = null;
state.phase_timer = { kind: "session_light", seconds: 90 };
overview = render(state);
assert.match(
  overview,
  /Lichtnachlauf noch 2 Minuten/,
  "only the compact end-of-session light timer remains off-session",
);
assert.doesNotMatch(
  overview,
  /availability-line/,
  "operation off has no empty availability information area",
);

// The state line distinguishes elapsed time from time that still has to pass.
// A fractional final minute must not look like a completed wait.
for (const [seconds, elapsed, remaining] of [
  [0, "0 Minuten", "0 Minuten"],
  [0.1, "unter 1 Minute", "unter 1 Minute"],
  [59.9, "unter 1 Minute", "unter 1 Minute"],
  [60, "1 Minute", "1 Minute"],
  [60.1, "1 Minute", "2 Minuten"],
  [119.9, "1 Minute", "2 Minuten"],
  [120, "2 Minuten", "2 Minuten"],
  [120.1, "2 Minuten", "3 Minuten"],
  [-1, "0 Minuten", "0 Minuten"],
  [null, "–", "–"],
  [undefined, "–", "–"],
  [NaN, "–", "–"],
  [Infinity, "–", "–"],
  [-Infinity, "–", "–"],
]) {
  state = baseState();
  state.phase = "saunagang";
  state.phase_timer = { kind: "gang", label: "Saunagang seit", seconds };
  assert.ok(
    render(state).includes(`seit ${elapsed}</span>`),
    `${seconds} elapsed seconds use a whole-minute label`,
  );
  assert.ok(
    render(state, "#details").includes(
      `<span data-phase-timer="gang">${elapsed}</span>`,
    ),
    "the detail timer uses the same elapsed duration as the state line",
  );
  state.phase = "nachlauf";
  state.phase_timer = { kind: "after_run", seconds, mode: "remaining" };
  assert.ok(
    render(state).includes(`noch ${remaining}</span>`),
    `${seconds} remaining seconds use a whole-minute label`,
  );
}

state = baseState();
state.session.heating.elapsed_seconds = 119.9;
state.mechanical_timer = { state: "running", remaining_seconds: 119.9 };
state.phase_timer = {
  kind: "minimum_heating",
  label: "Mindestheizzeit noch",
  seconds: 119.9,
};
state.session.after_run = {
  phase_id: "cooling-test",
  duration_seconds: 300,
  elapsed_seconds: 180.1,
  credited_seconds: 0,
  ends_at: "2026-09-20T10:01:59.900Z",
};
state.session.deadlines = [
  { purpose: "confirmation", due_at: "2026-09-20T10:01:59.900Z" },
];
state.manual_controls.heater = { override_ends_at: "2026-09-20T10:00:59.900Z" };
state.light_after_run = { ends_at: "2026-09-20T10:01:00Z" };
let details = render(state, "#details");
assert.match(details, /Heizsumme \(gezählt\)<\/dt><dd>1 Minute<\/dd>/);
assert.match(details, /data-mechanical-timer>2 Minuten ·/);
assert.match(details, /data-phase-timer="minimum_heating">2 Minuten<\/span>/);
assert.match(details, /Ofenkühlung<\/dt><dd>2 Minuten<\/dd>/);
assert.match(details, /Aufgussbestätigung<\/dt><dd>2 Minuten<\/dd>/);
assert.match(details, /Ofenübersteuerung<\/dt><dd>unter 1 Minute<\/dd>/);
assert.match(details, /Lichtnachlauf<\/dt><dd>1 Minute<\/dd>/);
assert.doesNotMatch(details, /\d+:\d{2}\s+(?:min|Minuten)/);

state.session.heating.elapsed_seconds = null;
state.mechanical_timer.remaining_seconds = NaN;
details = render(state, "#details");
assert.match(details, /Heizsumme \(gezählt\)<\/dt><dd>–<\/dd>/);
assert.match(details, /data-mechanical-timer>– ·/);

state = baseState();
state.start_availability = {
  minimum_wait_seconds: 60.1,
  blocker: { kind: "after_run" },
};
assert.match(render(state), /Start gesperrt – noch 2 Minuten/);

console.log("panel overview time regressions passed");

state = baseState();
state.heating_feedback = true;
state.heating_observation = { source: "contactor", estimated: true };
assert.match(
  render(state, "#details"),
  /Ofen · Ein/,
  "details use the same oven label for every feedback source",
);
assert.doesNotMatch(
  render(state),
  /Heizfreigabe/,
  "technical contactor wording stays out of the control page",
);
state.heating_observation = { source: "power", estimated: false };
assert.match(
  render(state, "#details"),
  /Ofen · Ein/,
  "measured power still describes actual heating",
);
assert.match(render(state), /class="badge"/);
state.last_session = state.session;
state.session = null;
assert.doesNotMatch(render(state), /class="badge"/);
