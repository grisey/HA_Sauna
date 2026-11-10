"use strict";
// W-3 Kalibrierablauf in Gängen: Grundablauf und Zusatzmodule im Raster, Gültigkeit
// je Teil im Ergebnis eines Laufs (W3-2), ältere Läufe weiter lesbar. Das Panel
// formatiert nur, was die Integration liefert (G19).
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
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
  Intl,
  clearTimeout,
  setTimeout,
});

const T0 = Date.parse("2026-11-10T16:00:00Z"),
  at = (seconds) => new Date(T0 + seconds * 1000).toISOString();
const step = (key, reference, expects = []) => ({ key, reference, expects });
const procedure = [
  {
    id: "P1",
    label: "P1",
    title: "Leere Kabine beim Aufheizen",
    steps: [step("empty", "Beginn „leer“"), step("door_front", "Person vor der Tür")],
  },
  {
    id: "G1",
    label: "Gang 1",
    title: "nach Bereit",
    steps: [
      step("entry", "Eintritt", ["door_open", "door_close"]),
      step("infusion_1", "Aufguss 1", ["infusion"]),
    ],
  },
];
const modules = [
  {
    id: "A1",
    label: "A1",
    title: "Austritt nach Aufguss",
    steps: [step("entry", "Eintritt", ["door_open", "door_close"])],
  },
];
const totals = {
  gangs: { reference: 1, found: 1, missing: 0, additional: 0 },
  events: { reference: 2, found: 2, outside: 0, missing: 0, additional: 0, merged: 0 },
  empty_cabin_reports: 0,
  excluded_steps: 0,
};
const cell = (key, reference, status, window = [at(10), at(20)]) => ({
  type: "step",
  step: key,
  reference,
  expects: [],
  outcome: status === "not_reached" ? null : "reply",
  window: status === "not_reached" ? null : window,
  status,
  status_text: {
    none: "keine Erkennung erwartet",
    unevaluated: "nicht ausgewertet",
    not_reached: "nicht erreicht",
    cancelled: "bei der Unterbrechung offen",
  }[status],
  expected: [],
});
const partial = {
  run_id: "r3",
  session_id: "s3",
  kind: "procedure",
  kind_text: "Grundablauf",
  modules: ["P1", "Gang 1", "P2", "Gang 2"],
  started_at: at(0),
  ended_at: at(3000),
  valid: false,
  reason: "operation_off",
  reason_text: "Betrieb aus",
  parts: { begun: 4, valid: 3, invalid: ["Gang 2"] },
  evaluated: true,
  evaluation_pending: false,
  totals,
};
const older = {
  run_id: "r1",
  session_id: "s1",
  kind: "session",
  kind_text: "Kalibriersitzung",
  modules: ["P", "A1"],
  started_at: at(-86400),
  ended_at: at(-85000),
  valid: false,
  reason: "cancelled",
  reason_text: "abgebrochen",
  parts: { begun: 1, valid: 0, invalid: ["P"] },
  evaluated: false,
  evaluation_pending: false,
  totals: null,
};
const execution = (number, label, title, cells, valid = true, reason_text = null) => ({
  execution: number,
  module: label,
  label,
  title,
  repeat: false,
  valid,
  reason_text,
  cells,
  gang: null,
  presence: null,
  merged: [],
});
const partialRun = {
  ...partial,
  presence_measured: true,
  executions: [
    execution(1, "P1", "Leere Kabine beim Aufheizen", [
      cell("empty", "Beginn „leer“", "none"),
    ]),
    execution(2, "Gang 1", "nach Bereit", [cell("entry", "Eintritt", "none")]),
    execution(3, "P2", "Leere Kabine nach Gang 1", [
      cell("empty", "Beginn „leer“", "none"),
    ]),
    execution(
      4,
      "Gang 2",
      "nach der Pause",
      [
        cell("outside_opening", "kurzer Türvorgang ohne Gang", "unevaluated"),
        cell("entry", "Eintritt", "cancelled"),
        cell("infusion_1", "Aufguss 1", "not_reached"),
      ],
      false,
      "Betrieb aus",
    ),
  ],
  additional: [],
};

