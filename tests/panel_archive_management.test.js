const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, p) => (Panel = p) },
});
function fixture() {
  const writes = [],
    nodes = { "#archive-management": {} };
  const p = Object.assign(Object.create(Panel.prototype), {
    entry: "e",
    generation: 1,
    archiveRevision: 0,
    state: {
      permissions: { admin: true },
      session: null,
      last_session: { timeline: { session_id: "old" } },
    },
    archiveAdminSessions: [
      {
        session_id: "old",
        started_at: "2030-01-01T10:00:00Z",
        ended_at: "2030-01-01T12:00:00Z",
      },
    ],
    cache: new Map([["old", { records: [1] }]]),
    $: (key) => nodes[key],
    updateMarkup: (key, html) => (nodes[key].innerHTML = html),
    clearHistoryDisplay() {},
    invalidateHistoryIndex() {},
    refresh: async () => {},
    api: async (path, method, body) => {
      if (method === "POST") {
        writes.push({ path, body });
        return { archive_revision: 1 };
      }
      return [];
    },
  });
  return { p, writes, nodes };
}
test("individual deletion requires confirmation and clears deleted state and cached data", async () => {
  const { p, writes, nodes } = fixture();
  await p.action("archive-delete:old");
  assert.equal(writes.length, 0);
  assert.match(nodes["#archive-management"].innerHTML, /Endgültig löschen/);
  await p.action("archive-confirm");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].body.session_id, "old");
  assert.equal(p.cache.size, 0);
  assert.equal(p.state.last_session, null);
  assert.equal(p.archiveDeletePending, null);
  assert.match(nodes["#archive-management"].innerHTML, /Sitzung wurde gelöscht/);
});
test("cancel, missing admin rights, and a newly started session prevent deletion", async () => {
  const { p, writes } = fixture();
  await p.action("archive-reset");
  await p.action("archive-cancel");
  await p.action("archive-confirm");
  assert.equal(writes.length, 0);
  await p.action("archive-reset");
  p.state.session = { timeline: { session_id: "new" } };
  await p.action("archive-confirm");
  assert.equal(writes.length, 0);
  p.state.session = null;
  p.state.permissions.admin = false;
  await p.action("archive-confirm");
  assert.equal(writes.length, 0);
});
test("reset sends one explicit reset request and invalidates other open history loads", async () => {
  const { p, writes } = fixture();
  p.historyLoad = { key: "old request" };
  await p.action("archive-reset");
  await p.action("archive-confirm");
  assert.equal(writes[0].body.reset, true);
  assert.equal(p.historyLoad, null);
  assert.equal(p.historySelectionGeneration, 1);
});
test("archive revision changes clear caches even when the latest session identity is unchanged", () => {
  const { p } = fixture();
  p.acceptState({ ...p.state, archive_revision: 2 });
  assert.equal(p.cache.size, 0);
  assert.equal(p.sessions, null);
  assert.equal(p.archiveRevision, 2);
});
