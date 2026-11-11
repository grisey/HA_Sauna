// LZ1: Verlaufsseiten mit spaltenweisen Messungen ergeben dieselben Kurven, Werte
// und Einzelbelege wie Seiten mit einzelnen Messdatensätzen.
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
});
const state = () => ({
  now: iso(700),
  operation_enabled: false,
  permissions: { admin: true },
  session: null,
  phase_projection: { intervals: [] },
});
// Originale wie in der Verlaufsprojektion: volle Messfelder und absolute Feuchte.
const measurement = (id, second, position, quantity, value, absolute = null) => ({
  id,
  entry_id: "e",
  session_id: "archived",
  kind: "measurement",
  received_at: iso(second),
  payload: {
    position,
    quantity,
    value,
    raw_value: String(value),
    source: `sensor.${position}_${quantity}`,
    received_at: iso(second),
    measured_at: iso(second),
    sample: { sensor_id: "s", boot_id: "b", sample_id: id, monotonic_ns: second },
  },
  display: {
    absolute_humidity: {
      value: absolute,
      received_at: iso(second),
      valid_until: absolute == null ? null : iso(second + 60),
    },
  },
});
const originals = () => {
  const records = [];
  let id = 0;
  for (let second = 1; second <= 30; second++) {
    records.push(measurement(++id, second, "upper", "temperature", 60 + second));
    records.push(
      measurement(++id, second, "upper", "humidity", 10 + (second % 7), 20 + second),
    );
    if (second % 10 === 0)
      records.push({
        id: ++id,
        kind: "phase",
        received_at: iso(second),
        payload: { phase: "bereit" },
      });
  }
  const snapshot = measurement(++id, 2, "lower", "temperature", 50);
  snapshot.kind = "source_snapshot";
  snapshot.received_at = iso(31);
  records.push(snapshot);
  return records;
};
// Spalten wie history_measurement_columns (Administrator).
const columns = (records) => {
  const result = {
      roles: [],
      role: [],
      id: [],
      received_at: [],
      value: [],
      absolute_humidity: [],
      absolute_humidity_valid_until: [],
      record_received_at: [],
    },
    rest = [];
  for (const record of records) {
    if (!["measurement", "source_snapshot"].includes(record.kind)) {
      rest.push(record);
      continue;
    }
    const p = record.payload,
      role = {
        kind: record.kind,
        position: p.position,
        quantity: p.quantity,
        source: p.source,
      };
    let index = result.roles.findIndex(
      (known) => JSON.stringify(known) === JSON.stringify(role),
    );
    if (index < 0) index = result.roles.push(role) - 1;
    result.role.push(index);
    result.id.push(record.id);
    result.received_at.push(p.received_at);
    result.value.push(p.value);
    result.absolute_humidity.push(record.display.absolute_humidity.value);
    result.absolute_humidity_valid_until.push(
      record.display.absolute_humidity.valid_until,
    );
    if (record.received_at !== p.received_at)
      result.record_received_at.push([record.id, record.received_at]);
  }
  return { records: rest, measurement_columns: result };
};
const pageOf = (records, more = null, format = "columns") => ({
  ...(format === "columns" ? columns(records) : { records }),
  next_after: more,
  session: session(),
  phase_projection: { intervals: [] },
});
function panel(api) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
    "#plots": { innerHTML: "" },
  };
  const p = Object.assign(fixturePanel(new Panel()), {
    entry: "e",
    generation: 0,
    selected: "archived",
    navigation: { main: "details", detail: "detail-history" },
    isConnected: true,
    state: state(),
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
    rendered: [],
    renderHistory() {},
    drawHistory() {
      this.draws++;
    },
    scheduleHistoryRender(reason) {
      this.rendered.push(reason);
    },
    drawCurrent() {},
    scheduleRefresh() {},
    message() {},
  });
  return p;
}
const paged = (records, size, format) => async (request) => {
  const after = Number(new URL(request, "http://test").searchParams.get("after"));
  const rest = records.filter((record) => record.id > after),
    rows = rest.slice(0, size);
  return pageOf(rows, rest.length > size ? rows.at(-1).id : null, format);
};
const indexed = (p) =>
  Object.fromEntries(
    [...p.historyIndex(p.historyCache("archived").records).series].map(
      ([key, values]) => [
        key,
        values.map(({ time, value, validUntil, serial }) => ({
          time,
          value,
          validUntil,
          serial,
        })),
      ],
    ),
  );

