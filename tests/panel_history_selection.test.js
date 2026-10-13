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
  removeAttribute(name) {
    this.attributes.delete(name);
  }
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(selector.startsWith(".") &&
      child.className?.split(/\s+/).includes(selector.slice(1))
        ? [child]
        : []),
      ...child.querySelectorAll(selector),
    ]);
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
  `${fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8")}\nglobalThis.HistoryInteraction = HistoryInteraction; globalThis.HistoryChart = HistoryChart;`,
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
  const nodes = new Map([
    ["svg.session-chart", surface],
    ["#tooltip", tooltip],
    ["#cursor", cursor],
    ["#history-overview svg", overview],
  ]);
  for (const selector of [
    "[data-history-selection]",
    "[data-history-selection-band]",
    "[data-history-selection-start]",
    "[data-history-selection-end]",
    "[data-history-selection-start-snap]",
    "[data-history-selection-end-snap]",
    "[data-history-snap-halo]",
    "[data-history-axes]",
    "[data-history-annotations]",
    "[data-history-events]",
    "[data-history-clip]",
    ".history-background",
    "#history-legends",
    "#history-zoom-value",
    "#history-event-inspection",
    '[data-action="clear-history-selection"]',
    '[data-action="history-to-selection"]',
  ])
    nodes.set(selector, new Node(document, "element", rect));
  const eventMarkers = new Map();
  const marker = (eventId) => {
    if (!eventMarkers.has(eventId)) {
      const node = new Node(document, "g", rect);
      node.dataset.historyEventId = eventId;
      eventMarkers.set(eventId, node);
    }
    return eventMarkers.get(eventId);
  };
  panel.$ = (selector) => nodes.get(selector) || null;
  panel.shadowRoot.querySelectorAll = (selector) =>
    selector === "[data-history-event-id]" ? [...eventMarkers.values()] : [];
  panel.updateMarkup = (selector, html) => {
    panel.$(selector).innerHTML = html;
  };
  const interaction = context.HistoryInteraction(
    panel,
    surface,
    surface,
    tooltip,
    cursor,
    overview,
  );
  const chart = Object.assign(Object.create(context.HistoryChart.prototype), {
    panel,
    identity: panel.historyValueIdentity(),
    prepared: new Map(),
    domain: panel.historyDomain(),
    surface,
    interaction,
    curves: { update() {} },
  });
  panel.historyChart = chart;
  const refreshModel = () =>
    (chart.model = panel.historyModel(chart, panel.shown.session, rect.width));
  refreshModel();
  panel.syncHistorySelection(chart.model);
  panel.syncHistoryEventInspection();
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
    marker,
    nodes,
  };
};

test("three plot clicks create a point, a normalized range and complete clearing; equal endpoints stay a point", () => {
  const { panel, surface, point, tooltip, nodes } = makeFixture();
  const click = (time) => {
    const input = { ...point(time), pointerType: "mouse", type: "pointerdown" };
    panel.beginChartPointer(input, surface);
    panel.endChartPointer({ ...input, type: "pointerup" });
  };
  click(at(60));
  assert.deepEqual(selectionBounds(panel), ["point", at(60), at(60)]);
  assert.equal(panel.historyValueSelection.identity, panel.historyValueIdentity());
  click(at(20));
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
  click(at(40));
  assert.equal(panel.historyValueSelection, null);
  assert.equal(tooltip.hidden, true);
  for (const selector of [
    "[data-history-selection]",
    "[data-history-selection-band]",
    "[data-history-selection-start]",
    "[data-history-selection-end]",
    "[data-history-selection-start-snap]",
    "[data-history-selection-end-snap]",
  ])
    assert.equal(nodes.get(selector).getAttribute("visibility"), "hidden");
  click(at(40));
  assert.deepEqual(selectionBounds(panel), ["point", at(40), at(40)]);
  click(at(40));
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
  const { panel, tooltip } = makeFixture({ history_phase_snap_radius_px: 0 });
  panel.shown.session.timeline.processed = [
    { event_id: "end", kind: "infusion", effective_at: iso(60) },
    { event_id: "middle", kind: "door_close", effective_at: iso(40) },
    { event_id: "begin", kind: "door_open", effective_at: iso(20) },
    { event_id: "before", kind: "infusion", effective_at: iso(19.999) },
    {
      event_id: "gang-begin",
      kind: "presence_confirmed",
      effective_at: iso(30),
      detected_at: iso(35),
    },
    { event_id: "control", kind: "operation_off", effective_at: iso(50) },
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
  assert.doesNotMatch(
    visibleText(tooltip),
    /Gangbeginn|Präsenz nach Türvorgang|Betrieb ausgeschaltet/,
    "range values omit recognition reasons and control events",
  );
  panel.clearHistorySelection();
  panel.selectHistoryTime(at(60));
  panel.selectHistoryTime(at(80));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["end"],
  );
  panel.clearHistorySelection();
  panel.selectHistoryTime(at(30));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["gang-begin"],
    "point inspection retains the original recognition event",
  );
  assert.match(visibleText(tooltip), /Gangbeginn · Präsenz nach Türvorgang/);
  panel.clearHistorySelection();
  panel.selectHistoryTime(at(50));
  assert.deepEqual(
    Array.from(panel.historySelectionEvents(), (event) => event.event_id),
    ["control"],
    "point inspection retains the original control event",
  );
  assert.match(visibleText(tooltip), /Betrieb ausgeschaltet/);
  assert.equal(
    JSON.stringify(panel.shown),
    original,
    "selection never rewrites archived events or phase evidence",
  );
});

