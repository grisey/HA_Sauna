// Display aggregation remains pure data work. Canvas checks below use a
// functional FakeCanvas only to verify paths and gap commands, never speed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const appearanceCatalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).appearance;
const color = (id) =>
  appearanceCatalog.colors.find((item) => item.id === id).default.toLowerCase();
const curveStyles = {
  "upper:temperature": { stroke: color("series_temperature") },
  "lower:temperature": { stroke: color("series_temperature") },
  "upper:humidity": { stroke: color("series_humidity") },
  "lower:humidity": { stroke: color("series_humidity") },
};
const vm = require("node:vm");

class FakePath2D {
  constructor() {
    this.commands = [];
  }
  moveTo(x, y) {
    this.commands.push(["M", x, y]);
  }
  bezierCurveTo(...values) {
    this.commands.push(["C", ...values]);
  }
}
class FakeContext {
  setTransform() {}
  clearRect() {}
  save() {}
  restore() {}
  beginPath() {}
  rect() {}
  clip() {}
  roundRect() {}
  fill() {}
  setLineDash() {}
  stroke() {}
}
class FakeCanvas {
  constructor() {
    this.width = 0;
    this.height = 0;
    this.style = {};
    this.context = new FakeContext();
  }
  getContext() {
    return this.context;
  }
}

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Path2D: FakePath2D,
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(
  `${source}\nglobalThis.displayExports = { HistoryCurves };`,
  sandbox,
);
const { HistoryCurves } = sandbox.displayExports;

const base = 1_700_000_000_000;
const iso = (second) => new Date(base + second * 1000).toISOString();
const record = (second, value, position = "upper", quantity = "temperature") => ({
  kind: "measurement",
  payload: { position, quantity, value, received_at: iso(second) },
});
const session = (seconds) => ({
  ended_at: iso(seconds),
  configuration: { parameters: { sensor_timeout_seconds: 5 } },
  timeline: { session_started_at: iso(0), processed: [], completed: [] },
  heating: { intervals: [] },
});
const panel = (seconds, records) =>
  Object.assign(Object.create(Panel.prototype), {
    positions: new Set(["upper"]),
    window: [base, base + seconds * 1000],
    state: {
      now: iso(seconds),
      configuration: { parameters: { sensor_timeout_seconds: 5 } },
    },
    shown: { records, session: session(seconds) },
  });
const modelFor = (p, records, seconds, chart) => {
  p.historyIndex(records);
  return p.historyModel(chart, session(seconds));
};
const geometry = {
  width: 1200,
  height: 480,
  dpr: 1,
};

// A coarse level preserves both extrema while returning near-pixel work.
{
  const records = [];
  for (let second = 0; second < 14_400; second++)
    records.push(record(second, second === 7_200 ? 120 : 70 + (second % 50) / 100));
  const p = panel(14_400, records);
  p.historyIndex(records);
  const display = p.historyDisplayValues(
    "upper",
    "temperature",
    base,
    base + 14_400_000,
    5_000,
    1_070,
  );
  assert.ok(display.some((point) => point.value === 120));
  assert.ok(display.length < 4_500);
}

// Normal append keeps the prepared hierarchy and updates its raw tail.
{
  const records = [record(0, 70), record(10, 80)],
    p = panel(30, records);
  p.historyIndex(records);
  p.historyDisplayValues("upper", "temperature", base, base + 30_000, 5_000, 100);
  const display = p.historyDisplay(p.series("upper", "temperature"));
  records.push(record(20, 90));
  p.invalidateHistoryIndex();
  p.historyIndex(records);
  assert.equal(p.historyDisplay(p.series("upper", "temperature")), display);
  assert.equal(display.values.at(-1).value, 90);
}

// Nulls and stale intervals descend to raw points and stay separate Canvas paths.
{
  const records = [
    record(0, 70),
    record(1, 71),
    record(2, null),
    record(3, 72),
    record(20, 73),
  ];
  const p = panel(30, records);
  const chart = {
    prepared: new Map(),
    domain: [base, base + 30_000],
  };
  const curves = new HistoryCurves(new FakeCanvas(), {
    Path2DClass: FakePath2D,
    styles: curveStyles,
  });
  curves.update(modelFor(p, records, 30, chart), geometry);
  assert.equal(
    curves.mainPaths
      .get("upper:temperature")
      .path.commands.filter(([kind]) => kind === "M").length,
    3,
  );
}

