const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {
    attachShadow() {}
  },
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  clearTimeout,
  setTimeout,
});
const at = (second) =>
  new Date(Date.parse("2032-01-01T08:00Z") + second * 1000).toISOString();
function diagnosticPanel() {
  const nodes = {
    "#history": { hidden: false },
    "#plots": { hidden: true },
    "#detection-plots": {
      innerHTML: "",
      getBoundingClientRect: () => ({ width: 800 }),
    },
  };
  const records = [10, 20].map((second, index) => ({
    id: index + 1,
    kind: "detector_trace",
    payload: {
      at: at(second),
      metrics: { upper: { door_temperature_slope: -2 } },
      signals: ["door_open"],
      conditions: {},
      holds: {},
      checks: {},
    },
  }));
  const session = {
    timeline: {
      session_id: "s",
      session_started_at: at(0),
      processed: [
        { event_id: "e", kind: "door_open", effective_at: at(10), detected_at: at(10) },
      ],
      completed: [],
      retracted: [],
    },
    heating: {},
    energy: {},
  };
  const p = Object.assign(new Panel(), {
    isConnected: true,
    shown: { records, session },
    historyDetail: true,
    state: {
      permissions: { admin: true },
      configuration: { parameters: { person_step_seconds: 5, door_open_slope: -1 } },
    },
    window: [Date.parse(at(0)), Date.parse(at(30))],
    $: (selector) => nodes[selector],
    ensureHistoryWindow() {},
    revealEventTarget() {},
  });
  let builds = 0;
  const draw = p.drawDiagnostics.bind(p);
  p.drawDiagnostics = () => {
    builds++;
    draw();
  };
  return {
    p,
    nodes,
    get builds() {
      return builds;
    },
  };
}
test("diagnostics retain markup and their index through unrelated live status progress", () => {
  const fixture = diagnosticPanel(),
    { p, nodes } = fixture;
  p.renderHistory();
  const html = nodes["#detection-plots"].innerHTML,
    index = p.diagnosticData();
  for (let i = 1; i <= 3; i++) {
    p.state.now = at(30 + i);
    p.shown.session = {
      ...p.shown.session,
      heating: { seconds: i },
      energy: { measured_kwh: i / 100 },
    };
    p.renderHistory(new Set(["status"]));
  }
  assert.equal(fixture.builds, 1);
  assert.equal(nodes["#detection-plots"].innerHTML, html);
  assert.equal(p.diagnosticData(), index);
  p.shown.records.push({
    id: 3,
    kind: "measurement",
    payload: {
      position: "upper",
      quantity: "temperature",
      value: 70,
      received_at: at(31),
    },
  });
  p.renderHistory(new Set(["archive"]));
  assert.equal(fixture.builds, 1, "measurements are not diagnostic inputs");
  p.shown.records.push({
    id: 4,
    kind: "detector_trace",
    payload: { ...index.traces[0], at: at(25) },
  });
  p.renderHistory(new Set(["archive"]));
  assert.equal(fixture.builds, 2);
  assert.equal(index.traces.length, 3, "every trace remains available");
  assert.notEqual(nodes["#detection-plots"].innerHTML, html);
  p.shown.records.push({
    id: 5,
    kind: "detection",
    payload: { event: { event_id: "e" }, trace_at: at(20) },
  });
  p.renderHistory(new Set(["archive"]));
  assert.equal(fixture.builds, 3);
  assert.equal(p.eventNavigation()[0].trace_at, at(20));
  p.state.configuration.parameters.door_open_slope = -3;
  p.renderHistory();
  assert.equal(fixture.builds, 4, "effective thresholds invalidate");
  p.window = [Date.parse(at(5)), Date.parse(at(30))];
  p.renderHistory();
  assert.equal(fixture.builds, 5, "viewport invalidates");
  nodes["#detection-plots"].getBoundingClientRect = () => ({ width: 500 });
  p.renderHistory();
  assert.equal(fixture.builds, 6, "geometry invalidates");
  p.state.permissions.admin = false;
  p.updateMarkup = (selector, html) => {
    nodes[selector].innerHTML = html;
  };
  p.renderHistory();
  assert.equal(nodes["#detection-plots"].innerHTML, "");
});
test("archive reset frees the source index and event mappings immediately", () => {
  const { p } = diagnosticPanel();
  p.renderHistory();
  assert.ok(p.chartDataIndex.records.length);
  assert.ok(p.historyNavigationCache);
  p.clearHistoryDisplay = () => {};
  p.clearArchiveCaches();
  assert.equal(p.chartDataIndex, null);
  assert.equal(p.historyNavigationCache, null);
  assert.equal(p.shown, null);
  assert.equal(p.cache.size, 0);
});
test("record-budget LRU keeps active sources and reloads an evicted archive from zero", async () => {
  const p = Object.assign(new Panel(), {
    entry: "e",
    isConnected: true,
    selected: "a",
    sessions: [],
    historyListStale: false,
    historyAccessProjection: "admin",
    state: {
      permissions: { admin: true },
      frontend_defaults: { history_cache_records: 4 },
      session: { timeline: { session_id: "live" } },
    },
    $: () => ({ hidden: false }),
    drawHistory() {},
    syncHistorySessions() {},
    message() {},
  });
  for (const id of ["live", "loading", "a", "b", "c"]) {
    const cache = p.historyCache(id);
    cache.records.push({ id: 1 }, { id: 2 });
    cache.after = 2;
    cache.pageRunLoaded = cache.finalSynced = true;
    cache.session = { timeline: { session_id: id }, ended_at: at(30) };
  }
  p.historyLoad = { sessionId: "loading" };
  p.trimHistoryCaches();
  assert.deepEqual([...p.cache.keys()], ["live", "loading", "a"]);
  p.historyLoad = null;
  p.trimHistoryCaches();
  assert.deepEqual([...p.cache.keys()], ["live", "a"]);
  const requests = [];
  p.api = async (path) => {
    requests.push(path);
    return {
      records: [{ id: 1 }, { id: 2 }],
      next_after: null,
      session: { timeline: { session_id: "b" }, ended_at: at(30) },
    };
  };
  p.selected = "b";
  await p.startHistoryLoad();
  assert.equal(requests.length, 1);
  assert.ok(requests[0].endsWith("after=0"));
  assert.deepEqual(
    Array.from(p.cache.get("b").records, (r) => r.id),
    [1, 2],
  );
  assert.equal(p.cache.has("a"), false);
  assert.equal(p.cache.has("live"), true);
});
test("temperature cancellation never commits and capture release during commit does not cancel", async () => {
  const p = Object.assign(new Panel(), {
    drawCurrent() {},
    applyAppearance() {},
    cancelProgramDrag() {},
    cancelHistoryFrame() {},
  });
  let commands = 0,
    releases = 0;
  p.changeTarget = async () => {
    commands++;
    assert.ok(p.temperatureInteraction.committing);
  };
  const svg = {
    hasPointerCapture: () => true,
    releasePointerCapture(id) {
      releases++;
      if (p.temperatureInteraction?.pointerId === id)
        p.cancelTemperatureDrag({ pointerId: id });
    },
  };
  p.temperatureInteraction = { pointerId: 7, svg, value: 80 };
  p.disconnectedCallback();
  assert.equal(p.temperatureInteraction, null);
  assert.equal(commands, 0);
  assert.equal(releases, 1);
  p.temperatureInteraction = { pointerId: 8, svg, value: 81 };
  p.cancelTemperatureDrag({ pointerId: 8 });
  assert.equal(commands, 0);
  p.temperatureInteraction = { pointerId: 9, svg, value: 82 };
  await p.endTemperatureDrag({ pointerId: 9 });
  assert.equal(commands, 1);
  assert.equal(p.temperatureInteraction, null);
});
