const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
const panelPath =
  process.env.PANEL_PATH ||
  path.join(__dirname, "../custom_components/ha_sauna/panel.js");
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
});

const openSession = (id = "live") => ({ timeline: { session_id: id } });
const closedSession = (id = "live") => ({
  timeline: { session_id: id },
  ended_at: "2032-01-01T12:10:00Z",
});
const state = (session = openSession()) => ({
  now: "2032-01-01T12:00:00Z",
  operation_enabled: !!session,
  session,
  phase_projection: { intervals: [] },
});
const page = (records, session, projection = { intervals: [] }, next_after = null) => ({
  records,
  session,
  phase_projection: projection,
  next_after,
});
const record = (id, kind = "measurement") => ({ id, kind, payload: { value: id } });

test("closed sessions continue cursor loading until their measurement window is complete", async () => {
  let calls = 0;
  const window = {
    started_at: "2032-01-01T11:45:00Z",
    ended_at: "2032-01-01T12:25:00Z",
    complete: false,
  };
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive")) return [];
    calls++;
    return {
      ...page([record(calls)], closedSession()),
      measurement_window: { ...window },
    };
  });
  await p.startHistoryLoad();
  const cache = p.cache.get("live");
  assert.equal(cache.pageRunLoaded, true);
  assert.equal(cache.finalSynced, false);
  await p.startHistoryLoad();
  assert.equal(calls, 2);
  assert.deepEqual(
    Array.from(cache.records, (item) => item.id),
    [1, 2],
  );
  window.complete = true;
  await p.startHistoryLoad();
  assert.equal(cache.finalSynced, true);
  assert.equal(cache.measurement_window.complete, true);
  assert.equal(p.shown.measurement_window, cache.measurement_window);
  await p.startHistoryLoad();
  assert.equal(calls, 3);
});

function makePanel(api) {
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "" },
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
    updateMarkup: (selector, html) => (nodes[selector].innerHTML = html),
    errors: {},
    drawHistory(reason) {
      (this.draws ??= []).push(reason);
    },
    message(error, source) {
      this.errors[source] = error;
    },
  });
  return { p, nodes };
}

test("aa1681d failure: cached live page refetches final pages after state.session clears", async () => {
  const paths = [];
  let closing = false;
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive"))
      return closing
        ? [{ session_id: "live", ended_at: closedSession().ended_at }]
        : [];
    paths.push(request);
    return closing
      ? page([record(2, "future_archive_kind"), record(3)], closedSession(), {
          intervals: ["final"],
        })
      : page([record(1)], openSession(), { intervals: ["open"] });
  });

  await p.startHistoryLoad();
  const cache = p.cache.get("live");
  assert.equal(cache.pageRunLoaded, true);
  assert.equal(cache.finalSynced, false);
  assert.deepEqual(
    Array.from(cache.records, (item) => item.id),
    [1],
  );

  closing = true;
  p.acceptState(state(null));
  await p.startHistoryLoad();

  assert.deepEqual(
    paths.map((request) => request.match(/after=(\d+)/)[1]),
    ["0", "1"],
  );
  assert.deepEqual(
    Array.from(cache.records, (item) => item.id),
    [1, 2, 3],
  );
  assert.equal(cache.records[1].kind, "future_archive_kind");
  assert.equal(cache.session.ended_at, closedSession().ended_at);
  assert.deepEqual(cache.phase_projection, { intervals: ["final"] });
  assert.equal(cache.finalSynced, true);
  assert.deepEqual(p.draws, ["archive", "archive"]);
});

test("an explicitly selected old live cache finalizes after a newer live session begins", async () => {
  let finalizeOld = false;
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive"))
      return finalizeOld
        ? [{ session_id: "old", ended_at: closedSession("old").ended_at }]
        : [];
    if (request.includes("session_id=old"))
      return finalizeOld
        ? page([record(2)], closedSession("old"))
        : page([record(1)], openSession("old"));
    throw Error(`unexpected archive request: ${request}`);
  });

  p.acceptState(state(openSession("old")));
  await p.startHistoryLoad();
  finalizeOld = true;
  p.acceptState(state(openSession("new")));
  p.selected = "old";
  await p.startHistoryLoad();

  const cache = p.cache.get("old");
  assert.deepEqual(
    Array.from(cache.records, (item) => item.id),
    [1, 2],
  );
  assert.equal(cache.session.ended_at, closedSession("old").ended_at);
  assert.equal(cache.finalSynced, true);
  assert.equal(p.shown.session.timeline.session_id, "old");
});

test("an empty final page updates session and projection without a measurement revision", async () => {
  let closing = false;
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive"))
      return closing
        ? [{ session_id: "live", ended_at: closedSession().ended_at }]
        : [];
    return closing
      ? page([], closedSession(), { intervals: ["closed"] })
      : page([record(1)], openSession(), { intervals: ["open"] });
  });

  await p.startHistoryLoad();
  const cache = p.cache.get("live"),
    measurements = cache.measurementRevision,
    recordsRevision = cache.recordsRevision,
    metadataRevision = cache.metadataRevision;
  closing = true;
  p.acceptState(state(null));
  await p.startHistoryLoad();

  assert.equal(cache.recordsRevision, recordsRevision);
  assert.equal(cache.measurementRevision, measurements);
  assert.ok(cache.metadataRevision > metadataRevision);
  assert.equal(cache.session.ended_at, closedSession().ended_at);
  assert.deepEqual(cache.phase_projection, { intervals: ["closed"] });
  assert.equal(cache.finalSynced, true);
});

test("a failed final request retains its cursor and is retried without duplicate records", async () => {
  let closing = false,
    fail = false;
  const paths = [];
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive"))
      return closing
        ? [{ session_id: "live", ended_at: closedSession().ended_at }]
        : [];
    paths.push(request);
    if (!closing) return page([record(1)], openSession());
    if (fail) throw Error("temporary final page failure");
    return page([record(1), record(2)], closedSession());
  });

  await p.startHistoryLoad();
  closing = true;
  fail = true;
  p.acceptState(state(null));
  await p.startHistoryLoad();
  const cache = p.cache.get("live");
  assert.equal(cache.after, 1);
  assert.equal(cache.finalSynced, false);
  assert.match(p.errors.history.message, /temporary final/);

  fail = false;
  await p.startHistoryLoad();
  assert.deepEqual(
    Array.from(cache.records, (item) => item.id),
    [1, 2],
  );
  assert.equal(cache.after, 2);
  assert.equal(cache.finalSynced, true);
  assert.equal(paths.filter((request) => request.endsWith("after=1")).length, 2);
});

test("a stale selection or disconnected panel cannot apply a final archive response", async () => {
  let release;
  const pending = new Promise((resolve) => (release = resolve));
  const { p } = makePanel(async (request) => {
    if (request.endsWith("/archive")) return [];
    return pending;
  });
  const loading = p.startHistoryLoad();
  await new Promise((resolve) => setTimeout(resolve, 0));
  p.selected = "other";
  release(page([record(1)], closedSession()));
  await loading;
  assert.equal(p.cache.get("live").records.length, 0);

  p.selected = "live";
  p.state = state();
  const disconnected = p.startHistoryLoad();
  p.isConnected = false;
  await disconnected;
  assert.equal(p.cache.get("live").records.length, 0);
});
