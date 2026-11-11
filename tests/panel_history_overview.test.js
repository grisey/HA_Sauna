// LZ6: Abgeschlossene Sitzungen kommen als Übersicht (M4). Zoom lädt die vollen Werte
// des Ausschnitts, Details laden alle; Originale werden nach ID vereinigt.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const panelPath = process.env.PANEL_PATH || "custom_components/ha_sauna/panel.js";
let Panel;
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
  HTMLElement: class {
    attachShadow() {}
  },
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
  clearTimeout,
  document: { hidden: false },
});
const origin = Date.UTC(2032, 0, 1, 12);
const iso = (second) => new Date(origin + second * 1000).toISOString();
const session = () => ({
  timeline: {
    session_id: "archived",
    session_started_at: iso(0),
    completed: [],
    active: null,
    processed: [],
    retracted: [],
  },
  ended_at: iso(600),
});
const roles = [
  {
    kind: "measurement",
    position: "upper",
    quantity: "temperature",
    source: "sensor.t",
  },
];
// Eine Messung je Sekunde; jede zehnte ID ist eine Phase (übriger Datensatz).
const rows = Array.from({ length: 600 }, (_, second) => ({
  id: second + 1,
  second,
  value: 60 + (second % 17),
}));
const columns = (selected) => ({
  roles,
  role: selected.map(() => 0),
  id: selected.map((row) => row.id),
  received_at: selected.map((row) => iso(row.second)),
  value: selected.map((row) => row.value),
  absolute_humidity: selected.map(() => null),
  absolute_humidity_valid_until: selected.map(() => null),
  record_received_at: [],
});
const overview = (selected, bin = 32768, more = {}) => ({
  session: session(),
  phase_projection: { intervals: [] },
  records: [
    { id: 1000, kind: "phase", received_at: iso(10), payload: { phase: "bereit" } },
  ],
  measurement_columns: columns(selected),
  next_after: null,
  measurement_resolution: { overview: true, bin_ms: bin, ...more },
});
const range = (request) => {
  const query = new URL(request, "http://test").searchParams,
    from = query.has("from") ? Number(query.get("from")) : -Infinity,
    to = query.has("to") ? Number(query.get("to")) : Infinity;
  return {
    session_id: "archived",
    measurement_columns: columns(
      rows.filter((row) => {
        const time = origin + row.second * 1000;
        return time >= from && time < to;
      }),
    ),
  };
};
function panel(api, width) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
    "#plots": {
      innerHTML: "",
      getBoundingClientRect: () => ({ width }),
    },
  };
  return Object.assign(fixturePanel(new Panel()), {
    entry: "e",
    generation: 0,
    selected: "archived",
    view: "history",
    navigation: { main: "history", detail: "detail" },
    isConnected: true,
    state: {
      now: iso(700),
      operation_enabled: false,
      permissions: { admin: true },
      session: null,
      phase_projection: { intervals: [] },
    },
    sessions: [{ session_id: "archived" }],
    historyListStale: false,
    cache: new Map(),
    shadowRoot: { activeElement: null, querySelectorAll: () => [] },
    api,
    $: (selector) => nodes[selector],
    updateMarkup: (selector, html) => {
      if (nodes[selector]) nodes[selector].innerHTML = html;
    },
    draws: 0,
    messages: [],
    renderHistory() {},
    drawHistory() {
      this.draws++;
    },
    scheduleHistoryRender() {},
    drawCurrent() {},
    scheduleRefresh() {},
    message(error) {
      if (error) this.messages.push(String(error));
    },
  });
}
const settle = async (p) => {
  while (p.historyMeasurementLoad) await p.historyMeasurementLoad.promise;
};
const sparse = rows.filter((row) => row.second % 5 === 0);

test("a cold load asks for the overview of the curve width and keeps it", async () => {
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    return request.includes("/measurements") ? range(request) : overview(sparse);
  }, 1295.5);
  await p.startHistoryLoad();
  assert.equal(requests.length, 1);
  assert.match(
    requests[0],
    /\/e\/archive\?session_id=archived&projection=history&resolution=overview&columns=1296&after=0$/,
  );
  const cache = p.historyCache("archived");
  assert.equal(cache.finalSynced, true);
  assert.deepEqual(
    { ...cache.resolution, ranges: [...cache.resolution.ranges] },
    { binMs: 32768, alignMs: 32768, ranges: [], all: false, failed: null },
  );
  assert.equal(cache.records.length, sparse.length + 1);
  // Ganze Sitzung mit passender Breite: nichts nachzuladen.
  p.historyCurvePixels = 10;
  p.ensureHistoryResolution();
  assert.equal(p.historyMeasurementLoad, undefined);
  // Hat der Server keine feinere Stufe, folgen alle Werte statt einer Schleife.
  p.window = p.historyDomain();
  p.historyCurvePixels = 100000;
  p.ensureHistoryResolution();
  await settle(p);
  p.ensureHistoryResolution();
  await settle(p);
  assert.match(requests[1], /resolution=overview&columns=100000&after=0$/);
  assert.equal(requests[2], "/e/archive/measurements?session_id=archived");
  assert.equal(cache.resolution.all, true);
  p.ensureHistoryResolution();
  assert.equal(requests.length, 3);
});

