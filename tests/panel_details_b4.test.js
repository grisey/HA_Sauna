"use strict";
// B4a Details: Unteransichten Zustand · Erkennung · Steuerung · Kalibrierung,
// Messpositionen mit Differenz (D5), Kalibrierung an einer Stelle (G12), S3.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
let Panel;
vm.runInNewContext(source, {
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
  clearTimeout,
  setTimeout,
});

const navigationPanel = (admin = true) => {
  const nodes = Object.fromEntries(
    [
      "#current",
      "#details",
      "#detail-calibration",
      "#history",
      "#statistics",
      "#settings",
      "#plots",
      "#detection-plots",
      "#gangs",
      "#event-list",
      ".detail-tabs",
    ].map((key) => [key, { hidden: false }]),
  );
  const button = (action) => ({
    dataset: { action },
    setAttribute(name, value) {
      this[name] = value;
    },
  });
  const main = ["overview", "history", "statistics", "details", "settings"].map(button);
  const detail = ["detail", "diagnostics", "detail-history", "detail-calibration"].map(
    button,
  );
  nodes['.main-tabs [data-action="details"]'] = main[3];
  const calls = [];
  const panel = Object.assign(fixturePanel(Panel), {
    state: { permissions: { admin } },
    message: () => {},
    refresh: async () => calls.push("refresh"),
    drawHistory: () => {},
    drawSettings: () => {},
    drawCalibration: () => calls.push("calibration"),
    loadStatistics: async () => {},
    cancelHistoryFrame: () => {},
    $: (selector) => nodes[selector] || null,
    shadowRoot: {
      querySelectorAll: (selector) =>
        selector === ".main-tabs button"
          ? main
          : selector === ".detail-tabs button"
            ? detail
            : [],
    },
  });
  return { panel, nodes, detail, calls };
};

test("detail tabs read Zustand, Erkennung, Steuerung, Kalibrierung", () => {
  const nav = source.match(/<nav class="tabs detail-tabs"[^]*?<\/nav>/)[0];
  const tabs = [...nav.matchAll(/data-action="([^"]+)"[^>]*>([^<]+)</g)].map(
    ([, action, label]) => [action, label],
  );
  assert.deepEqual(tabs, [
    ["detail", "Zustand"],
    ["diagnostics", "Erkennung"],
    ["detail-history", "Steuerung"],
    ["detail-calibration", "Kalibrierung"],
  ]);
});

test("the calibration subview shows only its own section and draws the calibration", async () => {
  const { panel, nodes, detail, calls } = navigationPanel();
  await panel.action("detail-calibration");
  assert.equal(panel.view, "detail-calibration");
  assert.equal(nodes["#detail-calibration"].hidden, false);
  for (const selector of ["#current", "#details", "#history", "#settings", "#statistics"])
    assert.equal(nodes[selector].hidden, true, selector);
  assert.equal(nodes[".detail-tabs"].hidden, false);
  assert.equal(
    detail.find((button) => button.dataset.action === "detail-calibration")["aria-current"],
    "page",
  );
  assert.deepEqual(calls, ["calibration"]);
  await panel.action("detail");
  assert.equal(nodes["#detail-calibration"].hidden, true);
  await panel.action("details");
  assert.equal(panel.view, "detail", "returning to details keeps the last subview");
});

test("the calibration subview is admin only", async () => {
  const { panel, nodes, calls } = navigationPanel(false);
  await panel.action("detail-calibration");
  assert.notEqual(panel.view, "detail-calibration");
  assert.deepEqual(calls, []);
});

test("calibration lives in Details only, not in the maintenance settings", () => {
  const maintenance = source.match(/maintenance: `[^`]*`/)[0];
  assert.doesNotMatch(maintenance, /id="calibration"/);
  assert.equal(source.match(/<div id="calibration"><\/div>/g).length, 1);
  assert.match(
    source,
    /<section id="detail-calibration"[^>]*hidden><div class="card"><h2>Kalibrierung<\/h2>[^]*?<div id="calibration"><\/div><\/div><div class="card" id="calibration-grid"><\/div><\/section>/,
  );
});

test("S3: start by button or switch comes before the programme library", () => {
  const programs = source.match(/programs: `[^`]*`/)[0];
  assert.ok(
    programs.indexOf("Start über Taster oder Betriebsschalter") <
      programs.indexOf("<h2>Programme</h2>"),
  );
});

