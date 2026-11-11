// LZ6/LZ7: Abgeschlossene Sitzungen kommen als Übersicht (M4). Danach lädt das Panel
// die vollen Werte abschnittsweise; Zoom nutzt sie zuerst, Details laden alle;
// Originale werden nach ID vereinigt.
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
// Abschnitte der vollen Werte: 2^17 ms, an der Epoche ausgerichtet.
const CHUNK = 2 ** 17;
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
  measurement_window: { started_at: iso(-60), ended_at: iso(660), complete: true },
  phase_projection: { intervals: [] },
  records: [
    { id: 1000, kind: "phase", received_at: iso(10), payload: { phase: "bereit" } },
  ],
  measurement_columns: columns(selected),
  next_after: null,
  measurement_resolution: { overview: true, bin_ms: bin, chunk_ms: CHUNK, ...more },
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
// Abschnittsnamen des Messfensters wie das Panel: Schlüssel, dann davor, danach.
const first = Math.floor((origin - 60_000) / CHUNK),
  last = Math.floor((origin + 660_000) / CHUNK);
const chunkQuery = (key) =>
  `/e/archive/measurements?session_id=archived&from=${key * CHUNK}&to=${(key + 1) * CHUNK}`;
const backgroundRequests = [
  ...Array.from({ length: last - first + 1 }, (_, index) => chunkQuery(first + index)),
  `/e/archive/measurements?session_id=archived&to=${first * CHUNK}`,
  `/e/archive/measurements?session_id=archived&from=${(last + 1) * CHUNK}`,
];
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
      now: iso(7000),
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
    draws: [],
    messages: [],
    renderHistory() {},
    drawHistory(reason) {
      this.draws.push(reason);
    },
    scheduleHistoryRender() {},
    drawCurrent() {},
    scheduleRefresh() {},
    message(error) {
      if (error) this.messages.push(String(error));
    },
  });
}
// Begrenztes Warten, damit ein Fehler den Lauf nicht anhält.
const waitFor = async (check) => {
  for (let step = 0; step < 2000 && !check(); step++)
    await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(check(), "Bedingung nicht erreicht");
};
const settle = async (p) => {
  for (;;) {
    const full = p.cache.get("archived")?.full;
    if (p.historyMeasurementLoad) await p.historyMeasurementLoad.promise;
    else if (full?.running) await full.running.promise;
    else return;
  }
};
const sparse = rows.filter((row) => row.second % 5 === 0);

test("a cold load asks for the overview, then loads the full values after drawing", async () => {
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
    { ...cache.resolution },
    { binMs: 32768, alignMs: 32768, chunkMs: CHUNK, all: false, failed: null },
  );
  const curve = cache.records;
  assert.equal(curve.length, sparse.length + 1);
  // Ganze Sitzung mit passender Breite: Hintergrund lädt Abschnitt für Abschnitt.
  p.draws = [];
  p.historyCurvePixels = 10;
  p.ensureHistoryResolution();
  assert.equal(p.historyMeasurementLoad, undefined);
  assert.equal(requests.length, 1, "erst nach dem Zeichnen");
  await settle(p);
  assert.deepEqual(requests.slice(1), backgroundRequests);
  assert.equal(cache.full.complete, true);
  assert.equal(cache.full.ids.size, rows.length);
  // Die Kurve bleibt bei der Übersicht: dieselben Datensätze, kein neues Zeichnen.
  assert.equal(cache.records, curve);
  assert.deepEqual(p.draws, ["values"]);
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(requests.length, 1 + backgroundRequests.length);
  // Breitere Kurvenfläche bei geladenen vollen Werten: alle zur Kurve, ohne Abruf.
  p.window = p.historyDomain();
  p.historyCurvePixels = 100000;
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(cache.resolution.all, true);
  assert.equal(requests.length, 1 + backgroundRequests.length);
  assert.equal(cache.records.length, rows.length + 1);
});

