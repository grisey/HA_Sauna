// Runs without Home Assistant or a browser.  Each assertion renders the
// overview from an availability DTO, so it covers the guest-facing result.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {get: () => undefined, define: (_name, value) => { Panel=value; }},
  Date, Map, Set, Math, Number, String, Object, Array, Infinity,
};
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), sandbox);

const parameters = {
  preset_count: 6, preset_start_c: 95, preset_step_c: 2,
  program_1_start_c: 70, program_1_end_c: 80,
  program_2_start_c: 80, program_2_end_c: 90,
  target_temperature_c: 80, final_temperature_c: 90, temperature_gangs: 3,
};
const baseState = () => ({
  now: "2026-09-20T10:00:00Z", phase: "bereit", operation_enabled: true,
  session: {timeline: {active: null, door: "closed", completed: []}, heating: {elapsed_seconds: 0}, deadlines: []},
  configuration: {parameters}, last_session: null, measurements: [], measurement_status: {},
  parameters: [{key: "target_temperature_c", minimum: 60, maximum: 100}, {key: "temperature_gangs", minimum: 1, maximum: 8}],
  mechanical_timer: {state: "idle"}, heating_limit_seconds: 5400,
  heating_feedback: false, permissions: {control: true, temperature: true, program: true, light: true},
  start_errors: [], issues: [], detection_channels: [], target_temperature: 80,
  manual_controls: {light: {}}, start_availability: {}, phase_timer: null,
});
const render = state => {
  const nodes = {};
  const panel = Object.assign(Object.create(Panel.prototype), {
    state, hass: {user: {name: "Test"}},
    $: selector => {
      if(selector === "#current" || selector === "#details" || selector === ".main-tabs [data-action=\"details\"]") {
        return nodes[selector] ||= {};
      }
      return null;
    },
  });
  panel.drawCurrent();
  return nodes["#current"].innerHTML;
};

let state = baseState();
state.phase = "aufheizen";
state.start_availability = {until_ready_seconds: 480, ready_estimated: true};
assert.doesNotMatch(render(state), /availability-card/, "readiness no longer occupies a separate overview card");
assert.match(render(state), /Heizen<\/strong><span class="availability-line ">noch 10 min bis bereit/, "heating and the estimate share one compact state line without repeating its state");
assert.doesNotMatch(render(state), /8:00 min/, "estimated readiness never exposes a changing seconds timer");

state = baseState();
state.start_availability = {until_ready_seconds: 0, ready_estimated: false, start_window_seconds: 1200, start_window_label: "mindestens"};
assert.match(render(state), /Bereit<\/strong><span class="availability-line ">noch 20 min/, "ready state shows the supplied conservative start window without repeating its state");
assert.doesNotMatch(render(state), /Jetzt bereit|Bereitschaft/, "ready state is not repeated in a second announcement");

state = baseState();
state.start_availability = {minimum_wait_seconds: 300, until_ready_seconds: null};
state.start_availability.blocker = {kind: "cooling"};
assert.match(render(state), /Start gesperrt – noch 5:00 min/, "a blocker remains distinct from readiness");

state = baseState();
state.phase = "aufheizen";
state.start_availability = {until_ready_seconds: 299, ready_estimated: true};
assert.match(render(state), /noch unter 5 min bis bereit/, "an estimate below five minutes never reaches zero before readiness");

state = baseState();
state.phase = "aufheizen";
state.start_availability = {until_ready_seconds: 360, ready_estimated: true};
assert.match(render(state), /noch 5 min bis bereit/, "an estimate above five minutes uses the nearest five-minute step");

state = baseState();
state.start_availability = {until_ready_seconds: 0, start_window_seconds: 299};
assert.match(render(state), /Bereit<\/strong><span class="availability-line ">noch unter 5 min/, "a positive start window never pretends it is already exhausted");

state = baseState();
state.start_availability = {until_ready_seconds: null, message: "Startzeit noch nicht abschätzbar."};
assert.match(render(state), /Startzeit noch nicht abschätzbar\./, "an unavailable estimate remains visible");

state = baseState();
state.phase = "saunagang";
state.start_availability = {gang_elapsed_seconds: 61};
state.phase_timer = {kind: "gang", seconds: 61};
assert.match(render(state), /Saunagang<\/strong><span class="availability-line ">seit 1:01 min/, "an active round shows its elapsed time beside the state");

state = baseState();
state.phase = "nachlauf";
state.start_availability = {blocker: {kind: "after_run"}, minimum_wait_seconds: 600};
state.phase_timer = {kind: "after_run", seconds: 300, mode: "remaining"};
assert.match(render(state), /Nachlauf<\/strong><span class="availability-line wait">noch 5:00 min/, "after-run time does not claim readiness");

state = baseState();
state.phase_timer = {kind: "thermostat_pause", label: "Heizpause noch", seconds: 600};
let overview = render(state);
assert.doesNotMatch(overview, /Heizpause/, "technical phase timers stay out of the overview");
assert.equal((overview.match(/data-action="preset:/g) || []).length, 3, "presets over the 100 °C maximum are hidden");
assert.match(overview, /class="program-buttons"/, "profiles have their own selection row");
assert.match(overview, /role="group" aria-label="Ofen aus und Solltemperatur"/, "the slider remains exposed within an accessible group");

state = baseState();
state.phase = "aufheizen";
state.start_availability = {until_ready_seconds: 480, ready_estimated: true};
state.phase_timer = {kind: "minimum_heating", label: "Mindestheizzeit noch", seconds: 600};
overview = render(state);
assert.match(overview, /noch 10 min bis bereit/, "minimum heating does not displace the readiness estimate");
assert.doesNotMatch(overview, /Mindestheizzeit/, "minimum heating stays in details");

state = baseState();
state.phase = "bereit";
state.start_availability = {until_ready_seconds: 0, start_window_seconds: 1200};
state.phase_timer = {kind: "thermostat_pause", label: "Heizpause noch", seconds: 600};
overview = render(state);
assert.match(overview, /Bereit<\/strong><span class="availability-line ">noch 20 min/, "a heating pause does not displace the start window");
assert.doesNotMatch(overview, /Heizpause/, "heating pauses remain in details when ready");

state = baseState();
state.operation_enabled = false;
state.session = null;
state.phase_timer = {kind: "session_light", seconds: 90};
overview = render(state);
assert.match(overview, /Lichtnachlauf noch 1:30 min/, "only the compact end-of-session light timer remains off-session");

console.log("panel overview time regressions passed");