test("the module grid lists each step with its expected recognitions", () => {
  const panel = fixturePanel(Panel);
  const modules = [
    {
      id: "A1",
      label: "A1",
      title: "Austritt nach Aufguss",
      steps: [
        { key: "a1", reference: "Aufguss", expects: ["infusion"] },
        { key: "a2", reference: "Austritt", expects: ["door_open", "door_close"] },
        { key: "a3", reference: "Warten", expects: [] },
      ],
    },
  ];
  const idle = panel.calibrationGridMarkup(modules, null);
  assert.match(idle, /<h2>Module und Schritte<\/h2>/);
  assert.match(idle, /<h3>A1 · Austritt nach Aufguss<\/h3>/);
  assert.match(idle, /<strong>Aufguss<\/strong><span>Aufguss<\/span>/);
  assert.match(idle, /<strong>Austritt<\/strong><span>Tür auf, zu<\/span>/);
  assert.match(idle, /<strong>Warten<\/strong><span>keine Erkennung erwartet<\/span>/);
  assert.doesNotMatch(idle, /aria-current/);
  const running = panel.calibrationGridMarkup(modules, { module: "A1", step: 2 });
  assert.equal(running.match(/aria-current="step"/g).length, 1);
  assert.match(running, /aria-current="step"><strong>Austritt</);
  assert.equal(panel.calibrationGridMarkup([], null), "");
});

test("the measurement card shows values per position, validity and the backend difference", () => {
  const panel = fixturePanel(Panel);
  panel.state = {
    regulation_temperature_position: "upper",
    position_values: {
      positions: [
        {
          position: "upper",
          temperature: 85.04,
          humidity: 12,
          absolute_humidity: 41.2,
          temperature_state: "current",
          humidity_state: "stale",
        },
        {
          position: "lower",
          temperature: 60,
          humidity: null,
          absolute_humidity: null,
          temperature_state: "current",
          humidity_state: "unavailable",
        },
      ],
      difference: { temperature: 25.04, absolute_humidity: null },
    },
  };
  const html = panel.renderPositionValues();
  assert.match(html, /^<dl data-position-values>/);
  assert.match(html, /<dt>Oben · Regelposition<\/dt><dd>85 °C · 12 % · 41,2 g\/m³ <span class="muted">gültig, veraltet<\/span>/);
  assert.match(html, /<dt>Unten<\/dt><dd>60 °C · – · – <span class="muted">gültig, fehlt<\/span>/);
  assert.match(html, /<dt>Oben − unten<\/dt><dd>\+25 °C · –<\/dd>/);
  panel.state.position_values = { positions: [], difference: null };
  assert.equal(panel.renderPositionValues(), "");
});

test("the measurement card shows the positions once, without the former per-position lines", () => {
  const panel = fixturePanel(Panel);
  panel.state = {
    position_values: {
      positions: [{ position: "upper", temperature: 80, humidity: 10, absolute_humidity: 30, temperature_state: "current", humidity_state: "current" }],
      difference: null,
    },
  };
  panel.renderEnvironmentDetails = () => "";
  panel.renderLiveEvidence = () => "";
  const html = panel.renderDetailView({ measurements: "<h3>Obere Messposition</h3>", doorText: "Tür geschlossen", detectionText: "", stateLine: "", timers: "", presence: "" });
  assert.match(html, /<h2>Messung<\/h2><p data-door-status>Tür geschlossen<\/p><dl data-position-values>/);
  assert.doesNotMatch(html, /Obere Messposition|Messpositionen/);
  panel.state.position_values = { positions: [], difference: null };
  assert.match(panel.renderDetailView({ measurements: "<h3>Obere Messposition</h3>", doorText: "", detectionText: "", stateLine: "", timers: "", presence: "" }), /<h3>Obere Messposition<\/h3>/);
});
