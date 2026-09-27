"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let zone = "UTC";
const context = {
  HTMLElement: class {},
  customElements: { get: () => null, define() {} },
  Intl: { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: zone }) }) },
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8") +
    ";globalThis.HistoryChartForTest = HistoryChart;",
  context,
);

test("retained history axes refresh when the local zone changes", () => {
  const nodes = new Map();
  const node = (key) => {
    if (!nodes.has(key)) nodes.set(key, { textContent: "", innerHTML: "", setAttribute() {} });
    return nodes.get(key);
  };
  const model = { start: 1, end: 2, low: 20, high: 100, humidityHigh: 80 };
  let axes = 0;
  const panel = {
    $: node,
    historyDetail: false,
    positions: new Set(["upper"]),
    historyTimelineRevision: 1,
    historyTitle: () => "Archiv",
    historyModel: () => model,
    historyAxes: () => `zone-${++axes}`,
    historyRecords: () => [],
    historyAnnotations: () => "",
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
  assert.equal(node("[data-history-axes]").innerHTML, "zone-1");
  chart.render(new Set(["status"]), session, []);
  assert.equal(axes, 1);
  zone = "Europe/Berlin";
  chart.render(new Set(["status"]), session, []);
  assert.equal(node("[data-history-axes]").innerHTML, "zone-2");
});
