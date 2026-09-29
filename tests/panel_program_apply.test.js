const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
const timers = [];
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout: (callback) => {
    timers.push(callback);
    return timers.length;
  },
  clearTimeout: () => {},
});

const programs = [
  { id: "quiet", name: "Ruhig", start_c: 75, end_c: 90, distribution_gangs: 3 },
];
const configuration = (mode = "constant") => ({
  control_mode: "automatic",
  program_mode: mode,
  selected_program_id: null,
  temperature_steps: null,
  temperature_programs: programs,
  parameters: {
    target_temperature_c: 80,
    final_temperature_c: 90,
    temperature_gangs: 3,
    preset_count: 0,
    preset_start_c: 70,
    preset_step_c: 5,
    session_light_brightness_percent: 50,
  },
});
function panel(config = configuration(), session = null, api = async () => ({})) {
  const calls = [];
  const p = Object.assign(Object.create(Panel.prototype), {
    entry: "entry",
    generation: 0,
    isConnected: true,
    state: {
      configuration: config,
      session,
      target_temperature: 86,
      permissions: { program: true, temperature: true },
      parameters: [
        { key: "target_temperature_c", minimum: 60, maximum: 100 },
        { key: "temperature_gangs", minimum: 1, maximum: 8 },
      ],
    },
    $: () => null,
    drawCurrent() {
      this.draws = (this.draws || 0) + 1;
    },
    refresh: async () => {},
    message: () => {},
    api: async (...args) => {
      calls.push(args);
      return api(...args);
    },
  });
  return { p, calls };
}
const plain = (value) => JSON.parse(JSON.stringify(value));

test("the individual distribution explains the current validated field draft", () => {
  const { p } = panel(configuration("progressive"));
  p.programInfoOpen = "free";
  p.progressionDraft = { "progression-end": "100" };
  let form = p.freeProgramForm(p.programBounds(), p.state.permissions);
  assert.match(form, /id="progression-end"[^>]*value="100"/);
  assert.match(form, /80 → 90 → 100 °C/);
  assert.doesNotMatch(form, /80 → 85 → 90 °C/);
  p.progressionDraft = { "progression-end": "" };
  form = p.freeProgramForm(p.programBounds(), p.state.permissions);
  assert.match(form, /id="progression-end"[^>]*value=""/);
  assert.match(form, /Start, Ende und Verteilung innerhalb der zulässigen Grenzen/);
  assert.doesNotMatch(form, /80 → 85 → 90 °C/);
});

test("a named program remains applicable after a queued target failed", async () => {
  const { p, calls } = panel(configuration(), { timeline: {} });
  p.temperatureChange = Promise.reject(Error("target failed"));
  p.programSelectionDraft = { mode: "program", id: "quiet" };
  await p.applyProgram();
  assert.deepEqual(plain(calls), [["/entry/program", "POST", { profile: "quiet" }]]);
});

test("off-session named choice applies directly and reflects authoritative response", async () => {
  const { p, calls } = panel(configuration(), null, async () => ({
    parameters: {
      target_temperature_c: 75,
      final_temperature_c: 90,
      temperature_gangs: 3,
    },
    program_mode: "progressive",
    selected_program_id: "quiet",
    temperature_steps: null,
  }));
  await p.action("program-select:quiet");
  assert.deepEqual(plain(calls), [["/entry/program", "POST", { profile: "quiet" }]]);
  assert.equal(p.state.configuration.selected_program_id, "quiet");
  assert.equal(p.programSelectionDraft, null);
  assert.equal(p.programSaveState, "saved");
  timers.at(-1)();
  assert.equal(p.programSaveState, null);
});

test("off-session individual choice applies its valid existing values directly", async () => {
  const { p, calls } = panel(configuration(), null, async () => ({
    parameters: {
      target_temperature_c: 80,
      final_temperature_c: 92,
      temperature_gangs: 4,
    },
    program_mode: "progressive",
    selected_program_id: null,
    temperature_steps: null,
  }));
  p.progressionValues = () => ({ start: 80, end: 92, gangs: 4 });
  await p.action("program-mode:individual");
  assert.deepEqual(plain(calls), [
    [
      "/entry/program",
      "POST",
      {
        target_temperature_c: 80,
        final_temperature_c: 92,
        temperature_gangs: 4,
      },
    ],
  ]);
  assert.equal(p.state.configuration.program_mode, "progressive");
  assert.equal(p.programSelectionDraft, null);
  assert.equal(p.programSaveState, "saved");
});

