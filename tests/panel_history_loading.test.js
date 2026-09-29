const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const panelPath = process.env.PANEL_PATH || "custom_components/ha_sauna/panel.js";
let Panel;
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
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
const state = (id = "live", ended = false) => ({
  now: "2032-01-01T12:00:00Z",
  operation_enabled: true,
  permissions: { admin: true },
  session: {
    timeline: { session_id: id },
    ...(ended ? { ended_at: "2032-01-01T12:10:00Z" } : {}),
  },
  phase_projection: { intervals: [] },
});
const record = (id) => ({ id, kind: "measurement", payload: { value: id } });
const page = (ids, more = null, id = "live", ended = false) => ({
  records: ids.map(record),
  next_after: more,
  session: state(id, ended).session,
  phase_projection: { intervals: [] },
});
function panel(api) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
    "#history-loading": {},
    "#gangs": { innerHTML: "" },
    "#event-list": { innerHTML: "" },
    "#detection-plots": { innerHTML: "" },
  };
  const p = Object.assign(Object.create(Panel.prototype), {
    entry: "e",
    generation: 0,
    selected: "live",
    navigation: { main: "details", detail: "detail-history" },
    isConnected: true,
    state: state(),
    cache: new Map(),
    shadowRoot: { activeElement: null, querySelectorAll: () => [] },
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
  const { p, nodes } = panel(async (request) => {
    if (request.endsWith("/state")) {
      states++;
      return state();
    }
    if (request.endsWith("/archive")) {
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
  assert.equal(p.cache.get("live").pageRunLoaded, false);
  await p.refresh();
  assert.equal(p.statusDraws, 3, "state keeps updating while a later page waits");
  second.resolve(page([3]));
  await loading;
  assert.deepEqual(
    Array.from(p.shown.records, (item) => item.id),
    [1, 2, 3],
  );
  assert.equal(nodes["#history-loading"].hidden, true);
});

test("failed later page retries committed cursor without duplicating overlap", async () => {
  const paths = [];
  let fail = true;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) return [];
    paths.push(request);
    if (request.endsWith("after=0")) return page([1, 2], 2);
    if (fail) throw Error("temporary page failure");
    return page([2, 3]);
  });
  await p.startHistoryLoad();
  assert.equal(p.cache.get("live").after, 2);
  assert.equal(p.cache.get("live").pageRunLoaded, false);
  assert.match(p.errors.history.message, /temporary/);
  fail = false;
  await p.startHistoryLoad();
  assert.equal(paths.filter((request) => request.endsWith("after=0")).length, 1);
  assert.deepEqual(
    Array.from(p.shown.records, (item) => item.id),
    [1, 2, 3],
  );
  assert.equal(p.errors.history, null);
});

test("selection change discards a stale page without overwriting the new view", async () => {
  const old = deferred();
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive"))
      return [
        {
          session_id: "other",
          started_at: "2032-01-01",
          ended_at: "2032-01-01T12:10:00Z",
        },
      ];
    return request.includes("session_id=other")
      ? page([11], null, "other", true)
      : old.promise;
  });
  const previous = p.startHistoryLoad();
  await turn();
  p.selected = "other";
  p.historySelectionGeneration = 1;
  const next = p.startHistoryLoad();
  await next;
  old.resolve(page([1, 2]));
  await previous;
  assert.equal(p.historySessionId, "other");
  assert.deepEqual(
    Array.from(p.shown.records, (item) => item.id),
    [11],
  );
  assert.equal(p.cache.get("live").after, 0);
});

test("instance generation and new live session both invalidate an old response", async () => {
  for (const change of [(p) => p.generation++, (p) => (p.state = state("new"))]) {
    const old = deferred();
    const { p } = panel(async (request) =>
      request.endsWith("/archive") ? [] : old.promise,
    );
    const loading = p.startHistoryLoad();
    await turn();
    change(p);
    old.resolve(page([1]));
    await loading;
    assert.equal(p.cache.get("live").records.length, 0);
    assert.equal(p.shown, undefined);
  }
});

