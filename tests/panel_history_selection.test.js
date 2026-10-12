"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const { panelFixture, parameterValues } = require("./catalog_fixture.cjs");

class EventTarget {
  constructor() {
    this.listeners = new Map();
  }
  addEventListener(type, callback) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), callback]);
  }
  removeEventListener(type, callback) {
    this.listeners.set(
      type,
      (this.listeners.get(type) || []).filter((entry) => entry !== callback),
    );
  }
  dispatch(type, event = {}) {
    for (const callback of this.listeners.get(type) || []) callback(event);
  }
}

class Node extends EventTarget {
  constructor(document, name, rect) {
    super();
    this.ownerDocument = document;
    this.name = name;
    this.rect = rect;
    this.children = [];
    this.style = {};
    this.attributes = new Map();
    this.dataset = {};
    this.hidden = false;
    this.text = "";
  }
  append(...nodes) {
    for (const node of nodes) node.remove();
    this.children.push(...nodes);
    for (const node of nodes) node.parentNode = this;
  }
  prepend(...nodes) {
    for (const node of nodes) node.remove();
    this.children.unshift(...nodes);
    for (const node of nodes) node.parentNode = this;
  }
  insertBefore(node, reference) {
    node.remove();
    const index = this.children.indexOf(reference);
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    node.parentNode = this;
  }
  remove() {
    if (this.parentNode)
      this.parentNode.children = this.parentNode.children.filter(
        (node) => node !== this,
      );
    this.parentNode = null;
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.text = "";
    this.append(...nodes);
  }
  get textContent() {
    return this.text || this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this.text = String(value);
  }
  getBoundingClientRect() {
    return { ...this.rect };
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
  getRootNode() {
    return this.parentNode?.getRootNode() || this;
  }
  closest(selector) {
    return selector === "svg.session-chart" && this.name === "svg" ? this : null;
  }
  setPointerCapture() {}
  releasePointerCapture() {}
}

const context = {
  HTMLElement: class {},
  customElements: {
    get: () => null,
    define: (_, value) => (context.Panel = value),
  },
  setTimeout,
  clearTimeout,
};
vm.runInNewContext(
  `${fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8")}\nglobalThis.HistoryInteraction = HistoryInteraction;`,
  context,
);

const base = Date.UTC(2032, 0, 1);
const at = (second) => base + second * 1000;
const iso = (second) => new Date(at(second)).toISOString();
const interval = (begin, end, phase = "bereit") => ({
  started_at: iso(begin),
  ended_at: iso(end),
  phase,
});
const record = (second, value) => ({
  kind: "measurement",
  payload: {
    position: "upper",
    quantity: "temperature",
    value,
    received_at: iso(second),
  },
});
const selectionBounds = (panel) => {
  const selection = panel.historyValueSelection;
  return selection && [selection.kind, selection.start, selection.end];
};
const visibleText = (node) =>
  node.hidden ? "" : node.text || node.children.map(visibleText).join("");

const makeFixture = (catalogOverrides = {}, scale = 1) => {
  const panel = Object.assign(panelFixture(context.Panel), {
    entry: "entry",
    state: {
      now: iso(100),
      configuration: { parameters: parameterValues() },
      permissions: { admin: true },
    },
    shown: {
      session: {
        configuration: { parameters: parameterValues() },
        timeline: {
          session_id: "selection-session",
          session_started_at: iso(0),
          processed: [],
        },
      },
      records: [record(20, 70), record(60, 80)],
      phase_projection: { intervals: [] },
    },
    positions: new Set(["upper"]),
    chartPointers: new Map(),
    historyValueSelection: null,
    shadowRoot: { querySelectorAll: () => [] },
    $: () => null,
    scheduleHistoryRender() {},
    drawHistory() {},
    message() {},
  });
  for (const [key, value] of Object.entries(catalogOverrides))
    panel.panel.config.appearance_catalog.preferences[key].default = value;
  panel.ensureHistoryWindow();
  panel.historyIndex(panel.shown.records);
  const plot = panel.historyGeometry();
  const rect = {
    left: 30,
    top: 50,
    width: plot.width * scale,
    height: plot.height * scale,
  };
  const view = Object.assign(new EventTarget(), { devicePixelRatio: 1 });
  const document = {
    defaultView: view,
    createElement: (name) => new Node(document, name, rect),
  };
  const surface = new Node(document, "svg", rect);
  const tooltip = new Node(document, "tooltip", rect);
  const cursor = new Node(document, "line", rect);
  const overview = new Node(document, "overview", rect);
  const interaction = context.HistoryInteraction(
    panel,
    surface,
    surface,
    tooltip,
    cursor,
    overview,
  );
  const chart = {
    identity: panel.historyValueIdentity(),
    prepared: new Map(),
    domain: panel.historyDomain(),
    surface,
    interaction,
  };
  panel.historyChart = chart;
  const refreshModel = () =>
    (chart.model = panel.historyModel(chart, panel.shown.session, rect.width));
  refreshModel();
  const point = (time, xOffset = 0, yOffset = 0, pointerId = 1) => ({
    svg: surface,
    target: surface,
    pointerId,
    pointerType: "touch",
    clientX: rect.left + chart.model.x(time) * scale + xOffset,
    clientY: rect.top + ((plot.top + plot.bottom) / 2) * scale + yOffset,
    preventDefault() {},
  });
  return {
    panel,
    interaction,
    chart,
    surface,
    overview,
    tooltip,
    cursor,
    view,
    point,
    refreshModel,
  };
};

test("time selections cycle point, normalized range, new point and keep equal endpoints a point", () => {
  const { panel } = makeFixture();
  panel.selectHistoryTime(at(60));
  assert.deepEqual(selectionBounds(panel), ["point", at(60), at(60)]);
  assert.equal(panel.historyValueSelection.identity, panel.historyValueIdentity());
  panel.selectHistoryTime(at(20));
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
  panel.selectHistoryTime(at(40));
  assert.deepEqual(selectionBounds(panel), ["point", at(40), at(40)]);
  panel.selectHistoryTime(at(40));
  assert.deepEqual(selectionBounds(panel), ["point", at(40), at(40)]);
  panel.clearHistorySelection();
  assert.equal(panel.historyValueSelection, null);
});

test("range phase names require containment in a single real projected interval", () => {
  const { panel, tooltip } = makeFixture({ history_phase_snap_radius_px: 0 });
  const heating = interval(10, 30, "aufheizen");
  const ready = interval(40, 70);
  panel.shown.phase_projection.intervals = [heating, ready];
  const original = JSON.stringify(panel.shown.phase_projection);
  const select = (begin, end) => {
    panel.clearHistorySelection();
    panel.selectHistoryTime(at(begin));
    panel.selectHistoryTime(at(end));
  };
  for (const [begin, end] of [
    [10, 30],
    [11, 29],
    [40, 70],
  ]) {
    select(begin, end);
    assert.equal(panel.historySelectionPhase(), begin < 30 ? heating : ready);
    assert.match(
      tooltip.children[0].textContent,
      begin < 30 ? /^Aufheizen ·/ : /^Bereit ·/,
    );
  }
  for (const [begin, end] of [
    [9, 30],
    [10, 31],
    [20, 50],
    [31, 39],
    [70, 80],
  ]) {
    select(begin, end);
    assert.equal(panel.historySelectionPhase(), null);
    assert.match(tooltip.children[0].textContent, /^Ausgewählter Zeitraum ·/);
  }
  panel.shown.phase_projection.intervals = [
    interval(10, 20, "aufheizen"),
    interval(20, 30, "aufheizen"),
  ];
  select(10, 30);
  assert.equal(
    panel.historySelectionPhase(),
    null,
    "separate intervals are not merged merely because their phase names match",
  );
  assert.match(tooltip.children[0].textContent, /^Ausgewählter Zeitraum ·/);
  panel.shown.phase_projection.intervals = [
    { started_at: iso(10), phase: "aufheizen" },
    { ended_at: iso(70), phase: "bereit" },
  ];
  select(20, 60);
  assert.equal(
    panel.historySelectionPhase(),
    null,
    "missing real boundaries are not inferred from the viewport",
  );
  assert.match(tooltip.children[0].textContent, /^Ausgewählter Zeitraum ·/);
  panel.shown.phase_projection.intervals = [heating, ready];
  panel.clearHistorySelection();
  panel.selectHistoryTime(at(70));
  assert.equal(
    panel.historySelectionPhase(),
    ready,
    "a point preserves the existing final phase endpoint contract",
  );
  assert.equal(JSON.stringify(panel.shown.phase_projection), original);
});

test("selection snippets include their beginning and exclude their end exactly once", () => {
  const { panel } = makeFixture({ history_phase_snap_radius_px: 0 });
  panel.shown.session.timeline.processed = [
    { event_id: "end", kind: "infusion", effective_at: iso(60) },
    { event_id: "middle", kind: "door_close", effective_at: iso(40) },
    { event_id: "begin", kind: "door_open", effective_at: iso(20) },
    { event_id: "before", kind: "infusion", effective_at: iso(19.999) },
  ];
  const original = JSON.stringify(panel.shown);
  panel.selectHistoryTime(at(20));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["begin"],
  );
  panel.selectHistoryTime(at(60));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["begin", "middle"],
  );
  panel.clearHistorySelection();
  panel.selectHistoryTime(at(60));
  panel.selectHistoryTime(at(80));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["end"],
  );
  assert.equal(
    JSON.stringify(panel.shown),
    original,
    "selection never rewrites archived events or phase evidence",
  );
});

