"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

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
      (this.listeners.get(type) || []).filter((candidate) => candidate !== callback),
    );
  }
  emit(type) {
    for (const callback of this.listeners.get(type) || []) callback({ type });
  }
  listenerCount() {
    return [...this.listeners.values()].reduce(
      (total, values) => total + values.length,
      0,
    );
  }
}

class Node extends EventTarget {
  constructor(document, rect = {}) {
    super();
    this.ownerDocument = document;
    this.parentNode = null;
    this.host = null;
    this.rect = { left: 0, top: 0, width: 1200, height: 480, ...rect };
    this.children = [];
    this.style = {};
    this.hidden = false;
    this.attributes = new Map();
    this.reads = 0;
    this.writes = [];
    this._textContent = "";
  }
  append(...nodes) {
    this.children.push(...nodes);
    for (const node of nodes) node.parentNode = this;
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  get textContent() {
    return this._textContent;
  }
  set textContent(value) {
    this.ownerDocument.trace?.push("write");
    this.writes.push(["text", value]);
    this._textContent = String(value);
  }
  getBoundingClientRect() {
    this.ownerDocument.trace?.push("read");
    this.reads++;
    return { ...this.rect };
  }
  setAttribute(name, value) {
    this.writes.push(["attribute", name, value]);
    this.attributes.set(name, value);
  }
  getRootNode() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node;
  }
}

class ResizeObserver {
  constructor(callback) {
    this.callback = callback;
    this.nodes = [];
    ResizeObserver.instances.push(this);
  }
  observe(node) {
    this.nodes.push(node);
  }
  disconnect() {
    this.disconnected = true;
  }
}
ResizeObserver.instances = [];

const makeEnvironment = () => {
  const window = new EventTarget();
  window.devicePixelRatio = 2;
  window.visualViewport = new EventTarget();
  window.ResizeObserver = ResizeObserver;
  const media = [];
  window.matchMedia = (query) => {
    const target = new EventTarget();
    target.query = query;
    media.push(target);
    return target;
  };
  const document = {
    defaultView: window,
    trace: [],
    createElement: () => new Node(document),
  };
  const host = new Node(document);
  const shadow = new Node(document);
  shadow.host = host;
  const scroller = new Node(document);
  scroller.parentNode = shadow;
  const surface = new Node(document, { left: 50, top: 70, width: 600, height: 240 });
  surface.parentNode = scroller;
  const wrap = new Node(document, { left: 40, top: 60, width: 620, height: 260 });
  const tooltip = new Node(document);
  const cursor = new Node(document);
  const overview = new Node(document, { left: 20, top: 400, width: 600, height: 23 });
  overview.parentNode = scroller;
  return {
    window,
    document,
    host,
    shadow,
    scroller,
    surface,
    wrap,
    tooltip,
    cursor,
    overview,
    media,
  };
};

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const load = () => {
  const context = {
    ResizeObserver,
    Date,
    Number,
    Math,
    String,
    Map,
    Set,
    Intl,
    HTMLElement: class {},
    customElements: { get: () => null, define() {} },
  };
  vm.runInNewContext(
    `${source}\nglobalThis.HistoryInteraction = HistoryInteraction;`,
    context,
  );
  return context.HistoryInteraction;
};

const panel = (calls) => ({
  window: [0, 1000],
  positions: new Set(["upper", "lower"]),
  historyDetail: true,
  shown: { session: { measurement_ttl_seconds: 2 } },
  state: {
    measurement_ttl_seconds: 1,
    configuration: { parameters: { sensor_timeout_seconds: 1 } },
  },
  scheduleHistoryRender: (reason) => calls.push(reason),
  nearestMeasurement: (position, quantity) =>
    position === "upper" && quantity === "temperature"
      ? {
          time: 500,
          value: 0,
          source: {
            raw_value: 0,
            received_at: "2032-01-01T08:00:00.987654Z",
            measured_at: "2032-01-01T08:00:00.123456Z",
          },
        }
      : null,
});

test("coordinates use the cached rectangles and logical chart sizes", () => {
  const environment = makeEnvironment();
  const HistoryInteraction = load(environment);
  const interaction = HistoryInteraction(
    panel([]),
    environment.surface,
    environment.wrap,
    environment.tooltip,
    environment.cursor,
    environment.overview,
  );
  const geometry = interaction.readGeometry();
  assert.equal(geometry.canvas.width, 1200);
  assert.equal(geometry.canvas.height, 480);
  assert.equal(geometry.canvas.dpr, 2);
  assert.equal(geometry.canvas.cssWidth, 600);
  assert.equal(geometry.canvas.cssHeight, 240);
  const chartPoint = interaction.coordinates(environment.surface, 350, 190);
  assert.equal(chartPoint.x, 600);
  assert.equal(chartPoint.y, 240);
  const overviewPoint = interaction.coordinates(environment.overview, 320, 411.5);
  assert.equal(overviewPoint.x, 600);
  assert.equal(overviewPoint.y, 23);
  const reads = environment.surface.reads;
  interaction.coordinates(environment.surface, 350, 190);
  assert.equal(
    environment.surface.reads,
    reads,
    "valid geometry must not force another layout read",
  );
});

