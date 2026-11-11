// LZ7: Nach der Übersicht lädt das Panel die vollen Sekundenwerte im Hintergrund.
// Danach sind Werte beim Zeigen, an Auswahlgrenzen, „–“ bei Ausfällen und die
// absolute Feuchte dieselben wie mit allen Seiten (b7); die Kurve bleibt unverändert.
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
const CHUNK = 2 ** 17;
const session = (id = "archived") => ({
  timeline: {
    session_id: id,
    session_started_at: iso(0),
    completed: [],
    active: null,
    processed: [],
    retracted: [],
  },
  ended_at: iso(600),
  measurement_ttl_seconds: 5,
});
const roles = ["temperature", "humidity"].map((quantity) => ({
  kind: "measurement",
  position: "upper",
  quantity,
  source: `sensor.${quantity}`,
}));
// Temperatur und Feuchte je Sekunde; Ausfall von 200 bis 229 s; kurze Gültigkeit
// der absoluten Feuchte, damit „–“ und Werte von der genauen Messung abhängen.
const rows = [];
for (let second = 0; second < 600; second++)
  for (const [role, base] of [
    [0, 70],
    [1, 20],
  ]) {
    const outage = role === 0 && second >= 200 && second < 230;
    rows.push({
      id: rows.length + 1,
      second: second + role * 0.25,
      role,
      value: outage ? null : base + ((second * 7) % 23) / 4,
      absolute: outage ? null : 9 + ((second * 3) % 11) / 10,
    });
  }
const columns = (selected) => ({
  roles,
  role: selected.map((row) => row.role),
  id: selected.map((row) => row.id),
  received_at: selected.map((row) => iso(row.second)),
  value: selected.map((row) => row.value),
  absolute_humidity: selected.map((row) => row.absolute),
  absolute_humidity_valid_until: selected.map((row) =>
    row.absolute == null ? null : iso(row.second + 2),
  ),
  record_received_at: [],
});
const page = (selected, resolution) => ({
  session: session(),
  measurement_window: { started_at: iso(-60), ended_at: iso(660), complete: true },
  phase_projection: { intervals: [] },
  records: [
    { id: 5000, kind: "phase", received_at: iso(10), payload: { phase: "bereit" } },
  ],
  measurement_columns: columns(selected),
  next_after: null,
  ...(resolution ? { measurement_resolution: resolution } : {}),
});
const range = (request) => {
  const query = new URL(request, "http://test").searchParams,
    from = query.has("from") ? Number(query.get("from")) : -Infinity,
    to = query.has("to") ? Number(query.get("to")) : Infinity;
  return {
    session_id: "archived",
    measurement_columns: columns(
      rows.filter((row) => {
        const time = Math.floor(origin + row.second * 1000);
        return time >= from && time < to;
      }),
    ),
  };
};
// Übersicht: jede zwanzigste Sekunde, wie eine grobe M4-Stufe ohne den Ausfall.
const sparse = rows.filter((row) => Math.floor(row.second) % 20 === 0);
const overview = () => page(sparse, { overview: true, bin_ms: 512, chunk_ms: CHUNK });
function panel(api) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
    "#plots": { innerHTML: "", getBoundingClientRect: () => ({ width: 800 }) },
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
// Werte wie beim Zeigen (frei und an Auswahlgrenzen) für alle Größen.
const readouts = (p) => {
  p.window = p.historyDomain();
  const result = [];
  for (let tenth = -100; tenth <= 6100; tenth += 5)
    for (const quantity of ["temperature", "humidity", "absolute_humidity"])
      for (const pinned of [false, true]) {
        const point = p.historyMeasurementAt(
          "upper",
          quantity,
          origin + tenth * 100,
          pinned,
        );
        result.push(point ? `${point.time}|${point.value}` : "–");
      }
  return result;
};

test("after the background load the readouts equal the paged full history (b7)", async () => {
  const b7 = panel(async () => page(rows));
  await b7.startHistoryLoad();
  const expected = readouts(b7);
  assert.ok(expected.includes("–"), "Ausfall und Gültigkeit kommen vor");
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    return request.includes("/measurements") ? range(request) : overview();
  });
  await p.startHistoryLoad();
  const cache = p.historyCache("archived"),
    curve = cache.records;
  // Vorher gilt die Übersicht (LZ6): andere Werte als mit allen Seiten.
  const early = readouts(p);
  assert.notDeepEqual(early, expected);
  p.historyCurvePixels = 800;
  p.ensureHistoryResolution();
  await settle(p);
  assert.equal(cache.full.complete, true);
  assert.deepEqual(readouts(p), expected);
  // Die Kurve wird nicht neu gerechnet: Datensätze und Kurvenindex unverändert.
  assert.equal(cache.records, curve);
  assert.equal(p.chartDataIndex.records, curve);
  assert.equal(p.chartDataIndex.indexedCount, curve.length);
  assert.ok(requests.every((request, index) => requests.indexOf(request) === index));
});

test("a session change stops the background load; returning reuses the sections", async () => {
  const requests = [];
  let release;
  const p = panel(async (request) => {
    requests.push(request);
    if (!request.includes("/measurements")) return overview();
    await new Promise((resolve) => (release = resolve));
    return range(request);
  });
  await p.startHistoryLoad();
  p.historyCurvePixels = 800;
  p.ensureHistoryResolution();
  await waitFor(() => release);
  const shown = p.shown;
  // Andere Sitzung gewählt, während der erste Abschnitt unterwegs ist.
  p.shown = { ...shown, session: session("other") };
  const pending = release;
  release = null;
  pending();
  await settle(p);
  const cache = p.historyCache("archived");
  assert.equal(requests.length, 2);
  assert.equal(cache.full.loaded.size, 1);
  assert.equal(cache.full.complete, false);
  // Zurück zur Sitzung: der Rest folgt, nichts doppelt.
  p.shown = shown;
  p.ensureHistoryResolution();
  for (let step = 0; step < 100 && cache.full.running; step++) {
    await waitFor(() => release || !cache.full.running);
    if (!release) break;
    const next = release;
    release = null;
    next();
  }
  await settle(p);
  assert.equal(cache.full.complete, true);
  assert.equal(new Set(requests).size, requests.length);
  assert.equal(requests.length, 1 + cache.full.names.length);
});

test("without a finer stored level and without full values the panel loads all once", async () => {
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    if (!request.includes("/measurements")) return overview();
    if (request.includes("from=") || request.includes("to=")) throw Error("offline");
    return range(request);
  });
  await p.startHistoryLoad();
  p.historyCurvePixels = 800;
  p.ensureHistoryResolution();
  await settle(p);
  const cache = p.historyCache("archived");
  assert.ok(cache.full.failed);
  assert.deepEqual(p.messages, []);
  p.window = p.historyDomain();
  p.historyCurvePixels = 100000;
  p.ensureHistoryResolution();
  await settle(p);
  p.ensureHistoryResolution();
  await settle(p);
  assert.match(requests.at(-2), /resolution=overview&columns=100000&after=0$/);
  assert.equal(requests.at(-1), "/e/archive/measurements?session_id=archived");
  assert.equal(cache.resolution.all, true);
  assert.equal(cache.records.length, rows.length + 1);
});