test("zooming loads its sections first, merges them and never twice", async () => {
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    return request.includes("/measurements") ? range(request) : overview(sparse, 4096);
  }, 800);
  await p.startHistoryLoad();
  p.historyCurvePixels = 800;
  const start = origin + 300_000,
    end = origin + 320_000;
  p.window = [start, end];
  p.ensureHistoryResolution();
  await settle(p);
  const from = (Math.floor(start / 4096) - 1) * 4096,
    to = (Math.floor(end / 4096) + 2) * 4096,
    keys = [];
  for (let key = Math.floor(from / CHUNK); key <= Math.floor((to - 1) / CHUNK); key++)
    keys.push(key);
  // Abschnitte des Ausschnitts zuerst, dann der Rest im Hintergrund, keiner doppelt.
  assert.deepEqual(requests.slice(1, 1 + keys.length), keys.map(chunkQuery));
  assert.deepEqual([...requests.slice(1)].sort(), [...backgroundRequests].sort());
  const cache = p.historyCache("archived"),
    ids = Array.from(cache.records, (record) => record.id);
  assert.deepEqual(
    ids,
    [...ids].sort((a, b) => a - b),
  );
  assert.equal(new Set(ids).size, ids.length);
  for (const row of rows) {
    const time = origin + row.second * 1000;
    if (time >= keys[0] * CHUNK && time < (keys.at(-1) + 1) * CHUNK)
      assert.ok(cache.recordIds.has(row.id), row.id);
  }
  assert.ok(p.draws.includes("archive"));
  // Ein anderer Ausschnitt nutzt die geladenen Abschnitte ohne Abruf.
  const before = requests.length;
  p.window = [origin + 30_000, origin + 60_000];
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(requests.length, before);
  assert.ok(cache.recordIds.has(31));
});

test("details load all values once, from the full values when they are there", async () => {
  const requests = [];
  let hold;
  const p = panel(async (request) => {
    requests.push(request);
    if (request.includes("/measurements")) {
      if (!request.includes("from=") && !request.includes("to=")) return range(request);
      await new Promise((resolve) => (hold = resolve));
      return range(request);
    }
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
  // Hintergrund läuft (erster Abschnitt wartet); Details holen alle Werte wie bisher.
  p.ensureHistoryResolution();
  await waitFor(() => hold);
  p.view = "diagnostics";
  p.ensureHistoryResolution();
  await p.historyMeasurementLoad.promise;
  assert.equal(requests.at(-1), "/e/archive/measurements?session_id=archived");
  assert.equal(cache.resolution.all, true);
  assert.equal(cache.records.length, rows.length + 1);
  hold();
  await settle(p);
  // Der Hintergrund endet: alle Werte sind schon da.
  assert.equal(
    requests.filter((request) => request.includes("from=") || request.includes("to="))
      .length,
    1,
  );
  // Sind die vollen Werte schon geladen, brauchen Details keinen Abruf.
  const quiet = [];
  const q = panel(async (request) => {
    quiet.push(request);
    return request.includes("/measurements") ? range(request) : overview(sparse);
  }, 400);
  await q.startHistoryLoad();
  q.historyCurvePixels = 10;
  q.ensureHistoryResolution();
  await settle(q);
  const loaded = quiet.length;
  q.view = "diagnostics";
  q.ensureHistoryResolution();
  assert.equal(quiet.length, loaded);
  const full = q.historyCache("archived");
  assert.equal(full.resolution.all, true);
  assert.equal(full.records.length, rows.length + 1);
});

test("a failed zoom section shows the error once; a background failure stays silent", async () => {
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
  assert.equal(failures, 2);
  assert.deepEqual(p.messages, ["Error: offline", "Error: offline"]);
  const quiet = panel(async (request) => {
    if (!request.includes("/measurements")) return overview(sparse);
    throw Error("offline");
  }, 800);
  await quiet.startHistoryLoad();
  quiet.historyCurvePixels = 10;
  quiet.ensureHistoryResolution();
  await settle(quiet);
  quiet.ensureHistoryResolution();
  await settle(quiet);
  assert.deepEqual(quiet.messages, []);
  assert.ok(quiet.historyCache("archived").full.failed);
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
