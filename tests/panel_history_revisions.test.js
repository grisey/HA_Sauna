const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
});
test("visible revision ignores late insert outside unchanged neighbours but includes inner and edge inserts", () => {
  const p = Object.create(Panel.prototype),
    cache = new Map(),
    origin = Date.parse("2032-01-01T08:00Z");
  const record = (second, id) => ({
    id,
    kind: "measurement",
    payload: {
      position: "upper",
      quantity: "temperature",
      value: 70,
      received_at: new Date(origin + second * 1000).toISOString(),
    },
  });
  const records = [0, 10, 20, 30, 40, 50].map((second, index) =>
    record(second, index + 1),
  );
  const get = () => {
    p.historyIndex(records);
    return p.historyPreparedSeries(
      "upper",
      "temperature",
      origin + 20000,
      origin + 30000,
      20000,
      1070,
      cache,
    );
  };
  const initial = get(),
    raw = p.series("upper", "temperature");
  records.push(record(1, 7));
  assert.equal(
    get(),
    initial,
    "insertion before unchanged predecessor retains visible aggregation",
  );
  records.push(record(49, 8));
  assert.equal(
    get(),
    initial,
    "insertion after unchanged successor retains visible aggregation",
  );
  records.push(record(25, 9));
  const inside = get();
  assert.notEqual(inside, initial, "inside addition invalidates");
  records.push(record(25, 10));
  const duplicate = get();
  assert.notEqual(duplicate, inside, "same-time independent record invalidates");
  records.push(record(19, 11));
  const edge = get();
  assert.notEqual(edge, duplicate, "new nearer predecessor changes boundary curve");
  assert.equal(
    p.series("upper", "temperature"),
    raw,
    "late inserts retain original series",
  );
  assert.equal(
    raw.filter((point) => point.time === origin + 25000).length,
    2,
    "both records survive",
  );
});
test("phase/diagnostic records do not advance measurement-series revisions", () => {
  const p = Object.create(Panel.prototype),
    records = [
      {
        id: 1,
        kind: "measurement",
        payload: {
          position: "upper",
          quantity: "temperature",
          value: 70,
          received_at: "2032-01-01T08:00Z",
        },
      },
    ];
  p.historyIndex(records);
  const state = p.chartDataIndex.seriesState.get("upper:temperature"),
    revision = state.revision;
  records.push(
    { id: 2, kind: "phase", payload: { phase: "bereit" } },
    { id: 3, kind: "diagnostic", payload: { messages: [] } },
  );
  p.historyIndex(records);
  assert.equal(state.revision, revision);
  assert.equal(p.historyRecords("phase").length, 1);
});