test("phase snapping uses the catalogue radius in CSS pixels at its inclusive boundary", () => {
  for (const scale of [1, 0.5]) {
    const { panel, interaction, chart, refreshModel } = makeFixture({}, scale);
    const plot = panel.historyGeometry();
    // One logical pixel is one second; the independent screen-pixel offsets
    // below therefore have exact millisecond boundaries even at half width.
    panel.window = [base, base + (plot.right - plot.left) * 1000];
    const model = refreshModel();
    panel.shown.phase_projection.intervals = [interval(100, 300)];
    const definition =
      panel.panel.config.appearance_catalog.preferences.history_phase_snap_radius_px;
    for (const radius of [definition.default, definition.default + definition.step]) {
      definition.default = radius;
      const millisecondsPerCssPixel = 1000 / scale;
      for (const boundary of [at(100), at(300)])
        for (const sign of [-1, 1]) {
          for (const offset of [radius - 1, radius]) {
            const time = boundary + sign * offset * millisecondsPerCssPixel;
            assert.equal(
              panel.historySnapTime(time, model, interaction.geometry),
              boundary,
            );
          }
          const outside = boundary + sign * (radius + 1) * millisecondsPerCssPixel;
          assert.equal(
            panel.historySnapTime(outside, model, interaction.geometry),
            outside,
          );
        }
    }
    definition.default = 0;
    const beside = at(100) + 1000;
    assert.equal(panel.historySnapTime(beside, model, interaction.geometry), beside);
    panel.shown.phase_projection.intervals = [];
    definition.default = definition.maximum;
    assert.equal(
      panel.historySnapTime(beside, model, interaction.geometry),
      beside,
      "unprojected boundaries never attract a selection",
    );
    assert.equal(chart.model, model);
  }
});

