const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const { parameterValues } = require("./catalog_fixture.cjs");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
  clearTimeout,
});
const iso = (second) => new Date(Date.UTC(2032, 0, 1) + second * 1000).toISOString();
const makePanel = () =>
  Object.assign(fixturePanel(Panel), {
    state: { now: iso(60), configuration: { parameters: parameterValues() } },
    shown: {
      session: {
        timeline: { session_id: "s", session_started_at: iso(0), processed: [] },
      },
      records: [],
    },
    shadowRoot: { querySelectorAll: () => [] },
    $: () => null,
    message() {},
  });

test("incoming session growth preserves full and maximally zoomed windows until explicit reset", () => {
  for (const zoom of [1, 256]) {
    const panel = makePanel();
    panel.ensureHistoryWindow();
    if (zoom === 256)
      panel.setHistoryWindow(Date.parse(iso(20)), Date.parse(iso(20)) + 1);
    const window = panel.window;
    const previousZoom = panel.zoom;
    panel.state.now = iso(36000);
    panel.ensureHistoryWindow();
    assert.equal(panel.window, window);
    assert.equal(panel.zoom, previousZoom);
    panel.window = null;
    panel.ensureHistoryWindow();
    assert.equal(panel.window[1], Date.parse(iso(36000)));
    assert.equal(panel.zoom, 1);
  }
});

test("archived thresholds are never backfilled from changed current settings", () => {
  const panel = makePanel();
  panel.shown.session.ended_at = iso(60);
  assert.equal(Object.keys(panel.diagnosticsParameters()).length, 0);
  panel.shown.session.configuration = { parameters: { door_open_slope: -42 } };
  assert.equal(panel.diagnosticsParameters().door_open_slope, -42);
  assert.equal(panel.diagnosticsParameters().person_step_seconds, undefined);
  delete panel.shown.session.configuration;
  delete panel.shown.session.ended_at;
  panel.state.session = panel.shown.session;
  assert.equal(
    panel.diagnosticsParameters().person_step_seconds,
    parameterValues().person_step_seconds,
  );
});

test("zooming a retained viewport uses its width instead of the expanded live domain", () => {
  const panel = makePanel();
  panel.ensureHistoryWindow();
  const width = panel.window[1] - panel.window[0];
  panel.state.now = iso(3600);
  panel.svgCoordinates = () => ({ x: 600 });
  panel.scheduleHistoryRender = () => {};
  panel.zoomAt(2, 0, {});
  assert.equal(panel.window[1] - panel.window[0], width / 2);
});

test("all stored checks, negative conditions, zero holds and readiness are accessible without a signal", () => {
  const panel = makePanel();
  const trace = {
    at: iso(5),
    metrics: { upper: { door_temperature_slope: 0 } },
    checks: { door_open: false },
    conditions: { door_open: false },
    holds: { door_open: 0 },
    readiness: { temperature: false },
    signals: [],
  };
  panel.shown.records.push({ kind: "detector_trace", payload: trace });
  const html = panel.diagnosticSelectionMarkup(panel.diagnosticData().traces);
  assert.match(html, /data-trace-at="2032-01-01T00:00:05.000Z"/);
  assert.match(html, /data-key="value:door_open">Nicht geprüft</);
  assert.match(html, /data-key="value:door_open">Nicht erfüllt</);
  assert.match(html, /data-key="value:door_open">0 Prüfpunkte</);
  assert.match(html, /data-key="value:temperature">Nein</);
  assert.match(html, /Signale: Keine/);
  panel.shown.records.push({
    kind: "detector_trace",
    payload: { ...trace, at: iso(10) },
  });
  panel.diagnosticSelectionMarkup(panel.diagnosticData().traces);
  assert.equal(panel.selectedDiagnosticTraceAt, iso(5));
});

test("a missing marker target clears a navigation request instead of stealing later focus", () => {
  const panel = makePanel();
  panel.view = "diagnostics";
  panel.pendingEventFocus = { kind: "marker", eventId: "absent", sessionId: "s" };
  panel.revealEventTarget("marker");
  assert.equal(panel.pendingEventFocus, null);
  panel.pendingEventFocus = {
    kind: "marker",
    eventId: "absent",
    sessionId: "old-session",
  };
  panel.revealEventTarget("marker");
  assert.equal(panel.pendingEventFocus, null);
});

