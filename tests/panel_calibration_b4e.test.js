"use strict";
// B4e Details · Kalibrierung: Lauf aus dem Archiv mit Ergebnis je Schritt (L-B4-3,
// L-W2c-1), Lauf wählen, Sprung in Details · Erkennung.
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

const T0 = Date.parse("2026-11-09T17:00:00Z"),
  at = (seconds) => new Date(T0 + seconds * 1000).toISOString();
const modules = [
  {
    id: "A1",
    label: "A1",
    title: "Austritt nach Aufguss",
    steps: [
      { key: "entry", reference: "Eintritt", expects: ["door_open", "door_close"] },
      { key: "infusion", reference: "Aufguss", expects: ["infusion"] },
    ],
  },
];
const totals = {
  gangs: { reference: 1, found: 1, missing: 0, additional: 0 },
  events: { reference: 3, found: 1, outside: 1, missing: 1, additional: 1, merged: 0 },
  empty_cabin_reports: 2,
  excluded_steps: 0,
};
const summary = (overrides = {}) => ({
  run_id: "r2",
  session_id: "s1",
  kind: "session",
  kind_text: "Kalibriersitzung",
  modules: ["A1"],
  started_at: at(0),
  ended_at: at(900),
  valid: true,
  reason: "finished",
  reason_text: null,
  evaluated: true,
  totals,
  ...overrides,
});
const evaluatedRun = () => ({
  ...summary(),
  presence_measured: true,
  executions: [
    {
      execution: 1,
      module: "A1",
      label: "A1",
      title: "Austritt nach Aufguss",
      repeat: false,
      cells: [
        {
          type: "step",
          step: "entry",
          reference: "Eintritt",
          expects: ["Tür auf", "Tür zu"],
          outcome: "reply",
          window: [at(10), at(30)],
          status: "mixed",
          status_text: null,
          expected: [
            {
              kind: "door_open",
              label: "Tür auf",
              status: "found",
              status_text: "im Fenster",
              at: at(15),
              offset_s: 0,
              event_id: "ev-open",
            },
            {
              kind: "door_close",
              label: "Tür zu",
              status: "outside",
              status_text: "außerhalb des Fensters",
              at: at(42),
              offset_s: 12.4,
              event_id: "ev-close",
            },
          ],
        },
        {
          type: "additional",
          kind: "door_open",
          label: "Tür auf",
          at: at(200),
          window: [at(200), at(200)],
          status: "additional",
          status_text: "zusätzlich",
          event_id: "ev-extra",
        },
        {
          type: "step",
          step: "infusion",
          reference: "Aufguss",
          expects: ["Aufguss"],
          outcome: "reply",
          window: [at(390), at(400)],
          status: "missing",
          status_text: "fehlt",
          expected: [
            {
              kind: "infusion",
              label: "Aufguss",
              status: "missing",
              status_text: "fehlt",
              at: null,
              offset_s: null,
              event_id: null,
            },
          ],
        },
      ],
      gang: {
        found: true,
        additional: 0,
        start_offset_s: 12.4,
        end_offset_s: null,
        infusions: 0,
        reference_infusions: 1,
        gang_heat_after_exit_s: null,
      },
      presence: {
        dropouts: { count: 1, durations_s: [3] },
        after_run_s: null,
        door_watch: null,
      },
      merged: [],
    },
  ],
  additional: [],
});

function fixture({
  active = null,
  runs = [
    summary(),
    summary({
      run_id: "r1",
      started_at: at(-3600),
      valid: false,
      reason: "cancelled",
      reason_text: "abgebrochen",
      evaluated: false,
      totals: null,
    }),
  ],
  run = evaluatedRun(),
  sessions = [{ session_id: "s1", started_at: at(-60), ended_at: at(1000) }],
} = {}) {
  const requests = [],
    nodes = { "#calibration": {}, "#calibration-grid": {} };
  const p = Object.assign(fixturePanel(Panel), {
    entry: "e",
    generation: 1,
    view: "detail-calibration",
    sessions,
    state: {
      permissions: { admin: true, control: true },
      session: null,
      archive_revision: 1,
      calibration: { active, last: null, modules, blocked: null },
    },
    calibrationTargets: 1,
    $: (key) => nodes[key],
    updateMarkup: (key, html) => (nodes[key].innerHTML = html),
    api: async (path) => {
      requests.push(path);
      if (path.startsWith("/e/archive/calibration")) {
        const choice = new URLSearchParams(path.split("?")[1] || "").get("run_id");
        if (choice === "gone") throw { status_code: 404, body: {} };
        return {
          runs,
          run:
            choice === "r1"
              ? { ...runs[1], presence_measured: null, executions: [], additional: [] }
              : run,
        };
      }
      return {};
    },
  });
  const settle = async () => {
    for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve));
  };
  return { p, requests, html: () => nodes["#calibration-grid"].innerHTML, settle };
}