test("pinned boundary values survive hover, pointer leave, viewport changes and same-session append", () => {
  const { panel, interaction, tooltip, point, refreshModel } = makeFixture();
  panel.selectHistoryTime(at(20));
  panel.selectHistoryTime(at(60));
  const selected = panel.historyValueSelection;
  const text = visibleText(tooltip);
  assert.match(text, /Beginn/);
  assert.match(text, /Ende/);
  assert.match(text, /70 °C/);
  assert.match(text, /80 °C/);
  interaction.hover(point(at(90)));
  interaction.hide();
  assert.equal(tooltip.hidden, false);
  assert.equal(visibleText(tooltip), text);
  panel.window = [at(80), at(100)];
  interaction.refreshSelection(refreshModel());
  assert.equal(
    visibleText(tooltip),
    text,
    "both pinned endpoints remain readable outside the viewport",
  );
  panel.state.now = iso(200);
  panel.shown.records.push(record(120, 90));
  panel.historyIndex(panel.shown.records);
  panel.syncHistoryValueIdentity();
  interaction.refreshSelection(refreshModel());
  assert.equal(panel.historyValueSelection, selected);
  assert.equal(visibleText(tooltip), text);
});

test("the displayed session identity clears an old pin before showing another session", () => {
  const { panel, tooltip } = makeFixture();
  panel.selectHistoryTime(at(20));
  panel.shown = {
    ...panel.shown,
    session: {
      ...panel.shown.session,
      timeline: { ...panel.shown.session.timeline, session_id: "next-session" },
    },
  };
  panel.syncHistoryValueIdentity();
  assert.equal(panel.historyValueSelection, null);
  assert.equal(panel.historyKeyboardTime, null);
  assert.equal(tooltip.hidden, true);
  panel.selectHistoryTime(at(60));
  assert.deepEqual(selectionBounds(panel), ["point", at(60), at(60)]);
  assert.equal(panel.historyValueSelection.identity, "entry:next-session");
  panel.entry = "another-entry";
  panel.syncHistoryValueIdentity();
  assert.equal(panel.historyValueSelection, null);
});