test("same-time traces remain independently selectable while new records append", async () => {
  const panel = makePanel();
  panel.state.permissions = { admin: true };
  const first = {
    at: iso(5),
    metrics: { upper: { door_temperature_slope: -1 } },
    signals: [],
  };
  const second = {
    at: iso(5),
    metrics: { upper: { door_temperature_slope: -2 } },
    signals: [],
  };
  panel.shown.records = [first, second].map((payload) => ({
    kind: "detector_trace",
    payload,
  }));
  panel.drawDiagnostics = () =>
    panel.diagnosticSelectionMarkup(panel.diagnosticData().traces);
  panel.drawDiagnostics();
  await panel.action("diagnostic-trace:next");
  assert.equal(panel.selectedDiagnosticTrace, second);
  panel.shown.records.push({
    kind: "detector_trace",
    payload: { ...first, at: iso(10) },
  });
  panel.drawDiagnostics();
  assert.equal(panel.selectedDiagnosticTrace, second);
});

test("old archives without sample cadence show isolated visible points and no borrowed thresholds", () => {
  const panel = makePanel();
  panel.state.permissions = { admin: true };
  panel.shown.session.ended_at = iso(60);
  panel.shown.records = [5, 10].map((second) => ({
    kind: "detector_trace",
    payload: {
      at: iso(second),
      metrics: { upper: { door_temperature_slope: -second } },
      signals: [],
    },
  }));
  panel.ensureHistoryWindow();
  const plots = { innerHTML: "" };
  panel.$ = (selector) => (selector === "#detection-plots" ? plots : null);
  panel.drawDiagnostics();
  const path = plots.innerHTML.match(
    /data-series="detector_door_temperature_slope_upper" d="([^"]*)"/,
  )[1];
  assert.equal((path.match(/l0,0/g) || []).length, 2);
  assert.doesNotMatch(path, /L/);
  assert.match(plots.innerHTML, /stroke-linecap="round"/);
  assert.doesNotMatch(plots.innerHTML, /Schwelle oben:/);
});

test("person markers identify the decision-making humidity feature, not contextual temperature", () => {
  const panel = makePanel();
  assert.equal(
    panel.diagnosticRoute({ kind: "person_strong" })[0],
    "strong_humidity_slope",
  );
  assert.equal(
    panel.diagnosticRoute({ kind: "person_weak" })[0],
    "weak_humidity_slope",
  );
});