test("hover uses the shared transform and does not read layout after root writes", () => {
  const environment = makeEnvironment();
  const HistoryInteraction = load(environment);
  const interaction = HistoryInteraction(
    panel([]),
    environment.surface,
    environment.wrap,
    environment.tooltip,
    environment.cursor,
    environment.overview,
  );
  const result = interaction.hover(
    { clientX: 350, clientY: 170 },
    { start: 1000, end: 3000, left: 100, right: 1100 },
  );
  assert.equal(
    Math.round(result.time),
    2000,
    "hover uses the chart model rather than panel.window",
  );
  assert.equal(
    environment.tooltip.children.length,
    4,
    "tooltip content is created at construction",
  );
  const descendants = (node) => [node, ...node.children.flatMap(descendants)];
  const writes = descendants(environment.tooltip).flatMap((node) => node.writes);
  assert.ok(writes.some(([kind, text]) => kind === "text" && text === "0 °C"));
  assert.ok(
    writes.every(
      ([kind, text]) =>
        kind !== "text" || !/Empfangen|Gemessen|\.987654|\.123456/.test(text),
    ),
    "value rows do not repeat the source timestamps",
  );
  assert.equal(
    environment.tooltip.style.transform,
    undefined,
    "embedded values must not follow the pointer over the chart",
  );
  assert.equal(environment.surface.reads, 1, "hover reuses the constructor read phase");
  assert.ok(
    environment.document.trace.indexOf("write") >
      environment.document.trace.lastIndexOf("read"),
    "the input turn completes all layout reads before its tooltip writes",
  );
  interaction.invalidateGeometry("gesture", false);
  const readsBeforeDirtyHover = environment.surface.reads;
  interaction.hover(
    { clientX: 350, clientY: 170 },
    { start: 1000, end: 3000, left: 100, right: 1100 },
  );
  assert.equal(
    environment.tooltip.children.length,
    4,
    "second hover keeps the same tooltip nodes",
  );
  assert.equal(
    environment.surface.reads,
    readsBeforeDirtyHover,
    "a dirty hover after root writes stays on the cached rectangle",
  );
  assert.equal(environment.cursor.attributes.get("visibility"), "visible");
});

test("scroll across a shadow boundary, viewport resize, and DPR changes invalidate once", () => {
  const environment = makeEnvironment();
  const calls = [];
  const HistoryInteraction = load(environment);
  const interaction = HistoryInteraction(
    panel(calls),
    environment.surface,
    environment.wrap,
    environment.tooltip,
    environment.cursor,
    environment.overview,
  );
  interaction.readGeometry();
  environment.scroller.emit("scroll");
  assert.deepEqual(calls, ["scroll"]);
  interaction.readGeometry();
  environment.shadow.emit("scroll");
  assert.deepEqual(calls, ["scroll", "scroll"]);
  interaction.readGeometry();
  environment.window.visualViewport.emit("resize");
  assert.deepEqual(calls, ["scroll", "scroll", "viewport-resize"]);
  interaction.readGeometry();
  const firstMedia = environment.media.at(-1);
  environment.window.devicePixelRatio = 3;
  firstMedia.emit("change");
  assert.equal(calls.at(-1), "pixelratio");
  assert.match(environment.media.at(-1).query, /3dppx/);
  assert.equal(interaction.readGeometry().canvas.dpr, 3);
});

test("dispose removes every listener, observer, and media subscription", () => {
  const environment = makeEnvironment();
  const calls = [];
  const HistoryInteraction = load(environment);
  const interaction = HistoryInteraction(
    panel(calls),
    environment.surface,
    environment.wrap,
    environment.tooltip,
    environment.cursor,
    environment.overview,
  );
  const observer = ResizeObserver.instances.at(-1);
  interaction.dispose();
  assert.equal(observer.disconnected, true);
  assert.equal(environment.scroller.listenerCount(), 0);
  assert.equal(environment.shadow.listenerCount(), 0);
  assert.equal(environment.window.listenerCount(), 0);
  assert.equal(environment.window.visualViewport.listenerCount(), 0);
  assert.equal(environment.media.at(-1).listenerCount(), 0);
  environment.scroller.emit("scroll");
  assert.deepEqual(calls, []);
});

test("slotted content subscribes to scrolling ancestors within its assigned shadow tree", () => {
  const environment = makeEnvironment(),
    calls = [];
  const slot = new Node(environment.document),
    slottedScroller = new Node(environment.document);
  slot.parentNode = slottedScroller;
  slottedScroller.parentNode = environment.shadow;
  environment.surface.assignedSlot = slot;
  const interaction = load()(
    panel(calls),
    environment.surface,
    environment.wrap,
    environment.tooltip,
    environment.cursor,
    environment.overview,
  );
  slottedScroller.emit("scroll");
  assert.deepEqual(calls, ["scroll"]);
  interaction.dispose();
  assert.equal(slottedScroller.listenerCount(), 0);
});
