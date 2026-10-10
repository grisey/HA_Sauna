const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const panelPath = process.env.PANEL_PATH || "custom_components/ha_sauna/panel.js";
const document = { hidden: false };
const frontendDefaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).frontend;
let Panel;
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
  HTMLElement: class {
    attachShadow() {}
  },
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
  clearTimeout,
  document,
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
  frontend_defaults: frontendDefaults,
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
    "#gangs": { innerHTML: "" },
    "#event-list": { innerHTML: "" },
    "#detection-plots": { innerHTML: "" },
    "#control-history": { innerHTML: "" },
  };
  const p = Object.assign(new Panel(), {
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
    scheduleRefresh() {},
    message(error, source) {
      this.errors[source] = error;
    },
  });
  return { p, nodes };
}

test("status polling is live during operation and sparse while idle", () => {
  const { p } = panel(async () => ({}));
  assert.equal(p.statusPollInterval(), 2000);
  p.state = { ...state(), session: null, operation_enabled: false };
  assert.equal(p.statusPollInterval(), 10000);
  p.state.light_after_run = { ends_at: "2032-01-01T12:01:00Z" };
  assert.equal(p.statusPollInterval(), 2000);
});

test("unrelated hass updates do not restart an in-flight status poll", async () => {
  const response = deferred();
  const idle = { ...state(), session: null, operation_enabled: false };
  let reads = 0;
  const { p, nodes } = panel(async (request) => {
    if (request !== "/e/state") throw Error(request);
    reads++;
    return response.promise;
  });
  nodes["#history"].hidden = true;
  p.state = idle;
  p.scheduleRefresh = Panel.prototype.scheduleRefresh;
  try {
    p.hass = { states: {} };
    await Promise.resolve();
    assert.equal(reads, 1, "initial hass assignment starts status polling");
    p.hass = { states: { "sensor.unrelated": { state: "changed" } } };
    response.resolve(idle);
    await turn();
    assert.equal(reads, 1, "a hass update during the request queues no read");
    assert.ok(p.timer, "the normal sparse status timer is scheduled");
    const timer = p.timer;
    p.hass = { states: { "sensor.unrelated": { state: "changed-again" } } };
    assert.equal(p.timer, timer, "a hass update leaves the scheduled poll intact");
    assert.equal(reads, 1);
  } finally {
    response.resolve(idle);
    await turn();
    clearTimeout(p.timer);
    p.timer = null;
  }
});

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
  assert.equal(p.cache.get("live").pageRunLoaded, false);
  await p.refresh();
  assert.equal(p.statusDraws, 3, "state keeps updating while a later page waits");
  second.resolve(page([3]));
  await loading;
  assert.deepEqual(
    Array.from(p.shown.records, (item) => item.id),
    [1, 2, 3],
  );
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
    assert.deepEqual(
      Array.from(p.shown.records, (item) => item.id),
      [1, 2001],
    );
    assert.equal(paths.filter((path) => path.endsWith("after=1000")).length, 1);
    assert.equal(p.cache.get("live").finalSynced, ended);
    assert.equal(p.errors.history, null);
  }
});

test("last-session fallback reuses its final cache without polling record pages", async () => {
  let pages = 0,
    lists = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) {
      lists++;
      return [
        {
          session_id: "old",
          started_at: "2032-01-01",
          ended_at: "2032-01-01T12:10:00Z",
        },
      ];
    }
    pages++;
    return page([1], null, "old", true);
  });
  p.state.session = null;
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(pages, 1);
  assert.equal(lists, 1);
  assert.equal(p.historySessionId, "old");
});

