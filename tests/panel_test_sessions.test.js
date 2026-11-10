"use strict";
// G25 (Nutzer 10.11.2026): Testbetrieb im Verlauf anzeigen und von Administratoren
// setzen oder aufheben.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(
  fs.readFileSync(
    path.join(__dirname, "../custom_components/ha_sauna/panel.js"),
    "utf8",
  ),
  {
    HTMLElement: class {},
    customElements: {
      get: () => undefined,
      define: (_name, value) => {
        Panel = value;
      },
    },
    Date,
    Map,
    Set,
    Math,
    Number,
    String,
    Object,
    Array,
    Infinity,
  },
);

function markup(admin, result) {
  const panel = Object.assign(fixturePanel(Panel), {
    state: { permissions: { admin, control: true } },
  });
  return panel.historyTestMarkup(result);
}

const calibration = { session_id: "s 1", test: true, test_source: "calibration" };
assert.match(markup(true, calibration), /<strong>Testbetrieb<\/strong> \(Kalibrierung\)/);
assert.match(
  markup(true, calibration),
  /data-action="session-test:s%201:0">Testbetrieb aufheben<\/button>/,
);
assert.match(markup(false, calibration), /Testbetrieb/);
assert.doesNotMatch(markup(false, calibration), /<button/);
const regular = { session_id: "s", test: false, test_source: null };
assert.match(
  markup(true, regular),
  /data-action="session-test:s:1">Als Testbetrieb kennzeichnen<\/button>/,
);
assert.equal(markup(false, regular), "");

(async () => {
  const calls = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    state: { permissions: { admin: true, control: true } },
    api: async (...args) => {
      calls.push(args);
      return { success: true, test: true, archive_revision: 7 };
    },
    loadHistoryKeyFigures: async () => calls.push(["reload"]),
    refresh: async () => {},
    message: () => {},
  });
  await panel.action("session-test:s%201:1");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-1/archive/label", "POST", { session_id: "s 1", test: true }],
    ["reload"],
  ]);
  assert.equal(panel.archiveRevision, 7);
  panel.state.permissions.admin = false;
  await panel.action("session-test:s%201:0");
  assert.equal(calls.length, 2);
})();
