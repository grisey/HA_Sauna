"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const { panelFixture, parameterValues } = require("./catalog_fixture.cjs");

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: {
    get: () => null,
    define: (_, value) => {
      Panel = value;
    },
  },
  setTimeout,
  clearTimeout,
});
const origin = Date.UTC(2032, 0, 1);
const at = (second) => origin + second * 1000;
const iso = (second) => new Date(at(second)).toISOString();
const start = (id, second) => ({
  event_id: id,
  session_id: "s",
  kind: "infusion",
  effective_at: iso(second),
  detected_at: iso(second + 2),
});
const end = (second) => ({
  effective_at: iso(second),
  confirmed_at: iso(second + 5),
  trace_at: iso(second + 3),
  basis: "door_open",
  time_basis: "physical_effective",
});
function fixture() {
  const events = [start("first", 20), start("second", 100)];
  const gang = {
    gang_id: "g",
    started_at: iso(10),
    ended_at: iso(160),
    infusion_events: events,
    infusions: [
      { source_event_id: "first", end: end(30) },
      { source_event_id: "second", end: end(115) },
    ],
  };
  const session = {
    ended_at: iso(180),
    configuration: { parameters: parameterValues() },
    timeline: {
      session_id: "s",
      session_started_at: iso(0),
      processed: events,
      completed: [gang],
      active: null,
      retracted: [],
    },
    heating: { intervals: [] },
  };
  const panel = Object.assign(panelFixture(Panel), {
    entry: "entry",
    window: [at(0), at(180)],
    state: { now: iso(180), configuration: session.configuration },
    shown: { session, records: [], phase_projection: { intervals: [] } },
    ownerDocument: { defaultView: { matchMedia: () => ({ matches: true }) } },
  });
  return { panel, session, gang, events };
}

test("individual infusion spans consume the owning end, without joining separate infusions", () => {
  const { panel, session, gang, events } = fixture();
  const before = JSON.stringify(session);
  const projection = panel.historyEventProjection();
  const groups = projection.groups.filter((group) => group.kind === "infusion");
  assert.deepEqual(
    Array.from(groups, (group) => [group.start, group.end]),
    [
      [at(20), at(30)],
      [at(100), at(115)],
    ],
  );
  assert.equal(groups[0].end_ref, gang.infusions[0].end);
  assert.equal(groups[1].first.event.source_ref, events[1]);
  assert.equal(groups[0].children.length, 0);
  assert.equal(
    projection.allEvents.length,
    2,
    "end references do not create persisted events",
  );
  assert.ok(
    projection.snapTargets.some(
      (target) =>
        target.time === at(30) &&
        target.eventId === "first" &&
        target.boundary === "end",
    ),
  );
  assert.equal(JSON.stringify(session), before);
});

test("selecting one infusion uses effective end and keeps confirmation as a separate detail", () => {
  const { panel } = fixture();
  panel.inspectHistoryEvent("first");
  assert.deepEqual(Array.from(panel.historyValueSelection.eventIds), ["first"]);
  assert.equal(panel.historyValueSelection.kind, "range");
  assert.equal(panel.historyValueSelection.start, at(20));
  assert.equal(panel.historyValueSelection.end, at(30));
  assert.equal(panel.historyValueSelection.endSnap.time, at(30));
  const markup = panel.historyInfusionEndMarkup(panel.historyInspectedEvent());
  assert.ok(markup.includes(`datetime="${iso(30)}"`));
  assert.ok(markup.includes(`datetime="${iso(35)}"`));
  assert.match(markup, /Ende.*Bestätigt/);
  panel.inspectHistoryEvent("second");
  assert.equal(panel.historyValueSelection.start, at(100));
  assert.equal(panel.historyValueSelection.end, at(115));
});

test("missing, withdrawn or foreign ends never borrow another infusion or the gang end", () => {
  const { panel, gang } = fixture();
  gang.infusions = [
    { source_event_id: "foreign", end: end(170) },
    { source_event_id: "second", end: null },
  ];
  let groups = panel.historyEventProjection().groups;
  assert.deepEqual(
    Array.from(groups, (group) => [group.start, group.end]),
    [
      [at(20), at(20)],
      [at(100), at(100)],
    ],
  );
  assert.match(
    panel.historyInfusionEndMarkup(groups[0].first.event),
    /Ende nicht belegt/,
  );
  gang.infusions = [{ source_event_id: "first", end: end(30) }];
  assert.equal(panel.historyEventProjection().groups[0].end, at(30));
  gang.infusions[0].end = null;
  groups = panel.historyEventProjection().groups;
  assert.equal(groups[0].end, at(20));
  assert.equal(panel.historyCurrentSnap({ eventId: "first", time: at(30) }), null);
  assert.equal(groups[1].end, at(100));
});

