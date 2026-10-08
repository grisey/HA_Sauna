"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const loadContext = (timeZone) => {
  const context = {
    HTMLElement: class {},
    customElements: {
      get: () => null,
      define: (_name, value) => (context.PanelForTest = value),
    },
    Intl: {
      DateTimeFormat: function (locale, options) {
        return new Intl.DateTimeFormat(locale, {
          ...options,
          timeZone: options?.timeZone || timeZone,
        });
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8") +
      ";globalThis.HistoryChartForTest = HistoryChart;",
    context,
  );
  return context;
};

test("history axes use the fixed local zone and reuse unchanged layers", () => {
  for (const [zone, clock] of [
    ["UTC", "10:10"],
    ["Europe/Berlin", "11:10"],
  ]) {
    const context = loadContext(zone);
    const nodes = new Map();
    const node = (key) => {
      if (!nodes.has(key))
        nodes.set(key, { textContent: "", innerHTML: "", setAttribute() {} });
      return nodes.get(key);
    };
    const start = Date.parse("2032-01-01T10:10:00Z"),
      end = Date.parse("2032-01-01T10:20:00Z");
    const model = {
      start,
      end,
      left: 0,
      right: 100,
      top: 0,
      bottom: 100,
      low: 20,
      high: 100,
      humidityHigh: 80,
      x: (time) => (time - start) / 6000,
    };
    let axes = 0,
      annotations = 0;
    const panel = {
      $: node,
      historyDetail: false,
      positions: new Set(["upper"]),
      historyTimelineRevision: 1,
      historyTitle: () => "Archiv",
      historyModel: () => model,
      historyAxes: (_model, geometry) => {
        axes++;
        return context.PanelForTest.prototype.historyAxes.call(panel, model, geometry);
      },
      historyRecords: () => [],
      historyAnnotations: () => `annotations-${++annotations}`,
      syncHistoryOverview() {},
      shown: { phase_projection: null },
      state: { now: "2032-01-01T00:00:00Z" },
    };
    const chart = Object.assign(Object.create(context.HistoryChartForTest.prototype), {
      panel,
      surface: { setAttribute() {} },
      interaction: {
        readGeometry: () => ({
          dpr: 1,
          canvas: { cssWidth: 1200, cssHeight: 480 },
          overviewCanvas: { cssWidth: 1200, cssHeight: 46 },
        }),
        invalidateGeometry() {},
      },
      curves: { update() {} },
    });
    const session = { ended_at: "2032-01-01T01:00:00Z" };
    chart.render(new Set(["status"]), session, []);
    assert.match(node("[data-history-axes]").innerHTML, new RegExp(clock));
    chart.render(new Set(["status"]), session, []);
    assert.equal(axes, 1);
    assert.equal(annotations, 1);
    assert.equal(node("[data-history-annotations]").innerHTML, "annotations-1");
    chart.interaction.readGeometry = () => ({
      dpr: 1,
      canvas: { cssWidth: 642, cssHeight: 420 },
      overviewCanvas: { cssWidth: 642, cssHeight: 46 },
    });
    chart.render(new Set(["size"]), session, []);
    assert.equal(axes, 2, "viewport resize redraws labels at their CSS pixel size");
    assert.equal(annotations, 1, "text sizing does not alter annotation coordinates");
  }
});

test("normal history never exposes height comparison controls or its note", () => {
  const context = loadContext("UTC"),
    session = {
      ended_at: "2032-01-01T10:20:00Z",
      timeline: { session_started_at: "2032-01-01T10:10:00Z" },
    },
    model = { start: 0, end: 1000, low: 20, high: 100, humidityHigh: 80 };
  const panel = Object.assign(Object.create(context.PanelForTest.prototype), {
    selected: "live",
    historyDetail: false,
    positions: new Set(["upper"]),
    state: { now: session.ended_at, session, operation_enabled: true },
    shown: { session, phase_projection: null },
    historyModel: () => model,
    historyAxes: () => "axes",
    historyRecords: () => [],
    historyAnnotations: () => "annotations",
    syncHistoryOverview() {},
  });
  const markup = panel.historyMarkup(session),
    nodes = new Map();
  // Only elements present in the productive markup can satisfy a selector.
  // A missing optional node must remain null, as it would in the browser.
  for (const match of markup.matchAll(/<([a-z][\w-]*)\b([^>]*)>/g)) {
    const attributes = new Map(
      [...match[2].matchAll(/([\w:-]+)(?:="([^"]*)")?/g)].map((attribute) => [
        attribute[1],
        attribute[2] || "",
      ]),
    );
    const node = {
      textContent: markup.slice(match.index + match[0].length).split("<")[0],
      innerHTML: "",
      hidden: attributes.has("hidden"),
      setAttribute: (name, value) => attributes.set(name, value),
    };
    const selectors = [];
    for (const [name, value] of attributes) {
      selectors.push(`[${name}]`, `[${name}="${value}"]`);
      if (name === "id") selectors.push(`#${value}`);
      if (name === "class")
        for (const token of value.split(/\s+/))
          selectors.push(`.${token}`, `${match[1]}.${token}`);
    }
    for (const selector of selectors)
      if (!nodes.has(selector)) nodes.set(selector, node);
  }
  panel.$ = (selector) => nodes.get(selector) || null;
  assert.equal(panel.$(".plot-note"), null);
  assert.equal(panel.$('[data-action="history-detail"]'), null);
  const chart = Object.assign(Object.create(context.HistoryChartForTest.prototype), {
    panel,
    surface: panel.$("svg.session-chart"),
    interaction: {
      readGeometry: () => ({
        dpr: 1,
        canvas: { cssWidth: 1200, cssHeight: 480 },
        overviewCanvas: { cssWidth: 1200, cssHeight: 46 },
      }),
      invalidateGeometry() {},
    },
    curves: { update() {} },
  });
  for (const detailed of [false, true, false]) {
    panel.historyDetail = detailed;
    assert.doesNotThrow(() => chart.render(new Set(["status"]), session, []));
    assert.equal(panel.historyDetail, false);
    assert.equal(panel.$("[data-history-positions]"), null);
  }
});