test("event inspection remains separate from the pinned time selection", () => {
  const { panel, tooltip } = makeFixture();
  panel.shown.session.timeline.processed = [
    { event_id: "infusion", kind: "infusion", effective_at: iso(90) },
  ];
  panel.selectHistoryTime(at(20));
  const selection = panel.historyValueSelection;
  panel.inspectHistoryEvent("infusion");
  assert.equal(panel.historyValueSelection, selection);
  assert.equal(panel.historyInspectedEvent().event_id, "infusion");
  assert.match(visibleText(tooltip), /Aufguss/);
  panel.selectHistoryTime(at(60));
  assert.equal(panel.historyInspectedEvent().event_id, "infusion");
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
});

test("pointer taps keep the starting time transform during incoming session growth", () => {
  const { panel, chart, surface, point, refreshModel } = makeFixture();
  const start = point(at(20));
  panel.beginChartPointer(start, surface);
  const frozenDomain = Array.from(panel.historyInputSnapshot.domain);
  const frozenModel = panel.historyInputSnapshot.model;
  panel.state.now = iso(400);
  panel.shown.records.push(record(200, 90));
  panel.historyIndex(panel.shown.records);
  panel.setHistoryWindow(at(10), at(70));
  refreshModel();
  assert.deepEqual(Array.from(panel.historyInputSnapshot.domain), frozenDomain);
  assert.equal(frozenModel.start, at(0));
  assert.equal(frozenModel.end, at(100));
  panel.endChartPointer({ ...start, type: "pointerup" });
  assert.deepEqual(selectionBounds(panel), ["point", at(20), at(20)]);
  assert.equal(panel.historyInputSnapshot, null);
  assert.deepEqual(Array.from(chart.domain), [at(0), at(400)]);
});

test("a frozen overview domain and its owning pointer govern a resize through session growth", () => {
  const { panel, chart, overview, refreshModel } = makeFixture();
  panel.setHistoryWindow(at(20), at(60));
  refreshModel();
  const rect = overview.getBoundingClientRect();
  const plot = panel.historyGeometry();
  const input = (fraction, pointerId = 1) => ({
    pointerId,
    target: { dataset: { historyHandle: "end" }, closest: () => null },
    clientX: rect.left + plot.left + fraction * (plot.right - plot.left),
    preventDefault() {},
  });
  panel.beginHistoryGesture(input(0.6), overview);
  const gesture = panel.historyGesture;
  panel.state.now = iso(400);
  chart.domain = panel.historyDomain();
  panel.updateHistoryGesture(input(0.8, 99));
  panel.endHistoryGesture(input(0.8, 99));
  assert.equal(
    panel.historyGesture,
    gesture,
    "another pointer cannot complete the resize",
  );
  assert.deepEqual(Array.from(panel.window), [at(20), at(60)]);
  panel.updateHistoryGesture(input(0.8));
  assert.deepEqual(Array.from(panel.window), [at(20), at(80)]);
  panel.endHistoryGesture({ ...input(0.8), type: "pointercancel" });
  assert.equal(panel.historyGesture, null);
  assert.equal(panel.historyInputSnapshot, null);
  assert.equal(panel.historyValueSelection, null);
});