test("archived diagnostics use their saved person source and retain raw measurement evidence", () => {
  for (const direct of [false, true]) {
    const panel = makePanel();
    panel.state.permissions = { admin: true };
    panel.state.configuration_model = {
      measurement_positions: ["lower"],
      presence: { source: direct ? "proxy" : "ha_presence", direct: !direct },
    };
    const savedModel = {
      measurement_positions: ["upper"],
      presence: { source: direct ? "ha_presence" : "proxy", direct },
    };
    panel.shown.session.ended_at = iso(60);
    panel.shown.session.configuration_model = savedModel;
    panel.shown.session.configuration = { parameters: parameterValues() };
    panel.shown.records.push({
      kind: "detector_trace",
      payload: {
        at: iso(5),
        metrics: {
          upper: {
            strong_humidity_slope: 2,
            strong_temperature_slope: -1,
            door_temperature_slope: -1,
          },
        },
        checks: { strong: false, door_open: false },
        conditions: { strong: null },
        holds: { strong: 0 },
        signals: [],
      },
    });
    const original = JSON.stringify(panel.shown.records);
    panel.ensureHistoryWindow();
    const plots = { innerHTML: "" };
    panel.$ = (selector) => (selector === "#detection-plots" ? plots : null);
    assert.equal(
      panel.historyConfigurationModel().presence.source,
      savedModel.presence.source,
    );
    assert.equal(panel.historyConfigurationModel().measurement_positions[0], "upper");
    panel.drawDiagnostics();
    assert.match(
      plots.innerHTML,
      direct
        ? /Personenquelle: Präsenzsensor mit Türereignis/
        : /Personenquelle: Temperatur und Feuchte/,
    );
    if (direct) {
      assert.doesNotMatch(plots.innerHTML, /data-series="detector_(strong|weak)_/);
      assert.doesNotMatch(plots.innerHTML, /data-key="(?:label|value):strong/);
    } else {
      assert.match(
        plots.innerHTML,
        /data-series="detector_strong_humidity_slope_upper"/,
      );
      const contextChart = plots.innerHTML
        .split('data-diagnostic-metric="strong_temperature_slope"')[1]
        .split("</svg>")[0];
      assert.doesNotMatch(contextChart, /Schwelle oben:/);
    }
    assert.match(
      plots.innerHTML,
      /data-series="detector_door_temperature_slope_upper"/,
    );
    assert.doesNotMatch(
      plots.innerHTML,
      /data-series="detector_strong_humidity_slope_lower"/,
    );
    assert.doesNotMatch(plots.innerHTML, /<summary>Messwerte unten<\/summary>/);
    assert.match(plots.innerHTML, /<summary>Messwerte oben<\/summary>/);
    assert.match(plots.innerHTML, /Nicht geprüft/);
    assert.equal(
      panel.diagnosticData().traces[0].metrics.upper.strong_humidity_slope,
      2,
    );
    delete panel.shown.session.configuration_model;
    assert.equal(panel.historyConfigurationModel(), null);
    panel.drawDiagnostics();
    assert.match(
      plots.innerHTML,
      /damalige Anlagenkonfiguration ist nicht gespeichert/,
    );
    assert.match(plots.innerHTML, /data-series="detector_strong_humidity_slope_upper"/);
    assert.equal(JSON.stringify(panel.shown.records), original);
  }
});

test("person curves, thresholds, markers and selected evidence stay inside effective proxy sections", () => {
  const panel = makePanel(),
    parameters = parameterValues(),
    step = Math.min(parameters.grid_seconds, parameters.person_step_seconds),
    plots = { innerHTML: "" };
  panel.state.permissions = { admin: true };
  panel.shown.session.ended_at = iso(2 * step);
  panel.shown.session.configuration = { parameters, presence_source: "ha_presence" };
  panel.shown.session.configuration_model = {
    measurement_positions: ["upper"],
    presence: { source: "ha_presence", direct: true },
  };
  // Compact synthetic sections expose a bridge within one normal sample gap.
  // Qualification belongs to runtime tests; the panel consumes archived markers.
  const markers = [
    [0, "ha_presence", null],
    [step, "proxy", "ha_presence"],
    [1.4 * step, "ha_presence", "proxy"],
    [1.6 * step, "proxy", "ha_presence"],
  ].map(([second, source, previous], index) => ({
    id: index + 1,
    kind: "presence_source",
    received_at: iso(second),
    payload: {
      configured_source: "ha_presence",
      effective_source: source,
      entity_id: "binary_sensor.saved",
      ...(previous
        ? { previous_source: previous, reason: "availability_qualified" }
        : { external_snapshot: {} }),
    },
  }));
  const kinds = [
      "person_strong",
      "person_weak",
      "door_open",
      "infusion",
      "ventilation_confirmed",
    ],
    traces = [0.5, 1.1, 1.2, 1.5, 1.8, 1.9].map((fraction, index) => ({
      at: iso(fraction * step),
      channels: ["upper"],
      metrics: {
        upper: {
          strong_humidity_slope: index + 1,
          weak_humidity_slope: index + 1,
          door_temperature_slope: -1,
          infusion_humidity_delta: 1,
          ventilation_temperature_loss: 1,
        },
      },
      checks: {
        strong: true,
        weak: true,
        door_open: true,
        infusion: true,
        ventilation: true,
      },
      conditions: { strong: true, weak: true, door_open: true },
      holds: { strong: 1, weak: 1, door_open: 1 },
      // Inactive person evidence stays in this archive to verify display masking.
      signals: kinds,
    }));
  panel.shown.records = [
    ...markers,
    ...traces.map((payload, index) => ({
      id: markers.length + index + 1,
      kind: "detector_trace",
      received_at: payload.at,
      payload,
    })),
  ]
    .sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at))
    .map((record, index) => ({ ...record, id: index + 1 }));
  panel.shown.session.timeline.processed = traces.flatMap((trace, index) =>
    kinds.map((kind) => ({
      event_id: `${kind}-${index}`,
      session_id: "s",
      kind,
      effective_at: trace.at,
      detected_at: trace.at,
      booking_at: trace.at,
    })),
  );
  const original = JSON.stringify(panel.shown.records);
  panel.window = [Date.parse(iso(0)), Date.parse(iso(2 * step))];
  panel.$ = (selector) => (selector === "#detection-plots" ? plots : null);
  panel.drawDiagnostics();
  const chart = (metric) =>
      plots.innerHTML
        .split(`data-diagnostic-metric="${metric}"`)[1]
        .split("</svg>")[0],
    path = (metric) =>
      chart(metric).match(
        new RegExp(`data-series="detector_${metric}_upper" d="([^"]*)"`),
      )[1],
    doorXs = [...path("door_temperature_slope").matchAll(/[ML]([\d.]+),/g)].map(
      (match) => Number(match[1]),
    );
  for (const [metric, kind] of [
    ["strong_humidity_slope", "person_strong"],
    ["weak_humidity_slope", "person_weak"],
  ]) {
    assert.equal((path(metric).match(/M/g) || []).length, 2);
    assert.equal((path(metric).match(/L/g) || []).length, 2);
    const spans = [
      ...chart(metric).matchAll(
        /<line x1="([^"]+)" x2="([^"]+)"[^>]*><title>Schwelle oben:/g,
      ),
    ].map((match) => [Number(match[1]), Number(match[2])]);
    assert.equal(spans.length, 2);
    assert.ok(spans[0][1] < spans[1][0], "thresholds leave the sensor section open");
    for (const [left, right] of spans) {
      assert.ok(right > left);
      assert.ok([doorXs[0], doorXs[3]].every((x) => x < left || x > right));
    }
    for (let index = 0; index < traces.length; index++) {
      assert.equal(
        chart(metric).includes(`data-event-id="${kind}-${index}"`),
        ![0, 3].includes(index),
      );
    }
  }
  for (const [metric, kind] of [
    ["door_temperature_slope", "door_open"],
    ["infusion_humidity_delta", "infusion"],
    ["ventilation_temperature_loss", "ventilation_confirmed"],
  ]) {
    assert.equal((path(metric).match(/M/g) || []).length, 1);
    assert.equal((path(metric).match(/L/g) || []).length, traces.length - 1);
    assert.ok(
      traces.every((_trace, index) =>
        chart(metric).includes(`data-event-id="${kind}-${index}"`),
      ),
    );
  }
  for (const [index, proxyActive] of [
    [0, false],
    [1, true],
    [3, false],
    [4, true],
  ]) {
    panel.selectedDiagnosticTrace = traces[index];
    const html = panel.diagnosticSelectionMarkup(panel.diagnosticData().traces);
    assert.equal(html.includes('data-key="label:strong_humidity_slope"'), proxyActive);
    assert.equal(html.includes('data-key="label:weak_humidity_slope"'), proxyActive);
    assert.equal(html.includes('data-key="label:strong"'), proxyActive);
    assert.equal(html.includes('data-key="label:weak"'), proxyActive);
    assert.equal(/Signale: [^<]*Person/.test(html), proxyActive);
    assert.match(html, /data-key="label:door_temperature_slope"/);
    assert.match(html, /data-key="label:infusion_humidity_delta"/);
    assert.match(html, /data-key="label:ventilation_temperature_loss"/);
    assert.equal(
      panel.diagnosticsParameters(traces[index].at).strong_humidity_upper,
      proxyActive ? parameters.strong_humidity_upper : undefined,
    );
  }
  assert.equal(JSON.stringify(panel.shown.records), original);
});