test("phase snapping uses the catalogue radius in CSS pixels at its inclusive boundary", () => {
  for (const scale of [1, 0.5]) {
    const { panel, interaction, chart, surface, tooltip, cursor, nodes, point, refreshModel } = makeFixture({}, scale);
    const plot = panel.historyGeometry();
    // One logical pixel is one second; the independent screen-pixel offsets
    // below therefore have exact millisecond boundaries even at half width.
    panel.window = [base, base + (plot.right - plot.left) * 1000];
    const model = refreshModel();
    const phase = { ...interval(100, 300), complete: true };
    panel.shown.phase_projection.intervals = [phase];
    panel.state.now = phase.ended_at;
    const definition =
      panel.panel.config.appearance_catalog.preferences.history_phase_snap_radius_px;
    const originalRadius = definition.default;
    const liveEnd = Date.parse(phase.ended_at),
      besideLiveEnd = liveEnd - (originalRadius / 2) * (1000 / scale);
    assert.equal(panel.shown.session.ended_at ?? null, null);
    const liveTarget = panel.historySnapTarget(besideLiveEnd, model, interaction.geometry);
    assert.equal(liveTarget.time, besideLiveEnd,
      "complete evidence does not make the live read horizon a phase transition");
    assert.equal(liveTarget.snap, null);
    panel.shown.session.ended_at = phase.ended_at;
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
    panel.shown.phase_projection.intervals = [interval(100, 300)];
    definition.default = originalRadius + definition.step;
    const radius = definition.default;
    const nearStart = point(at(100), radius - definition.step);
    interaction.hover(nearStart, model);
    assert.equal(panel.historyHoverPreview.time, at(100));
    assert.equal(cursor.getAttribute("data-snap"), "true");
    assert.equal(cursor.getAttribute("visibility"), "visible");
    assert.equal(Number(cursor.getAttribute("x1")), model.x(at(100)));
    const previewHalo = nodes.get("[data-history-snap-halo]");
    assert.equal(previewHalo.getAttribute("visibility"), "visible");
    assert.equal(Number(previewHalo.getAttribute("cx")), model.x(at(100)));
    const snapHint = tooltip.querySelector(".history-tooltip-snap");
    assert.ok(snapHint && !snapHint.hidden, "the phase boundary is visible before clicking");
    assert.equal(visibleText(snapHint), `Phasengrenze · ${panel.tooltipWhen(at(100))}`);
    interaction.hover(point(at(100), radius + definition.step), model);
    assert.notEqual(cursor.getAttribute("data-snap"), "true");
    assert.equal(previewHalo.getAttribute("visibility"), "hidden");
    assert.ok(!snapHint.parentNode || snapHint.hidden, "moving beyond the radius removes snap feedback");
    const input = { ...nearStart, type: "pointerdown" };
    panel.beginChartPointer(input, surface);
    panel.endChartPointer({ ...input, type: "pointerup" });
    assert.deepEqual(selectionBounds(panel), ["point", at(100), at(100)]);
    const pin = panel.historyValueSelection;
    const heading = tooltip.children[0].textContent;
    const first = nodes.get("[data-history-selection-start]");
    assert.equal(first.getAttribute("data-snap"), "true");
    const selectedHalo = nodes.get("[data-history-selection-start-snap]");
    assert.equal(selectedHalo.getAttribute("visibility"), "visible");
    assert.equal(Number(selectedHalo.getAttribute("cx")), model.x(at(100)));
    interaction.hover(point(at(300), -radius + definition.step), model);
    assert.equal(panel.historyValueSelection, pin);
    assert.equal(tooltip.children[0].textContent, heading);
    assert.equal(cursor.getAttribute("data-snap"), "true");
    assert.equal(Number(cursor.getAttribute("x1")), model.x(at(300)));
    assert.equal(Number(previewHalo.getAttribute("cx")), model.x(at(300)));
    assert.ok(!tooltip.querySelector(".history-tooltip-snap").hidden);
    panel.shown.phase_projection.intervals = [];
    panel.syncHistorySelection(model);
    interaction.refreshHover(model);
    assert.equal(panel.historyValueSelection, pin);
    assert.deepEqual(selectionBounds(panel), ["point", at(100), at(100)]);
    assert.equal(panel.historyHoverPreview.time, at(300));
    assert.equal(first.getAttribute("visibility"), "visible");
    assert.equal(Number(first.getAttribute("x1")), model.x(at(100)));
    assert.notEqual(first.getAttribute("data-snap"), "true");
    assert.equal(selectedHalo.getAttribute("visibility"), "hidden");
    assert.equal(cursor.getAttribute("visibility"), "visible");
    assert.equal(Number(cursor.getAttribute("x1")), model.x(at(300)));
    assert.notEqual(cursor.getAttribute("data-snap"), "true");
    assert.equal(previewHalo.getAttribute("visibility"), "hidden");
    const correctedHint = tooltip.querySelector(".history-tooltip-snap");
    assert.ok(!correctedHint || correctedHint.hidden,
      "retracted phase boundaries lose their feedback without moving the selected times");
    panel.clearHistorySelection();
    assert.equal(panel.historyHoverPreview, null);
    assert.equal(cursor.getAttribute("visibility"), "hidden");
    assert.notEqual(cursor.getAttribute("data-snap"), "true");
    assert.notEqual(first.getAttribute("data-snap"), "true");
    assert.equal(first.getAttribute("visibility"), "hidden");
    assert.equal(selectedHalo.getAttribute("visibility"), "hidden");
    assert.equal(previewHalo.getAttribute("visibility"), "hidden");
    interaction.hover(point(at(200)), model);
    assert.notEqual(cursor.getAttribute("data-snap"), "true");
    const clearedHint = tooltip.querySelector(".history-tooltip-snap");
    assert.ok(!clearedHint || clearedHint.hidden, "a later plain hover cannot restore old snap feedback");
  }
});