function fixture({ active = null, runs = [partial, older], run = partialRun } = {}) {
  const nodes = { "#calibration": {}, "#calibration-grid": {} };
  const p = Object.assign(fixturePanel(Panel), {
    entry: "e",
    generation: 1,
    view: "detail-calibration",
    sessions: [],
    state: {
      permissions: { admin: true, control: true },
      session: null,
      archive_revision: 1,
      calibration: { active, last: null, procedure, modules, blocked: null },
    },
    calibrationTargets: 1,
    $: (key) => nodes[key],
    updateMarkup: (key, html) => (nodes[key].innerHTML = html),
    api: async (path) => {
      if (!path.startsWith("/e/archive/calibration")) return {};
      const choice = new URLSearchParams(path.split("?")[1] || "").get("run_id");
      const chosen = runs.find((item) => item.run_id === choice);
      return {
        runs,
        run:
          chosen && chosen !== runs[0]
            ? { ...chosen, presence_measured: null, executions: [], additional: [] }
            : run,
      };
    },
  });
  const settle = async () => {
    for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve));
  };
  return { p, html: () => nodes["#calibration-grid"].innerHTML, settle };
}

test("a run with valid parts and an invalid Gang 2 names both (W3-2)", async () => {
  const { p, html, settle } = fixture();
  p.drawCalibration();
  await settle();
  const markup = html();
  assert.match(
    markup,
    /<h2>Letzter Lauf<\/h2><p class="muted">[^<]* · Grundablauf P1, Gang 1, P2, Gang 2<\/p>/,
  );
  assert.match(
    markup,
    /<option value="r3" selected>[^<]*Grundablauf P1, Gang 1, P2, Gang 2 · teilweise gültig<\/option>/,
  );
  assert.match(
    markup,
    /<option value="r1">[^<]*Kalibriersitzung P, A1 · ungültig<\/option>/,
  );
  assert.match(
    markup,
    /<p class="calibration-run-state" data-valid="partial"><strong>Teilweise gültig:<\/strong> Gang 2 ungültig \(Betrieb aus\)\. Die übrigen Teile sind ausgewertet\.<\/p>/,
  );
  // Totals of the evaluated parts; one row per part, the invalid one marked.
  assert.match(
    markup,
    /Gänge gefunden<\/p><p class="stat-value">1<small>von 1<\/small>/,
  );
  assert.match(markup, /<h3>Gang 1 · nach Bereit<\/h3>/);
  assert.match(
    markup,
    /data-calibration-execution="Gang 2" data-valid="false"><h3>Gang 2 · nach der Pause · ungültig: Betrieb aus<\/h3>/,
  );
  assert.match(
    markup,
    /<strong>kurzer Türvorgang ohne Gang<\/strong><span>keine Erkennung erwartet<\/span><span>nicht ausgewertet<\/span>/,
  );
  assert.match(
    markup,
    /data-calibration-status="not_reached"><strong>Aufguss 1<\/strong>/,
  );
  assert.doesNotMatch(markup, /r3<|operation_off|validity/);
});

test("an older run stays readable with its whole-run validity", async () => {
  const { p, html, settle } = fixture();
  p.drawCalibration();
  await settle();
  p.selectCalibrationRun("r1");
  await settle();
  assert.match(html(), /<h2>Gewählter Lauf<\/h2>/);
  assert.match(
    html(),
    /<p class="calibration-run-state" data-valid="false"><strong>Ungültig:<\/strong> abgebrochen\. Ungültige Läufe werden nicht ausgewertet\.<\/p>/,
  );
});