test("closed archive cache does not fetch pages again and nonadvancing cursor is rejected", async () => {
  let pages = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) return [];
    pages++;
    return page([1], null, "archive", true);
  });
  p.selected = "archive";
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(pages, 1);
  p.selected = "live";
  p.historySelectionGeneration = 1;
  p.api = async (request) => (request.endsWith("/archive") ? [] : page([], 0));
  await p.startHistoryLoad();
  assert.match(p.errors.history.message, /ohne Fortschritt/);
  assert.equal(p.historyLoad, null);
});

test("redacted empty pages advance the server cursor and retry later failures", async () => {
  for (const ended of [false, true]) {
    const paths = [];
    let fail = true;
    const { p } = panel(async (request) => {
      if (request.endsWith("/archive")) return [];
      paths.push(request);
      if (request.endsWith("after=0")) return page([1], 1000, "live", ended);
      if (request.endsWith("after=1000")) return page([], 2000, "live", ended);
      if (fail) throw Error("temporary page failure");
      return page([2001], null, "live", ended);
    });
    await p.startHistoryLoad();
    assert.equal(p.cache.get("live").after, 2000);
    assert.match(p.errors.history.message, /temporary/);
    fail = false;
    await p.startHistoryLoad();
    assert.deepEqual(Array.from(p.shown.records, (item) => item.id), [1, 2001]);
    assert.equal(paths.filter((path) => path.endsWith("after=1000")).length, 1);
    assert.equal(p.cache.get("live").finalSynced, ended);
    assert.equal(p.errors.history, null);
  }
});

test("last-session fallback reuses its final cache without polling record pages", async () => {
  let pages = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive"))
      return [
        {
          session_id: "old",
          started_at: "2032-01-01",
          ended_at: "2032-01-01T12:10:00Z",
        },
      ];
    pages++;
    return page([1], null, "old", true);
  });
  p.state.session = null;
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(pages, 1);
  assert.equal(p.historySessionId, "old");
});

test("fixed user and admin panels load their finalized archive records", async () => {
  for (const admin of [false, true]) {
    const requests = [];
    const { p } = panel(async (request) => {
      if (request.endsWith("/archive")) return [{ session_id: "archive" }];
      requests.push(request);
      const result = page([1, 3], null, "archive", true);
      if (admin)
        result.records.splice(1, 0, { id: 2, kind: "detector_trace", payload: {} });
      return result;
    });
    p.selected = "archive";
    p.state.permissions.admin = admin;
    await p.startHistoryLoad();
    const cache = p.cache.get("archive");
    assert.equal(cache.accessProjection, admin ? "admin" : "public");
    assert.deepEqual(
      Array.from(cache.records, (item) => item.id),
      admin ? [1, 2, 3] : [1, 3],
    );
    assert.equal(cache.finalSynced, true);
    assert.equal(cache.after, 3);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].endsWith("after=0"));
  }
});

test("role loss clears diagnostic consumers even while history is hidden", async () => {
  const { p, nodes } = panel(async (request) => {
    assert.ok(request.endsWith("/state"));
    return { ...state(), permissions: { admin: false } };
  });
  p.syncHistoryProjection();
  const oldCache = p.historyCache("live");
  oldCache.records.push({ id: 1, kind: "detector_trace", payload: {} });
  p.shown = { session: p.state.session, records: oldCache.records };
  p.chartDataIndex = p.historyIndex(oldCache.records);
  p.historyEventKey = p.historyDiagnosticsKey = "old-admin";
  p.pendingEventFocus = { kind: "marker", eventId: "old" };
  nodes["#event-list"].innerHTML = '<button data-action="event-row:old">Marker</button>';
  nodes["#detection-plots"].innerHTML = '<svg class="diagnostic-marker"></svg>';
  nodes["#history"].hidden = true;

  await p.refresh();
  assert.equal(p.cache.size, 0);
  assert.equal(p.shown, null);
  assert.equal(p.chartDataIndex, null);
  assert.equal(p.pendingEventFocus, null);
  assert.equal(p.historyEventKey, null);
  assert.equal(p.historyDiagnosticsKey, null);
  assert.equal(nodes["#event-list"].innerHTML, "");
  assert.equal(nodes["#detection-plots"].innerHTML, "");
  p.drawDiagnostics();
  assert.equal(nodes["#detection-plots"].innerHTML, "");
});