test("hover and pinned boundaries retain absolute times through zoom, pan and same-session append", () => {
  const { panel, interaction, chart, surface, tooltip, cursor, point, nodes } = makeFixture();
  const render = (reason = "viewport") =>
    chart.render(new Set([reason]), panel.shown.session, []);
  render("status");
  interaction.hover(point(at(20)), chart.model);
  const hoverModel = chart.model;
  const initialCursorX = Number(cursor.getAttribute("x1"));
  assert.equal(panel.historyHoverPreview.time, at(20));
  panel.zoomAt(2, point(at(30)).clientX, surface);
  render();
  assert.notEqual(chart.model, hoverModel);
  assert.equal(panel.historyHoverPreview.time, at(20));
  assert.equal(cursor.getAttribute("visibility"), "visible");
  assert.equal(Number(cursor.getAttribute("x1")), chart.model.x(at(20)));
  assert.notEqual(Number(cursor.getAttribute("x1")), initialCursorX);
  assert.match(visibleText(tooltip), /70 °C/);
  panel.setHistoryWindow(at(30), at(90));
  render();
  assert.equal(panel.historyHoverPreview.time, at(20));
  assert.equal(tooltip.hidden, true);
  assert.equal(cursor.getAttribute("visibility"), "hidden");
  panel.setHistoryWindow(at(0), at(100));
  render();
  assert.equal(panel.historyHoverPreview.time, at(20));
  assert.equal(Number(cursor.getAttribute("x1")), chart.model.x(at(20)));
  assert.equal(cursor.getAttribute("visibility"), "visible");
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
  const initialSelectionModel = chart.model;
  const first = nodes.get("[data-history-selection-start]");
  const last = nodes.get("[data-history-selection-end]");
  const initialStartX = Number(first.getAttribute("x1"));
  const initialEndX = Number(last.getAttribute("x1"));
  panel.zoomAt(2, point(at(40)).clientX, surface);
  render();
  assert.notEqual(chart.model, initialSelectionModel);
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
  assert.equal(Number(first.getAttribute("x1")), chart.model.x(at(20)));
  assert.equal(Number(last.getAttribute("x1")), chart.model.x(at(60)));
  assert.notEqual(Number(first.getAttribute("x1")), initialStartX);
  assert.notEqual(Number(last.getAttribute("x1")), initialEndX);
  panel.setHistoryWindow(at(30), at(70));
  render();
  assert.equal(first.getAttribute("visibility"), "hidden");
  assert.equal(last.getAttribute("visibility"), "visible");
  assert.equal(Number(last.getAttribute("x1")), chart.model.x(at(60)));
  panel.setHistoryWindow(at(80), at(100));
  render();
  assert.equal(first.getAttribute("visibility"), "hidden");
  assert.equal(last.getAttribute("visibility"), "hidden");
  assert.equal(
    visibleText(tooltip),
    text,
    "both pinned endpoints remain readable outside the viewport",
  );
  panel.state.now = iso(200);
  panel.shown.records.push(record(120, 90));
  panel.historyIndex(panel.shown.records);
  panel.syncHistoryValueIdentity();
  render("archive");
  assert.equal(panel.historyValueSelection, selected);
  assert.equal(visibleText(tooltip), text);
  panel.setHistoryWindow(at(0), at(100));
  render();
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
  assert.equal(Number(first.getAttribute("x1")), initialStartX);
  assert.equal(Number(last.getAttribute("x1")), initialEndX);
  assert.equal(first.getAttribute("visibility"), "visible");
  assert.equal(last.getAttribute("visibility"), "visible");
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

test("event inspection remains separate from time selection and clears completely with or without a pin", () => {
  const { panel, tooltip, nodes, marker } = makeFixture();
  panel.shown.session.timeline.processed = [
    { event_id: "infusion", kind: "infusion", effective_at: iso(90) },
  ];
  const eventMarker = marker("infusion");
  const inspection = nodes.get("#history-event-inspection");
  const clear = nodes.get('[data-action="clear-history-selection"]');
  panel.selectHistoryTime(at(20));
  const selection = panel.historyValueSelection;
  panel.inspectHistoryEvent("infusion");
  assert.equal(panel.historyValueSelection, selection);
  assert.equal(panel.historyInspectedEvent().event_id, "infusion");
  assert.equal(eventMarker.getAttribute("aria-pressed"), "true");
  assert.equal(inspection.hidden, false);
  assert.match(visibleText(tooltip), /Aufguss/);
  panel.selectHistoryTime(at(60));
  assert.equal(panel.historyInspectedEvent().event_id, "infusion");
  assert.deepEqual(selectionBounds(panel), ["range", at(20), at(60)]);
  panel.clearHistorySelection();
  assert.equal(panel.historyValueSelection, null);
  assert.equal(panel.historyInspectedEvent(), null);
  assert.equal(eventMarker.getAttribute("aria-pressed"), "false");
  assert.equal(eventMarker.getAttribute("data-selected"), "false");
  assert.equal(inspection.hidden, true);
  assert.equal(inspection.textContent, "");
  assert.equal(clear.disabled, true);
  assert.equal(tooltip.hidden, true);
  panel.inspectHistoryEvent("infusion");
  assert.equal(panel.historyValueSelection, null);
  assert.equal(eventMarker.getAttribute("aria-pressed"), "true");
  assert.equal(clear.disabled, false, "an event inspection alone can be cleared");
  panel.clearHistorySelection();
  assert.equal(panel.historyInspectedEvent(), null);
  assert.equal(eventMarker.getAttribute("aria-pressed"), "false");
  assert.equal(inspection.hidden, true);
  assert.equal(clear.disabled, true);
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