test("a completed background session refreshes the list without discarding archives or an explicit selection", async () => {
  for (const initial of ["latest", "selected", "empty", "running"]) {
    const old = page([1], null, "old", true),
      active = page([3], null, "active", false),
      next = page([2], null, "next", true),
      listed = (result) => ({
        session_id: result.session.timeline.session_id,
        started_at: result.session.timeline.session_started_at,
        ended_at: result.session.ended_at,
      });
    old.session.timeline.session_started_at = "2032-01-01T10:00:00Z";
    old.session.ended_at = "2032-01-01T11:00:00Z";
    active.session.timeline.session_started_at = "2032-01-01T11:15:00Z";
    next.session.timeline.session_started_at = "2032-01-01T12:15:00Z";
    next.session.ended_at = "2032-01-01T12:30:00Z";
    let latest = initial === "empty" ? null : old,
      running = initial === "running",
      lists = 0,
      pages = 0;
    const snapshot = () => ({
      ...state(),
      now: running ? "2032-01-01T12:00:00Z" : "2032-01-01T13:00:00Z",
      session: running ? active.session : null,
      operation_enabled: running,
      last_session: latest?.session || null,
    });
    const { p } = panel(async (request) => {
      if (request.endsWith("/state")) return snapshot();
      if (request.endsWith("/archive")) {
        lists++;
        return latest === next
          ? [
              listed(next),
              ...(initial === "running" ? [listed(active)] : []),
              ...(initial === "empty" ? [] : [listed(old)]),
            ]
          : latest
            ? [listed(old)]
            : [];
      }
      pages++;
      if (request.includes("session_id=active")) return active;
      return request.includes("session_id=old") ? old : next;
    });
    p.state = snapshot();
    p.selected = initial === "selected" ? "old" : "live";
    await p.refresh();
    await p.historyLoad?.promise;
    const cachedId = initial === "running" ? "active" : "old",
      oldCache = p.cache.get(cachedId);
    assert.equal(lists, 1);

    p.setPanelView("overview");
    running = false;
    active.session = { ...active.session, ended_at: "2032-01-01T12:10:00Z" };
    latest = next;
    await p.refresh();
    assert.equal(p.state.session, null);
    assert.equal(p.state.last_session.timeline.session_id, "next");
    assert.equal(lists, 1, "hidden history does not fetch archives");
    p.setPanelView("history");
    await p.startHistoryLoad();
    assert.equal(lists, 2, initial);
    assert.equal(p.sessions[0].session_id, "next", initial);
    assert.equal(p.historySessionId, initial === "selected" ? "old" : "next");
    assert.equal(
      p.cache.get(cachedId),
      oldCache,
      "previous archive cache is preserved",
    );
    const settledPages = pages;
    for (let cycle = 0; cycle < 3; cycle++) await p.refresh();
    assert.equal(lists, 2, "unchanged status does not reload the list");
    assert.equal(pages, settledPages, "complete selected archive is not reread");
  }
});

test("an older pending list cannot hide a newer last-session identity", async () => {
  const delayedList = deferred();
  let lists = 0;
  const idle = (id) => ({
    ...state(),
    session: null,
    operation_enabled: false,
    last_session: state(id, true).session,
  });
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) {
      lists++;
      if (lists === 1) return delayedList.promise;
      return [{ session_id: "new", started_at: "2032-01-01" }];
    }
    return page([1], null, "new", true);
  });
  p.state = idle("old");
  p.sessions = [{ session_id: "old", started_at: "2032-01-01" }];
  p.acceptState(idle("middle"));
  const previous = p.startHistoryLoad();
  p.acceptState(idle("new"));
  const current = p.startHistoryLoad();
  assert.equal(lists, 2, "new last session has its own current list request");
  await current;
  delayedList.resolve([{ session_id: "middle", started_at: "2032-01-01" }]);
  await previous;
  assert.equal(p.sessions[0].session_id, "new");
  assert.equal(p.historySessionId, "new");
  assert.equal(p.historyListStale, false);
});

test("a confirmed empty archive list remains loaded across status cycles", async () => {
  let lists = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/state")) return { ...state(), session: null };
    lists++;
    return [];
  });
  p.state.session = null;
  await p.refresh();
  await p.refresh();
  await p.refresh();
  assert.equal(lists, 1);
  assert.deepEqual(p.sessions, []);
});

test("returning from a hidden document reconciles its archive list once", async () => {
  let lists = 0;
  const idle = { ...state(), session: null, operation_enabled: false };
  const { p } = panel(async (request) => {
    if (request.endsWith("/state")) return idle;
    lists++;
    return [];
  });
  p.state = idle;
  p._hass = {};
  await p.refresh();
  await p.historyLoad?.promise;
  assert.equal(lists, 1);
  document.hidden = true;
  p.visibilityHandler();
  assert.equal(lists, 1, "hiding the panel does not fetch archives");
  document.hidden = false;
  p.visibilityHandler();
  await turn();
  await p.historyLoad?.promise;
  assert.equal(lists, 2, "resume checks once even when no live session was seen");
  await p.refresh();
  await p.refresh();
  assert.equal(lists, 2, "a confirmed empty list remains valid afterwards");
});

test("live page polling advances without reloading the archive list", async () => {
  let lists = 0,
    pages = 0;
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) {
      lists++;
      return [];
    }
    pages++;
    return page([pages], null, "live", false);
  });
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  await p.startHistoryLoad();
  assert.equal(lists, 1);
  assert.equal(pages, 3);
  assert.deepEqual(
    Array.from(p.historyCache("live").records, (item) => item.id),
    [1, 2, 3],
  );
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
  nodes["#event-list"].innerHTML =
    '<button data-action="event-row:old">Marker</button>';
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
      result.records.push({
        id: 2,
        kind: "detector_trace",
        payload: { current: true },
      });
    return result;
  });
  p.selected = "archive";
  const previous = p.startHistoryLoad();
  await turn();
  p.state.permissions.admin = false;
  await p.startHistoryLoad();
  const publicCache = p.cache.get("archive");
  assert.deepEqual(
    Array.from(publicCache.records, (item) => item.id),
    [1],
  );
  p.state.permissions.admin = true;
  await p.startHistoryLoad();
  const currentCache = p.cache.get("archive");
  old.resolve({
    ...page([98], null, "archive", true),
    phase_projection: { intervals: ["stale admin"] },
  });
  await previous;
  assert.equal(p.cache.get("archive"), currentCache);
  assert.deepEqual(
    Array.from(currentCache.records, (item) => item.id),
    [1, 2],
  );
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
      Date.parse(session.timeline.session_started_at),
      Date.parse(session.ended_at),
    ]);
    p.setHistoryWindow(domain[0] + 30 * 60000, domain[0] + 60 * 60000);

    await p.action("zoom-in");
    assert.equal(p.zoom, 8);
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