test("opaque named IDs retain their mode through request, saved selection and drafts", async () => {
  const catalog = ["individual", "other", "custom:one", "custom:one:two"].map((id) => ({
    id,
    name: `Benannt ${id}`,
    temperature_steps: [76, 83, 90],
  }));
  const { p, calls } = panel({ ...configuration(), temperature_programs: catalog });
  for (const program of catalog) {
    await p.action(`program-select:${program.id}`);
    assert.deepEqual(plain(calls.at(-1)), [
      "/entry/program", "POST", { profile: program.id },
    ]);
    assert.equal(p.state.configuration.selected_program_id, program.id);
    assert.equal(p.programMode(catalog), "program");
    assert.equal(p.programChoiceLabel(p.programChoice(catalog), catalog), program.name);
    assert.equal(p.programDirty(), false);
  }
  p.state.configuration.selected_program_id = "individual";
  p.state.configuration.temperature_steps = [76, 83, 90];
  p.state.session = { timeline: {} };
  await p.action("program-mode:individual");
  assert.equal(p.programMode(catalog), "individual");
  assert.equal(p.programDirty(), true);
  assert.equal(p.programChoiceLabel(p.programChoice(catalog), catalog), "Individuell");
  await p.action("program-cancel-draft");
  assert.equal(p.programMode(catalog), "program");
  assert.equal(p.state.configuration.selected_program_id, "individual");
  p.freeProgramKind = "steps";
  p.freeProgramValues = () => [80, 86, 90];
  await p.action("program-mode:individual");
  await p.action("program-apply");
  assert.deepEqual(plain(calls.at(-1)), [
    "/entry/program", "POST", { temperature_steps: [80, 86, 90] },
  ]);
  assert.equal(p.state.configuration.selected_program_id, null);
  assert.equal(p.programMode(catalog), "individual");
  await p.action("program-mode:constant");
  await p.action("program-apply");
  assert.deepEqual(plain(calls.at(-1)), [
    "/entry/program", "POST", { profile: "constant" },
  ]);
  assert.equal(p.programMode(catalog), "constant");
});

test("session and gap selections stay staged until the main apply action", async () => {
  for (const session of [
    { timeline: {} },
    { timeline: {}, deadlines: [{ purpose: "session_gap" }] },
  ]) {
    const { p, calls } = panel(configuration(), session);
    await p.action("program-select:quiet");
    assert.deepEqual(plain(p.programSelectionDraft), { mode: "program", id: "quiet" });
    assert.equal(calls.length, 0);
    await p.action("program-cancel-draft");
    assert.equal(p.programSelectionDraft, null);
    await p.action("program-select:quiet");
    p.state.session = null;
    assert.equal(calls.length, 0, "ending a session never submits a staged choice");
    await p.action("program-apply");
    assert.deepEqual(plain(calls), [["/entry/program", "POST", { profile: "quiet" }]]);
  }
});

test("live progression changes end and count without replacing the running target", async () => {
  const config = configuration("progressive");
  const { p, calls } = panel(config, { timeline: {} }, async () => ({
    parameters: {
      target_temperature_c: 80,
      final_temperature_c: 95,
      temperature_gangs: 4,
    },
  }));
  p.progressionValues = () => ({ start: 80, end: 95, gangs: 4 });
  p.progressionDraft = { "progression-end": "95", "progression-gangs": "4" };
  await p.action("program-apply");
  assert.deepEqual(plain(calls), [
    [
      "/entry/temperature",
      "POST",
      {
        final_temperature_c: 95,
        temperature_gangs: 4,
      },
    ],
  ]);
  assert.equal(
    p.state.target_temperature,
    86,
    "the effective target is left to the next state response",
  );
  assert.equal(p.state.configuration.parameters.final_temperature_c, 95);
});