test("a configured but currently missing measurement position stays explicit in the evidence", () => {
  const panel = makePanel();
  panel.shown.session.configuration_model = {
    measurement_positions: ["upper", "lower"],
    presence: { direct: false },
  };
  const trace = {
    at: iso(5),
    metrics: { upper: { door_temperature_slope: -1 } },
    signals: [],
  };
  panel.shown.records = [{ kind: "detector_trace", payload: trace }];
  const html = panel.diagnosticSelectionMarkup(panel.diagnosticData().traces);
  assert.match(
    html,
    /<summary>Messwerte unten<\/summary><dl><dt>Angaben<\/dt><dd>Nicht gespeichert/,
  );
});

test("incremental attribute updates preserve user-opened details", () => {
  const panel = makePanel();
  const attributes = new Map([
    ["open", ""],
    ["id", "evidence"],
    ["obsolete", "old"],
  ]);
  const current = {
    nodeName: "DETAILS",
    get attributes() {
      return [...attributes].map(([name, value]) => ({ name, value }));
    },
    getAttribute: (name) => attributes.get(name),
    removeAttribute: (name) => attributes.delete(name),
    setAttribute: (name, value) => attributes.set(name, value),
  };
  const next = {
    attributes: [{ name: "id", value: "evidence" }],
    hasAttribute: (name) => name === "id",
  };
  panel.patchAttributes(current, next);
  assert.equal(attributes.has("open"), true);
  assert.equal(attributes.has("obsolete"), false);
});

