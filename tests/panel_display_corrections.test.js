// AL1: door start, calibration protocol, labels, power rows and positions.
const { panelFixture: fixturePanel, parameterValues } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_name, value) => (Panel = value) },
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
const iso = (second) => new Date(Date.UTC(2026, 10, 2, 16, 0, second)).toISOString();
const record = (id, kind, payload, extra = {}) => ({
  id,
  kind,
  received_at: iso(id),
  payload,
  ...extra,
});
const session = (processed = []) => ({
  ended_at: iso(90),
  timeline: {
    session_id: "al1",
    session_started_at: iso(0),
    processed,
    completed: [],
    retracted: [],
    active: null,
  },
  configuration: { parameters: parameterValues() },
  contactor_history: [],
  energy: { measured_kwh: 0, estimated_kwh: 1, accounted_at: iso(90) },
});
const panel = (shown, admin = true) => {
  const p = Object.assign(fixturePanel(Panel), {
    state: {
      permissions: { admin },
      configuration: { parameters: parameterValues() },
      now: iso(90),
    },
    shown,
    markup: {},
  });
  p.updateMarkup = (selector, html) => (p.markup[selector] = html);
  return p;
};
const detailLabels = (html) =>
  [...html.matchAll(/data-detail-key="([^"]+)"><dt>([^<]*)<\/dt>/g)].map(
    ([, identity, label]) => ({ key: identity.split(":").at(-1), label }),
  );
const rawLabels = (html) =>
  detailLabels(html).filter(
    ({ key, label }) => /^[a-z]/.test(key) && label === key.replaceAll("_", " "),
  );

