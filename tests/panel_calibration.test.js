const {
  panelFixture: fixturePanel,
  parameterValues,
  catalog,
} = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, p) => (Panel = p) },
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
// Modules as the runtime offers them; P only with a presence sensor.
const modules = [
  { id: "P", label: "P", title: "Leere Kabine" },
  { id: "A1", label: "A1", title: "Austritt nach Aufguss" },
  { id: "CB", label: "C/B", title: "Kurze Öffnungen" },
];
function fixture({
  session = null,
  calibration = {},
  permissions = {},
  targets = 1,
} = {}) {
  const requests = [],
    nodes = { "#calibration": {} };
  const p = Object.assign(fixturePanel(Panel), {
    entry: "e",
    generation: 1,
    state: {
      permissions: { admin: true, control: true, ...permissions },
      session,
      calibration: { active: null, last: null, modules, ...calibration },
    },
    $: (key) => nodes[key],
    updateMarkup: (key, html) => (nodes[key].innerHTML = html),
    refresh: async () => requests.push({ refresh: true }),
    api: async (path, method, body) => {
      requests.push({ path, method, body });
      if (path === "/e/calibration" && method === undefined) return { targets };
      return {};
    },
  });
  return { p, requests, html: () => nodes["#calibration"].innerHTML };
}
const sent = (requests) =>
  requests
    .filter((item) => item.path)
    .map(({ path, method, body }) => ({
      path,
      method,
      body: body && JSON.parse(JSON.stringify(body)),
    }));

test("without a session all offered modules are preselected for a calibration session", async () => {
  const { p, requests, html } = fixture();
  p.drawCalibration();
  assert.match(html(), /Companion-Ziele werden geprüft/);
  assert.match(
    html(),
    /data-action="calibration-start" disabled>Kalibriersitzung starten/,
  );
  await p.action("calibration-targets");
  for (const module of modules)
    assert.match(
      html(),
      new RegExp(`data-calibration-module="${module.id}" checked >`),
    );
  assert.match(html(), /data-action="calibration-start" >Kalibriersitzung starten/);
  assert.match(html(), /Die Module folgen nach Bereit\./);
  p.selectCalibrationModule("P", false);
  assert.match(html(), /data-calibration-module="P"  >/);
  await p.action("calibration-start");
  assert.deepEqual(sent(requests), [
    { path: "/e/calibration", method: undefined, body: undefined },
    { path: "/e/calibration", method: "POST", body: { modules: ["A1", "CB"] } },
  ]);
  assert.ok(requests.some((item) => item.refresh));
});

test("without a presence sensor module P is not offered", async () => {
  const { p, html } = fixture({ calibration: { modules: modules.slice(1) } });
  await p.action("calibration-targets");
  assert.doesNotMatch(html(), /data-calibration-module="P"/);
  assert.match(html(), /data-calibration-module="A1" checked/);
});

test("in a running session one module starts once no gang is active", async () => {
  const { p, requests, html } = fixture({ session: { session_id: "s" } });
  await p.action("calibration-targets");
  assert.match(html(), /<select id="calibration-module" >/);
  assert.match(html(), />Modul starten</);
  assert.match(html(), /Das Modul startet, sobald kein Gang aktiv ist\./);
  assert.doesNotMatch(html(), /Kalibriersitzung starten/);
  p.calibrationModule = "CB";
  await p.action("calibration-start");
  assert.deepEqual(sent(requests).at(-1), {
    path: "/e/calibration",
    method: "POST",
    body: { module: "CB" },
  });
});

test("a running calibration shows module, step and what it waits for and can be cancelled", async () => {
  const run = {
    kind: "module",
    modules: ["A1"],
    module: "A1",
    step: 2,
    steps: 4,
    waiting: "reply",
  };
  const { p, requests, html } = fixture({
    session: { session_id: "s" },
    calibration: { active: run },
  });
  await p.action("calibration-targets");
  assert.match(
    html(),
    /Einzelnes Modul läuft:<\/strong> A1, Schritt 2 von 4, wartet auf Antwort/,
  );
  assert.match(html(), /data-action="calibration-cancel" >Kalibrierung abbrechen/);
  assert.match(html(), /Nach dem Abbruch ist der Lauf ungültig/);
  assert.match(html(), /Es läuft bereits eine Kalibrierung\./);
  assert.doesNotMatch(html(), /calibration-start/);
  await p.action("calibration-start");
  await p.action("calibration-cancel");
  assert.deepEqual(sent(requests).slice(1), [
    { path: "/e/calibration/cancel", method: "POST", body: {} },
  ]);
  for (const [waiting, text] of [
    ["ready", "Bereit"],
    ["gang_end", "Gangende"],
    ["hold", "Wartezeit"],
  ])
    assert.match(
      p.calibrationProgress({ ...run, waiting, step: waiting === "hold" ? 2 : null }),
      new RegExp(`wartet auf ${text}$`),
    );
});

