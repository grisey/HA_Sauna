// Runs without Home Assistant or a browser. The retained renderer receives a
// prepared historyModel; FakeCanvas is functional coverage, not a timing claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const appearanceCatalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/appearance_catalog.json", "utf8"),
);
const color = (id) =>
  appearanceCatalog.colors.find((item) => item.id === id).default.toLowerCase();
const curveStyles = {
  "upper:temperature": { stroke: color("series_temperature") },
  "lower:temperature": { stroke: color("series_temperature") },
  "upper:humidity": { stroke: color("series_humidity") },
  "lower:humidity": { stroke: color("series_humidity") },
  overview: { stroke: color("series_overview"), track: color("chart_minimap_track") },
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
  constructor() {
    this.calls = [];
  }
  setTransform(...values) {
    this.calls.push(["transform", ...values]);
  }
  clearRect(...values) {
    this.calls.push(["clear", ...values]);
  }
  save() {}
  restore() {}
  beginPath() {}
  rect() {}
  clip() {}
  roundRect() {}
  fill() {}
  setLineDash(values) {
    this.calls.push(["dash", ...values]);
  }
  stroke(path) {
    this.calls.push(["stroke", path]);
  }
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
const panelSource = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(
  `${panelSource}\nglobalThis.chartExports = { HistoryCurves, monotoneHistoryCommands };`,
  sandbox,
);
const { HistoryCurves } = sandbox.chartExports;

assert.ok(panelSource.includes("`/${entry}/temperature`"));
assert.ok(panelSource.includes("`/${entry}/program`"));
assert.match(panelSource, /data-target-arc[\s\S]*role="slider"/);
assert.match(panelSource, /data-action="light:false"/);
assert.match(panelSource, /permissions\.temperature/);
assert.doesNotMatch(panelSource, /changeTarget\(Number\(action\.slice\(7\)\),true\)/);

const base = 1_700_000_000_000;
const iso = (second) => new Date(base + second * 1000).toISOString();
const record = (second, value, quantity = "temperature", position = "upper") => ({
  kind: "measurement",
  payload: { position, quantity, value, received_at: iso(second) },
});
const session = (seconds = 30) => ({
  ended_at: iso(seconds),
  configuration: { parameters: { sensor_timeout_seconds: 5 } },
  timeline: { session_started_at: iso(0), processed: [], completed: [] },
  heating: { intervals: [] },
});
const panel = (seconds, records, positions = ["upper"]) =>
  Object.assign(Object.create(Panel.prototype), {
    positions: new Set(positions),
    window: [base, base + seconds * 1000],
    state: {
      now: iso(seconds),
      configuration: { parameters: { sensor_timeout_seconds: 5 } },
    },
    shown: { records, session: session(seconds) },
  });
const modelFor = (p, records, seconds, domain = [base, base + seconds * 1000]) => {
  p.historyIndex(records);
  return p.historyModel(
    { prepared: new Map(), preparedOverview: new Map(), domain },
    session(seconds),
  );
};
const draw = (model) => {
  const main = new FakeCanvas(),
    overview = new FakeCanvas();
  const curves = new HistoryCurves(main, overview, {
    Path2DClass: FakePath2D,
    styles: curveStyles,
  });
  curves.update(model, {
    width: 1200,
    height: 480,
    dpr: 1,
    overview: { width: 1200, height: 46, dpr: 1 },
  });
  return { curves, main, overview };
};

// Dense readings collapse by pixels but retain one continuous numeric path.
{
  const records = [];
  for (let second = 0; second <= 10_800; second++)
    records.push(record(second, 70 + second / 10_800));
  const p = panel(10_800, records);
  const { curves } = draw(modelFor(p, records, 10_800));
  assert.equal(
    curves.mainPaths
      .get("upper:temperature")
      .path.commands.filter(([kind]) => kind === "M").length,
    1,
    "pixel decimation must not create TTL gaps",
  );
}

// A raw TTL interval and a raw null are independent Canvas subpaths.
{
  const records = [record(0, 70), record(1, 71), record(20, 72), record(21, 73)];
  const p = panel(30, records);
  assert.equal(
    draw(modelFor(p, records, 30))
      .curves.mainPaths.get("upper:temperature")
      .path.commands.filter(([kind]) => kind === "M").length,
    2,
  );
  const missing = [record(0, 70), record(1, null), record(2, 71)];
  const missingPanel = panel(30, missing);
  assert.equal(
    draw(modelFor(missingPanel, missing, 30))
      .curves.mainPaths.get("upper:temperature")
      .path.commands.filter(([kind]) => kind === "M").length,
    2,
  );
}

// Raw indexed lookup remains the source for hover and includes boundaries.
{
  const records = [record(20, 72), record(0, 70), record(10, 71)];
  const p = panel(30, records);
  p.historyIndex(records);
  assert.equal(p.nearestMeasurement("upper", "temperature", base).value, 70);
  assert.equal(p.nearestMeasurement("upper", "temperature", base + 20_000).value, 72);
  assert.equal(p.nearestMeasurement("upper", "temperature", base + 16_000).value, 72);
  const stalePanel = panel(30, [record(0, 70)]);
  stalePanel.historyIndex(stalePanel.shown.records);
  const stale = stalePanel.nearestMeasurement("upper", "temperature", base + 15_000);
  assert.ok(
    Math.abs(stale.time - (base + 15_000)) > 5_000,
    "tooltip callers can reject a raw value outside its TTL",
  );
}

// Archive pages extend one numeric index and do not replace its raw series.
{
  const records = [record(0, 70)],
    p = panel(30, records);
  p.historyIndex(records);
  const index = p.chartDataIndex;
  records.push(record(2, 71));
  p.invalidateHistoryIndex();
  p.historyIndex(records);
  assert.equal(p.chartDataIndex, index);
  assert.equal(p.series("upper", "temperature").length, 2);
}

// A long ordinary series prepares and draws both the main path and minimap
// without an argument-spread reduction or a full SVG string.
{
  const records = [];
  for (let index = 0; index < 200_000; index++)
    records.push(record(index / 10, 60 + (index % 20)));
  const p = panel(20_000, records);
  assert.doesNotThrow(() => {
    const rendered = draw(modelFor(p, records, 20_000));
    assert.ok(rendered.curves.overviewPath);
  });
}

// The model computes useful humidity bounds without using SVG axis strings.
{
  const records = [record(1, 80), record(1, 55, "humidity")],
    p = panel(30, records);
  assert.ok(modelFor(p, records, 30).humidityHigh >= 60);
}

// Edge neighbours survive preparation and become a cubic Canvas segment.
{
  const records = [record(0, 70), record(4, 71)],
    p = panel(10, records);
  p.window = [base + 2_000, base + 10_000];
  assert.ok(
    draw(modelFor(p, records, 10))
      .curves.mainPaths.get("upper:temperature")
      .path.commands.some(([kind]) => kind === "C"),
  );
}

// A retained renderer and paths survive a non-data update; a changed visible
// source key repaints the same canvas instance exactly once.
{
  const records = [record(0, 70), record(1, 71)],
    p = panel(30, records);
  const chart = {
    prepared: new Map(),
    preparedOverview: new Map(),
    domain: [base, base + 30_000],
  };
  p.historyIndex(records);
  const first = p.historyModel(chart, session(30));
  const main = new FakeCanvas(),
    overview = new FakeCanvas();
  const curves = new HistoryCurves(main, overview, {
    Path2DClass: FakePath2D,
    styles: curveStyles,
  });
  const geometry = {
    width: 1200,
    height: 480,
    dpr: 1,
    overview: { width: 1200, height: 46, dpr: 1 },
  };
  assert.equal(curves.update(first, geometry).mainDrawn, true);
  const retainedPath = curves.mainPaths.get("upper:temperature").path;
  assert.equal(
    curves.update(p.historyModel(chart, session(30)), geometry).mainDrawn,
    false,
  );
  assert.equal(curves.mainPaths.get("upper:temperature").path, retainedPath);
  records.push(record(2, 72));
  p.invalidateHistoryIndex();
  p.historyIndex(records);
  assert.equal(
    curves.update(p.historyModel(chart, session(30)), geometry).mainDrawn,
    true,
  );
  assert.equal(main.width, 1200);
}

console.log("panel chart numeric regressions passed");
