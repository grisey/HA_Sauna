// Runs without Home Assistant or a browser: controls stay on the control page.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const frontendDefaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).frontend;

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
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const appearanceCatalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).appearance;
vm.runInNewContext(source, sandbox);

assert.match(source, /data-action="control-mode:automatic"/);
assert.match(source, /data-action="control-mode:manual"/);
assert.match(source, /manual-light-overview/);
assert.doesNotMatch(source, /Normallicht/);
assert.doesNotMatch(source, /Raumlicht/);
assert.doesNotMatch(source, /Zwangskühlung pausiert/);
assert.ok(!source.includes("Übersteuerung: höchstens"));
assert.doesNotMatch(source, /bis zum nächsten Phasenwechsel aktiv/);
assert.match(source, /s\.configuration\.program_mode\s*===\s*"progressive"/);
assert.doesNotMatch(
  source,
  /Temperaturprogramm<\/dt><dd>\$\{p\.final_temperature_c!=null/,
);
assert.doesNotMatch(source, /Servus|greeting/);

const makePanel = (permissions, lightValue = "42") => {
  const calls = [];
  const panel = Object.assign(Object.create(Panel.prototype), {
    entry: "entry-1",
    state: {
      frontend_defaults: frontendDefaults,
      permissions,
      configuration: { control_mode: "manual" },
      operation_enabled: true,
    },
    api: async (...args) => calls.push(args),
    refresh: async () => {},
    message: () => {},
    $: (selector) =>
      ["#manual-light-value", "#manual-light-value-overview"].includes(selector)
        ? { value: lightValue }
        : null,
  });
  return { panel, calls };
};

const renderCurrent = (
  mode,
  controls = {},
  heatingFeedback = false,
  target = "#current",
  grants = {},
  locked = false,
  session = null,
  startErrors = [],
  program = {},
  operationEnabled = false,
) => {
  const permissions = {
    admin: true,
    control: true,
    heater: true,
    light: true,
    temperature: true,
    program: true,
    ...grants,
  };
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
    return nodes.get(selector);
  };
  const panel = Object.assign(Object.create(Panel.prototype), {
    hass: { user: { name: "Testperson", is_admin: permissions.admin } },
    $: node,
    progressionDraft: null,
    programSelectionDraft: program.selectionDraft ?? null,
    manualLightDraft: null,
    state: {
      frontend_defaults: frontendDefaults,
      now: "2026-09-20T12:00:00Z",
      session,
      last_session: null,
      appearance_catalog: appearanceCatalog,
      appearance: program.appearance,
      configuration: {
        control_mode: mode,
        program_mode: "constant",
        selected_program_id: null,
        temperature_programs: [],
        parameters: {
          preset_count: 0,
          preset_start_c: 70,
          preset_step_c: 5,
          session_light_brightness_percent: 50,
          manual_override_minutes: 10,
        },
        ...(program.configuration || {}),
      },
      parameters: [
        { key: "target_temperature_c", minimum: 30, maximum: 100, integer: false },
      ],
      measurements: [],
      measurement_status: {},
      mechanical_timer: { state: "idle", remaining_seconds: 0 },
      manual_controls: {
        light: { normal: 25, automatic: 12, ...controls.light },
        heater: controls.heater || {},
      },
      permissions,
      configuration_locked: locked,
      issues: [],
      start_errors: startErrors,
      operation_enabled: operationEnabled,
      heating_feedback: heatingFeedback,
      heating_observation: { source: "unknown" },
      phase: "manuell",
      gang_count: 0,
      energy_kwh: 0,
      energy_source: "estimated",
      target_temperature: 80,
      start_availability: null,
      phase_timer: null,
    },
  });
  panel.drawCurrent();
  return nodes.get(target).innerHTML;
};