test("column pages give the history the same records, order and curves", async () => {
  const records = originals();
  const full = panel(paged(records, 25, "records")),
    compact = panel(paged(records, 25, "columns"));
  await full.startHistoryLoad();
  await compact.startHistoryLoad();
  const shown = compact.historyCache("archived").records,
    expected = full.historyCache("archived").records;
  assert.deepEqual(
    shown.map((record) => record.id),
    expected.map((record) => record.id),
  );
  for (const [index, record] of shown.entries()) {
    const original = expected[index];
    if (!["measurement", "source_snapshot"].includes(original.kind)) {
      assert.equal(record, original);
      continue;
    }
    const { position, quantity, value, source, received_at } = original.payload;
    // Objekte aus dem Panelkontext: Vergleich der Inhalte.
    assert.deepEqual(JSON.parse(JSON.stringify(record)), {
      id: original.id,
      kind: original.kind,
      received_at: original.received_at,
      payload: { position, quantity, source, value, received_at },
      display: original.display,
    });
  }
  assert.deepEqual(indexed(compact), indexed(full));
  assert.equal(compact.historyCache("archived").finalSynced, true);
});

test("a page run draws after its first and its last page only", async () => {
  const records = originals();
  const requests = [];
  const p = panel(async (request) => {
    requests.push(request);
    return paged(records, 20, "columns")(request);
  });
  await p.startHistoryLoad();
  assert.equal(requests.length, 4);
  assert.ok(requests.every((request) => request.includes("projection=history")));
  assert.equal(p.draws, 2);
  assert.equal(p.historyCache("archived").records.length, records.length);
  const single = panel(paged(records, 1000, "columns"));
  await single.startHistoryLoad();
  assert.equal(single.draws, 1);
});

test("single records of column measurements load their originals on demand", async () => {
  const records = originals();
  const requests = [];
  let answer;
  const p = panel(async (request) => {
    requests.push(request);
    if (request.includes("projection=history"))
      return paged(records, 1000, "columns")(request);
    const after = Number(new URL(request, "http://test").searchParams.get("after"));
    return new Promise((resolve) => {
      answer = () =>
        resolve({
          session: session(),
          records: records.filter((record) => record.id > after).slice(0, 7),
          next_after: after + 7,
        });
    });
  });
  await p.startHistoryLoad();
  p.showHistoryCache();
  p.state.appearance = { preferences: { detail_records_per_page: 5 } };
  p.detailRecordPage = 1;
  const waiting = p.renderHistoryRecords(p.shown.records);
  assert.equal((waiting.match(/Originalfelder werden geladen/g) || []).length, 5);
  assert.equal(requests.length, 2);
  assert.match(requests[1], /\/e\/archive\?session_id=archived&after=5$/);
  assert.doesNotMatch(requests[1], /projection/);
  // Während des Abrufs startet dieselbe Seite keinen zweiten.
  p.renderHistoryRecords(p.shown.records);
  assert.equal(requests.length, 2);
  p.rendered = [];
  answer();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(p.rendered, ["records"]);
  const loaded = p.renderHistoryRecords(p.shown.records);
  assert.doesNotMatch(loaded, /Originalfelder/);
  assert.match(loaded, /Originalwert/);
  assert.match(loaded, /data-record-id="6"/);
  assert.match(loaded, /data-record-id="10"/);
  // Seiten mit einzelnen Messdatensätzen brauchen keinen Abruf.
  const full = panel(paged(records, 1000, "records"));
  await full.startHistoryLoad();
  full.showHistoryCache();
  const direct = full.renderHistoryRecords(full.shown.records);
  assert.match(direct, /Originalwert/);
});

test("a failed original request shows the error and retries after a page change", async () => {
  const records = originals();
  let failures = 0;
  const p = panel(async (request) => {
    if (request.includes("projection=history"))
      return paged(records, 1000, "columns")(request);
    failures++;
    throw Error("offline");
  });
  await p.startHistoryLoad();
  p.showHistoryCache();
  p.renderHistoryRecords(p.shown.records);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(p.renderHistoryRecords(p.shown.records), /konnten nicht geladen werden/);
  assert.equal(failures, 1);
  p.renderHistoryRecords(p.shown.records);
  assert.equal(failures, 1, "no retry loop while the page stays");
  p.renderControlHistory = () => {};
  await p.action("detail-record-page:0");
  p.renderHistoryRecords(p.shown.records);
  assert.equal(failures, 2);
});