test("individual program is clean until an explicit draft edit exists", () => {
  const { p } = panel(configuration("progressive"), { timeline: {} });
  p.progressionValues = () => {
    throw Error("form has not rendered yet");
  };
  assert.equal(
    p.programDirty(),
    false,
    "first render does not depend on missing DOM inputs",
  );
  p.progressionValues = () => ({ start: 80, end: 90, gangs: 3 });
  p.state.configuration.parameters.final_temperature_c = 95;
  assert.equal(
    p.programDirty(),
    false,
    "old form values do not turn an external update into a draft",
  );
  p.progressionDraft = { "progression-end": "90" };
  assert.equal(p.programDirty(), true, "an explicit field edit enables apply");
  p.progressionDraft = null;
  p.freeProgramKind = "steps";
  p.freeProgramValues = () => [80, 90, 95];
  assert.equal(p.programDirty(), true, "an explicit kind change enables apply");
});

test("invalid individual draft is rejected before entering pending state", async () => {
  const { p, calls } = panel(configuration(), { timeline: {} });
  p.programSelectionDraft = { mode: "individual" };
  p.progressionValues = () => {
    throw Error("invalid progression");
  };
  await assert.rejects(() => p.action("program-apply"), /invalid progression/);
  assert.equal(p.programRequest, undefined);
  assert.equal(p.programSaveState, undefined);
  assert.equal(calls.length, 0);
});

test("a pending direct target completes before program submit; failure keeps the draft", async () => {
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  let fail = true;
  const { p, calls } = panel(configuration(), { timeline: {} }, async () => {
    if (fail) throw Error("save failed");
    return {
      parameters: {
        target_temperature_c: 75,
        final_temperature_c: 95,
        temperature_gangs: 4,
      },
      program_mode: "progressive",
      selected_program_id: null,
      temperature_steps: null,
    };
  });
  p.programSelectionDraft = { mode: "individual" };
  p.progressionDraft = { "progression-end": "95" };
  p.progressionValues = () => ({ start: 80, end: 95, gangs: 4 });
  p.temperatureChange = pending;
  const saving = p.action("program-apply");
  assert.equal(p.programSaveState, "saving");
  assert.equal(calls.length, 0);
  release({ target_temperature_c: 75 });
  await assert.rejects(saving, /save failed/);
  assert.deepEqual(plain(calls[0]), [
    "/entry/program",
    "POST",
    {
      target_temperature_c: 75,
      final_temperature_c: 95,
      temperature_gangs: 4,
    },
  ]);
  assert.deepEqual(plain(p.programSelectionDraft), { mode: "individual" });
  assert.deepEqual(plain(p.progressionDraft), { "progression-end": "95" });
  fail = false;
  p.temperatureChange = null;
  await p.action("program-apply");
  assert.equal(calls.length, 2);
  assert.equal(p.state.configuration.parameters.target_temperature_c, 75);
});

test("an off-session automatic save becomes a staged choice if a session starts", async () => {
  let release;
  const target = new Promise((resolve) => (release = resolve));
  const { p, calls } = panel(configuration(), null);
  p.programSelectionDraft = { mode: "program", id: "quiet" };
  p.temperatureChange = target;
  const saving = p.applyProgram();
  p.state.session = { timeline: {} };
  release({ target_temperature_c: 80 });
  await saving;
  assert.equal(calls.length, 0);
  assert.deepEqual(plain(p.programSelectionDraft), { mode: "program", id: "quiet" });
  assert.equal(p.programChoiceOpen, true);
  assert.equal(p.programSaveState, null);
});