test("the latest run from the archive shows totals and the result of every step", async () => {
  const { p, requests, html, settle } = fixture();
  p.drawCalibration();
  assert.match(html(), /Läufe werden geladen/);
  await settle();
  assert.deepEqual(requests, ["/e/archive/calibration"]);
  const markup = html();
  assert.match(markup, /<h2>Letzter Lauf<\/h2>/);
  assert.match(markup, /Kalibriersitzung A1/);
  // Lauf wählen: alle Läufe, dazu das Raster der Module und Schritte
  assert.match(
    markup,
    /<label for="calibration-run">Lauf wählen<\/label><select id="calibration-run"><option value="r2" selected>/,
  );
  assert.match(
    markup,
    /<option value="r1">[^<]*ungültig<\/option><option value="modules">Module und Schritte<\/option>/,
  );
  // Summen wie im Brett
  assert.match(
    markup,
    /<p class="stat-key">Gänge gefunden<\/p><p class="stat-value">1<small>von 1<\/small>/,
  );
  assert.match(
    markup,
    /Türen und Aufgüsse im Fenster<\/p><p class="stat-value">1<small>von 3<\/small><\/p><p class="stat-line">1 fehlen, 1 zusätzlich/,
  );
  assert.match(markup, /Außerhalb des Fensters<\/p><p class="stat-value">1<\/p>/);
  assert.match(
    markup,
    /Anwesend bei leerer Kabine<\/p><p class="stat-value">2<small>-mal<\/small>/,
  );
  // Zellen: im Fenster, außerhalb mit Lage, zusätzlich, fehlt
  assert.match(
    markup,
    /data-calibration-status="mixed"><button type="button" class="calibration-cell" data-action="calibration-cell:0:0"><strong>Eintritt<\/strong>/,
  );
  assert.match(markup, /data-calibration-status="found">Tür auf · im Fenster<\/span>/);
  assert.match(
    markup,
    /data-calibration-status="outside">Tür zu · 12,4 s nach dem Fenster<\/span>/,
  );
  assert.match(
    markup,
    /data-calibration-status="additional"><button[^>]*data-action="calibration-cell:0:1"><strong>zusätzlich<\/strong><span>Tür auf · /,
  );
  assert.match(
    markup,
    /data-calibration-status="missing">[^]*<strong>Aufguss<\/strong><span class="calibration-mark" data-calibration-status="missing">Aufguss · fehlt/,
  );
  assert.match(
    markup,
    /Gang gefunden, Beginn 12,4 s nach dem Fenster des Eintritts, 0 von 1 Aufgüssen/,
  );
  assert.match(markup, /Präsenz: 1 Aussetzer \(3 s\)/);
  assert.match(markup, /class="calibration-legend"/);
  assert.doesNotMatch(markup, /run_id|r2</);
});

test("an invalid run names its reason and stays without evaluation", async () => {
  const { p, requests, html, settle } = fixture();
  p.drawCalibration();
  await settle();
  p.selectCalibrationRun("r1");
  await settle();
  assert.equal(requests.at(-1), "/e/archive/calibration?run_id=r1");
  assert.match(html(), /<h2>Gewählter Lauf<\/h2>/);
  assert.match(
    html(),
    /<strong>Ungültig:<\/strong> abgebrochen\. Ungültige Läufe werden nicht ausgewertet\./,
  );
  assert.doesNotMatch(html(), /calibration-totals|calibration-legend/);
});

