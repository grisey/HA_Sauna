/* Synthetic transport only: every panel render/index/gesture method is real. */
window.installHistoryFixture = async function ({ seconds = 14400, both = false } = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  const outer = host.attachShadow({ mode: "open" });
  outer.innerHTML =
    '<div id="scroll" style="height:960px;overflow:auto"><div id="content"></div><div style="height:600px"></div></div>';
  const panel = document.createElement("ha-sauna-panel");
  outer.querySelector("#content").append(panel);
  panel.entry = "synthetic";
  panel._hass = { user: { name: "Synthetic", is_admin: false } };
  panel.setPanelView("history");
  panel.positions = new Set(both ? ["upper", "lower"] : ["upper"]);
  panel.historyDetail = both;
  const origin = Date.parse("2032-01-01T08:00:00Z"),
    iso = (t) => new Date(t).toISOString();
  const records = [];
  const addSecond = (second) => {
    for (const [index, [position, quantity]] of [
      ["upper", "temperature"],
      ["upper", "humidity"],
      ["lower", "temperature"],
      ["lower", "humidity"],
    ].entries()) {
      // Different source times remain four independent series, never aligned.
      const time = origin + second * 1000 + index * 103;
      const value =
        (quantity === "temperature" ? 72 : 28) +
        (position === "lower" ? -5 : 0) +
        Math.sin(second / 47 + index) * 3;
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
          measured_at: iso(time - 71),
        },
      });
    }
  };
  for (let i = 0; i < seconds; i++) addSecond(i);
  const configuration = {
    control_mode: "manual",
    program_mode: "constant",
    temperature_programs: [],
    parameters: {
      sensor_timeout_seconds: 5,
      preset_count: 0,
      preset_start_c: 70,
      preset_step_c: 5,
      session_light_brightness_percent: 50,
      manual_override_minutes: 10,
    },
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
      intervals: [{ started_at: iso(origin), ended_at: iso(origin + 300000) }],
      elapsed_seconds: 300,
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
    parameters: [{ key: "target_temperature_c", minimum: 30, maximum: 100 }],
    measurements: [],
    measurement_status: {},
    mechanical_timer: { state: "idle", remaining_seconds: 0 },
    manual_controls: { light: { normal: 25 }, heater: {} },
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
    target_temperature: 80,
  };
  state.phase_projection = projection();
  let delay = 0,
    fail = 0,
    requests = [],
    pageSize = 1000;
  panel.api = async (path) => {
    if (path.endsWith("/state")) return structuredClone(state);
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
    await frame();
    await frame();
    await frame();
  };
  const refresh = async () => {
    await panel.refresh();
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
    addSecond(seconds++);
    addSecond(seconds++);
    state.now = iso(origin + seconds * 1000);
    state.phase_projection = projection();
    await refresh();
  };
  const status = async () => {
    state.now = iso(Date.parse(state.now) + 2000);
    state.phase_projection = projection();
    panel.state = structuredClone(state);
    panel.showHistoryCache?.();
    panel.drawHistory("status");
    await settle();
  };
  return {
    panel,
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
        throw Error(JSON.stringify(panel.messages));
    },
  };
};