test("gap control keeps its apply action inside the open program editor", () => {
  const gap = {
    timeline: { active: null, completed: [], door: "closed" },
    heating: { elapsed_seconds: 0 },
    deadlines: [
      { purpose: "session_gap", token: "gap", due_at: "2032-01-01T12:10:00Z" },
    ],
  };
  const { p } = panel(configuration(), gap);
  const nodes = new Map();
  p.$ = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
    return nodes.get(selector);
  };
  p.drawCurrent = Panel.prototype.drawCurrent;
  Object.assign(p.state, {
    now: "2032-01-01T12:00:00Z",
    last_session: null,
    operation_enabled: false,
    phase: "aus",
    gang_count: 0,
    measurements: [],
    measurement_status: {},
    mechanical_timer: { state: "idle", remaining_seconds: 0 },
    manual_controls: { heater: {}, light: {} },
    start_errors: [],
    issues: [],
    heating_observation: { source: "unknown" },
    heating_feedback: false,
    energy_kwh: 0,
    energy_source: "estimated",
    phase_timer: null,
    start_availability: null,
  });
  p.state.permissions = {
    admin: false,
    control: true,
    heater: true,
    light: true,
    program: true,
    temperature: true,
  };
  p.programSelectionDraft = { mode: "program", id: "quiet" };
  p.drawCurrent();
  const control = nodes.get("#current").innerHTML;
  assert.equal((control.match(/data-action="program-apply"/g) || []).length, 1);
  assert.ok(
    control.indexOf('data-action="program-apply"') >
      control.indexOf('data-action="finish-session:gap"'),
  );
  assert.match(
    control,
    /data-action="program-apply"[^>]*program-main[^>]*>Programm übernehmen/,
  );
  assert.match(control, /data-action="program-select:quiet" aria-pressed="false"/);
  assert.match(control, /program-draft-label">Vorgemerkt/);
  assert.match(control, /data-action="program-cancel-draft"/);
  assert.match(control, /data-action="operation"[^>]*>Fortsetzen/);
  assert.doesNotMatch(nodes.get("#details").innerHTML, /data-action=|<button/);
  p.state.configuration.program_mode = "progressive";
  nodes.set("#progression-start", { value: "80" });
  nodes.set("#progression-end", { value: "95" });
  nodes.set("#progression-gangs", { value: "3" });
  p.programSelectionDraft = null;
  p.progressionDraft = { "progression-end": "95" };
  p.markProgramEdited();
  assert.match(
    nodes.get("#current").innerHTML,
    /data-action="program-apply"[^>]*program-main/,
    "an individual edit immediately creates the main apply button",
  );
  p.progressionDraft = null;
  p.state.session = null;
  p.programSelectionDraft = null;
  p.programSaveState = "saved";
  p.drawCurrent();
  assert.equal(
    (nodes.get("#current").innerHTML.match(/data-action="program-apply"/g) || [])
      .length,
    0,
    "off-session confirmation reuses the selected program control",
  );
  assert.match(nodes.get("#current").innerHTML, /✓ Übernommen/);
  p.programSaveState = null;
  p.state.configuration.control_mode = "manual";
  p.state.session = gap;
  p.drawCurrent();
  assert.doesNotMatch(
    nodes.get("#current").innerHTML,
    /data-action="program-apply"/,
    "manual mode has no idle program button",
  );
});

test("a rejected apply from an old entry does not surface after switching", async () => {
  let rejectRequest;
  const response = new Promise((_, reject) => {
    rejectRequest = reject;
  });
  const { p } = panel(configuration(), { timeline: {} }, async () => response);
  p.programSelectionDraft = { mode: "program", id: "quiet" };
  const saving = p.action("program-apply");
  await Promise.resolve();
  p.entry = "another-entry";
  p.generation++;
  p.programRequest = null;
  rejectRequest(Error("old entry failed"));
  await assert.doesNotReject(saving);
  assert.equal(p.state.configuration.selected_program_id, null);
});

test("a pre-save poll is discarded; the next poll may report an external change", async () => {
  let releaseOld;
  const oldPoll = new Promise((resolve) => {
    releaseOld = resolve;
  });
  let polls = 0;
  const oldState = { configuration: configuration() };
  const external = {
    configuration: {
      ...configuration(),
      parameters: {
        ...configuration().parameters,
        target_temperature_c: 72,
      },
    },
  };
  const { p } = panel(configuration(), null);
  p.$ = () => ({ hidden: true });
  p.refresh = Panel.prototype.refresh;
  p.syncNavigation = () => {};
  p.drawSettings = () => {};
  p.api = async (path) => {
    if (path.endsWith("/state")) return ++polls === 1 ? oldPoll : external;
    return {
      parameters: {
        target_temperature_c: 75,
        final_temperature_c: 90,
        temperature_gangs: 3,
      },
      program_mode: "progressive",
      selected_program_id: "quiet",
      temperature_steps: null,
    };
  };
  const first = p.refresh();
  await Promise.resolve();
  p.programSelectionDraft = { mode: "program", id: "quiet" };
  await p.action("program-apply");
  assert.equal(p.state.configuration.selected_program_id, "quiet");
  releaseOld(oldState);
  await first;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(p.state.configuration.parameters.target_temperature_c, 72);
  assert.equal(p.state.configuration.selected_program_id, null);
});