test("the door lane starts closed until the first door event", () => {
  const p = panel({
    session: session([
      { event_id: "open", kind: "door_open", effective_at: iso(20), detected_at: iso(21) },
      { event_id: "close", kind: "door_close", effective_at: iso(30), detected_at: iso(31) },
    ]),
    records: [],
    phase_projection: { intervals: [] },
  });
  p.renderHistoryValues = () => "";
  // A first render of a session resets the cursor; select it beforehand.
  p.controlHistorySession = "al1";
  p.controlHistoryEnd = Date.parse(iso(90));
  const status = (second) => {
    p.controlHistoryCursor = Date.parse(iso(second));
    p.renderControlHistory();
    return p.markup["#control-history"].match(/<dt>Tür<\/dt><dd>([^<]*)<\/dd>/)[1];
  };
  assert.equal(status(5), "Geschlossen");
  assert.equal(status(25), "Offen");
  assert.equal(status(40), "Geschlossen");
  const lane = p.markup["#control-history"].match(
    /data-detail-key="lane-Tür">(.*?)<i /,
  )[1];
  assert.match(lane, /aria-label="Tür: Geschlossen ab /);
  assert.doesNotMatch(lane, /Unbekannt|background:transparent/);
});

test("calibration protocol rows appear in the detail activity with German text", () => {
  const p = panel({ session: session(), records: [], phase_projection: { intervals: [] } });
  const rows = p.historyAdditionalActivity([
    record(1, "calibration", {
      event: "start",
      run_id: "run",
      at: iso(1),
      kind: "module",
      modules: ["A1"],
      user_id: "u",
    }),
    record(2, "calibration", {
      event: "step",
      run_id: "run",
      at: iso(2),
      execution: 0,
      module: "A1",
      step: "open",
      outcome: "reply",
      action: "Tür geöffnet",
    }),
    record(
      3,
      "calibration",
      { event: "end", run_id: "run", at: iso(3), reason: "cancelled", valid: false },
      { display: { reason_text: "abgebrochen" } },
    ),
  ]);
  assert.deepEqual(
    rows.map(({ source, label }) => [source, label]),
    [
      ["Kalibrierung", "Kalibrierung gestartet"],
      ["Kalibrierung", "Kalibrierschritt abgeschlossen"],
      ["Kalibrierung", "Kalibrierung beendet"],
    ],
  );
  assert.equal(rows[2].detail, "Ungültig, abgebrochen");
  assert.match(rows[1].detail, /Modul: A1 · Schritt: open · Ergebnis: reply/);
  assert.doesNotMatch(rows.map((row) => row.detail).join(" "), /run_id|run id|Laufkennung/);
  assert.equal(p.historyRecordLabel("calibration"), "Kalibrierprotokoll");
  const [message] = p.historyAdditionalActivity([
    record(4, "calibration", {
      event: "message",
      run_id: "run",
      at: iso(4),
      seq: 1,
      target: { transport: "companion", endpoint: "mobile_app_test" },
      accepted: true,
    }),
  ]);
  assert.equal(message.label, "Kalibrieranweisung übergeben");
  assert.match(message.detail, /Übertragungsweg: companion · Ziel: mobile_app_test/);
  assert.doesNotMatch(message.detail, /Sollwert/);
});

test("live and archived detail fields render labels instead of raw keys", () => {
  const p = panel({ session: session(), records: [], phase_projection: { intervals: [] } });
  p.state = {
    ...p.state,
    manual_controls: {
      heater: {
        blocked_on_reason: null,
        observation: { available: true, on: false },
        manual: null,
        commanded: false,
        override_ends_at: null,
        automatic: false,
      },
      light: {
        observation: { available: true, brightness_percent: 40 },
        manual: null,
        manual_feedback_target: null,
        override_ends_at: null,
        automatic: 40,
        normal: 60,
      },
    },
    start_availability: {
      until_ready_seconds: 600,
      ready_estimated: true,
      minimum_wait_seconds: null,
      message: "Heizen",
      blocker: null,
      gang_elapsed_seconds: null,
    },
    session: {
      ...session(),
      after_run: {
        requested_at: iso(1),
        required_seconds: 600,
        remaining_seconds: 300,
        minimum_duration_requested: false,
        active_intervals: [],
        cooling_calculation: {
          base_minutes: 10,
          maximum_minutes: 30,
          half_life_minutes: 60,
          heat_idle_ratio: 3,
          weighted_heat_minutes: 12,
          weighted_idle_minutes: 2,
          window_started_at: iso(0),
          window_ended_at: iso(1),
          quality: "complete",
        },
      },
      heating: { state_history: [{ at: iso(0), heating: true }] },
    },
  };
  const live = p.renderLiveEvidence();
  assert.ok(detailLabels(live).length > 20);
  assert.deepEqual(rawLabels(live), []);
  const archived = [
    record(1, "detection", { event: { kind: "door_open", detected_at: iso(1) }, probability: 0.9 }),
    record(2, "measurement", {
      position: "upper",
      quantity: "temperature",
      value: 80,
      original_id: "o",
      source_matches_binding: true,
      sample: { sample_id: "s", sensor_id: "x", boot_id: "b", monotonic_ns: 1 },
    }),
    record(3, "manual_heater", {
      value: false,
      selection_id: "sel",
      ends_previous_only: true,
      change: { previous_value: null, control_mode: "automatic" },
    }),
    record(4, "session", {
      configuration: {
        control_input_mode: "button",
        presence_source: "proxy",
        bindings: { heater: "switch.ofen" },
        appearance: { preferences: { status_active_poll_ms: 2000 } },
      },
    }),
  ];
  const evidence = archived.map((item) => p.detailFields(item.payload, `record-${item.id}`));
  assert.deepEqual(rawLabels(evidence.join("")), []);
  assert.equal(p.detailFieldLabel("status_active_poll_ms"), "Status bei laufendem Betrieb");
});

test("the detail label table holds no entries without a delivering backend", () => {
  const p = panel({ session: session(), records: [], phase_projection: { intervals: [] } });
  for (const key of ["command_pending", "confidence", "confirmation_at"])
    assert.equal(p.detailFieldLabel(key), key.replaceAll("_", " "));
});

const informationContent = (shown) => ({
  session: shown.session,
  selection: null,
  start: Date.parse(iso(0)),
  end: Date.parse(iso(90)),
  completed: true,
  measurements: new Map(),
  sectionPhase: null,
});

test("power rows require a power measurement in detail history and leave the history view", () => {
  for (const [model, measuredPower, shownRows] of [
    [{ power_sensor_configured: false }, null, false],
    [null, null, false],
    [{ power_sensor_configured: true }, null, true],
    [null, { mean_w: 6000, peak_w: 6200, measured_seconds: 60, complete: true }, true],
  ]) {
    const shown = {
      session: { ...session(), configuration_model: model },
      records: [],
      phase_projection: { intervals: [] },
    };
    const p = panel(shown);
    p.$ = () => null;
    p.syncHistoryInformation(null, null, true, informationContent(shown));
    // B3b: Leistung und Energie stehen nicht mehr in der Auswahl des Verlaufs (R-02).
    const summary = p.markup["[data-history-session-summary]"];
    assert.doesNotMatch(summary, /Gemessene Leistung|Energie/, JSON.stringify(model));
    p.historyMeasuredPower = () => measuredPower;
    const detail = p.renderSessionEvidenceContents(shown.session);
    assert.equal(
      /Gemessene Leistung|Messbezug|Gemessene mittlere Leistung/.test(detail),
      shownRows,
      JSON.stringify(model),
    );
  }
});

test("the history selection shows no measuring positions and no panel-computed figures", () => {
  const shown = {
    session: { ...session(), configuration_model: { power_sensor_configured: false } },
    records: [],
    phase_projection: { intervals: [] },
  };
  for (const detail of [false, true]) {
    const p = panel(shown);
    p.$ = () => null;
    p.historyDetail = detail;
    p.syncHistoryInformation(null, null, true, informationContent(shown));
    const summary = p.markup["[data-history-session-summary]"];
    assert.doesNotMatch(summary, /Messposition|oben − unten|Sollabweichung|Maximum/);
  }
});