// A timeout inside one coarse bucket is still represented by a raw display gap.
{
  const records = [record(0, 20), record(1, 21), record(12, 22)],
    p = panel(32, records);
  p.historyIndex(records);
  const display = p.historyDisplayValues(
    "upper",
    "temperature",
    base,
    base + 32_000,
    5_000,
    1,
  );
  assert.equal(display.length, 3);
  assert.equal(display[2].displayGap, true);
}

// Crossing a power-of-two level merges cached bins instead of rereading raw values.
{
  const records = [];
  for (let second = 0; second < 3_000; second++) records.push(record(second, 70));
  const p = panel(3_000, records);
  p.historyIndex(records);
  p.historyDisplayValues("upper", "temperature", base, base + 2_000_000, 5_000, 1_070);
  let rawVisits = 0;
  const original = p.appendHistoryDisplayLevel;
  p.appendHistoryDisplayLevel = (...args) => {
    rawVisits++;
    return original.apply(p, args);
  };
  p.historyDisplayValues("upper", "temperature", base, base + 2_200_000, 5_000, 1_070);
  assert.equal(rawVisits, 0);
}

// A coarse boundary never imports an older extrema beyond the exact neighbour.
{
  const records = [record(0, 120), record(5, 70), record(10, 71), record(15, 72)];
  const p = panel(20, records);
  p.historyIndex(records);
  const display = p.historyDisplayValues(
    "upper",
    "temperature",
    base + 10_000,
    base + 15_000,
    5_000,
    1,
  );
  assert.ok(!display.some((point) => point.value === 120));
  assert.ok(display.some((point) => point.value === 70));
}

// A projection change has an annotation output but leaves the model's curve
// keys and retained Canvas paths alone. The outer markup itself has no phase
// markup, so it need not be rebuilt for this change.
{
  const active = session(20);
  const p = panel(20, []);
  p.shown = {
    records: [],
    session: active,
    phase_projection: {
      intervals: [{ started_at: iso(0), ended_at: iso(10), phase: "saunagang" }],
    },
  };
  p.historyIndex([]);
  const chart = {
    prepared: new Map(),
    domain: [base, base + 20_000],
  };
  const curves = new HistoryCurves(new FakeCanvas(), {
    Path2DClass: FakePath2D,
    styles: curveStyles,
  });
  const markup = p.historyMarkup(active);
  const before = p.historyModel(chart, active);
  assert.equal(curves.update(before, geometry).mainDrawn, true);
  p.shown.phase_projection = {
    intervals: [{ started_at: iso(0), ended_at: iso(20), phase: "bereit" }],
  };
  const after = p.historyModel(chart, active);
  assert.equal(curves.update(after, geometry).mainDrawn, false);
  assert.equal(p.historyMarkup(active), markup);
  assert.match(p.historyAnnotations(after, active, []), /class="ready"/);
}

// A late record invalidates only its own prepared source; raw lookup retains
// order and duplicate timestamps.
{
  const records = [record(0, 70), record(10, 80), record(10, 81), record(20, 90)];
  const p = panel(30, records);
  p.historyIndex(records);
  p.historyDisplayValues("upper", "temperature", base, base + 30_000, 5_000, 100);
  records.push(record(5, 75));
  p.invalidateHistoryIndex();
  p.historyIndex(records);
  assert.equal(p.nearestMeasurement("upper", "temperature", base + 5_000).value, 75);
  assert.doesNotThrow(() =>
    p.historyDisplayValues("upper", "temperature", base, base + 30_000, 5_000, 100),
  );
}

// Real discontinuities are never capped. Command collection uses loops, so a
// missing-value stream cannot become a spread-argument overflow.
{
  const records = Array.from({ length: 150_000 }, (_, index) =>
    record(index / 10, index % 2 ? null : 70),
  );
  const p = panel(15_000, records);
  const chart = {
    prepared: new Map(),
    domain: [base, base + 15_000_000],
  };
  const curves = new HistoryCurves(new FakeCanvas(), {
    Path2DClass: FakePath2D,
    styles: curveStyles,
  });
  assert.doesNotThrow(() =>
    curves.update(modelFor(p, records, 15_000, chart), geometry),
  );
}

console.log("panel history display numeric regressions passed");
