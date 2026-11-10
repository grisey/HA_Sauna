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
      "#recognition-control",
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
  assert.equal(nodes["#recognition-control"].hidden, true);
  await panel.action("diagnostics");
  assert.equal(nodes["#recognition-control"].hidden, false, "B4b: control only under Erkennung");
  assert.equal(nodes["#detail-calibration"].hidden, true);
  await panel.action("detail");
  assert.equal(nodes["#detail-calibration"].hidden, true);
  assert.equal(nodes["#recognition-control"].hidden, true);
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
    /<section id="detail-calibration"[^>]*hidden><div class="card calibration-start"><h2>Kalibrierung<\/h2>[^]*?<div id="calibration"><\/div><\/div><div class="card calibration-result" id="calibration-grid"><\/div><\/section>/,
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
          temperature_spacing_seconds: 1.02,
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
      valid: false,
    },
  };
  const html = panel.renderPositionValues();
  assert.match(html, /^<section class="card detail-positions" aria-label="Messpositionen" data-position-values>/);
  assert.match(
    html,
    /data-position="upper"><p class="stat-key">Oben · Regelposition<\/p><p class="stat-value">85<small>°C<\/small><\/p><p class="stat-line">12 % · 41,2 g\/m³<\/p><p class="stat-line">gültig, veraltet · alle 1 s<\/p>/,
  );
  assert.match(
    html,
    /data-position="lower"><p class="stat-key">Unten<\/p><p class="stat-value">60<small>°C<\/small><\/p><p class="stat-line">– · –<\/p><p class="stat-line">gültig, fehlt<\/p>/,
  );
  assert.match(
    html,
    /data-position="difference"><p class="stat-key">Oben − unten<\/p><p class="stat-value">\+25<small>°C<\/small><\/p><p class="stat-line">–<\/p><\/div>/,
  );
  panel.state.position_values = { positions: [], difference: null, valid: false };
  assert.match(panel.renderPositionValues(), /Keine Messwerte verfügbar/);
});

test("the state view shows the positions once, without the former per-position lines", () => {
  const panel = fixturePanel(Panel);
  panel.state = {
    position_values: {
      positions: [{ position: "upper", temperature: 80, humidity: 10, absolute_humidity: 30, temperature_state: "current", humidity_state: "current" }],
      difference: null,
      valid: true,
    },
  };
  panel.renderEnvironmentDetails = () => "";
  panel.renderLiveEvidence = () => "";
  const html = panel.renderDetailView({});
  assert.equal(html.match(/data-position-values/g).length, 1);
  assert.doesNotMatch(html, /Obere Messposition|Untere Messposition|class="card hero"/);
  assert.doesNotMatch(html, /data-position="difference"/, "one position has no difference");
});
