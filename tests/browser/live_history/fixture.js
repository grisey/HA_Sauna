/* Synthetic transport only: every panel render/index/gesture method is real. */
window.installHistoryFixture = async function ({
  seconds,
  defaults,
  settings = window.testSettings,
} = {}) {
  const inputs = settings.fixtures.history;
  seconds = seconds ?? inputs.duration_seconds;
  const initialRecordCount =
    seconds * inputs.positions.length * inputs.quantities.length;
  const recordsPerTick =
    inputs.samples_per_tick * inputs.positions.length * inputs.quantities.length;
  const host = document.createElement("div");
  document.body.append(host);
  const outer = host.attachShadow({ mode: "open" });
  outer.innerHTML = `<div id="scroll" style="height:${inputs.outer_height_px}px;overflow:auto"><div id="content"></div><div style="height:${inputs.outer_tail_px}px"></div></div>`;
  const panel = document.createElement("ha-sauna-panel");
  panel.panel = {
    config: {
      appearance_catalog: defaults.appearance,
      frontend_defaults: defaults.frontend,
    },
  };
  outer.querySelector("#content").append(panel);
  panel.entry = "synthetic";
  panel._hass = { user: { name: "Synthetic", is_admin: false } };
  panel.setPanelView("history");
  const origin = Date.parse(inputs.origin),
    iso = (t) => new Date(t).toISOString();
  const records = [];
  const addSecond = (second) => {
    for (const [index, [position, quantity]] of inputs.positions
      .flatMap((position) => inputs.quantities.map((quantity) => [position, quantity]))
      .entries()) {
      // Different source times remain four independent series, never aligned.
      const time = origin + second * 1000 + index * inputs.source_offset_ms;
      const value =
        inputs[quantity] +
        (position === "lower" ? inputs.lower_offset : 0) +
        Math.sin(second / inputs.wave_period_seconds + index) * inputs.wave_amplitude;
      records.push({
        id: records.length + 1,
        kind: "measurement",
        received_at: iso(time),
        payload: {
          position,
          quantity,
          value,
          raw_value: String(value),
          received_at: iso(time),
          measured_at: iso(time - inputs.measurement_lag_ms),
        },
      });
    }
  };
  for (let i = 0; i < seconds; i++) addSecond(i);
  const definitions = new Map(defaults.parameters.map((item) => [item.key, item]));
  const configuredValue = (key) => {
    const value = definitions.get(key).default;
    return value && typeof value === "object"
      ? configuredValue(value.parameter)
      : value;
  };
  const parameters = Object.fromEntries(
    [...definitions.keys()].map((key) => [key, configuredValue(key)]),
  );
  const configuration = {
    control_mode: "manual",
    program_mode: "constant",
    temperature_programs: [],
    parameters,
  };
  let session = {
    ended_at: null,
    configuration,
    timeline: {
      session_id: "synthetic",
      session_started_at: iso(origin),
      active: null,
      completed: [],
      processed: [],
      retracted: [],
    },
    heating: {
      intervals: [
        {
          started_at: iso(origin),
          ended_at: iso(origin + inputs.heating_seconds * 1000),
        },
      ],
      elapsed_seconds: inputs.heating_seconds,
    },
  };
  const projection = () => ({
    intervals: [
      {
        phase: "bereit",
        started_at: iso(origin),
        ended_at: state.now,
        complete: false,
      },
    ],
  });
  let state = {
    now: iso(origin + seconds * 1000),
    session,
    configuration,
    frontend_defaults: defaults.frontend,
    appearance_catalog: defaults.appearance,
    appearance: {
      preferences: Object.fromEntries(
        Object.entries(defaults.appearance.preferences)
          .filter(([, spec]) => spec.configuration_surface === "panel")
          .map(([key, spec]) => [key, spec.default]),
      ),
    },
    configuration_model: {
      measurement_positions: inputs.positions,
      presence: { source: "proxy", direct: false, observer: false },
      button: { session_gestures: [] },
      regulation_causes: [],
    },
    temperature_control: {
      scope: "current",
      value: parameters.target_temperature_c,
      current_target: parameters.target_temperature_c,
      next_target: parameters.target_temperature_c,
      program_mode: "constant",
    },
    parameters: [
      {
        ...definitions.get("target_temperature_c"),
        minimum: parameters.sauna_min_temperature_c,
      },
    ],
    measurements: [],
    measurement_status: {},

    manual_controls: { light: { normal: inputs.normal_light }, heater: {} },
    permissions: { admin: false, control: true, light: true, temperature: true },
    configuration_locked: true,
    issues: [],
    start_errors: [],
    operation_enabled: true,
    heating_feedback: false,
    heating_observation: { source: "contactor" },
    phase: "bereit",
    gang_count: 0,
    energy_kwh: 0,
    energy_source: "estimated",
    target_temperature: parameters.target_temperature_c,
  };
  state.phase_projection = projection();
  let delay = 0,
    fail = 0,
    requests = [],
    pageSize = inputs.page_size;
  panel.api = async (path) => {
    if (path.split("?")[0].endsWith("/state")) {
      const response = structuredClone(state);
      if (
        new URL(path, "http://fixture").searchParams.get("include_catalog") === "false"
      )
        delete response.appearance_catalog;
      return response;
    }
    if (path.endsWith("/archive"))
      return [
        {
          session_id: session.timeline.session_id,
          started_at: iso(origin),
          ended_at: session.ended_at,
        },
      ];
    const after = Number(new URL(path, "http://fixture").searchParams.get("after"));
    requests.push(after);
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    if (fail) {
      fail--;
      throw Error("synthetic retry");
    }
    const page = records.filter((record) => record.id > after).slice(0, pageSize);
    return {
      session: structuredClone(session),
      phase_projection: structuredClone(state.phase_projection),
      records: structuredClone(page),
      next_after: page.length === pageSize ? page.at(-1).id : null,
    };
  };
  const measurements = {},
    counts = {};
  const spy = (object, name, label = name) => {
    if (!object || typeof object[name] !== "function") return;
    const original = object[name];
    object[name] = function (...args) {
      const begin = performance.now();
      counts[label] = (counts[label] || 0) + 1;
      try {
        return original.apply(this, args);
      } finally {
        (measurements[label] ??= []).push(performance.now() - begin);
      }
    };
  };
  for (const name of [
    "drawCurrent",
    "historyIndex",
    "historyDisplayValues",
    "historyModel",
    "renderHistory",
    "drawHistory",
    "chart",
    "minimapBackground",
    "appendHistoryCacheRecords",
    "hoverChart",
    "historyAnnotations",
  ])
    spy(panel, name);
  const frame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
  const settle = async () => {
    for (let count = 0; count < settings.browser.settle_frames; count++) await frame();
  };
  const refresh = async () => {
    // Deliver this explicit fixture batch immediately. Background polling is
    // exercised separately with refresh() and its real refresh interval.
    await panel.refresh(true);
    await panel.historyLoad?.promise;
    await settle();
  };
  const cold = performance.now();
  await refresh();
  const coldMs = performance.now() - cold;
  if (panel.historyChart)
    for (const name of ["buildMainPath", "paintMain", "update"])
      spy(panel.historyChart.curves, name, `curves.${name}`);
  let rendered = panel.historyChart,
    canvas = panel.$("canvas.history-curves"),
    surface = panel.$("svg.session-chart"),
    overview = panel.$("#history-overview svg");
  const identity = () => ({
    hasInstance: !!panel.historyChart,
    hasCanvas: !!panel.$("canvas.history-curves"),
    instance: rendered === panel.historyChart,
    canvas: canvas === panel.$("canvas.history-curves"),
    surface: surface === panel.$("svg.session-chart"),
    overview: overview === panel.$("#history-overview svg"),
  });
  const reset = () => {
    for (const key of Object.keys(measurements)) delete measurements[key];
    for (const key of Object.keys(counts)) delete counts[key];
  };
  const tick = async () => {
    for (let count = 0; count < inputs.samples_per_tick; count++) addSecond(seconds++);
    state.now = iso(origin + seconds * 1000);
    state.phase_projection = projection();
    await refresh();
  };
  const status = async () => {
    state.now = iso(Date.parse(state.now) + panel.preference("status_active_poll_ms"));
    state.phase_projection = projection();
    panel.state = structuredClone(state);
    panel.showHistoryCache?.();
    panel.drawHistory("status");
    await settle();
  };
  return {
    panel,
    inputs,
    initialRecordCount,
    recordsPerTick,
    host,
    outer,
    records,
    counts,
    measurements,
    coldMs,
    refresh,
    settle,
    tick,
    status,
    identity,
    reset,
    iso,
    origin,
    get state() {
      return state;
    },
    get session() {
      return session;
    },
    get requests() {
      return requests;
    },
    setDelay(value) {
      delay = value;
    },
    setFail(value = 1) {
      fail = value;
    },
    setPageSize(value) {
      pageSize = value;
    },
    close(empty = false) {
      if (!empty) {
        addSecond(seconds++);
      }
      session.ended_at = iso(origin + seconds * 1000);
      state.session = null;
      state.operation_enabled = false;
      state.phase_projection = {
        intervals: [
          {
            phase: "bereit",
            started_at: iso(origin),
            ended_at: session.ended_at,
            complete: true,
          },
        ],
      };
    },
    addRecord(record) {
      records.push({ id: records.length + 1, ...record });
    },
    assertClean() {
      if (panel.messages?.refresh || panel.messages?.history)
        throw Error(
          JSON.stringify(panel.messages, (_key, value) =>
            value instanceof Error
              ? { message: value.message, stack: value.stack }
              : value,
          ),
        );
    },
  };
};