test("an end-only update changes the history revision while original event starts stay fixed", () => {
  const { panel, gang, events } = fixture();
  const before = panel.updateHistoryTimelineRevision(panel.shown.session);
  const starts = JSON.stringify(events);
  gang.infusions[0].end = end(32);
  assert.ok(panel.updateHistoryTimelineRevision(panel.shown.session) > before);
  assert.equal(panel.historyEventProjection().groups[0].end, at(32));
  assert.equal(JSON.stringify(events), starts);
});

test("source-bound selection follows late, corrected and withdrawn ends while a manual range stays fixed", () => {
  const { panel, gang } = fixture();
  gang.infusions[0].end = null;
  panel.inspectHistoryEvent("first");
  assert.equal(panel.historyValueSelection.kind, "point");
  const window = [...panel.window];
  gang.infusions[0].end = end(30);
  panel.syncHistorySelection();
  assert.equal(panel.historyValueSelection.kind, "range");
  assert.equal(panel.historyValueSelection.end, at(30));
  gang.infusions[0].end = end(32);
  panel.syncHistorySelection();
  assert.equal(panel.historyValueSelection.end, at(32));
  gang.infusions[0].end = null;
  panel.syncHistorySelection();
  assert.equal(panel.historyValueSelection.kind, "point");
  assert.equal(panel.historyValueSelection.end, at(20));
  assert.deepEqual(Array.from(panel.window), window);
  panel.historyValueSelection = {
    identity: panel.historyValueIdentity(),
    kind: "range",
    start: at(10),
    end: at(40),
  };
  const manual = panel.historyValueSelection;
  gang.infusions[0].end = end(35);
  panel.syncHistorySelection();
  assert.equal(panel.historyValueSelection, manual);
});

test("visible door events without a complete cycle retain their original snap target", () => {
  const { panel, session } = fixture();
  panel.historyEventVisibility.set("door", true);
  session.timeline.processed.push({
    event_id: "orphan-door",
    session_id: "s",
    kind: "door_open",
    effective_at: iso(50),
  });
  const targets = panel.historyEventProjection().snapTargets;
  assert.equal(targets.filter((target) => target.eventId === "orphan-door").length, 1);
  assert.ok(
    targets.some(
      (target) => target.eventId === "orphan-door" && target.time === at(50),
    ),
  );
});

test("detail-only event selection uses the same visible source during initial and later synchronization", () => {
  const { panel, session } = fixture();
  panel.historyDetail = true;
  session.timeline.processed.push({
    event_id: "presence",
    session_id: "s",
    kind: "presence_confirmed",
    effective_at: iso(10),
  });
  panel.inspectHistoryEvent("presence");
  panel.syncHistorySelection();
  assert.deepEqual(Array.from(panel.historyValueSelection.eventIds), ["presence"]);
  assert.equal(panel.historyValueSelection.start, at(10));
});

test("withdrawal of the selected event clears its keyboard and hover consumers without resurrecting it", () => {
  const { panel, session } = fixture();
  panel.inspectHistoryEvent("first");
  panel.historyKeyboardPreview = true;
  panel.historyKeyboardTarget = {
    time: at(30),
    snap: panel.historyValueSelection.endSnap,
  };
  panel.historyHoverPreview = { time: at(20) };
  panel.pendingHover = panel.lastHistoryPointer = { time: at(20) };
  session.timeline.processed = session.timeline.processed.filter(
    (event) => event.event_id !== "first",
  );
  panel.syncHistorySelection();
  assert.equal(panel.historyValueSelection, null);
  assert.equal(panel.historyKeyboardTime, null);
  assert.equal(panel.historyKeyboardTarget, null);
  assert.equal(panel.historyKeyboardPreview, false);
  assert.equal(panel.historyHoverPreview, null);
  assert.equal(panel.pendingHover, null);
  assert.equal(panel.lastHistoryPointer, null);
  assert.equal(panel.historyInspectedEvent(), null);
});