test("changed selected text retains its anchor and focus within the remaining text", () => {
  const panel = makePanel();
  const current = {
    nodeType: 3,
    data: "abcdefghij",
    get length() {
      return this.data.length;
    },
  };
  let restored;
  const selection = {
    anchorNode: current,
    anchorOffset: 2,
    focusNode: current,
    focusOffset: 9,
    setBaseAndExtent: (...args) => (restored = args),
  };
  panel.ownerDocument = { getSelection: () => selection };
  panel.patchNode(current, { data: "abcde" });
  assert.deepEqual(restored, [current, 2, current, 5]);
});

test("markup reconciliation restores retained focus and backwards text selection after a keyed move", () => {
  const panel = makePanel();
  const text = { nodeType: 3, isConnected: true, length: 10 };
  const focusCalls = [];
  const focused = {
    isConnected: true,
    focus(options) {
      focusCalls.push(options);
      panel.shadowRoot.activeElement = this;
    },
  };
  let restored;
  const selection = {
    anchorNode: text,
    anchorOffset: 8,
    focusNode: text,
    focusOffset: 2,
    setBaseAndExtent: (...values) => (restored = values),
  };
  const target = {
    contains: (node) => node === focused || node === text,
    cloneNode: () => ({}),
  };
  panel.$ = () => target;
  panel.shadowRoot.activeElement = focused;
  panel.shadowRoot.getSelection = () => selection;
  panel.syncInfo = () => {};
  // Browser insertBefore has this side effect for retained nodes; the browser
  // regression exercises that actual operation with a late light-command row.
  panel.patchNode = () => {
    panel.shadowRoot.activeElement = null;
    selection.anchorNode = selection.focusNode = null;
    selection.anchorOffset = selection.focusOffset = 0;
  };
  panel.updateMarkup("#history", "new sibling before retained row");
  assert.equal(panel.shadowRoot.activeElement, focused);
  assert.equal(focusCalls.length, 1);
  assert.equal(focusCalls[0].preventScroll, true);
  assert.deepEqual(restored, [text, 8, text, 2]);
});

test("markup reconciliation never refocuses removed, replaced or disabled controls", () => {
  for (const change of ["removed", "replaced", "disabled", "outside"]) {
    const panel = makePanel();
    let connected = true;
    let contained = change !== "outside";
    let focusedAgain = false;
    const focused = {
      get isConnected() {
        return connected;
      },
      focus() {
        focusedAgain = true;
      },
    };
    const target = {
      contains: (node) => contained && node === focused,
      cloneNode: () => ({}),
    };
    panel.$ = () => target;
    panel.shadowRoot.activeElement = focused;
    panel.syncInfo = () => {};
    panel.patchNode = () => {
      panel.shadowRoot.activeElement = null;
      if (change === "removed") connected = false;
      if (change === "replaced") contained = false;
      if (change === "disabled") focused.disabled = true;
    };
    panel.updateMarkup("#history", "changed markup");
    assert.equal(focusedAgain, false, change);
  }
});

test("a moved text field keeps its input selection without scrolling", () => {
  const panel = makePanel();
  const calls = [];
  const focused = {
    isConnected: true,
    selectionStart: 2,
    selectionEnd: 6,
    selectionDirection: "backward",
    focus: (options) => calls.push(["focus", options.preventScroll]),
    setSelectionRange: (...values) => calls.push(["range", ...values]),
  };
  const target = { contains: (node) => node === focused, cloneNode: () => ({}) };
  panel.$ = () => target;
  panel.shadowRoot.activeElement = focused;
  panel.syncInfo = () => {};
  panel.patchNode = () => {
    panel.shadowRoot.activeElement = null;
    focused.selectionStart = focused.selectionEnd = 0;
  };
  panel.updateMarkup("#history", "new sibling");
  assert.deepEqual(calls, [
    ["focus", true],
    ["range", 2, 6, "backward"],
  ]);
});