(async () => {
  const enabled = { admin: true, heater: true, light: true };
  let { panel, calls } = makePanel(enabled);
  await panel.action("heater:true");
  await panel.action("heater:false");
  await panel.action("heater:auto");
  await panel.action("manual-light-overview");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/heater", "POST", { value: true }],
    ["/entry-1/heater", "POST", { value: false }],
    ["/entry-1/heater", "POST", { value: null }],
    ["/entry-1/light", "POST", { value: 42 }],
  ]);

  for (const actualOn of [false, true]) {
    const same = makePanel(enabled);
    same.panel.state.manual_controls = {
      heater: { manual: null, observation: { available: true, on: actualOn } },
      light: {
        manual: null,
        observation: {
          available: true,
          brightness_percent: actualOn ? 18 : 0,
        },
      },
    };
    await same.panel.action(`heater:${actualOn}`);
    await same.panel.action(`light:${actualOn}`);
    assert.equal(
      same.calls.length,
      0,
      "clicking the observed selected state must not send a new command or renew an override",
    );
    await same.panel.action(`heater:${!actualOn}`);
    await same.panel.action(`light:${!actualOn}`);
    await same.panel.action("heater:auto");
    await same.panel.action("light:auto");
    assert.deepEqual(JSON.parse(JSON.stringify(same.calls)), [
      ["/entry-1/heater", "POST", { value: !actualOn }],
      ["/entry-1/light", "POST", { value: !actualOn }],
      ["/entry-1/heater", "POST", { value: null }],
      ["/entry-1/light", "POST", { value: null }],
    ]);
  }

  ({ panel, calls } = makePanel(enabled, "101"));
  await assert.rejects(
    () => panel.action("manual-light-overview"),
    /Helligkeit zwischen 0 und 100/,
  );
  assert.equal(calls.length, 0, "invalid brightness must not reach the actuator API");

  ({ panel, calls } = makePanel({ admin: false, heater: false, light: false }));
  await panel.action("heater:true");
  await panel.action("manual-light-overview");
  assert.equal(calls.length, 0, "denied controls must not route API calls");

  ({ panel, calls } = makePanel(enabled));
  panel.state.configuration.control_mode = "automatic";
  panel.state.operation_enabled = false;
  panel.state.session = { timeline: {} };
  await panel.action("heater:true");
  await panel.action("light:true");
  await panel.action("manual-light-overview");
  assert.equal(calls.length, 0, "paused automatic controls remain unavailable");
  panel.state.operation_enabled = true;
  await panel.action("heater:true");
  await panel.action("light:true");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/heater", "POST", { value: true }],
    ["/entry-1/light", "POST", { value: true }],
  ]);

  ({ panel, calls } = makePanel({
    admin: true,
    heater: true,
    light: true,
    control: true,
  }));
  panel.state.configuration = { control_mode: "manual" };
  panel.state.operation_enabled = false;
  await panel.action("heater:true");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/heater", "POST", { value: true }],
  ]);
  let finishStart;
  const delayed = makePanel(enabled);
  delayed.panel.generation = 1;
  delayed.panel.state.configuration = { control_mode: "manual" };
  delayed.panel.state.operation_enabled = false;
  delayed.panel.api = async (...args) => {
    delayed.calls.push(args);
    if (args[0] === "/entry-1/heater")
      await new Promise((resolve) => (finishStart = resolve));
  };
  const pendingStart = delayed.panel.action("heater:true");
  delayed.panel.entry = "entry-2";
  delayed.panel.generation++;
  finishStart();
  await pendingStart;
  assert.deepEqual(JSON.parse(JSON.stringify(delayed.calls)), [
    ["/entry-1/heater", "POST", { value: true }],
  ]);
  let rejectStart;
  delayed.panel.entry = "entry-1";
  delayed.panel.generation++;
  delayed.panel.api = async () =>
    new Promise((_resolve, reject) => (rejectStart = reject));
  const staleFailure = delayed.panel.action("heater:true");
  delayed.panel.entry = "entry-2";
  delayed.panel.generation++;
  rejectStart(Error("alter Startfehler"));
  await staleFailure;
  const preferenceCalls = [],
    startButton = { disabled: false, textContent: "Als Startseite festlegen" },
    startStatus = { textContent: "" };
  const startPage = Object.assign(Object.create(Panel.prototype), {
    state: {
      frontend_defaults: frontendDefaults,
      permissions: { admin: false },
    },
    message: () => {},
    api: async () => {
      throw Error("Sauna API must not be called");
    },
    hass: {
      user: { is_admin: false },
      callWS: async (request) => {
        preferenceCalls.push(request);
        return request.type === "frontend/get_user_data"
          ? {
              value: { default_panel: "lovelace", theme: "night", sidebarHidden: true },
            }
          : undefined;
      },
    },
    $: (selector) =>
      selector === '[data-action="default-page"]'
        ? startButton
        : selector === "#start-page-status"
          ? startStatus
          : null,
  });
  await startPage.action("default-page");
  assert.deepEqual(JSON.parse(JSON.stringify(preferenceCalls)), [
    { type: "frontend/get_user_data", key: "core" },
    {
      type: "frontend/set_user_data",
      key: "core",
      value: { default_panel: "ha-sauna", theme: "night", sidebarHidden: true },
    },
  ]);
  assert.equal(startButton.textContent, "Als Startseite festgelegt");

  const failedButton = { disabled: false },
    failedStatus = { textContent: "vorher" };
  const failedStartPage = Object.assign(Object.create(Panel.prototype), {
    state: {
      frontend_defaults: frontendDefaults,
      permissions: { admin: false },
    },
    message: () => {},
    hass: {
      callWS: async () => {
        throw Error("profile unavailable");
      },
    },
    $: (selector) =>
      selector === '[data-action="default-page"]'
        ? failedButton
        : selector === "#start-page-status"
          ? failedStatus
          : null,
  });
  await assert.rejects(
    () => failedStartPage.action("default-page"),
    /profile unavailable/,
  );
  assert.equal(
    failedButton.disabled,
    false,
    "a failed profile update leaves the button usable",
  );
  const automatic = renderCurrent("automatic"),
    manual = renderCurrent("manual");
  assert.match(automatic, /Temperaturwahl/);
  assert.match(automatic, /id="program-choice-body" >/);
  assert.doesNotMatch(automatic, /program-current|data-action="program-toggle"/);
  assert.doesNotMatch(automatic, /Vorgemerkt|program-draft-label/);
  assert.match(automatic, /data-action="program-mode:program" aria-pressed="false"/);
  assert.match(automatic, /data-action="program-mode:individual" aria-pressed="false"/);
  assert.match(automatic, /data-action="program-mode:constant" aria-pressed="true"/);
  assert.match(automatic, /temperature-presets/);
  assert.doesNotMatch(automatic, /program-named-list|program-form/);
  for (const html of [automatic, manual]) {
    assert.match(html, /data-light-arc[^>]*role="slider"/);
    assert.doesNotMatch(
      html,
      /manual-overrides|manual-status|oven-feedback|light-editor/,
    );
    assert.doesNotMatch(html, /<button[^>]*data-action="manual-light-overview"/);
  }
  assert.doesNotMatch(
    automatic,
    /Übersteuerung: höchstens|Keine laufende Übersteuerung/,
  );
  assert.equal((automatic.match(/Übersteuerung: höchstens/g) || []).length, 0);
  assert.match(automatic, /class="manual-section manual-controls manual-entry"/);
  assert.match(automatic, /Ofen und Licht/);
  assert.match(automatic, /data-action="manual-entry"[^>]*>Manuell steuern<\/button>/);
  assert.doesNotMatch(
    automatic,
    /manual-section manual-(heater|light)|data-action="(?:heater|light):/,
  );
  assert.match(manual, /class="manual-section manual-heater"/);
  assert.doesNotMatch(manual, /data-action="(?:operation|finish-session:|end-phase:)/);
  assert.match(automatic, /data-action="operation"/);
  assert.doesNotMatch(automatic, /Ein startet zuerst den Saunabetrieb/);
  assert.doesNotMatch(automatic, /Gedimmt/);
  assert.match(manual, /class="manual-section manual-light"/);
  assert.doesNotMatch(manual, /Manuelle Ofenübersteuerung/);
  assert.match(automatic, /data-light-arc/);
  assert.equal(
    (automatic.match(/data-action="light:auto"/g) || []).length,
    0,
    "idle automatic mode exposes the explicit manual entry",
  );
  const deniedOverride = renderCurrent("manual", {}, false, "#current", {
    admin: true,
    heater: false,
    light: false,
  });
  assert.match(deniedOverride, /data-action="heater:true"[^>]*disabled/);
  assert.match(deniedOverride, /data-light-arc[^>]*aria-disabled="true"/);
  for (const feedback of [false, true, null])
    assert.doesNotMatch(
      renderCurrent("automatic", {}, feedback),
      /oven-feedback|manual-status/,
    );
  assert.doesNotMatch(automatic, /data-door-status|environment-status/);
  assert.doesNotMatch(manual, /Temperaturwahl|program-types|temperature-presets/);
  assert.match(manual, /data-light-arc/);
  assert.doesNotMatch(
    manual,
    /id="manual-light-value-overview"|Gedimmt|Hell<\/button>/,
  );
  assert.match(manual, /data-action="light:true"[^>]*>Ein<\/button>/);
  assert.doesNotMatch(
    manual,
    /environment-status|data-door-status/,
    "manual overview has no environment status tile",
  );

  const userAutomatic = renderCurrent("automatic", {}, false, "#current", {
    admin: false,
    heater: false,
  });
  assert.match(userAutomatic, /Temperaturwahl/);
  assert.match(userAutomatic, /data-target-arc="true"/);
  assert.match(
    userAutomatic,
    /data-action="control-mode:manual" aria-pressed="false" >Manuell/,
  );
  assert.doesNotMatch(userAutomatic, /Gedimmt|Manuelle Ofenübersteuerung/);
  assert.doesNotMatch(userAutomatic, /data-action="heater:/);
  assert.match(userAutomatic, /data-light-arc[^>]*aria-disabled="true"/);
  for (const preset of ["auto", "false", "true"])
    assert.doesNotMatch(
      userAutomatic,
      new RegExp(`data-action="light:${preset}"[^>]*disabled`),
    );
  const userManual = renderCurrent("manual", {}, false, "#current", { admin: false });
  assert.doesNotMatch(
    userManual,
    /data-action="(?:operation|finish-session:|end-phase:)/,
  );
  assert.match(
    userManual,
    /data-action="control-mode:automatic" aria-pressed="false" >Automatik/,
  );
  assert.match(userManual, /data-action="heater:true" aria-pressed="false" >Ein/);
  assert.match(userManual, /data-action="heater:false" aria-pressed="false" >Aus/);
  for (const preset of ["false", "true"])
    assert.ok(userManual.includes(`data-action="light:${preset}"`));
  assert.doesNotMatch(userManual, /Temperaturwahl|data-target-arc|heater:auto/);
  const readOnly = renderCurrent("manual", {}, false, "#current", {
    admin: false,
    control: false,
    heater: false,
    light: false,
  });
  for (const action of [
    "control-mode:automatic",
    "control-mode:manual",
    "heater:true",
    "heater:false",
    "light:true",
  ])
    assert.match(readOnly, new RegExp(`data-action="${action}"[^>]*disabled`));

  const pausedSession = {
    timeline: {
      active: null,
      completed: [],
      session_started_at: "2026-09-20T11:00:00Z",
    },
    heating: { elapsed_seconds: 0 },
    deadlines: [
      { purpose: "session_gap", token: "gap-current", due_at: "2026-09-20T12:10:00Z" },
    ],
  };
  const userLocked = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    { admin: false },
    true,
    pausedSession,
  );
  assert.doesNotMatch(userLocked, /data-action="control-mode:/);
  const controlCases = [
    { mode: "automatic", session: null, operating: false },
    { mode: "automatic", session: pausedSession, operating: false },
    { mode: "automatic", session: pausedSession, operating: true },
    { mode: "manual", session: null, operating: false },
    { mode: "manual", session: pausedSession, operating: false },
    { mode: "manual", session: pausedSession, operating: true },
  ];
  for (const admin of [false, true]) {
    for (const light of [false, true]) {
      for (const scenario of controlCases) {
        const allowed =
            scenario.mode === "manual" || !!(scenario.session && scenario.operating),
          idleEntry = scenario.mode === "automatic" && !scenario.session,
          context = JSON.stringify({ admin, light, ...scenario }),
          html = renderCurrent(
            scenario.mode,
            {},
            false,
            "#current",
            { admin, light },
            false,
            scenario.session,
            [],
            {},
            scenario.operating,
          ),
          presets = Array.from(
            html.matchAll(/<button\b[^>]*data-action="light:([^"]+)"[^>]*>/g),
          );
        assert.equal(
          /data-action="control-mode:/.test(html),
          !scenario.session,
          context,
        );
        assert.deepEqual(
          presets.map((match) => match[1]),
          idleEntry
            ? []
            : scenario.mode === "manual"
              ? ["false", "true"]
              : ["auto", "false", "true"],
          context,
        );
        for (const [tag, preset] of presets) {
          assert.equal(/\bdisabled\b/.test(tag), !(light && allowed), context);
          const invocation = makePanel({ admin, light, heater: true });
          Object.assign(invocation.panel.state, {
            configuration: { control_mode: scenario.mode },
            session: scenario.session,
            operation_enabled: scenario.operating,
          });
          await invocation.panel.action(`light:${preset}`);
          assert.deepEqual(
            JSON.parse(JSON.stringify(invocation.calls)),
            light && allowed
              ? [
                  [
                    "/entry-1/light",
                    "POST",
                    {
                      value: { auto: null, false: false, normal: "normal", true: true }[
                        preset
                      ],
                    },
                  ],
                ]
              : [],
            context,
          );
        }
        const invocation = makePanel({ admin, light, heater: true });
        Object.assign(invocation.panel.state, {
          configuration: { control_mode: scenario.mode },
          session: scenario.session,
          operation_enabled: scenario.operating,
        });
        await invocation.panel.action("manual-light-overview");
        assert.equal(invocation.calls.length, Number(light && allowed), context);
        const arc = html.match(/<[^>]*data-light-arc[^>]*>/)[0];
        assert.match(arc, /role="slider"/);
        assert.equal(/aria-disabled="true"/.test(arc), !(light && allowed), context);
        assert.doesNotMatch(html, /id="manual-light-value-overview"/);
        if (idleEntry) assert.match(html, /Manuell steuern/);
        else assert.match(html, /class="manual-section manual-heater"/);
        invocation.calls.length = 0;
        await invocation.panel.action("heater:true");
        assert.deepEqual(
          JSON.parse(JSON.stringify(invocation.calls)),
          allowed ? [["/entry-1/heater", "POST", { value: true }]] : [],
          context,
        );
      }
    }
  }
  const namedPrograms = [
    { id: "p1", name: "Mild", start_c: 75, end_c: 80, distribution_gangs: 2 },
    {
      id: "p2",
      name: "Sehr langes Abendprogramm mit mehreren Temperaturstufen",
      start_c: 80,
      end_c: 90,
      distribution_gangs: 2,
    },
  ];
  const programConfig = {
    temperature_programs: namedPrograms,
    selected_program_id: "p1",
    program_mode: "progressive",
  };
  const namedOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    {},
    false,
    pausedSession,
    [],
    { configuration: programConfig },
  );
  assert.match(
    namedOverview,
    /class="program-active-label">Mild<\/strong>[\s\S]*data-action="program-toggle"[^>]*aria-expanded="false"[^>]*>Ändern<\/button>/,
  );
  assert.match(namedOverview, /id="program-choice-body" hidden/);
  const draftedOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    {},
    false,
    pausedSession,
    [],
    { configuration: programConfig, selectionDraft: { mode: "program", id: "p2" } },
  );
  assert.match(draftedOverview, /class="program-active-label">Mild<\/strong>/);
  assert.match(draftedOverview, /Noch nicht übernommen: Sehr langes Abendprogramm/);
  assert.match(draftedOverview, /data-action="program-select:p2" aria-pressed="true"/);
  assert.match(draftedOverview, /data-action="program-select:p1" aria-pressed="false"/);
  assert.doesNotMatch(draftedOverview, /Vorgemerkt|program-draft-label/);
  const confirmedOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    {},
    false,
    pausedSession,
    [],
    { configuration: { ...programConfig, selected_program_id: "p2" } },
  );
  assert.match(
    confirmedOverview,
    /class="program-active-label">Sehr langes Abendprogramm mit mehreren Temperaturstufen<\/strong>/,
  );
  const individualOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    {},
    false,
    pausedSession,
    [],
    { configuration: { program_mode: "progressive" } },
  );
  assert.match(
    individualOverview,
    /class="program-active-label">Individuell<\/strong>/,
  );
  const constantOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    {},
    false,
    pausedSession,
  );
  assert.match(constantOverview, /class="program-active-label">Konstant<\/strong>/);
  const pausedOverview = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    { admin: false, control: true },
    false,
    pausedSession,
  );
  const pausedDetails = renderCurrent(
    "automatic",
    {},
    false,
    "#details",
    { admin: false, control: true },
    false,
    pausedSession,
  );
  assert.match(
    pausedOverview,
    /class="tile operation stop" data-action="finish-session:gap-current"[^>]*>Endgültig beenden<\/button>/,
  );
  assert.match(pausedOverview, /data-action="operation"[^>]*>Fortsetzen<\/button>/);
  assert.doesNotMatch(pausedOverview, /Einschalten/);
  assert.doesNotMatch(pausedDetails, /data-action=|<button|<input/);
  assert.match(pausedDetails, /Wiederaufnahme/);
  const pausedWithStartError = renderCurrent(
    "automatic",
    {},
    false,
    "#current",
    { admin: false, control: true },
    false,
    pausedSession,
    ["missing_sensor"],
  );
  assert.match(
    pausedWithStartError,
    /data-action="finish-session:gap-current"(?![^>]*disabled)/,
  );
  assert.match(
    pausedWithStartError,
    /data-action="operation"[^>]*disabled>Fortsetzen<\/button>/,
  );
  ({ panel, calls } = makePanel({ admin: false, control: true }));
  panel.state.operation_enabled = false;
  await panel.action("finish-session:gap-current");
  await panel.action("operation");
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls)),
    [
      ["/entry-1/finish-session", "POST", { token: "gap-current" }],
      ["/entry-1/control", "POST", { enabled: true }],
    ],
    "finishing uses the displayed gap token while resuming stays a regular control request",
  );
  calls.length = 0;
  panel.state.permissions.control = false;
  await panel.action("finish-session:gap-current");
  assert.equal(
    calls.length,
    0,
    "finishing also requires control permission in the action handler",
  );

  ({ panel, calls } = makePanel({
    admin: false,
    control: true,
    heater: true,
    light: true,
  }));
  panel.state.configuration = { control_mode: "manual" };
  panel.state.operation_enabled = false;
  await panel.action("heater:true");
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls)),
    [["/entry-1/heater", "POST", { value: true }]],
    "a regular user sends one atomic heater command",
  );
  calls.length = 0;
  await panel.action("control-mode:automatic");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/control-mode", "POST", { mode: "automatic" }],
  ]);
  calls.length = 0;
  panel.state.configuration_locked = true;
  await panel.action("control-mode:manual");
  await panel.action("manual-light-overview");
  await panel.action("details");
  assert.deepEqual(
    JSON.parse(JSON.stringify(calls)),
    [["/entry-1/light", "POST", { value: 42 }]],
    "session locking does not disable light control",
  );
  calls.length = 0;
  panel.state.permissions.heater = false;
  await panel.action("heater:true");
  assert.equal(
    calls.length,
    0,
    "automatic heater overrides still require the server's permission",
  );
  panel.state.permissions.control = false;
  panel.state.configuration_locked = false;
  await panel.action("control-mode:automatic");
  assert.equal(
    calls.length,
    0,
    "a user without control permission cannot change modes",
  );

  const automaticHeater = renderCurrent(
    "automatic",
    {
      heater: { manual: null, observation: { available: true, on: true } },
      light: { manual: null },
    },
    true,
    "#current",
    {},
    false,
    pausedSession,
    [],
    {},
    true,
  );
  assert.match(automaticHeater, /data-action="heater:auto" aria-pressed="true"/);
  assert.match(automaticHeater, /data-action="heater:true" aria-pressed="true"/);
  assert.match(
    renderCurrent(
      "automatic",
      { heater: { manual: null }, light: { manual: null } },
      true,
      "#details",
    ),
    /Ofen · Ein/,
    "physical feedback remains visible in details",
  );
  assert.doesNotMatch(automaticHeater, /data-action="heater:true" class="primary"/);
  const automaticLight = renderCurrent(
    "automatic",
    {
      light: { manual: null, observation: { available: true, brightness_percent: 18 } },
    },
    false,
    "#current",
    {},
    false,
    pausedSession,
    [],
    {},
    true,
  );
  assert.match(automaticLight, /data-action="light:auto" aria-pressed="true"/);
  assert.match(automaticLight, /data-action="light:true" aria-pressed="true"/);

  const liveSession = {
    timeline: {
      active: null,
      completed: [],
      session_started_at: "2026-09-20T11:00:00Z",
    },
    heating: { elapsed_seconds: 0 },
    deadlines: [],
  };
  const liveAutomatic = renderCurrent(
    "automatic",
    { heater: { manual: null }, light: { manual: null } },
    true,
    "#current",
    {},
    false,
    liveSession,
    [],
    {},
    true,
  );
  assert.doesNotMatch(liveAutomatic, /data-action="heater:auto"[^>]*disabled/);
  assert.doesNotMatch(liveAutomatic, /data-action="light:auto"[^>]*disabled/);
  assert.ok(
    liveAutomatic.indexOf('data-action="heater:auto"') <
      liveAutomatic.indexOf('data-action="heater:false"'),
  );
  assert.ok(
    liveAutomatic.indexOf('data-action="heater:false"') <
      liveAutomatic.indexOf('data-action="heater:true"'),
  );
  assert.ok(
    liveAutomatic.indexOf('data-action="light:auto"') <
      liveAutomatic.indexOf('data-action="light:false"'),
  );

  for (const observed of [false, true, null]) {
    const blocked = renderCurrent(
      "automatic",
      {
        heater: {
          manual: null,
          observation: { available: observed !== null, on: observed },
          blocked_on_reason: 'Ofenkühlung: "Ein" wartet <noch> & bleibt gesperrt',
        },
      },
      observed,
      "#current",
      {},
      false,
      liveSession,
      [],
      {},
      true,
    );
    const on = blocked.match(/<button\b[^>]*data-action="heater:true"[^>]*>/)[0];
    assert.match(on, /\bdisabled\b/);
    assert.match(
      on,
      /title="Ofenkühlung: &quot;Ein&quot; wartet &lt;noch&gt; &amp; bleibt gesperrt"/,
    );
    assert.match(on, new RegExp(`aria-pressed="${observed === true}"`));
    for (const action of [
      "heater:false",
      "heater:auto",
      "light:false",
      "light:true",
      "light:auto",
    ]) {
      const button = blocked.match(
        new RegExp(`<button\\b[^>]*data-action="${action}"[^>]*>`),
      )[0];
      assert.doesNotMatch(
        button,
        /\bdisabled\b/,
        "a blocked heater-on command must not block release or independent light controls",
      );
    }
  }

  const lightOff = renderCurrent("manual", {
    heater: { manual: false, observation: { available: true, on: true } },
    light: { manual: 42, observation: { available: true, brightness_percent: 0 } },
  });
  assert.match(lightOff, /data-action="heater:true" aria-pressed="true"/);
  assert.match(lightOff, /data-action="light:false" aria-pressed="true"/);
  for (const brightness of [0.1, 25.4, 42, 50.4, 100]) {
    const observed = renderCurrent("manual", {
      heater: { manual: true, observation: { available: true, on: false } },
      light: {
        manual: 0,
        observation: { available: true, brightness_percent: brightness },
      },
    });
    assert.match(observed, /data-action="heater:false" aria-pressed="true"/);
    assert.match(observed, /data-action="light:true" aria-pressed="true"/);
    assert.match(observed, /data-action="light:false" aria-pressed="false"/);
    assert.doesNotMatch(observed, /light:normal|Gedimmt/);
  }
  for (const manualValue of [true, false, null]) {
    const unknown = renderCurrent(
      "manual",
      {
        heater: { manual: manualValue, observation: { available: false, on: true } },
        light: {
          manual: manualValue === true ? 42 : 0,
          observation: { available: false, brightness_percent: 42 },
        },
      },
      true,
    );
    assert.doesNotMatch(
      unknown,
      /data-action="(?:heater|light):(?:false|true)" aria-pressed="true"/,
      "unavailable physical feedback never highlights the requested switch state",
    );
  }
  const linearLight = renderCurrent(
    "manual",
    {},
    false,
    "#current",
    {},
    false,
    null,
    [],
    { appearance: { instruments: { light: "linear" } } },
  );
  assert.match(
    linearLight,
    /id="manual-light-value-overview"[^>]*data-manual-light-value[^>]*type="range"/,
  );
  assert.doesNotMatch(linearLight, /data-light-arc/);

  const detailNodes = new Map();
  const detailNode = (selector) => {
    if (!detailNodes.has(selector))
      detailNodes.set(selector, { innerHTML: "", hidden: false });
    return detailNodes.get(selector);
  };
  const detailPanel = Object.assign(Object.create(Panel.prototype), {
    hass: { user: { name: "Admin", is_admin: true } },
    $: detailNode,
    progressionDraft: null,
    manualLightDraft: null,
    state: {
      frontend_defaults: frontendDefaults,
      now: "2026-09-20T12:00:00Z",
      phase: "nachlauf",
      operation_enabled: true,
      session: {
        timeline: { active: null, door: "closed", completed: [] },
        heating: { elapsed_seconds: 120 },
        deadlines: [{ purpose: "confirmation", due_at: "2026-09-20T12:03:00Z" }],
        after_run: {
          paused_at: "2026-09-20T11:59:00Z",
          duration_seconds: 300,
          elapsed_seconds: 120,
        },
      },
      last_session: null,
      configuration: {
        control_mode: "automatic",
        program_mode: "constant",
        selected_program_id: null,
        temperature_programs: [],
        parameters: {
          preset_count: 0,
          preset_start_c: 70,
          preset_step_c: 5,
          session_light_brightness_percent: 50,
          manual_override_minutes: 10,
          readiness_hysteresis_c: 3,
          nominal_power_kw: 4.5,
        },
      },
      parameters: [
        { key: "target_temperature_c", minimum: 30, maximum: 100, integer: false },
      ],
      measurements: [],
      measurement_status: {},
      mechanical_timer: {
        state: "paused",
        remaining_seconds: 600,
        pause_reason: "contactor_off",
      },
      manual_controls: {
        light: { normal: 25, automatic: 40, override_ends_at: "2026-09-20T12:04:00Z" },
        heater: {},
      },
      permissions: {
        admin: true,
        control: true,
        heater: true,
        light: true,
        temperature: true,
        program: true,
      },
      issues: [],
      start_errors: [],
      heating_feedback: false,
      heating_observation: { source: "unknown" },
      gang_count: 0,
      energy_kwh: 1.25,
      energy_source: "estimated",
      target_temperature: 80,
      thermostat_target: 85,
      thermostat_restart_temperature: 77,
      start_availability: null,
      phase_timer: { kind: "heating", label: "Aufheizen seit", seconds: 600 },
      light_after_run: { ends_at: "2026-09-20T12:09:00Z" },
      detection_channels: ["upper"],
      decision_text: "Ofen bleibt aus.",
    },
  });
  detailPanel.drawCurrent();
  const details = detailNodes.get("#details").innerHTML;
  assert.match(details, /Ofenkühlung pausiert[\s\S]*3 Minuten/);
  assert.doesNotMatch(details, /Zwangskühlung/);
  assert.match(details, /data-phase-timer="heating"/);
  assert.match(details, /Lichtnachlauf/);
  assert.match(details, /Nennleistung für die Verbrauchsschätzung/);
  assert.match(details, /Erkennung mit: oberer Messposition/);
  assert.match(details, /Licht<\/h2>[\s\S]*Automatik/);
  assert.match(details, /Wiedereinschaltschwelle<\/dt><dd>77 °C<\/dd>/);
  assert.doesNotMatch(
    details,
    /data-action=|<button|<input/,
    "details present status without controls",
  );
  // The backend owns the threshold, including fractional values and absence.
  // Do not derive it from either the upper threshold or local parameters.
  for (const [value, display] of [
    [76.5, "76,5"],
    [null, "–"],
  ]) {
    detailPanel.state.thermostat_restart_temperature = value;
    detailPanel.drawCurrent();
    assert.ok(
      detailNodes
        .get("#details")
        .innerHTML.includes(`Wiedereinschaltschwelle</dt><dd>${display} °C</dd>`),
    );
  }

  const nodes = {
    "#current": {},
    "#details": {},
    "#history": { hidden: true },
    "#settings": {},
    "#plots": {},
    "#detection-plots": {},
    "#gangs": {},
    "#event-list": {},
  };
  const detailTabs = [
    {
      dataset: { action: "detail" },
      setAttribute(name, value) {
        this[name] = value;
      },
    },
    {
      dataset: { action: "detail-history" },
      setAttribute(name, value) {
        this[name] = value;
      },
    },
  ];
  const navigation = Object.assign(Object.create(Panel.prototype), {
    state: {
      frontend_defaults: frontendDefaults,
      permissions: { admin: true },
    },
    message: () => {},
    $: (selector) => nodes[selector],
    drawHistory: () => {},
    shadowRoot: {
      querySelectorAll: (selector) =>
        selector === ".detail-tabs button" ? detailTabs : [],
    },
  });
  await navigation.action("detail-history");
  assert.equal(navigation.view, "history");
  assert.equal(nodes["#history"].hidden, false);
  assert.equal(detailTabs[1]["aria-current"], "page");
  assert.equal(detailTabs[0]["aria-current"], "false");

  let finishLight;
  const owned = makePanel(enabled, "40").panel;
  owned.manualLightDraft = "40";
  owned.manualLightRevision = 1;
  owned.api = () =>
    new Promise((resolve) => {
      finishLight = resolve;
    });
  const savingLight = owned.action("manual-light-overview");
  owned.manualLightDraft = "80";
  owned.manualLightRevision++;
  finishLight();
  await savingLight;
  assert.equal(owned.manualLightDraft, "80", "older response retains the newer draft");
  console.log("panel manual control regressions passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
