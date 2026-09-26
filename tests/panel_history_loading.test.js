const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
});
const turn = () => new Promise((resolve) => setTimeout(resolve, 5));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const state = (id = "live") => ({
  now: "2032-01-01T12:00:00Z",
  operation_enabled: true,
  session: { timeline: { session_id: id } },
  phase_projection: { intervals: [] },
});
const record = (id) => ({ id, kind: "measurement", payload: { value: id } });
const page = (ids, more = null, id = "live") => ({
  records: ids.map(record),
  next_after: more,
  session: state(id).session,
  phase_projection: { intervals: [] },
});
function panel(api) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
    "#history-loading": {},
  };
  const p = Object.assign(Object.create(Panel.prototype), {
    entry: "e",
    generation: 0,
    selected: "live",
    isConnected: true,
    state: state(),
    cache: new Map(),
    shadowRoot: { activeElement: null },
    api,
    $: (selector) => nodes[selector],
    updateMarkup: (selector, html) => {
      nodes[selector].innerHTML = html;
    },
    draws: 0,
    statusDraws: 0,
    errors: {},
    drawHistory() {
      this.draws++;
    },
    drawCurrent() {
      this.statusDraws++;
    },
    drawSettings() {},
    message(error, source) {
      this.errors[source] = error;
    },
  });
  return { p, nodes };
}

test("slow archive pages do not hold status refresh and partial pages are visible", async () => {
  const first = deferred(),
    second = deferred();
  let states = 0,
    lists = 0,
    pages = 0;
  const { p, nodes } = panel(async (path) => {
    if (path.endsWith("/state")) {
      states++;
      return state();
    }
    if (path.endsWith("/archive")) {
      lists++;
      return [];
    }
    pages++;
    return pages === 1 ? first.promise : second.promise;
  });
  await p.refresh();
  const loading = p.historyLoad.promise;
  assert.equal(p.busy, false);
  assert.equal(p.statusDraws, 1);
  await p.refresh();
  assert.equal(states, 2);
  assert.equal(lists, 1, "one background load owns pagination");
  first.resolve(page([1, 2], 2));
  await turn();
  assert.equal(p.shown.records.length, 2);
  assert.match(nodes["#history-loading"].textContent, /2 Mess-/);
  assert.equal(p.cache.get("live").loaded, false);
  await p.refresh();
  assert.equal(p.statusDraws, 3, "state keeps updating while a later page waits");
  second.resolve(page([3]));
  await loading;
  assert.deepEqual(
    Array.from(p.shown.records, (r) => r.id),
    [1, 2, 3],
  );
  assert.equal(nodes["#history-loading"].hidden, true);
});

test("failed later page retries committed cursor without duplicating overlap", async () => {
  const paths = [];
  let fail = true;
  const { p } = panel(async (path) => {
    if (path.endsWith("/archive")) return [];
    paths.push(path);
    if (path.endsWith("after=0")) return page([1, 2], 2);
    if (fail) throw Error("temporary page failure");
    return page([2, 3]);
  });
  await p.startHistoryLoad();
  assert.equal(p.cache.get("live").after, 2);
  assert.equal(p.cache.get("live").loaded, false);
  assert.match(p.errors.history.message, /temporary/);
  fail = false;
  await p.startHistoryLoad();
  assert.equal(paths.filter((s) => s.endsWith("after=0")).length, 1);
  assert.deepEqual(
    Array.from(p.shown.records, (r) => r.id),
    [1, 2, 3],
  );
  assert.equal(p.errors.history, null);
});

test("selection change discards a stale page without overwriting the new view", async () => {
  const old = deferred();
  const { p } = panel(async (path) => {
    if (path.endsWith("/archive"))
      return [{ session_id: "other", started_at: "2032-01-01" }];
    return path.includes("session_id=other") ? page([11], null, "other") : old.promise;
  });
  const previous = p.startHistoryLoad();
  await turn();
  p.selected = "other";
  const next = p.startHistoryLoad();
  await next;
  old.resolve(page([1, 2]));
  await previous;
  assert.equal(p.historySessionId, "other");
  assert.deepEqual(
    Array.from(p.shown.records, (r) => r.id),
    [11],
  );
  assert.equal(p.cache.get("live").after, 0);
});

test("instance generation and new live session both invalidate an old response", async () => {
  for (const change of [(p) => p.generation++, (p) => (p.state = state("new"))]) {
    const old = deferred();
    const { p } = panel(async (path) => (path.endsWith("/archive") ? [] : old.promise));
    const loading = p.startHistoryLoad();
    await turn();
    change(p);
    old.resolve(page([1]));
    await loading;
    assert.equal(p.cache.get("live").records.length, 0);
    assert.equal(p.shown, undefined);
  }
});

test("completed archive cache does not fetch pages again and nonadvancing cursor is rejected", async () => {
  let pages = 0;
  const { p } = panel(async (path) => {
    if (path.endsWith("/archive")) return [];
    pages++;
    return page([1]);
  });
  p.selected = "archive";
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(pages, 1);
  p.selected = "live";
  p.api = async (path) => (path.endsWith("/archive") ? [] : page([], 9));
  await p.startHistoryLoad();
  assert.match(p.errors.history.message, /ohne Fortschritt/);
  assert.equal(p.historyLoad, null);
});

test("last-session fallback reuses its completed cache without polling record pages", async () => {
  let pages = 0;
  const { p } = panel(async (path) => {
    if (path.endsWith("/archive"))
      return [{ session_id: "old", started_at: "2032-01-01" }];
    pages++;
    return page([1], null, "old");
  });
  p.state.session = null;
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(pages, 1);
  assert.equal(p.historySessionId, "old");
});