test("a valid run before its session end waits for the evaluation", async () => {
  const run = { ...evaluatedRun(), evaluated: false, totals: null };
  run.executions[0].cells = [
    {
      type: "step",
      step: "entry",
      reference: "Eintritt",
      expects: ["Tür auf", "Tür zu"],
      outcome: "reply",
      window: [at(10), at(30)],
      status: "pending",
      status_text: "Auswertung nach dem Sitzungsende",
      expected: [],
    },
  ];
  const { p, html, settle } = fixture({
    run,
    runs: [summary({ evaluated: false, totals: null })],
  });
  p.drawCalibration();
  await settle();
  assert.match(
    html(),
    /Gültig\. Die Auswertung der Erkennung folgt nach dem Sitzungsende\./,
  );
  assert.match(
    html(),
    /<strong>Eintritt<\/strong><span>Tür auf, Tür zu<\/span><span>Auswertung nach dem Sitzungsende<\/span>/,
  );
});

test("without runs and while a run is active the grid of modules and steps stays", async () => {
  let { p, html, settle } = fixture({ runs: [], run: null });
  p.drawCalibration();
  await settle();
  assert.match(html(), /<h2>Module und Schritte<\/h2>/);
  assert.doesNotMatch(html(), /calibration-run"/);
  assert.match(html(), /<strong>Eintritt<\/strong><span>Tür auf, zu<\/span>/);
  ({ p, html, settle } = fixture({
    active: {
      run_id: "r3",
      kind: "module",
      module: "A1",
      step: 2,
      steps: 2,
      waiting: "reply",
    },
  }));
  p.drawCalibration();
  await settle();
  assert.match(html(), /<h2>Module und Schritte<\/h2>/);
  assert.match(
    html(),
    /<option value="modules" selected>Module und Schritte<\/option>/,
  );
  assert.match(html(), /aria-current="step"><strong>Aufguss</);
  p.selectCalibrationRun("r2");
  await settle();
  assert.match(html(), /<h2>Letzter Lauf<\/h2>/);
});

test("a chosen run that the archive no longer holds falls back to the latest", async () => {
  const { p, requests, html, settle } = fixture();
  p.drawCalibration();
  await settle();
  p.selectCalibrationRun("gone");
  await settle();
  // The latest run is already loaded; it is shown again without a new request.
  assert.deepEqual(requests, [
    "/e/archive/calibration",
    "/e/archive/calibration?run_id=gone",
  ]);
  assert.equal(p.calibrationRunChoice, null);
  assert.match(html(), /<h2>Letzter Lauf<\/h2>/);
});

test("a click on a cell shows the session at the time of the step under Details · Erkennung", async () => {
  const { p, settle } = fixture();
  p.drawCalibration();
  await settle();
  const calls = [];
  Object.assign(p, {
    selected: "live",
    chooseHistorySession: (value) => {
      calls.push(["session", value]);
      p.selected = value;
    },
    setHistoryWindow: (left, right) => calls.push(["window", left, right]),
    setPanelView: (view) => calls.push(["view", view]),
    refresh: async () => calls.push(["refresh"]),
  });
  await p.action("calibration-cell:0:0");
  const span = p.preference("history_marker_focus_span_ms"),
    middle = T0 + 20 * 1000;
  // The session is not shown yet: its window waits for it.
  assert.deepEqual(calls, [["session", "s1"], ["view", "diagnostics"], ["refresh"]]);
  assert.deepEqual(JSON.parse(JSON.stringify(p.pendingHistoryWindow)), {
    sessionId: "s1",
    from: middle - span / 2,
    to: middle + span / 2,
  });
  assert.equal(p.recognitionSelected, "ev-open");
  calls.length = 0;
  p.shown = { session: { timeline: { session_id: "other" } } };
  p.applyPendingHistoryWindow();
  assert.deepEqual(calls, []);
  p.shown = { session: { timeline: { session_id: "s1" } } };
  p.applyPendingHistoryWindow();
  assert.deepEqual(calls, [["window", middle - span / 2, middle + span / 2]]);
  assert.equal(p.pendingHistoryWindow, null);
  // The same session already shown: the window applies at once.
  calls.length = 0;
  await p.action("calibration-cell:0:1");
  assert.equal(p.recognitionSelected, "ev-extra");
  assert.deepEqual(calls[0], [
    "window",
    T0 + 200000 - span / 2,
    T0 + 200000 + span / 2,
  ]);
});
