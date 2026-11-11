"use strict";
// Sitzungswahl im Verlauf: „Letzte Sitzung“ steht für die laufende bzw. die neueste
// archivierte Sitzung; jede Sitzung erscheint genau einmal (Nutzer 11.11.2026).
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {
    attachShadow() {}
  },
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  clearTimeout,
  setTimeout,
});

const archived = ["c", "b", "a"].map((id, index) => ({
  session_id: id,
  started_at: `2032-01-0${3 - index}T17:00:00Z`,
}));

function choice({ selected, live = null }) {
  const select = { innerHTML: "", value: "" };
  const p = Object.assign(fixturePanel(new Panel()), {
    selected,
    sessions: archived,
    state: { session: live },
    $: (selector) => (selector === "#session" ? select : null),
  });
  p.sessionLabel = (session) =>
    session.session_id || session.timeline.session_id;
  p.syncHistorySessions();
  const values = [...select.innerHTML.matchAll(/value="([^"]+)">([^<]*)</g)].map(
    (match) => [match[1], match[2]],
  );
  return { values, value: select.value };
}

test("ohne laufende Sitzung erscheint die neueste archivierte Sitzung nur einmal", () => {
  const { values, value } = choice({ selected: "live" });
  assert.deepEqual(values, [
    ["live", "c"],
    ["b", "b"],
    ["a", "a"],
  ]);
  assert.equal(value, "live");
});

test("eine ältere Auswahl ändert die Beschriftung von „Letzte Sitzung“ nicht", () => {
  const { values, value } = choice({ selected: "a" });
  assert.deepEqual(values, [
    ["live", "c"],
    ["b", "b"],
    ["a", "a"],
  ]);
  assert.equal(value, "a");
});

test("die neueste Sitzung als eigene Auswahl zeigt „Letzte Sitzung“", () => {
  assert.equal(choice({ selected: "c" }).value, "live");
});

test("eine laufende Sitzung steht vorn, alle archivierten folgen", () => {
  const { values, value } = choice({
    selected: "live",
    live: { timeline: { session_id: "d" } },
  });
  assert.deepEqual(values, [
    ["live", "d"],
    ["c", "c"],
    ["b", "b"],
    ["a", "a"],
  ]);
  assert.equal(value, "live");
});
