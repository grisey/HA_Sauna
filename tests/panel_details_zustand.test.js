"use strict";
// B4c Details · Zustand nach Brett: Statuszeile, Messpositionen mit K-Z2,
// Geräte und Rückmeldungen, Archiv und Start, Fehler, Rohdaten erst beim Öffnen.
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

const state = () => ({
  now: "2026-11-02T20:30:00Z",
  phase: "bereit",
  archive_revision: 3,
  permissions: { admin: true },
  regulation_temperature_position: "upper",
  detection_channels: ["upper", "lower"],
  heating_feedback: true,
  heating_observation: { source: "contactor", power_w: 6120 },
  energy_kwh: 9.24,
  configuration_model: {
    presence: { direct: true },
    power_sensor_configured: false,
    measurement_positions: ["upper", "lower"],
  },
  manual_controls: {
    heater: {},
    light: { automatic: 30, observation: { available: true, brightness_percent: 30 } },
  },
  presence: {
    current: {
      available: true,
      occupancy: "absent",
      assertion: "direct_presence",
      effective_at: "2026-11-02T19:55:46Z",
    },
  },
  session: {
    timeline: { session_id: "s1", door: "closed" },
    heating: { elapsed_seconds: 5400 },
  },
  issues: [],
  position_values: {
    positions: [
      {
        position: "upper",
        temperature: 91.4,
        humidity: 6.4,
        absolute_humidity: 28.2,
        temperature_state: "current",
        humidity_state: "current",
        temperature_spacing_seconds: 1,
      },
      {
        position: "lower",
        temperature: 79.7,
        humidity: 9.6,
        absolute_humidity: 27.5,
        temperature_state: "current",
        humidity_state: "current",
        temperature_spacing_seconds: 1,
      },
    ],
    difference: { temperature: 11.7, absolute_humidity: 0.7 },
    valid: true,
  },
});

const panel = (overrides = {}) =>
  Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    view: "detail",
    state: state(),
    api: async () => {
      throw new Error("not loaded in this test");
    },
    ...overrides,
  });

test("the status line names validity, faults and the time of the state", () => {
  const p = panel();
  assert.match(
    p.detailStatusMarkup(),
    /data-detail-status="ok"><i aria-hidden="true"><\/i><span>Alles gültig · keine Fehler · Stand /,
  );
  p.state.issues = [{ message: "Obere Temperatur: Der letzte Messwert ist veraltet." }];
  p.state.archive_error = "disk full";
  p.state.position_values.valid = false;
  assert.match(
    p.detailStatusMarkup(),
    /data-detail-status="warning">[^]*Nicht alle Messwerte gültig · 2 Fehler/,
  );
  const faults = p.detailFaultsMarkup();
  assert.match(faults, /<h2>Fehler<\/h2><ul><li>Obere Temperatur: Der letzte Messwert ist veraltet\.<\/li><li>Archiv: disk full<\/li><\/ul>/);
  p.state.issues = [];
  p.state.archive_error = null;
  assert.equal(p.detailFaultsMarkup(), "");
});

test("positions show the backend means of the same session only", () => {
  const p = panel();
  let html = p.renderPositionValues();
  assert.match(html, /Regelung und Verlauf nutzen oben; die Erkennung beide/);
  assert.match(html, /data-position="difference"><p class="stat-key">Oben − unten<\/p><p class="stat-value">\+11,7<small>°C<\/small><\/p><p class="stat-line">\+0,7 g\/m³<\/p><\/div>/);
  p.detailOverview = {
    entry: "entry-1",
    result: { position_difference: { session_id: "s1", session_c: 10.84, ready_c: 11.66 } },
  };
  html = p.renderPositionValues();
  assert.match(html, /<p class="stat-line">im Mittel der Sitzung \+10,8 °C, ab Bereit \+11,7 °C<\/p>/);
  p.detailOverview.result.position_difference.session_id = "older";
  assert.doesNotMatch(p.renderPositionValues(), /im Mittel/);
  p.state.session = null;
  assert.match(p.renderPositionValues(), /die Erkennung ruht außerhalb einer Sitzung/);
});

test("devices name the feedback source, the light mode and power only with a sensor (W5)", () => {
  const p = panel();
  let html = p.detailDevicesMarkup({
    timerRows: [
      ["Heizsumme (gezählt)", "90 Minuten"],
      ["Aufgussbestätigung", "2 Minuten"],
    ],
  });
  assert.match(html, /<dt>Ofen<\/dt><dd>Ein · laut Schaltausgang<\/dd>/);
  assert.match(html, /<dt>Heizzeit<\/dt><dd>gezählt 90 Minuten, 9,2 kWh · aus dem Schaltausgang, ohne Leistungsmessung<\/dd>/);
  assert.doesNotMatch(html, /Leistung<\/dt>|6\.120 W/);
  assert.match(html, /<dt>Licht<\/dt><dd>30 % · Automatik, Phase Bereit<\/dd>/);
  assert.match(html, /<dt>Präsenzsensor<\/dt><dd>abwesend seit [0-9:]+ · verfügbar<\/dd>/);
  assert.match(html, /<dt>Tür<\/dt><dd data-door-status>geschlossen \(erkannt\)<\/dd>/);
  assert.match(html, /<dt>Aufgussbestätigung<\/dt><dd>2 Minuten<\/dd>/);
  assert.doesNotMatch(html, /Heizsumme/);
  p.state.configuration_model.power_sensor_configured = true;
  p.state.manual_controls.light.manual = 60;
  html = p.detailDevicesMarkup();
  assert.match(html, /<dt>Leistung<\/dt><dd>6\.120 W gemessen<\/dd>/);
  assert.match(html, /mit Leistungsmessung/);
  assert.match(html, /<dt>Licht<\/dt><dd>30 % · von Hand 60 %, Automatik 30 %<\/dd>/);
});

