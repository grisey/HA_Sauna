"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
const sourcePath = process.env.PANEL_SOURCE || "custom_components/ha_sauna/panel.js";
const source = fs.readFileSync(sourcePath, "utf8");
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Date,
  Intl,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
  setTimeout,
  clearTimeout,
});

const iso = (second) => new Date(1_700_000_000_000 + second * 1000).toISOString();
const session = {
  ended_at: iso(3600),
  timeline: { session_started_at: iso(0), processed: [], completed: [] },
};
const panel = () =>
  Object.assign(Object.create(Panel.prototype), {
    shown: { session, records: [] },
    state: {
      now: iso(3600),
      configuration: { parameters: { sensor_timeout_seconds: 5 } },
    },
    $: () => null,
    scheduleHistoryRender: () => {},
  });

test("overview handles remain bounded and synchronous zoom retains its anchor", () => {
  const p = panel();
  const [start, end] = p.historyDomain();
  const span = end - start;
  p.setHistoryWindow(start - 10_000, end + 10_000);
  assert.equal(p.window[0], start);
  assert.equal(p.window[1], end);
  p.setHistoryWindow(end - 1, end);
  assert.equal(p.window[1], end);
  assert.ok(p.window[1] - p.window[0] >= span / 256);

  p.setHistoryWindow(start, end);
  p.svgCoordinates = () => ({ x: 600 });
  const before = (p.window[0] + p.window[1]) / 2;
  p.zoomAt(2, 0, {});
  assert.equal((p.window[0] + p.window[1]) / 2, before);
  const width = p.window[1] - p.window[0];
  p.zoomAt(2, 0, {});
  assert.equal(p.window[1] - p.window[0], width / 2);
});

test("gesture ownership captures the overview pointer and defers rendering until movement", () => {
  const p = panel();
  p.window = [10, 20];
  const invalidations = [];
  p.historyChart = {
    interaction: { invalidateGeometry: (...args) => invalidations.push(args) },
  };
  const captures = [];
  const svg = { setPointerCapture: (pointerId) => captures.push(pointerId) };
  const event = {
    pointerId: 7,
    target: { dataset: { historyHandle: "start" }, closest: () => null },
    preventDefault: () => (event.prevented = true),
    clientX: 100,
  };
  p.overviewFraction = () => 0.25;
  p.beginHistoryGesture(event, svg);
  assert.equal(event.prevented, true);
  assert.deepEqual(captures, [7]);
  assert.deepEqual(invalidations, [["geometry", false]]);
  assert.equal(p.historyGesture.pointerId, 7);
  assert.equal(p.historyGesture.kind, "start");
  assert.equal(p.historyGesture.fraction, 0.25);
});

test("a cursor frame preserves the existing chart object and interaction nodes", () => {
  const p = panel();
  const reads = [];
  const chart = { interaction: { readGeometry: () => reads.push("read") } };
  p.historyChart = chart;
  p.isConnected = true;
  p.$ = (selector) => (selector === "#history" ? { hidden: false } : null);
  p.pendingHover = { clientX: 25, clientY: 30 };
  p.hoverChart = (event) => reads.push(event);
  p.renderHistory(new Set(["cursor"]));
  assert.equal(p.historyChart, chart);
  assert.deepEqual(reads, ["read", p.pendingHover]);
});

test("only history-owned SVGs use history interaction coordinates", () => {
  const p = panel();
  const calls = [];
  const historySvg = {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 480 }),
  };
  const temperatureSvg = {
    getBoundingClientRect: () => ({ left: 20, top: 10, width: 200, height: 100 }),
  };
  p.historyChart = {
    surface: historySvg,
    interaction: {
      coordinates: (...args) => {
        calls.push(args);
        return { x: 777, y: 333 };
      },
    },
  };
  const historyPoint = p.svgCoordinates(historySvg, 1, 2);
  assert.equal(historyPoint.x, 777);
  assert.equal(historyPoint.y, 333);
  assert.equal(calls.length, 1);
  const temperaturePoint = p.svgCoordinates(temperatureSvg, 120, 60);
  assert.equal(temperaturePoint.x, 600);
  assert.equal(temperaturePoint.y, 240);
  assert.equal(
    calls.length,
    1,
    "temperature controls keep their own coordinate surface",
  );
});

