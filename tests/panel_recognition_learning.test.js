const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
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
  const requests = [],
    nodes = { "#recognition-learning": {} };
  let sessions = [
    {
      session_id: "new",
      started_at: "2030-01-02T10:00:00Z",
      door_openings: 4,
      infusions: 3,
      released: false,
    },
    {
      session_id: "old",
      started_at: null,
      door_openings: 5,
      infusions: 0,
      released: true,
    },
  ];
  const p = Object.assign(fixturePanel(Panel), {
    entry: "e",
    generation: 1,
    state: { permissions: { admin: true }, session: null },
    $: (key) => nodes[key],
    updateMarkup: (key, html) => (nodes[key].innerHTML = html),
    api: async (path, method, body) => {
      requests.push({ path, method, body });
      if (method === "POST")
        sessions = sessions.map((session) =>
          session.session_id === body.session_id
            ? { ...session, released: body.released }
            : session,
        );
      return sessions;
    },
  });
  return { p, requests, nodes };
}
test("stored learning sessions are listed with their release status", async () => {
  const { p, requests, nodes } = fixture();
  await p.action("learning-list");
  assert.deepEqual(requests, [
    { path: "/e/recognition/sessions", method: undefined, body: undefined },
  ]);
  const html = nodes["#recognition-learning"].innerHTML;
  assert.match(html, /Sitzung vom .+?: 4 Türöffnungen, 3 Aufgüsse, nicht freigegeben/);
  assert.match(html, /Sitzung old: 5 Türöffnungen, 0 Aufgüsse, freigegeben/);
  assert.match(html, /data-action="learning-release:new"[^>]*>Freigeben</);
  assert.match(html, /data-action="learning-withdraw:old"[^>]*>Freigabe zurücknehmen</);
});
test("release and withdrawal send one explicit request and show the stored status", async () => {
  const { p, requests, nodes } = fixture();
  await p.action("learning-list");
  await p.action("learning-release:new");
  // The body is created in the panel context; compare its fields.
  assert.equal(requests.at(-1).path, "/e/recognition/sessions");
  assert.equal(requests.at(-1).method, "POST");
  assert.equal(requests.at(-1).body.session_id, "new");
  assert.equal(requests.at(-1).body.released, true);
  assert.match(
    nodes["#recognition-learning"].innerHTML,
    /data-action="learning-withdraw:new"/,
  );
  assert.match(
    nodes["#recognition-learning"].innerHTML,
    /Sitzung für das Nachlernen freigegeben\./,
  );
  await p.action("learning-withdraw:old");
  assert.equal(requests.at(-1).body.session_id, "old");
  assert.equal(requests.at(-1).body.released, false);
  assert.match(
    nodes["#recognition-learning"].innerHTML,
    /data-action="learning-release:old"/,
  );
  assert.match(nodes["#recognition-learning"].innerHTML, /Freigabe zurückgenommen\./);
  assert.equal(requests.length, 3);
});
test("without administrator rights no learning request is sent", async () => {
  const { p, requests } = fixture();
  p.state.permissions.admin = false;
  await p.action("learning-list");
  await p.action("learning-release:new");
  assert.equal(requests.length, 0);
});
test("a response for a previously selected instance is not shown", async () => {
  const { p } = fixture();
  p.api = async () => {
    p.entry = "other";
    return [{ session_id: "x", released: false }];
  };
  await p.action("learning-list");
  assert.equal(p.recognitionSessions ?? null, null);
});