test("tables, diagnostics, annotations and session labels use each fixed local zone", () => {
  for (const [zone, clock] of [
    ["UTC", "10:10"],
    ["Europe/Berlin", "11:10"],
  ]) {
    const context = loadContext(zone);
    const at = "2032-01-01T10:10:00Z",
      later = "2032-01-01T10:20:00Z";
    const event = {
      event_id: "infusion",
      kind: "infusion",
      effective_at: at,
      detected_at: at,
    };
    const gang = {
      gang_id: "gang",
      started_at: at,
      detected_at: at,
      ended_at: later,
      infusion_events: [event],
    };
    const session = {
      ended_at: later,
      timeline: {
        session_id: "session",
        session_started_at: at,
        completed: [gang],
        active: null,
        processed: [event],
        retracted: [],
      },
      heating: { intervals: [{ started_at: at, ended_at: later }] },
    };
    const records = [
      {
        kind: "detector_trace",
        payload: {
          at,
          metrics: { upper: { infusion_humidity_delta: 3 } },
          signals: ["infusion"],
          conditions: {},
          holds: {},
        },
      },
    ];
    const nodes = new Map();
    const node = (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
      return nodes.get(selector);
    };
    const panel = Object.assign(Object.create(context.PanelForTest.prototype), {
      entry: "entry",
      selected: "session",
      sessions: [{ session_id: "session", started_at: at, ended_at: later }],
      state: {
        now: later,
        permissions: { admin: true },
        configuration: { parameters: {} },
      },
      shown: { session, records },
      historyChart: { identity: "entry:session", render() {} },
      historyDetail: true,
      historyTimelineRevision: 1,
      historyDatasetRevision: 1,
      historyWindowRevision: 1,
      window: [Date.parse(at), Date.parse(later)],
      isConnected: true,
      view: "history",
      $: node,
      shadowRoot: { querySelectorAll: () => [] },
      ensureHistoryWindow() {},
      updateHistoryTimelineRevision() {},
      revealEventTarget() {},
    });
    // Build the complete productive record index, including trace/event links.
    panel.historyIndex(records);
    panel.renderHistory(new Set(["status"]));
    panel.syncHistorySessions();
    for (const selector of ["#gangs", "#event-list", "#session"])
      assert.match(node(selector).innerHTML, new RegExp(clock));
    const model = {
      start: Date.parse(at),
      end: Date.parse(later),
      left: 0,
      right: 100,
      top: 0,
      bottom: 100,
      x: (time) => (time - Date.parse(at)) / 6000,
    };
    assert.match(panel.historyAnnotations(model, session, [gang]), new RegExp(clock));
    node("#plots").hidden = true;
    panel.view = "diagnostics";
    panel.renderHistory(new Set(["status"]));
    assert.match(node("#detection-plots").innerHTML, new RegExp(clock));
  }
});

test("history axis labels cancel unequal SVG scaling and reduce narrow time ticks", () => {
  const { PanelForTest } = loadContext("UTC");
  const start = Date.parse("2032-01-01T10:00:00Z"),
    end = start + 3600000;
  const model = {
    start,
    end,
    left: 65,
    right: 1135,
    top: 18,
    bottom: 435,
    low: 20,
    high: 100,
    humidityHigh: 80,
    x: (time) => 65 + ((time - start) / (end - start)) * 1070,
  };
  const axes = (width, height) =>
    PanelForTest.prototype.historyAxes.call({}, model, { width, height });
  for (const [width, height] of [
    [1200, 480],
    [642, 420],
    [300, 420],
  ]) {
    const html = axes(width, height);
    for (const match of html.matchAll(/scale\(([^ ]+) ([^)]+)\)/g)) {
      assert.ok(Math.abs((Number(match[1]) * width) / 1200 - 1) < 1e-12);
      assert.ok(Math.abs((Number(match[2]) * height) / 480 - 1) < 1e-12);
    }
    assert.match(html, />10:00<\/text>/);
    assert.match(html, />11:00<\/text>/);
  }
  const times = (html) => [...html.matchAll(/>\d{2}:\d{2}<\/text>/g)].length;
  assert.ok(times(axes(300, 420)) < times(axes(1200, 480)));
});