test("the history heading and timeline revisions remain independent of samples", () => {
  const p = panel();
  p.selected = "live";
  p.state.operation_enabled = true;
  p.state.session = { timeline: { session_id: "live" } };
  assert.equal(p.historyTitle(session), "Laufende Sitzung");
  p.state.operation_enabled = false;
  assert.equal(p.historyTitle(session), "Letzte Sitzung");
  p.selected = "archive-1";
  p.sessions = [{ session_id: "archive-1", started_at: iso(10) }];
  assert.match(p.historyTitle({ timeline: {} }), /^Sitzung vom /);

  const live = {
    ...session,
    energy: null,
    heating: { intervals: [] },
    timeline: { ...session.timeline, session_id: "s", active: null, retracted: [] },
  };
  const initial = p.updateHistoryTimelineRevision(live);
  live.timeline = {
    ...live.timeline,
    processed: [{ event_id: "event-1", kind: "infusion" }],
  };
  assert.ok(p.updateHistoryTimelineRevision(live) > initial);
});

test("pointer-owned pinch suppresses duplicate Safari gesture zoom", () => {
  const p = panel();
  const svg = {
    setPointerCapture: () => {},
    getBoundingClientRect: () => ({ left: 0, width: 1200 }),
  };
  let zooms = 0;
  let prevented = 0;
  p.chartPointers = new Map();
  p.zoomAt = () => zooms++;
  p.beginChartPointer({ pointerId: 1, clientX: 10, clientY: 10 }, svg);
  p.beginChartPointer({ pointerId: 2, clientX: 20, clientY: 20 }, svg);
  const event = {
    target: { closest: () => svg },
    preventDefault: () => prevented++,
    scale: 2,
    clientX: 15,
  };
  p.beginWebkitGesture(event);
  p.updateWebkitGesture(event);
  p.endWebkitGesture(event);
  assert.equal(zooms, 0);
  assert.equal(p.historyInputMode, "pointer");
  assert.ok(prevented >= 3);
});

test("a retargeted WebKit gesture resolves its chart through the composed path", () => {
  const p = panel();
  const svg = {
    matches: (selector) => selector === "svg.session-chart",
    getBoundingClientRect: () => ({ left: 10, width: 800 }),
  };
  const factors = [];
  let prevented = 0;
  p.historyChart = {
    interaction: {
      invalidateGeometry: () => {},
      readGeometry: () => ({ rects: { surface: { left: 10, width: 800 } } }),
    },
  };
  p.zoomAt = (...args) => factors.push(args);
  const event = {
    target: { closest: () => null },
    composedPath: () => [{}, svg, {}],
    preventDefault: () => prevented++,
    scale: 1,
    clientX: 210,
  };
  p.beginWebkitGesture(event);
  event.scale = 2;
  p.updateWebkitGesture(event);
  p.endWebkitGesture(event);
  assert.equal(factors.length, 1);
  assert.equal(factors[0][0], 2);
  assert.equal(factors[0][1], 210);
  assert.equal(factors[0][2], svg);
  assert.equal(p.historyInputMode, null);
  assert.equal(prevented, 3);
});

test("explicit navigation during a periodic refresh queues one fresh read", async () => {
  let releaseFirst;
  let stateReads = 0;
  const firstState = new Promise((resolve) => (releaseFirst = resolve));
  const p = Object.assign(Object.create(Panel.prototype), {
    entry: "entry",
    generation: 0,
    isConnected: true,
    shadowRoot: { activeElement: null },
    api: async (path) => {
      if (path !== "/entry/state") throw Error(path);
      stateReads++;
      return stateReads === 1 ? firstState : {};
    },
    $: (selector) => (selector === "#history" ? { hidden: true } : null),
    drawCurrent: () => {},
    drawSettings: () => {},
    scheduleRefresh: () => {},
    message: () => {},
  });
  const periodic = p.refresh();
  await Promise.resolve();
  await p.refresh(true);
  assert.equal(p.refreshPending, true);
  releaseFirst({});
  await periodic;
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(stateReads, 2);
});