test("a valid run ended in a pause and one waiting for its evaluation", async () => {
  const pause = {
    ...partial,
    valid: true,
    reason: "cancelled",
    reason_text: "abgebrochen",
    parts: { begun: 3, valid: 3, invalid: [] },
    evaluated: false,
    evaluation_pending: true,
    totals: null,
  };
  let { p, html, settle } = fixture({
    runs: [pause],
    run: { ...pause, presence_measured: null, executions: [], additional: [] },
  });
  p.drawCalibration();
  await settle();
  assert.match(
    html(),
    /<p class="calibration-run-state">Beendet: abgebrochen\. Gültig\. Die Auswertung der Erkennung folgt nach dem Sitzungsende\.<\/p>/,
  );
  assert.match(html(), /<option value="r3" selected>[^<]* · gültig<\/option>/);
  // A run the integration no longer evaluates (unloaded) says so.
  const unloaded = {
    ...partial,
    reason_text: "Integration entladen oder neu gestartet",
    evaluated: false,
    totals: null,
  };
  ({ p, html, settle } = fixture({
    runs: [unloaded],
    run: { ...unloaded, presence_measured: null, executions: [], additional: [] },
  }));
  p.drawCalibration();
  await settle();
  assert.match(
    html(),
    /Gang 2 ungültig \(Integration entladen oder neu gestartet\)\. Die übrigen Teile sind nicht ausgewertet\./,
  );
});

test("the grid shows the basic procedure and the additional modules as groups", async () => {
  let { p, html, settle } = fixture({ runs: [], run: null });
  p.drawCalibration();
  await settle();
  assert.match(
    html(),
    /<h2>Module und Schritte<\/h2><\/div><p class="stat-key calibration-group">Grundablauf<\/p><div class="calibration-module"><h3>P1 · Leere Kabine beim Aufheizen<\/h3>[^]*<p class="stat-key calibration-group">Zusatzmodule<\/p><div class="calibration-module"><h3>A1 · Austritt nach Aufguss<\/h3>/,
  );
  // The running step is marked only in the group of the running run.
  ({ p, html, settle } = fixture({
    active: {
      run_id: "r4",
      kind: "procedure",
      module: "Gang 1",
      step: 2,
      steps: 2,
      waiting: "reply",
    },
  }));
  p.drawCalibration();
  await settle();
  assert.equal(html().match(/aria-current="step"/g).length, 1);
  assert.match(html(), /aria-current="step"><strong>Aufguss 1</);
  // In a pause no step runs.
  p.state.calibration.active = {
    run_id: "r4",
    kind: "procedure",
    module: "Gang 1",
    step: 2,
    steps: 2,
    waiting: "pause",
    next: "P2",
    actions: [],
  };
  p.drawCalibration();
  await settle();
  assert.doesNotMatch(html(), /aria-current/);
  const grid = p.calibrationGridMarkup(modules, null, procedure);
  assert.match(grid, /Grundablauf[^]*Zusatzmodule/);
});

test("protocol rows name the parts and their validity", () => {
  const p = fixturePanel(Panel);
  const record = (id, payload, display = {}) => ({
    id,
    kind: "calibration",
    received_at: at(id),
    payload: { run_id: "r", at: at(id), ...payload },
    ...display,
  });
  const rows = p.historyAdditionalActivity([
    record(1, { event: "module", execution: 1, module: "P1" }),
    record(2, {
      event: "module_end",
      execution: 1,
      module: "P1",
      valid: true,
      reason: null,
    }),
    record(
      3,
      {
        event: "module_end",
        execution: 2,
        module: "G1",
        valid: false,
        reason: "operation_off",
      },
      { display: { reason_text: "Betrieb aus" } },
    ),
    record(
      4,
      { event: "end", reason: "operation_off", valid: false, valid_executions: [1] },
      { display: { reason_text: "Betrieb aus" } },
    ),
    record(5, {
      event: "end",
      reason: "finished",
      valid: true,
      valid_executions: [1, 2],
    }),
  ]);
  assert.deepEqual(
    rows.map(({ label, detail }) => [
      label,
      /Ausführung: 1/.test(detail) ? "…" : detail,
    ]),
    [
      ["Kalibrierteil begonnen", "…"],
      ["Kalibrierteil beendet", "Gültig"],
      ["Kalibrierteil beendet", "Ungültig, Betrieb aus"],
      ["Kalibrierung beendet", "Beendet, Betrieb aus"],
      ["Kalibrierung beendet", "Abgeschlossen"],
    ],
  );
});
