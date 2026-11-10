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
// Additional modules and the parts of the basic procedure (W-3) as the runtime
// offers them; P, P1 and P2 only with a presence sensor.
const modules = [
  { id: "P", label: "P", title: "Leere Kabine" },
  { id: "A1", label: "A1", title: "Austritt nach Aufguss" },
  { id: "CB", label: "C/B", title: "Kurze Öffnungen" },
];
const procedure = [
  { id: "P1", label: "P1", title: "Leere Kabine beim Aufheizen" },
  { id: "G1", label: "Gang 1", title: "nach Bereit" },
  { id: "P2", label: "P2", title: "Leere Kabine nach Gang 1" },
  { id: "G2", label: "Gang 2", title: "nach der Pause" },
  { id: "G3", label: "Gang 3", title: "freiwillig", optional: true },
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
      calibration: {
        active: null,
        last: null,
        procedure,
        modules,
        blocked: null,
        ...calibration,
      },
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

test("without a session the basic procedure starts with its parts (W-3)", async () => {
  const { p, requests, html } = fixture();
  p.drawCalibration();
  assert.match(html(), /Companion-Ziele werden geprüft/);
  assert.match(html(), /data-action="calibration-start" disabled>Kalibrierung starten/);
  await p.action("calibration-targets");
  assert.match(
    html(),
    /<ol class="calibration-parts" aria-label="Grundablauf"><li><strong>P1<\/strong><span>Leere Kabine beim Aufheizen<\/span><\/li><li><strong>Gang 1<\/strong><span>nach Bereit<\/span><\/li>/,
  );
  assert.match(html(), /<strong>Gang 3<\/strong><span>freiwillig<\/span>/);
  assert.doesNotMatch(html(), /type="checkbox"|Kalibriersitzung/);
  assert.match(html(), /data-action="calibration-start" >Kalibrierung starten/);
  assert.match(html(), /eine Unterbrechung macht nur den laufenden Teil ungültig\./);
  await p.action("calibration-start");
  assert.deepEqual(sent(requests), [
    { path: "/e/calibration", method: undefined, body: undefined },
    { path: "/e/calibration", method: "POST", body: {} },
  ]);
  assert.ok(requests.some((item) => item.refresh));
});

test("without a presence sensor P, P1 and P2 are not offered", async () => {
  const { p, html } = fixture({
    session: { session_id: "s" },
    calibration: {
      modules: modules.slice(1),
      procedure: procedure.filter((part) => part.id.startsWith("G")),
    },
  });
  await p.action("calibration-targets");
  assert.doesNotMatch(html(), /value="P"/);
  assert.match(
    html(),
    /<option value="A1" selected>A1 – Austritt nach Aufguss<\/option>/,
  );
  const idle = fixture({ calibration: { procedure: procedure.slice(1, 2) } });
  await idle.p.action("calibration-targets");
  assert.doesNotMatch(idle.html(), /<strong>P1<\/strong>/);
});

test("in a running session one additional module starts once no gang is active", async () => {
  const { p, requests, html } = fixture({ session: { session_id: "s" } });
  await p.action("calibration-targets");
  assert.match(
    html(),
    /<label for="calibration-module">Zusatzmodul<\/label><select id="calibration-module" >/,
  );
  assert.match(html(), />Zusatzmodul starten</);
  assert.match(html(), /Das Zusatzmodul startet, sobald kein Gang aktiv ist\./);
  assert.doesNotMatch(html(), /Kalibrierung starten|calibration-parts/);
  p.calibrationModule = "CB";
  await p.action("calibration-start");
  assert.deepEqual(sent(requests).at(-1), {
    path: "/e/calibration",
    method: "POST",
    body: { module: "CB" },
  });
});

test("a running calibration shows part, step and what it waits for and can be cancelled", async () => {
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
    /Zusatzmodul läuft:<\/strong> A1, Schritt 2 von 4, wartet auf Antwort/,
  );
  assert.match(html(), /data-action="calibration-cancel" >Kalibrierung abbrechen/);
  assert.match(html(), /Ein Abbruch macht nur den laufenden Teil ungültig/);
  // The own status replaces the lock reason of a running calibration.
  assert.doesNotMatch(html(), /Es läuft bereits eine Kalibrierung/);
  assert.doesNotMatch(html(), /calibration-start|calibration-pause/);
  await p.action("calibration-start");
  await p.action("calibration-cancel");
  assert.deepEqual(sent(requests).slice(1), [
    { path: "/e/calibration/cancel", method: "POST", body: {} },
  ]);
  // L-W2c-3: with its run_id the panel cancels only the run it shows.
  p.state.calibration.active = { ...run, run_id: "r1" };
  await p.action("calibration-cancel");
  assert.deepEqual(sent(requests).at(-1), {
    path: "/e/calibration/cancel",
    method: "POST",
    body: { run_id: "r1" },
  });
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

test("in a pause the panel offers its answers next to the cancel (W-3)", async () => {
  const run = {
    run_id: "r1",
    kind: "procedure",
    modules: ["P1", "Gang 1", "P2", "Gang 2"],
    module: "Gang 2",
    step: 7,
    steps: 7,
    waiting: "pause",
    next: "Gang 3",
    actions: [
      { action: "next", label: "Nächster Gang" },
      { action: "finish", label: "Beenden" },
    ],
  };
  const { p, requests, html } = fixture({
    session: { session_id: "s" },
    calibration: { active: run },
  });
  await p.action("calibration-targets");
  assert.match(
    html(),
    /Kalibrierung läuft:<\/strong> Gang 2 beendet, Pause vor Gang 3, wartet auf „Nächster Gang“ oder „Beenden“/,
  );
  // Pause answers and the cancel stand in separate rows (G16).
  assert.match(
    html(),
    /<div class="row"><button class="confirm" data-action="calibration-pause:next" >Nächster Gang<\/button><button data-action="calibration-pause:finish" >Beenden<\/button><\/div><div class="row"><button class="stop" data-action="calibration-cancel" >/,
  );
  await p.action("calibration-pause:finish");
  assert.deepEqual(sent(requests).at(-1), {
    path: "/e/calibration/pause",
    method: "POST",
    body: { run_id: "r1", action: "finish" },
  });
  // An answer the pause does not offer is not sent.
  const count = requests.length;
  await p.action("calibration-pause:repeat");
  assert.equal(requests.length, count);
  assert.equal(
    p.calibrationProgress(run, { waiting: false }),
    "Gang 2 beendet, Pause vor Gang 3",
  );
  // Without a pause there is no pause answer.
  p.state.calibration.active = {
    ...run,
    waiting: "reply",
    step: 1,
    actions: undefined,
  };
  p.drawCalibration();
  assert.doesNotMatch(html(), /calibration-pause/);
});

test("the start card no longer repeats the last result; it comes from the archive (B4e)", async () => {
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
  const last = {
    kind: "module",
    modules: ["A1"],
    valid: true,
    reason: "finished",
    totals,
  };
  const { p, html } = fixture({ calibration: { last } });
  p.drawCalibration();
  assert.doesNotMatch(html(), /Letztes Ergebnis/);
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

test("in a running session the module start is locked while a module would end at once", async () => {
  for (const [reason, text] of [
    ["manual_mode", "manueller Modus"],
    ["heater_override", "manuelle Ofenwahl"],
    ["presence_unavailable", "Präsenzsensor unbekannt oder nicht verfügbar"],
  ]) {
    const { p, requests, html } = fixture({
      session: { session_id: "s" },
      calibration: { blocked: { reason, reason_text: text } },
    });
    await p.action("calibration-targets");
    assert.match(
      html(),
      new RegExp(`Ein jetzt gestartetes Zusatzmodul wäre sofort ungültig: ${text}\\.`),
    );
    assert.match(html(), /<select id="calibration-module" disabled>/);
    assert.match(
      html(),
      /data-action="calibration-start" disabled>Zusatzmodul starten/,
    );
    await p.action("calibration-start");
    assert.equal(sent(requests).length, 1);
  }
});

test("W-2e: the integration's lock text is shown for additional module and procedure", async () => {
  // Modul vor der Mindesttemperatur und in der Wiederaufnahmefrist
  for (const text of [
    "Zusatzmodul starten ist bis zum ersten Erreichen der Mindesttemperatur gesperrt: vorher zählt kein Eintritt, der Gang des Zusatzmoduls fehlte.",
    "Ein jetzt gestartetes Zusatzmodul wäre sofort ungültig: Betrieb aus.",
  ]) {
    const { p, requests, html } = fixture({
      session: { session_id: "s" },
      calibration: { blocked: { reason: "x", reason_text: "y", text } },
    });
    await p.action("calibration-targets");
    assert.ok(html().includes(`<p class="muted">${text}</p>`));
    assert.match(
      html(),
      /data-action="calibration-start" disabled>Zusatzmodul starten/,
    );
    await p.action("calibration-start");
    assert.equal(sent(requests).length, 1, "only the target check, no start");
  }
  // Kalibriersitzung ohne bekannten Präsenzzustand
  const { p, requests, html } = fixture({
    calibration: {
      blocked: {
        reason: "presence_unavailable",
        reason_text: "Präsenzsensor unbekannt oder nicht verfügbar",
        text: "Eine jetzt gestartete Kalibrierung wäre sofort ungültig: Präsenzsensor unbekannt oder nicht verfügbar.",
      },
    },
  });
  await p.action("calibration-targets");
  assert.match(html(), /Eine jetzt gestartete Kalibrierung wäre sofort ungültig/);
  assert.match(html(), /data-action="calibration-start" disabled>Kalibrierung starten/);
  await p.action("calibration-start");
  assert.equal(sent(requests).length, 1);
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
