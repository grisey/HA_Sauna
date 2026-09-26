const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
});

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

// A coarse display level keeps each extrema while returning work proportional
// to pixels. It must not silently cap a real discontinuity.
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
  assert.ok(
    display.some((point) => point.value === 120),
    "a short maximum remains visible",
  );
  assert.ok(display.length < 4_500, "dense values are prepared near plot resolution");
}

// A normal chronological addition keeps the prepared hierarchy. The advancing
// projection is deliberately absent from the outer panel key, so it redraws
// SVG geometry without reparsing the toolbar and legends.
{
  const records = [record(0, 70), record(10, 80)];
  const p = panel(30, records);
  p.historyIndex(records);
  p.historyDisplayValues("upper", "temperature", base, base + 30_000, 5_000, 100);
  const display = p.historyDisplay(p.series("upper", "temperature"));
  records.push(record(20, 90));
  p.invalidateHistoryIndex();
  p.historyIndex(records);
  assert.equal(p.historyDisplay(p.series("upper", "temperature")), display);
  assert.equal(display.values.at(-1).value, 90);
}

// Nulls and stale intervals descend to raw points, even when they are shorter
// than a pixel. The chart still splits the rendered path at both boundaries.
{
  const records = [
    record(0, 70),
    record(1, 71),
    record(2, null),
    record(3, 72),
    record(20, 73),
  ];
  const p = panel(30, records);
  const svg = p.chart(records, session(30), []);
  const path = /data-series="upper_temperature" d="([^"]*)"/.exec(svg)[1];
  assert.equal((path.match(/M/g) || []).length, 3, "nulls and TTL gaps stay split");
}

// A timeout inside a single coarse bucket is still a real curve break. This
// catches an easy regression where raw fallback accidentally marks every
// member as continuously displayable.
{
  const records = [record(0, 20), record(1, 21), record(12, 22)];
  const p = panel(32, records);
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

// Crossing a coarser power-of-two threshold combines cached buckets instead
// of re-reading the previous raw measurements.
{
  const records = [];
  for (let second = 0; second < 3_000; second++)
    records.push(record(second, 70, "upper", "temperature"));
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
  assert.equal(rawVisits, 0, "the next coarser level is merged from cached bins");
}

// A coarse boundary bucket may contain an old extreme outside the selected
// window. Only the exact neighbouring raw point is retained at that edge.
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
  assert.ok(
    display.some((point) => point.value === 70),
    "the exact left neighbour remains",
  );
}

// A changing open projection redraws the SVG but leaves outer history markup
// untouched, while a corrected projection continues to render its replacement
// phase without an artificial gap.
{
  const started = iso(0),
    middle = iso(10),
    ended = iso(20);
  const timeline = { session_id: "live", processed: [], completed: [] };
  const activeSession = { ...session(20), timeline };
  let plots = 0,
    svgUpdates = 0,
    hasSvg = false;
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: activeSession,
      records: [],
      phase_projection: {
        intervals: [{ started_at: started, ended_at: middle, phase: "saunagang" }],
      },
    },
    state: {
      now: middle,
      operation_enabled: true,
      configuration: activeSession.configuration,
    },
    selected: "live",
    positions: new Set(["upper"]),
    historyDetail: false,
    ensureHistoryWindow() {},
    renderHistoryOverview() {},
    updateHistoryTimelineRevision() {
      this.historyTimelineRevision = 1;
    },
    historyTitle: () => "Laufende Sitzung",
    chart: () => '<svg class="session-chart"></svg>',
    historyRecords: () => [],
    updateHistoryChart: () => {
      svgUpdates++;
    },
    updateMarkup(selector) {
      if (selector === "#plots") {
        plots++;
        hasSvg = true;
      }
    },
    $: (selector) => (selector === "svg.session-chart" && hasSvg ? {} : null),
    shadowRoot: { querySelectorAll: () => [] },
    historyEventKey: "1:0",
    historyGangKey: "1:",
  });
  p.drawHistory();
  p.shown.phase_projection = {
    intervals: [{ started_at: started, ended_at: ended, phase: "bereit" }],
  };
  p.state.now = ended;
  p.drawHistory();
  assert.equal(plots, 1);
  assert.equal(svgUpdates, 1);
  const rendered = Object.assign(Object.create(Panel.prototype), panel(20, []), {
    shown: { phase_projection: p.shown.phase_projection },
  }).chart([], activeSession, []);
  assert.match(rendered, /class="ready"/);
}

// An exceptional late point invalidates only its series display cache. Exact
// raw lookup remains ordered, including duplicate timestamps.
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

console.log("panel history display regressions passed");

// Real discontinuities are never capped. A large missing-value stream may
// therefore exceed the engine argument limit even after display aggregation.
{
  const records = Array.from({ length: 150_000 }, (_, i) =>
    record(i / 10, i % 2 ? null : 70),
  );
  const p = panel(15_000, records);
  assert.doesNotThrow(
    () => p.chart(records, session(15_000), []),
    "arbitrarily many real gaps do not become a spread-argument overflow",
  );
}