test("zooming loads the aligned range once and merges originals by ID", async () => {
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    return request.includes("/measurements") ? range(request) : overview(sparse, 4096);
  }, 800);
  await p.startHistoryLoad();
  p.historyCurvePixels = 800;
  const start = origin + 100_000,
    end = origin + 200_000;
  p.window = [start, end];
  p.ensureHistoryResolution();
  await settle(p);
  const from = (Math.floor(start / 4096) - 1) * 4096,
    to = (Math.floor(end / 4096) + 2) * 4096;
  assert.deepEqual(requests.slice(1), [
    `/e/archive/measurements?session_id=archived&from=${from}&to=${to}`,
  ]);
  const cache = p.historyCache("archived"),
    ids = Array.from(cache.records, (record) => record.id);
  assert.deepEqual(
    ids,
    [...ids].sort((a, b) => a - b),
  );
  assert.equal(new Set(ids).size, ids.length);
  for (const row of rows) {
    const time = origin + row.second * 1000;
    if (time >= from && time < to) assert.ok(cache.recordIds.has(row.id), row.id);
  }
  assert.ok(p.draws >= 2);
  // Derselbe oder ein enthaltener Ausschnitt lädt nichts mehr.
  p.window = [start + 10_000, end - 10_000];
  p.ensureHistoryResolution();
  assert.equal(p.historyMeasurementLoad, null);
  p.window = [origin + 400_000, origin + 450_000];
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(requests.length, 3);
});

test("details load all values once, a wider curve area a finer overview", async () => {
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    if (request.includes("/measurements")) return range(request);
    const columnsParam = new URL(request, "http://test").searchParams.get("columns");
    return columnsParam === "5000"
      ? overview(rows.slice(0, 300), 128)
      : overview(sparse);
  }, 400);
  await p.startHistoryLoad();
  // Breitere Kurvenfläche: kleinere Abschnitte als die Übersicht trägt.
  p.window = p.historyDomain();
  p.historyCurvePixels = 4999.2;
  p.ensureHistoryResolution();
  await settle(p);
  assert.match(requests[1], /resolution=overview&columns=5000&after=0$/);
  const cache = p.historyCache("archived");
  assert.equal(cache.resolution.binMs, 128);
  assert.equal(cache.resolution.alignMs, 32768);
  assert.equal(
    cache.records.length,
    new Set([...sparse, ...rows.slice(0, 300)]).size + 1,
  );
  p.view = "diagnostics";
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(requests[2], "/e/archive/measurements?session_id=archived");
  assert.equal(cache.resolution.all, true);
  assert.equal(cache.records.length, rows.length + 1);
  p.window = [origin + 1000, origin + 2000];
  p.ensureHistoryResolution();
  assert.equal(requests.length, 3);
});

test("a failed range shows the error once and pages without overview load nothing", async () => {
  let failures = 0;
  const p = panel(async (request) => {
    if (!request.includes("/measurements")) return overview(sparse);
    failures++;
    throw Error("offline");
  }, 800);
  await p.startHistoryLoad();
  p.historyCurvePixels = 800;
  p.window = [origin + 100_000, origin + 200_000];
  p.ensureHistoryResolution();
  await settle(p);
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(failures, 1);
  assert.deepEqual(p.messages, ["Error: offline"]);
  // Ein anderer Ausschnitt versucht es erneut.
  p.window = [origin + 300_000, origin + 400_000];
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(failures, 2);
  const requests = [];
  const running = panel(async (request) => {
    requests.push(request);
    return { ...overview(rows), measurement_resolution: undefined };
  }, 800);
  await running.startHistoryLoad();
  running.window = [origin + 100_000, origin + 200_000];
  running.view = "diagnostics";
  running.ensureHistoryResolution();
  assert.equal(requests.length, 1);
  assert.equal(running.historyCache("archived").resolution, undefined);
});