test("six-hour four-source cold pagination indexes each original once and reuses warm geometry", async () => {
  const { performance } = require("node:perf_hooks");
  const origin = Date.parse("2032-01-01T00:00:00Z");
  const records = [];
  for (let second = -900; second <= 6 * 3600 + 900; second += 2)
    for (const position of ["upper", "lower"])
      for (const quantity of ["temperature", "humidity"]) {
        const value = second % 997 === 0 ? null : 40.125 + (second % 101) / 8;
        records.push({
          id: records.length + 1,
          kind: "measurement",
          payload: {
            position,
            quantity,
            received_at: new Date(origin + second * 1000).toISOString(),
            value,
            raw_value: value == null ? null : String(value),
          },
        });
      }
  const archived = {
    timeline: {
      session_id: "volume",
      session_started_at: new Date(origin).toISOString(),
    },
    ended_at: new Date(origin + 6 * 3600 * 1000).toISOString(),
    measurement_ttl_seconds: 5,
  };
  let requests = 0,
    bytes = 0,
    parseMilliseconds = 0,
    preparationMilliseconds = 0,
    indexedRecordReads = 0,
    binVisits = 0;
  const chart = { prepared: new Map() };
  const { p } = panel(async (request) => {
    if (request.endsWith("/archive")) return [{ session_id: "volume" }];
    requests++;
    const after = Number(
      new URL(request, "http://synthetic.test").searchParams.get("after"),
    );
    const rows = records.slice(after, after + 5000);
    const wire = JSON.stringify({
      session: archived,
      phase_projection: { intervals: [] },
      records: rows,
      next_after: rows.length === 5000 ? rows.at(-1).id : null,
    });
    bytes += Buffer.byteLength(wire);
    const start = performance.now();
    const decoded = JSON.parse(wire);
    parseMilliseconds += performance.now() - start;
    return decoded;
  });
  p.selected = "volume";
  p.historySessionId = "volume";
  p.positions = new Set(["upper", "lower"]);
  p.window = [origin - 900000, origin + (6 * 3600 + 900) * 1000];
  const cache = p.historyCache("volume");
  cache.records = new Proxy(cache.records, {
    get(target, key, receiver) {
      if (typeof key === "string" && /^\d+$/.test(key)) indexedRecordReads++;
      return Reflect.get(target, key, receiver);
    },
  });
  const append = p.appendHistoryDisplayLevel;
  p.appendHistoryDisplayLevel = function (...args) {
    binVisits++;
    return append.apply(this, args);
  };
  p.drawHistory = function () {
    const start = performance.now();
    this.historyIndex(this.shown.records);
    this.historyModel(chart, this.shown.session);
    preparationMilliseconds += performance.now() - start;
  };
  const start = performance.now();
  await p.startHistoryLoad();
  const coldMilliseconds = performance.now() - start;
  assert.equal(p.errors.history, null);
  assert.equal(cache.records.length, 46804);
  assert.equal(requests, 10);
  assert.equal(indexedRecordReads, records.length, "earlier pages are never reindexed");
  assert.equal(
    binVisits,
    records.filter((record) => record.payload.value != null).length,
    "cold pages index each available original into display bins once",
  );
  const prepared = [...chart.prepared.values()];
  const readsBeforeWarm = indexedRecordReads;
  const binsBeforeWarm = binVisits;
  await p.startHistoryLoad();
  p.drawHistory();
  assert.equal(requests, 10, "a completed warm archive performs no requests");
  assert.equal(indexedRecordReads, readsBeforeWarm);
  assert.equal(binVisits, binsBeforeWarm);
  assert.ok(
    [...chart.prepared.values()].every((value, index) => value === prepared[index]),
  );
  for (const position of p.positions)
    for (const quantity of ["temperature", "humidity"])
      for (const point of p.series(position, quantity)) {
        assert.equal(point.time, Date.parse(point.source.received_at));
        assert.equal(point.value, point.source.value);
      }
  console.log(
    "HISTORY_VOLUME_BENCHMARK " +
      JSON.stringify({
        records: records.length,
        sessionHours: 6,
        sources: 4,
        pages: requests,
        responseBytes: bytes,
        indexedRecordReads,
        binVisits,
        coldMilliseconds,
        parseMilliseconds,
        preparationMilliseconds,
        scope:
          "production pagination, JSON decode, index and display preparation; excludes network and browser paint",
      }),
  );
});