test("a stale admin page cannot write after role loss and return to admin", async () => {
  const old = deferred();
  let adminRequests = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) return [{ session_id: "archive" }];
    assert.ok(request.endsWith("after=0"));
    if (p.state.permissions.admin && ++adminRequests === 1) return old.promise;
    const result = page([1], null, "archive", true);
    if (p.state.permissions.admin)
      result.records.push({ id: 2, kind: "detector_trace", payload: { current: true } });
    return result;
  });
  p.selected = "archive";
  const previous = p.startHistoryLoad();
  await turn();
  p.state.permissions.admin = false;
  await p.startHistoryLoad();
  const publicCache = p.cache.get("archive");
  assert.deepEqual(Array.from(publicCache.records, (item) => item.id), [1]);
  p.state.permissions.admin = true;
  await p.startHistoryLoad();
  const currentCache = p.cache.get("archive");
  old.resolve({
    ...page([98], null, "archive", true),
    phase_projection: { intervals: ["stale admin"] },
  });
  await previous;
  assert.equal(p.cache.get("archive"), currentCache);
  assert.deepEqual(Array.from(currentCache.records, (item) => item.id), [1, 2]);
  assert.deepEqual(currentCache.phase_projection, { intervals: [] });
  assert.equal(currentCache.finalSynced, true);
  assert.equal(p.shown.records, currentCache.records);
});

test("fixed user and admin panels navigate an archive while its first page loads", async () => {
  const session = {
    timeline: { session_id: "old", session_started_at: "2020-01-01T10:00:00Z" },
    ended_at: "2020-01-01T12:00:00Z",
  };
  for (const admin of [false, true]) {
    const response = deferred(),
      entered = deferred();
    const { p } = panel(async (request) => {
      if (request.endsWith("/archive"))
        return [
          {
            session_id: "old",
            started_at: session.timeline.session_started_at,
            ended_at: session.ended_at,
          },
        ];
      entered.resolve();
      return response.promise;
    });
    p.selected = "old";
    p.scheduleHistoryRender = () => {};
    p.state = { ...state(), session: null, permissions: { admin } };
    p.sessions = [
      {
        session_id: "old",
        started_at: session.timeline.session_started_at,
        ended_at: session.ended_at,
      },
    ];
    p.showHistoryCache();
    const pending = p.startHistoryLoad();
    await entered.promise;
    assert.equal(p.shown, null);
    assert.equal(p.historyCache("old").records.length, 0);
    p.zoom = 1;
    p.ensureHistoryWindow();
    const domain = Array.from(p.historyDomain());
    assert.deepEqual(domain, [
      Date.parse(session.timeline.session_started_at) - 15 * 60000,
      Date.parse(session.ended_at) + 15 * 60000,
    ]);
    p.setHistoryWindow(domain[0] + 30 * 60000, domain[0] + 60 * 60000);

    await p.action("zoom-in");
    assert.equal(p.zoom, 10);
    const zoomed = Array.from(p.window);
    p.setHistoryWindow(zoomed[0] + 60000, zoomed[1] + 60000);
    const shifted = Array.from(p.window);
    p.overviewFraction = () => 0.5;
    p.historyGesture = { kind: "move", fraction: 0.4, window: shifted, svg: {} };
    p.updateHistoryGesture({ clientX: 0, preventDefault() {} });
    assert.ok(p.window[0] > shifted[0]);
    assert.ok(p.window[0] >= domain[0] && p.window[1] <= domain[1]);
    await p.action("reset-zoom");
    assert.deepEqual(Array.from(p.window), domain);
    await p.action("zoom-in");
    const navigated = Array.from(p.window);
    response.resolve({ records: [], next_after: null, session });
    await pending;
    p.ensureHistoryWindow();
    assert.deepEqual(Array.from(p.window), navigated);
    assert.equal(p.zoom, 2);
    p.selected = "missing";
    p.shown = null;
    assert.ok(p.historyDomain()[0] > Date.parse("2031-01-01"));
    p.selected = "old";
    p.sessions = null;
    assert.ok(p.historyDomain()[0] > Date.parse("2031-01-01"));
  }
});