test("the last result names validity, reason and the totals of the evaluation", async () => {
  const totals = {
    gangs: { reference: 1, found: 1, missing: 0, additional: 0 },
    events: {
      reference: 5,
      found: 4,
      outside: 1,
      missing: 0,
      additional: 2,
      merged: 0,
    },
    empty_cabin_reports: 0,
    excluded_steps: 0,
  };
  const last = { kind: "module", modules: ["A1"], valid: true, reason: "finished" };
  let { p, html } = fixture({ calibration: { last: { ...last, totals } } });
  p.drawCalibration();
  assert.match(html(), /Letztes Ergebnis:<\/strong> gültig/);
  assert.match(
    html(),
    /Gänge 1 von 1 gefunden, 0 zusätzlich\. Türen und Aufgüsse 4 von 5 im Fenster gefunden, 1 außerhalb des Fensters zugeordnet, 0 fehlen, 2 zusätzlich\./,
  );
  assert.match(html(), /Einzelheiten stehen im Archiv/);
  ({ p, html } = fixture({ calibration: { last: { ...last, totals: null } } }));
  p.drawCalibration();
  assert.match(html(), /folgt nach dem Sitzungsende/);
  ({ p, html } = fixture({
    calibration: {
      last: {
        ...last,
        valid: false,
        reason: "cancelled",
        reason_text: "abgebrochen",
        totals: null,
      },
    },
  }));
  p.drawCalibration();
  assert.match(html(), /Letztes Ergebnis:<\/strong> ungültig, abgebrochen/);
  assert.doesNotMatch(html(), /Türen und Aufgüsse/);
});

test("the start is locked with its reason", async () => {
  let { p, requests, html } = fixture({ targets: 0 });
  await p.action("calibration-targets");
  assert.match(
    html(),
    /Kein Ziel „Companion: Daten und Antworten“ für diesen Benutzer\./,
  );
  assert.match(html(), /data-action="calibration-start" disabled/);
  assert.match(html(), /Companion-Ziele erneut prüfen/);
  await p.action("calibration-start");
  assert.equal(sent(requests).length, 1);
  ({ p, requests, html } = fixture({ permissions: { control: false } }));
  await p.action("calibration-targets");
  p.drawCalibration();
  assert.match(html(), /Für die Kalibrierung fehlt die Steuerberechtigung\./);
  assert.match(html(), /data-action="calibration-start" disabled/);
  assert.equal(sent(requests).length, 0);
  ({ p, requests } = fixture({ permissions: { admin: false } }));
  await p.action("calibration-targets");
  await p.action("calibration-start");
  assert.equal(sent(requests).length, 0);
});

test("a rejected start shows the reason of the integration", async () => {
  const { p, html } = fixture();
  await p.action("calibration-targets");
  p.api = async () => {
    throw {
      status_code: 409,
      body: { error: "Kein Companion-Ziel mit Antworten für den startenden Benutzer" },
    };
  };
  await p.action("calibration-start");
  assert.match(html(), /Kein Companion-Ziel mit Antworten für den startenden Benutzer/);
});

test("the control view shows a short hint while a calibration runs", () => {
  const nodes = {};
  const state = {
    frontend_defaults: catalog.frontend,
    appearance_catalog: catalog.appearance,
    now: "2026-11-02T10:00:00Z",
    phase: "bereit",
    operation_enabled: true,
    session: {
      timeline: { active: null, door: "closed", completed: [] },
      heating: { elapsed_seconds: 0 },
      deadlines: [],
    },
    configuration: { parameters: parameterValues() },
    last_session: null,
    measurements: [],
    measurement_status: {},
    parameters: [],
    permissions: { control: true, temperature: true, program: true, light: true },
    start_errors: [],
    issues: [],
    detection_channels: [],
    target_temperature: 80,
    temperature_control: {
      scope: "current",
      value: 80,
      current_target: 80,
      next_target: 80,
    },
    manual_controls: { light: {} },
    start_availability: { until_ready_seconds: 0, message: "", blocker: null },
    phase_timer: null,
    calibration: {
      active: {
        kind: "module",
        module: "A1",
        step: 2,
        steps: 4,
        waiting: "reply",
      },
    },
  };
  const p = Object.assign(fixturePanel(Panel), {
    state,
    hass: { user: { name: "Test" } },
    $: (selector) =>
      ["#current", "#details", '.main-tabs [data-action="details"]'].includes(selector)
        ? (nodes[selector] ||= {})
        : null,
  });
  p.drawCurrent();
  assert.match(nodes["#current"].innerHTML, /Kalibrierung läuft: A1, Schritt 2 von 4</);
  state.calibration = { active: null };
  p.drawCurrent();
  assert.doesNotMatch(nodes["#current"].innerHTML, /Kalibrierung läuft/);
});