test("foreign pointers, cancellation, scrolling, motion and pinch cannot become a tap selection", () => {
  for (const cancelType of ["pointercancel", "lostpointercapture"]) {
    const { panel, surface, point } = makeFixture();
    const start = point(at(50));
    panel.beginChartPointer(start, surface);
    panel.endChartPointer({ ...start, pointerId: 99, type: "pointerup" });
    panel.endChartPointer({ ...start, pointerId: 99, type: cancelType }, true);
    assert.equal(panel.chartPointers.size, 1);
    assert.equal(panel.historyValueSelection, null);
    panel.endChartPointer({ ...start, type: cancelType }, true);
    assert.equal(panel.chartPointers.size, 0);
    assert.equal(panel.historyValueSelection, null);
  }
  for (const movement of ["beyond", "scroll", "pinch"]) {
    const { panel, surface, view, point } = makeFixture();
    const start = point(at(50));
    panel.beginChartPointer(start, surface);
    if (movement === "beyond") {
      panel.updateChartPointer({
        ...start,
        clientX: start.clientX + panel.preference("history_tap_movement_px") + 1,
      });
      panel.updateChartPointer(start);
    }
    if (movement === "scroll") view.dispatch("scroll");
    if (movement === "pinch") {
      const second = point(at(70), 0, 0, 2);
      panel.beginChartPointer(second, surface);
      panel.updateChartPointer({ ...second, clientX: second.clientX + 20 });
      panel.pinchZoom(surface);
      panel.endChartPointer({ ...second, type: "pointerup" });
    }
    panel.endChartPointer({ ...start, type: "pointerup" });
    assert.equal(panel.historyValueSelection, null, movement);
    assert.equal(panel.chartPointers.size, 0);
    assert.equal(panel.historyInputSnapshot, null);
  }
  const { panel, surface, point } = makeFixture();
  const start = point(at(50));
  panel.beginChartPointer(start, surface);
  const edge = {
    ...start,
    clientY: start.clientY + panel.preference("history_tap_movement_px"),
  };
  panel.updateChartPointer(edge);
  panel.endChartPointer({ ...edge, type: "pointerup" });
  assert.deepEqual(
    selectionBounds(panel),
    ["point", at(50), at(50)],
    "the inclusive movement boundary remains a tap",
  );
});

test("keyboard selection moves by its catalogue fraction, commits boundaries and clears with Escape", () => {
  const { panel, tooltip, cursor, chart, interaction } = makeFixture({
    history_phase_snap_radius_px: 0,
  });
  const definition =
    panel.panel.config.appearance_catalog.preferences
      .history_keyboard_selection_fraction;
  definition.default += definition.step;
  const key = (name, shiftKey = false) => {
    let prevented = false;
    panel.keyHistorySelection({
      key: name,
      shiftKey,
      preventDefault: () => (prevented = true),
    });
    return prevented;
  };
  assert.equal(key("Enter"), true);
  const middle = (panel.window[0] + panel.window[1]) / 2;
  assert.deepEqual(selectionBounds(panel), ["point", middle, middle]);
  const pin = panel.historyValueSelection;
  const pinnedText = visibleText(tooltip);
  assert.equal(
    key("ArrowRight"),
    false,
    "plain arrow navigation keeps its existing viewport meaning",
  );
  assert.deepEqual(selectionBounds(panel), ["point", middle, middle]);
  assert.equal(key("ArrowRight", true), true);
  const next = middle + (panel.window[1] - panel.window[0]) * definition.default;
  assert.equal(panel.historyKeyboardTime, next);
  assert.equal(
    panel.historyValueSelection,
    pin,
    "previewing the range end retains the original pin",
  );
  assert.equal(visibleText(tooltip), pinnedText);
  assert.equal(cursor.getAttribute("visibility"), "visible");
  assert.equal(Number(cursor.getAttribute("x1")), chart.model.x(next));
  assert.equal(Number(cursor.getAttribute("x2")), chart.model.x(next));
  assert.notEqual(Number(cursor.getAttribute("x1")), chart.model.x(middle));
  interaction.refreshSelection(chart.model);
  assert.equal(
    cursor.getAttribute("visibility"),
    "visible",
    "an incoming render preserves the visible keyboard target",
  );
  assert.equal(Number(cursor.getAttribute("x1")), chart.model.x(next));
  assert.equal(panel.historyValueSelection, pin);
  assert.equal(key(" "), true);
  assert.deepEqual(selectionBounds(panel), ["range", middle, next]);
  assert.equal(
    cursor.getAttribute("visibility"),
    "hidden",
    "committing the end replaces the preview with the selected range",
  );
  assert.equal(key("Escape"), true);
  assert.equal(panel.historyValueSelection, null);
  assert.equal(tooltip.hidden, true);
  panel.selectHistoryTime(panel.window[0]);
  key("ArrowLeft", true);
  key("Enter");
  assert.deepEqual(selectionBounds(panel), ["point", panel.window[0], panel.window[0]]);
});