test("archive and start come from the backend; loading and errors are named", () => {
  const p = panel();
  assert.match(p.detailArchiveMarkup(), /<dt>Archiv<\/dt><dd>wird geladen …<\/dd><dt>Fehler<\/dt><dd>keine<\/dd>/);
  p.detailOverview = {
    entry: "entry-1",
    result: { sessions: 15, records: 861495, size_bytes: 1.1e9, start_seconds: 0.48 },
  };
  assert.match(
    p.detailArchiveMarkup(),
    /<dt>Sitzungen<\/dt><dd>15 im Archiv<\/dd><dt>Datensätze<\/dt><dd>861\.495 · 1,1 GB<\/dd><dt>Start des Archivs<\/dt><dd>0,5 s<\/dd>/,
  );
  p.detailOverview.result.start_seconds = null;
  assert.match(p.detailArchiveMarkup(), /Start des Archivs<\/dt><dd>nicht gemessen/);
  p.detailOverview = { entry: "other", result: { sessions: 1 } };
  assert.match(p.detailArchiveMarkup(), /wird geladen/, "another instance's overview is not shown");
});

test("raw data stay closed and render their fields only after opening", async () => {
  const p = panel();
  let drawn = 0;
  p.drawCurrent = () => drawn++;
  p.message = () => {};
  const closed = p.renderLiveEvidence();
  assert.deepEqual(
    [...closed.matchAll(/<div class="detail-raw-row"><span>([^<]+)</g)].map((m) => m[1]),
    [
      "Entscheidung und Regelbedingungen",
      "Messwerte und Messstatus",
      "Präsenz",
      "Geräteausgabe und Rückmeldungen",
      "Heizung, Energie und Ofenkühlung",
    ],
  );
  assert.doesNotMatch(closed, /class="detail-data"|detail-raw-body/);
  await p.action("detail-raw:presence");
  assert.equal(drawn, 1);
  const open = p.renderLiveEvidence();
  assert.match(open, /data-action="detail-raw:presence" aria-expanded="true" aria-controls="detail-presence">schließen<\/button><\/div><div class="detail-raw-body" id="detail-presence">/);
  assert.equal(open.match(/detail-raw-body/g).length, 1);
  await p.action("detail-raw:presence");
  assert.doesNotMatch(p.renderLiveEvidence(), /detail-raw-body/);
  p.state.permissions = { admin: false };
  assert.equal(p.renderLiveEvidence(), "");
});

test("the overview loads once per cadence, only in the state view and for administrators", async () => {
  const calls = [];
  const p = panel({
    api: async (path) => {
      calls.push(path);
      return { sessions: 2, records: 10, size_bytes: 1000, start_seconds: 0.1, position_difference: null };
    },
  });
  p.drawCurrent = () => {};
  await p.loadDetailOverview();
  await p.loadDetailOverview();
  assert.deepEqual(calls, ["/entry-1/archive/overview"]);
  assert.equal(p.detailOverview.result.sessions, 2);
  p.state.archive_revision = 4;
  await p.loadDetailOverview();
  assert.equal(calls.length, 2, "a new archive revision reloads");
  p.view = "history";
  p.state.archive_revision = 5;
  await p.loadDetailOverview();
  p.view = "detail";
  p.state.permissions = { admin: false };
  await p.loadDetailOverview();
  assert.equal(calls.length, 2);
});

test("the state view follows the board order and keeps no hero, lamp or presence card", () => {
  const p = panel();
  p.loadDetailOverview = async () => {};
  const html = p.renderDetailView({});
  const order = [
    "detail-status",
    'aria-label="Messpositionen"',
    'aria-label="Geräte und Rückmeldungen"',
    'aria-label="Archiv und Start"',
    'aria-label="Rohdaten"',
  ].map((needle) => html.indexOf(needle));
  assert.ok(order.every((index, i) => index >= 0 && (i === 0 || index > order[i - 1])), order.join());
  assert.doesNotMatch(html, /class="card hero"|<h2>Ofen<\/h2>|<h2>Licht<\/h2>|Präsenz und Regelursache|Aktuelle Daten im Detail/);
});
