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

function renderControls(p) {
  const nodes = new Map();
  p.$ = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: true });
    return nodes.get(selector);
  };
  p.shadowRoot = { activeElement: null };
  p.drawCurrent = Panel.prototype.drawCurrent;
  Object.assign(p.state, {
    now: "2032-01-01T12:00:00Z",
    last_session: null,
    operation_enabled: !!p.state.session,
    phase: p.state.session ? "bereit" : "aus",
    gang_count: 0,
    measurements: [],
    measurement_status: {},

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
  p.state.permissions.control = true;
  for (const method of [
    "syncHistoryProjection",
    "applyAppearance",
    "syncNavigation",
    "drawSettings",
    "syncAppearanceEditor",
  ])
    p[method] = () => {};
  p.drawCurrent();
  return () => nodes.get("#current").innerHTML;
}

function individualFields(p, values = { start: "80", end: "90", gangs: "3" }) {
  const fields = new Map(
    Object.entries(values).map(([key, value]) => [`#progression-${key}`, { value }]),
  );
  p.$ = (selector) => fields.get(selector) || null;
  return (key, value) => {
    fields.get(`#progression-${key}`).value = value;
    p.progressionDraft = { ...p.progressionDraft, [`progression-${key}`]: value };
  };
}

test("fractional quick-preset spacing exposes unique whole-degree commands", async () => {
  const config = configuration();
  Object.assign(config.parameters, {
    preset_start_c: 80,
    preset_step_c: 0.5,
    preset_count: 6,
  });
  const { p, calls } = panel(config);
  const current = renderControls(p);
  assert.deepEqual(
    Array.from(current().matchAll(/data-action="preset:([^\"]+)"/g), (match) =>
      Number(match[1]),
    ),
    [80, 81, 82, 83],
  );
  await p.action("preset:81");
  assert.deepEqual(plain(calls[0]), [
    "/entry/temperature",
    "POST",
    { target_temperature_c: 81 },
  ]);
});

test("free explicit stages send and retain whole-degree commands", async () => {
  const { p, calls } = panel(configuration("progressive"), {
    timeline: { session_id: "running" },
  });
  p.freeProgramKind = "steps";
  p.freeProgramStepsDraft = ["80.4", "85.5", "90.6"];
  p.shadowRoot = {
    querySelectorAll: () => p.freeProgramStepsDraft.map((value) => ({ value })),
  };
  await p.applyProgram();
  assert.deepEqual(plain(calls[0]), [
    "/entry/program",
    "POST",
    { temperature_steps: [80, 86, 91] },
  ]);
  assert.deepEqual(plain(p.state.configuration.temperature_steps), [80, 86, 91]);
});

test("temperature choice returns to direct selection after finally ending a session", async () => {
  const session = {
    timeline: {
      session_id: "current-session",
      session_started_at: "2032-01-01T11:00:00Z",
      active: null,
      completed: [],
      door: "closed",
    },
    heating: { elapsed_seconds: 0 },
    deadlines: [],
  };
  let reportedState;
  const { p, calls } = panel(configuration(), session, async (path, method, body) => {
    if (path === "/entry/state") return reportedState;
    if (path === "/entry/control") {
      reportedState = {
        ...reportedState,
        operation_enabled: body.enabled,
        session: {
          ...session,
          deadlines: body.enabled
            ? []
            : [
                {
                  purpose: "session_gap",
                  token: "gap-token",
                  due_at: "2032-01-01T12:10:00Z",
                },
              ],
        },
      };
      return {};
    }
    assert.equal(path, "/entry/finish-session");
    assert.equal(method, "POST");
    assert.deepEqual(plain(body), { token: "gap-token" });
    reportedState = { ...reportedState, session: null, last_session: session };
    return {};
  });
  const current = renderControls(p);
  p.refresh = Panel.prototype.refresh;
  reportedState = p.state;
  p.controlSessionKey = session.timeline.session_started_at;

  const checkDisclosure = async () => {
    if (p.programChoiceOpen) await p.action("program-toggle");
    assert.match(current(), /id="program-choice-body" hidden/);
    assert.match(current(), /data-action="program-toggle"[^>]*aria-expanded="false"/);
    await p.action("program-toggle");
    assert.match(current(), /id="program-choice-body" >/);
    assert.match(
      current(),
      /data-action="program-toggle"[^>]*aria-expanded="true"[^>]*>Schließen<\/button>/,
    );
    await p.action("program-toggle");
    assert.match(current(), /id="program-choice-body" hidden/);
  };

  await checkDisclosure();
  await p.action("operation");
  assert.equal(p.state.operation_enabled, false);
  assert.equal(p.state.session.timeline.session_id, "current-session");
  assert.match(current(), /data-action="finish-session:gap-token"/);
  assert.match(current(), /data-action="operation"[^>]*>Fortsetzen<\/button>/);
  await checkDisclosure();
  await p.action("operation");
  assert.equal(p.state.operation_enabled, true);
  assert.equal(p.state.session.timeline.session_id, "current-session");
  await checkDisclosure();
  await p.action("operation");
  await p.action("finish-session:gap-token");
  assert.equal(p.state.session, null);
  assert.match(current(), /id="program-choice-body" >/);
  assert.doesNotMatch(current(), /program-current|data-action="program-toggle"/);
  assert.match(current(), /data-action="program-mode:constant" aria-pressed="true"/);
  assert.doesNotMatch(current(), /data-action="finish-session:/);
  assert.equal(
    calls.filter(([path]) => path === "/entry/program" || path === "/entry/temperature")
      .length,
    0,
    "opening and closing the temperature choice never writes a program",
  );
});

test("a session draft keeps its cancel and apply actions after the session ends", async () => {
  for (const choice of ["named", "individual", "constant", "preset"])
    for (const outcome of ["cancel", "apply"]) {
      const session = {
        timeline: {
          session_id: "current-session",
          session_started_at: "2032-01-01T11:00:00Z",
          active: null,
          completed: [],
          door: "closed",
        },
        heating: { elapsed_seconds: 0 },
        deadlines: [],
      };
      let reportedState;
      const { p, calls } = panel(
        configuration(choice === "constant" ? "progressive" : "constant"),
        session,
        async (path, _method, body) => {
          if (path === "/entry/state") return reportedState;
          if (path === "/entry/control") {
            reportedState = {
              ...reportedState,
              operation_enabled: body.enabled,
              session: {
                ...session,
                deadlines: [
                  {
                    purpose: "session_gap",
                    token: "gap-token",
                    due_at: "2032-01-01T12:10:00Z",
                  },
                ],
              },
            };
            return {};
          }
          if (path === "/entry/finish-session") {
            reportedState = {
              ...reportedState,
              session: null,
              last_session: session,
            };
            return {};
          }
          assert.equal(path, "/entry/program");
          return {};
        },
      );
      p.state.configuration.parameters.preset_count = 3;
      const current = renderControls(p);
      p.refresh = Panel.prototype.refresh;
      p.progressionValues = () => ({ start: 80, end: 95, gangs: 4 });
      reportedState = p.state;
      p.controlSessionKey = session.timeline.session_started_at;
      await p.action("program-toggle");
      await p.action(
        choice === "named"
          ? "program-mode:program"
          : choice === "preset"
            ? "preset:75"
            : `program-mode:${choice}`,
      );
      const draft = p.programSelectionDraft;
      assert.equal(p.programDirty(), true);
      assert.doesNotMatch(current(), /Vorgemerkt/);
      assert.match(
        current(),
        new RegExp(
          `data-action="program-mode:${choice === "named" ? "program" : choice === "preset" ? "constant" : choice}" aria-pressed="true"`,
        ),
      );
      assert.match(
        current(),
        choice === "constant"
          ? /class="program-active-label">Individuell<\/strong>/
          : /class="program-active-label">Konstant<\/strong>/,
      );
      assert.equal(p.state.target_temperature, 86);
      await p.action("operation");
      assert.equal(p.programSelectionDraft, draft);
      assert.match(current(), /data-action="program-apply"/);
      await p.action("finish-session:gap-token");
      assert.equal(p.state.session, null);
      assert.equal(p.programSelectionDraft, draft);
      assert.match(current(), /id="program-choice-body" >/);
      assert.doesNotMatch(current(), /program-current|data-action="program-toggle"/);
      assert.doesNotMatch(current(), /Vorgemerkt/);
      assert.match(current(), /data-action="program-cancel-draft"/);
      assert.match(current(), /data-action="program-apply"[^>]*>Übernehmen<\/button>/);
      assert.equal(
        calls.filter(([path]) =>
          ["/entry/program", "/entry/temperature"].includes(path),
        ).length,
        0,
        `${choice}: ending a session does not apply its draft`,
      );
      if (outcome === "cancel") {
        await p.action("program-cancel-draft");
        assert.equal(p.programDirty(), false);
        assert.match(current(), /id="program-choice-body" >/);
        assert.equal(p.state.target_temperature, 86);
        assert.equal(
          calls.filter(([path]) =>
            ["/entry/program", "/entry/temperature"].includes(path),
          ).length,
          0,
        );
      } else {
        await p.action("program-apply");
        const expected =
          choice === "named"
            ? { profile: "quiet" }
            : choice === "constant"
              ? { profile: "constant" }
              : choice === "preset"
                ? { target_temperature_c: 75 }
                : {
                    target_temperature_c: 80,
                    final_temperature_c: 95,
                    temperature_gangs: 4,
                  };
        assert.deepEqual(
          plain(
            calls.filter(([path]) =>
              ["/entry/program", "/entry/temperature"].includes(path),
            ),
          ),
          [["/entry/program", "POST", expected]],
        );
        assert.equal(p.programDirty(), false);
        if (choice === "preset") {
          assert.equal(p.state.target_temperature, 75);
          assert.equal(p.state.configuration.program_mode, "constant");
        }
      }
    }
});

test("session presets only change the chosen temperature until apply", async () => {
  for (const mode of ["constant", "progressive"])
    for (const outcome of ["cancel", "apply"]) {
      const config = configuration(mode);
      config.parameters.preset_count = 3;
      const { p, calls } = panel(config, {
        timeline: { active: null, completed: [], door: "closed" },
        heating: { elapsed_seconds: 0 },
        deadlines: [],
      });
      const current = renderControls(p);
      await p.action("program-toggle");
      if (mode === "progressive") await p.action("program-mode:constant");
      assert.match(current(), /data-action="preset:75" aria-pressed="false"/);
      await p.action("preset:75");
      assert.equal(calls.length, 0);
      assert.equal(p.state.target_temperature, 86);
      assert.equal(p.state.configuration.parameters.target_temperature_c, 80);
      assert.equal(p.state.configuration.program_mode, mode);
      assert.deepEqual(plain(p.programSelectionDraft), {
        mode: "constant",
        temperature: 75,
      });
      assert.equal(p.programDirty(), true);
      assert.match(current(), /data-action="preset:75" aria-pressed="true"/);
      assert.match(
        current(),
        /data-action="program-apply"[^>]*>Programm übernehmen<\/button>/,
      );
      assert.doesNotMatch(current(), /Vorgemerkt/);
      await p.action(outcome === "cancel" ? "program-cancel-draft" : "program-apply");
      assert.equal(p.programDirty(), false);
      assert.equal(p.state.target_temperature, outcome === "cancel" ? 86 : 75);
      assert.equal(
        p.state.configuration.program_mode,
        outcome === "cancel" ? mode : "constant",
      );
      assert.deepEqual(
        plain(calls),
        outcome === "cancel"
          ? []
          : [["/entry/program", "POST", { target_temperature_c: 75 }]],
      );
    }
});

test("an off-session preset updates the directly visible temperature selection", async () => {
  const config = configuration();
  config.parameters.preset_count = 3;
  const { p, calls } = panel(config, null);
  const current = renderControls(p);
  p.refresh = async () => p.drawCurrent();
  assert.match(current(), /id="program-choice-body" >/);
  assert.match(current(), /data-action="preset:75" aria-pressed="false"/);
  assert.doesNotMatch(current(), /program-current|data-action="program-toggle"/);
  await p.action("preset:75");
  assert.deepEqual(plain(calls), [
    ["/entry/temperature", "POST", { target_temperature_c: 75 }],
  ]);
  assert.equal(p.state.target_temperature, 75);
  assert.equal(p.programDirty(), false);
  assert.match(current(), /data-action="program-mode:constant" aria-pressed="true"/);
  assert.match(current(), /data-action="preset:75" aria-pressed="true"/);
  assert.match(current(), /id="program-choice-body" >/);
});

test("a failed automatic selection keeps visible retry and cancel actions", async () => {
  for (const outcome of ["cancel", "retry"]) {
    let fail = true;
    const { p, calls } = panel(configuration(), null, async () => {
      if (fail) throw Error("save failed");
      return {};
    });
    const current = renderControls(p);
    await assert.rejects(() => p.action("program-mode:program"), /save failed/);
    assert.equal(p.programDirty(), true);
    assert.match(current(), /data-action="program-mode:program" aria-pressed="true"/);
    assert.deepEqual(plain(p.storedProgramChoice(programs)), { mode: "constant" });
    assert.match(current(), /id="program-choice-body" >/);
    assert.match(current(), /Noch nicht übernommen: Ruhig/);
    assert.match(current(), /data-action="program-cancel-draft"/);
    assert.match(current(), /data-action="program-apply"(?![^>]*disabled)/);
    fail = false;
    await p.action(outcome === "cancel" ? "program-cancel-draft" : "program-apply");
    assert.equal(p.programDirty(), false);
    assert.equal(calls.length, outcome === "cancel" ? 1 : 2);
    assert.match(
      current(),
      outcome === "cancel"
        ? /data-action="program-mode:constant" aria-pressed="true"/
        : /data-action="program-select:quiet" aria-pressed="true"/,
    );
  }
});

test("changing individual steps to even saves without a session and stages within one", async () => {
  for (const session of [null, { timeline: {} }]) {
    const { p, calls } = panel(
      { ...configuration("progressive"), temperature_steps: [70, 88, 90] },
      session,
    );
    const fields = new Map(
      [70, 88, 90].map((value, index) => [
        `#free-step-${index}`,
        { value: String(value) },
      ]),
    );
    p.$ = (selector) => fields.get(selector) || null;
    p.shadowRoot = {
      querySelectorAll: () =>
        [...fields]
          .filter(([selector]) => /^#free-step-\d+$/.test(selector))
          .map(([, input]) => input),
    };
    // This fixture reads the production renderer's values; it does not derive
    // the expected distribution or reproduce the program conversion.
    p.drawCurrent = () => {
      const form = p.freeProgramForm(p.programBounds());
      fields.clear();
      for (const [, id, value] of form.matchAll(
        /<input id="([^"]+)"[^>]*value="([^"]*)"/g,
      ))
        fields.set(`#${id}`, { value });
    };
    p.progressionDraft = { "progression-start": "85", "progression-end": "95" };
    await p.action("program-kind:even");
    if (session) {
      assert.equal(calls.length, 0);
      assert.equal(p.programDirty(), true);
      await p.action("program-apply");
    }
    assert.deepEqual(plain(calls), [
      [
        "/entry/program",
        "POST",
        {
          target_temperature_c: 70,
          final_temperature_c: 90,
          temperature_gangs: 3,
        },
      ],
    ]);
    assert.equal(p.state.configuration.temperature_steps, null);
    assert.deepEqual(plain(p.freeSteps()), [70, 80, 90]);
    if (!session) {
      await p.action("program-kind:steps");
      const before = calls.length,
        countInput = {
          id: "free-step-count",
          matches: (selector) => selector === "[data-free-step-count]",
        };
      for (const value of ["", "0", "1.5", "9"]) {
        countInput.value = value;
        assert.throws(() => p.finishFreeProgramInput(countInput), /Stufenzahl/);
        await assert.rejects(() => p.applyProgram(), /Stufenzahl/);
        assert.equal(p.progressionDraft["free-step-count"], value);
        assert.match(
          p.freeProgramForm(p.programBounds()),
          new RegExp(`id="free-step-count"[^>]*value="${value}"`),
        );
      }
      assert.equal(calls.length, before);
      countInput.value = "4";
      await p.finishFreeProgramInput(countInput);
      await p.finishFreeProgramInput(countInput);
      assert.equal(
        calls.length,
        before + 1,
        "Enter followed by change saves one count",
      );
      assert.deepEqual(plain(calls.at(-1)), [
        "/entry/program",
        "POST",
        {
          temperature_steps: [70, 80, 90, 90],
        },
      ]);
    }
  }
});

test("completed fields serialize while a newer unfinished input survives both responses", async () => {
  const releases = [];
  const { p, calls } = panel(
    configuration("progressive"),
    null,
    (_path, _method, body) =>
      new Promise((resolve) => releases.push(() => resolve({ parameters: body }))),
  );
  const edit = individualFields(p);
  edit("end", "91");
  const saving = p.applyProgram();
  await Promise.resolve();
  assert.doesNotMatch(
    p.freeProgramForm(p.programBounds()),
    /<input[^>]*disabled/,
    "saving one field leaves the following fields operable",
  );
  edit("gangs", "4");
  await p.applyProgram();
  await p.applyProgram(); // Enter followed by change is the same completed value.
  edit("start", "7");
  const unfinished = p.progressionDraft;
  assert.equal(calls.length, 1);
  releases[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(plain(calls), [
    ["/entry/temperature", "POST", { final_temperature_c: 91 }],
    ["/entry/temperature", "POST", { temperature_gangs: 4 }],
  ]);
  assert.equal(p.progressionDraft, unfinished);
  releases[1]();
  await saving;
  assert.equal(p.progressionDraft, unfinished);
  assert.equal(p.$("#progression-start").value, "7");
  assert.equal(p.state.configuration.parameters.target_temperature_c, 80);
  assert.equal(p.state.configuration.parameters.final_temperature_c, 91);
  assert.equal(p.state.configuration.parameters.temperature_gangs, 4);
  assert.equal(
    p.programSaveState,
    null,
    "an unfinished draft is not reported as saved",
  );
});

test("clicking the already selected input kind during autosave leaves one request", async () => {
  let release;
  const { p, calls } = panel(
    configuration("progressive"),
    null,
    (_path, _method, body) =>
      new Promise((resolve) => (release = () => resolve({ parameters: body }))),
  );
  const edit = individualFields(p);
  edit("end", "91");
  const saving = p.applyProgram();
  await Promise.resolve();
  const draft = p.progressionDraft;
  await p.action("program-kind:even");
  assert.equal(p.progressionDraft, draft);
  release();
  await saving;
  assert.deepEqual(plain(calls), [
    ["/entry/temperature", "POST", { final_temperature_c: 91 }],
  ]);
});

test("returning to the saved input kind clears its completed draft", async () => {
  let release;
  const { p, calls } = panel(
    configuration("progressive"),
    null,
    (_path, _method, body) =>
      new Promise((resolve) => (release = () => resolve({ parameters: body }))),
  );
  const fields = new Map();
  p.$ = (selector) => fields.get(selector) || null;
  p.shadowRoot = {
    querySelectorAll: (selector) =>
      selector === "[data-free-step]"
        ? [...fields]
            .filter(([key]) => /^#free-step-\d+$/.test(key))
            .map(([, input]) => input)
        : [],
  };
  p.drawCurrent = () => {
    const form = p.freeProgramForm(p.programBounds());
    fields.clear();
    for (const [, id, value] of form.matchAll(
      /<input id="([^"]+)"[^>]*value="([^"]*)"/g,
    ))
      fields.set(`#${id}`, { value });
  };
  p.drawCurrent();
  fields.get("#progression-end").value = "91";
  p.progressionDraft = { "progression-end": "91" };
  const saving = p.applyProgram();
  await Promise.resolve();
  await p.action("program-kind:steps");
  await p.action("program-kind:even");
  release();
  await saving;
  assert.deepEqual(plain(calls), [
    ["/entry/temperature", "POST", { final_temperature_c: 91 }],
  ]);
  assert.equal(p.progressionDraft, null);
  p.state.configuration.parameters.final_temperature_c = 95;
  p.drawCurrent();
  assert.match(
    p.freeProgramForm(p.programBounds()),
    /id="progression-end"[^>]*value="95"/,
  );
  fields.get("#progression-end").value = "96";
  p.progressionDraft = { "progression-end": "96" };
  const second = p.applyProgram();
  await Promise.resolve();
  await p.action("program-kind:steps");
  await p.action("program-kind:even");
  p.progressionDraft = { ...p.progressionDraft, "progression-end": "" };
  p.drawCurrent();
  release();
  await second;
  assert.equal(p.progressionDraft["progression-end"], "");
  assert.equal(p.programSaveState, null);
  assert.match(
    p.freeProgramForm(p.programBounds()),
    /id="progression-end"[^>]*value=""/,
  );
});

test("a named response cannot change the visible individual form of a queued choice", async () => {
  const releases = [];
  const { p, calls } = panel(
    configuration(),
    null,
    () => new Promise((resolve) => releases.push(resolve)),
  );
  p.drawCurrent = () => {
    p.form =
      p.programMode(p.state.configuration.temperature_programs) === "individual"
        ? p.freeProgramForm(p.programBounds())
        : "";
  };
  p.$ = (selector) => {
    const id = selector.slice(1),
      value = p.form.match(new RegExp(`<input id="${id}"[^>]*value="([^"]*)"`));
    return value ? { value: value[1] } : null;
  };
  p.shadowRoot = { querySelectorAll: () => [] };
  const first = p.action("program-select:quiet");
  await Promise.resolve();
  await p.action("program-mode:individual");
  assert.match(p.form, /id="progression-end"[^>]*value="90"/);
  releases[0]({
    parameters: {
      target_temperature_c: 80,
      final_temperature_c: 100,
      temperature_gangs: 3,
    },
    program_mode: "progressive",
    selected_program_id: "quiet",
    temperature_steps: [80, 90, 100],
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(p.form, /id="progression-end"[^>]*value="90"/);
  assert.match(p.form, /data-action="program-kind:even" aria-pressed="true"/);
  assert.deepEqual(plain(calls), [
    ["/entry/program", "POST", { profile: "quiet" }],
    [
      "/entry/program",
      "POST",
      {
        target_temperature_c: 80,
        final_temperature_c: 90,
        temperature_gangs: 3,
      },
    ],
  ]);
  releases[1]({
    parameters: {
      target_temperature_c: 80,
      final_temperature_c: 90,
      temperature_gangs: 3,
    },
    program_mode: "progressive",
    selected_program_id: null,
    temperature_steps: null,
  });
  await first;
});

test("returning to the request's named choice needs no duplicate save or leftover draft", async () => {
  let release;
  const { p, calls } = panel(
    {
      ...configuration(),
      temperature_programs: [
        ...programs,
        { id: "active", name: "Aktiv", start_c: 80, end_c: 95, distribution_gangs: 3 },
      ],
    },
    null,
    () => new Promise((resolve) => (release = resolve)),
  );
  const first = p.action("program-select:quiet");
  await Promise.resolve();
  await p.action("program-select:active");
  await p.action("program-select:quiet");
  release({ selected_program_id: "quiet", program_mode: "progressive" });
  await first;
  assert.deepEqual(plain(calls), [["/entry/program", "POST", { profile: "quiet" }]]);
  assert.equal(p.programSelectionDraft, null);
  assert.equal(p.programDirty(), false);
  await p.action("program-mode:program");
  await p.action("program-select:quiet");
  assert.equal(calls.length, 1);
  assert.equal(p.programSelectionDraft, null);
  p.state.configuration.selected_program_id = null;
  p.state.configuration.program_mode = "constant";
  assert.equal(p.programMode(p.state.configuration.temperature_programs), "constant");
});

test("a failed initial or queued field save keeps the latest draft and stops automatic writes", async () => {
  for (const failAt of [1, 2]) {
    let release;
    let requestCount = 0;
    const { p, calls } = panel(
      configuration("progressive"),
      null,
      async (_path, _method, body) => {
        const number = ++requestCount;
        if (number === 1) await new Promise((resolve) => (release = resolve));
        if (number === failAt) throw Error("save failed");
        return { parameters: body };
      },
    );
    const edit = individualFields(p);
    edit("end", "91");
    const saving = p.applyProgram();
    await Promise.resolve();
    edit("gangs", "4");
    await p.applyProgram();
    const latest = p.progressionDraft;
    const rejected = assert.rejects(saving, /save failed/);
    release();
    await rejected;
    assert.equal(calls.length, failAt);
    assert.equal(p.progressionDraft, latest);
    assert.equal(
      p.state.configuration.parameters.final_temperature_c,
      failAt === 1 ? 90 : 91,
    );
    assert.equal(p.state.configuration.parameters.temperature_gangs, 3);
    assert.equal(p.programSaveState, null);
    await p.refresh(true);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls.length, failAt, "errors do not retry a waiting draft");
    await p.action("program-kind:even");
    assert.equal(calls.length, failAt + 1, "same input kind retries the draft");
    assert.equal(p.programDirty(), false);
  }
});

test("a queued automatic change requires confirmation after session start and never crosses an entry or disconnect", async () => {
  for (const interrupt of ["session", "entry", "disconnect"]) {
    let release;
    const { p, calls } = panel(
      configuration("progressive"),
      null,
      async () => new Promise((resolve) => (release = resolve)),
    );
    const edit = individualFields(p);
    edit("end", "91");
    const saving = p.applyProgram();
    await Promise.resolve();
    edit("gangs", "4");
    await p.applyProgram();
    const latest = p.progressionDraft;
    if (interrupt === "session") p.state.session = { timeline: {} };
    if (interrupt === "entry") {
      p.entry = "another-entry";
      p.generation++;
      p.programRequest = null;
    }
    if (interrupt === "disconnect") p.isConnected = false;
    release({ parameters: { final_temperature_c: 91 } });
    await saving;
    assert.equal(calls.length, 1);
    assert.equal(p.progressionDraft, latest);
    assert.equal(p.state.configuration.parameters.temperature_gangs, 3);
    assert.equal(
      p.state.configuration.parameters.final_temperature_c,
      interrupt === "session" ? 91 : 90,
    );
    if (interrupt === "session") {
      assert.equal(p.programChoiceOpen, true);
      assert.equal(p.programSaveState, null);
    }
  }
});

test("the individual distribution explains the current validated field draft", () => {
  const { p } = panel(configuration("progressive"));
  p.programInfoOpen = "free";
  p.progressionDraft = { "progression-end": "100" };
  let form = p.freeProgramForm(p.programBounds());
  assert.match(form, /id="progression-end"[^>]*value="100"/);
  assert.match(form, /80 → 90 → 100 °C/);
  assert.doesNotMatch(form, /80 → 85 → 90 °C/);
  p.progressionDraft = { "progression-end": "" };
  form = p.freeProgramForm(p.programBounds());
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
      "/entry/program",
      "POST",
      { profile: program.id },
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
    "/entry/program",
    "POST",
    { temperature_steps: [80, 86, 90] },
  ]);
  assert.equal(p.state.configuration.selected_program_id, null);
  assert.equal(p.programMode(catalog), "individual");
  await p.action("program-mode:constant");
  await p.action("program-apply");
  assert.deepEqual(plain(calls.at(-1)), [
    "/entry/program",
    "POST",
    { profile: "constant" },
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
  for (const mode of ["program", "individual", "constant"])
    assert.match(control, new RegExp(`data-action="program-mode:${mode}"`));
  assert.equal((control.match(/data-action="program-apply"/g) || []).length, 1);
  assert.ok(
    control.indexOf('data-action="program-apply"') >
      control.indexOf('data-action="finish-session:gap"'),
  );
  assert.match(
    control,
    /data-action="program-apply"[^>]*program-main[^>]*>Programm übernehmen/,
  );
  assert.match(control, /data-action="program-select:quiet" aria-pressed="true"/);
  assert.doesNotMatch(control, /Vorgemerkt|program-draft-label/);
  assert.match(control, /data-action="program-cancel-draft"/);
  assert.match(control, /data-action="operation"[^>]*>Fortsetzen/);
  assert.doesNotMatch(nodes.get("#details").innerHTML, /data-action=|<button/);
  p.programSelectionDraft = null;
  p.programSaveState = "saved";
  p.drawCurrent();
  assert.match(
    nodes.get("#current").innerHTML,
    /class="program-pending" aria-hidden="true" inert/,
    "the status row keeps its space during confirmation without a hidden action",
  );
  assert.doesNotMatch(
    nodes.get("#current").innerHTML,
    /program-draft-label">Vorgemerkt/,
  );
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
