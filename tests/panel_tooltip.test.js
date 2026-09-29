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
      (this.listeners.get(type) || []).filter((entry) => entry !== callback),
    );
  }
}

class Node extends EventTarget {
  constructor(document, name, rect = {}) {
    super();
    this.ownerDocument = document;
    this.name = name;
    this.children = [];
    this.style = {};
    this.hidden = true;
    this.rect = { left: 0, top: 0, width: 1200, height: 480, ...rect };
    this.attributes = new Map();
    this.text = "";
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
  getRootNode() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node;
  }
}

const panelSource = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
// Freeze the previous timestamp functions only; the product interaction and
// its existing explicit text assertions remain the behavior under test.
const frozenTimestampSource =
  'const when = (v) =>\n  v\n    ? new Date(v).toLocaleString("de-DE", {\n        day: "2-digit",\n        month: "2-digit",\n        hour: "2-digit",\n        minute: "2-digit",\n        second: "2-digit",\n      })\n    : "\u2013";\nconst tooltipWhen = (v) => {\n  if (\n    (typeof v !== "string" && (typeof v !== "number" || !Number.isFinite(v))) ||\n    (typeof v === "string" && !v.trim())\n  )\n    return null;\n  const date = new Date(v);\n  if (!Number.isFinite(date.getTime())) return null;\n  const fraction =\n    typeof v === "string" ? v.match(/\\.(\\d+)(?:Z|[+-]\\d\\d:\\d\\d)?$/i)?.[1] : null;\n  return new Intl.DateTimeFormat("de-DE", {\n    day: "2-digit",\n    month: "2-digit",\n    hour: "2-digit",\n    minute: "2-digit",\n    second: "2-digit",\n    timeZoneName: "shortOffset",\n  })\n    .formatToParts(date)\n    .map((part) =>\n      part.type === "second" && fraction ? `${part.value}.${fraction}` : part.value,\n    )\n    .join("");\n};\nconst clock = (v) =>\n  new Date(v).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });\nconst localTimeZone = () => undefined;\n';
const frozenPanelSource =
  panelSource.slice(0, panelSource.indexOf("const timestampFormatterLimit")) +
  frozenTimestampSource +
  panelSource.slice(panelSource.indexOf("const num ="));

let Panel;
const loadHelper = (window, source = panelSource, intl = Intl) => {
  const context = {
    ...window,
    Date,
    Number,
    Math,
    String,
    Map,
    Set,
    Intl: intl,
    HTMLElement: class {},
    customElements: { get: () => null, define: (_, value) => (Panel = value) },
  };
  vm.runInNewContext(
    `${source}\nglobalThis.HistoryInteraction = HistoryInteraction; globalThis.timestampFunctions = { when, tooltipWhen, clock };`,
    context,
  );
  context.HistoryInteraction.timestampFunctions = context.timestampFunctions;
  return context.HistoryInteraction;
};

const measurement = (overrides = {}) => ({
  position: "upper",
  quantity: "temperature",
  value: 79.123456,
  raw_value: "79.123456",
  received_at: "2026-01-01T08:00:20.987654Z",
  measured_at: "2026-01-01T08:00:10.876543Z",
  ...overrides,
});

const inTimeZone = (timeZone, run) => {
  const previous = process.env.TZ;
  process.env.TZ = timeZone;
  try {
    return run();
  } finally {
    if (previous == null) delete process.env.TZ;
    else process.env.TZ = previous;
  }
};

const tooltipFor = (
  source,
  kind = "measurement",
  panelCode = panelSource,
  intl = Intl,
) => {
  const received =
    typeof source.received_at === "number"
      ? source.received_at
      : Date.parse(source.received_at);
  const window = new EventTarget();
  window.devicePixelRatio = 1;
  window.visualViewport = new EventTarget();
  const document = {
    defaultView: window,
    createElement: (name) => new Node(document, name),
  };
  const surface = new Node(document, "svg");
  const wrap = new Node(document, "div");
  const tooltip = new Node(document, "tooltip");
  const cursor = new Node(document, "line");
  const overview = new Node(document, "overview", { width: 1200, height: 46 });
  const interactionFactory = loadHelper(window, panelCode, intl);
  const panel = Object.assign(Object.create(Panel.prototype), {
    positions: new Set(["upper"]),
    window: [received - 30000, received + 30000],
    state: { configuration: { parameters: { sensor_timeout_seconds: 120 } } },
    shown: {
      session: { configuration: { parameters: { sensor_timeout_seconds: 120 } } },
      records: [{ kind, payload: source }],
    },
    historyDetail: true,
  });
  const interaction = interactionFactory(
    panel,
    surface,
    wrap,
    tooltip,
    cursor,
    overview,
  );
  interaction.hover({ svg: surface, clientX: 600, clientY: 100 });
  return { tooltip, interaction };
};

const trackingIntl = () => {
  let constructions = 0;
  return {
    intl: Object.assign(Object.create(Intl), {
      DateTimeFormat: function (...args) {
        constructions++;
        return new Intl.DateTimeFormat(...args);
      },
    }),
    get constructions() {
      return constructions;
    },
  };
};

test("persistent hover text preserves original value and both precise source times", () => {
  const { tooltip } = inTimeZone("Europe/Berlin", () => tooltipFor(measurement()));
  assert.match(tooltip.textContent, /Originalwert 79\.123456 °C/);
  assert.match(tooltip.textContent, /Empfangen .*09:00:20\.987654 GMT\+1/);
  assert.match(tooltip.textContent, /Gemessen .*09:00:10\.876543 GMT\+1/);
});

test("persistent hover only labels valid supplied measurement times", () => {
  for (const measured_at of [undefined, null, false, [], {}, "not-a-time"]) {
    const { tooltip } = tooltipFor(measurement({ measured_at }));
    assert.match(tooltip.textContent, /Empfangen/);
    assert.doesNotMatch(tooltip.textContent, /Gemessen/);
  }
});

test("raw zero, fallback text, and supplied markup stay textual", () => {
  const zero = tooltipFor(measurement({ value: 0, raw_value: 0 })).tooltip;
  const missing = tooltipFor(
    measurement({
      raw_value: undefined,
      value: 12.3456789,
      received_at: "2032-01-01T08:00:20.987654Z",
      measured_at: undefined,
    }),
    "source_snapshot",
  ).tooltip;
  const escaped = tooltipFor(measurement({ raw_value: '<raw&"value>' })).tooltip;
  assert.match(zero.textContent, /Originalwert 0 °C/);
  assert.match(
    missing.textContent,
    /Wert 12,345679 °C · kein Originalwert gespeichert/,
  );
  assert.doesNotMatch(missing.textContent, /Originalwert 12,345679 °C/);
  assert.match(escaped.textContent, /Originalwert <raw&"value> °C/);
  const names = (node) => [node.name, ...node.children.flatMap(names)];
  assert.deepEqual(names(escaped), [
    "tooltip",
    "strong",
    "div",
    "div",
    "span",
    "br",
    "span",
    "div",
    "span",
    "br",
    "span",
    "div",
    "span",
    "br",
    "span",
    "div",
    "span",
    "br",
    "span",
  ]);
  assert.equal(
    "innerHTML" in escaped,
    false,
    "raw text cannot create injected markup nodes",
  );
});

test("persistent hover retains source offsets across DST and browser-local zones", () => {
  const [beforeFallback, afterFallback] = inTimeZone("Europe/Berlin", () => [
    tooltipFor(measurement({ received_at: "2026-10-25T00:30:00.123456Z" })).tooltip
      .textContent,
    tooltipFor(measurement({ received_at: "2026-10-25T01:30:00.123456Z" })).tooltip
      .textContent,
  ]);
  assert.match(beforeFallback, /02:30:00\.123456 GMT\+2/);
  assert.match(afterFallback, /02:30:00\.123456 GMT\+1/);
  const utc = inTimeZone("UTC", () => tooltipFor(measurement()).tooltip.textContent);
  const newYork = inTimeZone(
    "America/New_York",
    () => tooltipFor(measurement()).tooltip.textContent,
  );
  assert.match(utc, /08:00:20\.987654 GMT/);
  assert.match(newYork, /03:00:20\.987654 GMT-5/);
});

test("persistent tooltip nodes are reused", () => {
  const { tooltip, interaction } = tooltipFor(measurement());
  const nodes = [...tooltip.children];
  interaction.hover({ clientX: 600, clientY: 100 });
  assert.deepEqual(tooltip.children, nodes);
});

test("cached tooltip text matches frozen formatter behavior in each fixed local zone", () => {
  const formatterInputs = {
    when: [undefined, null, false, 0, "", "not-a-time", "2026-01-01T08:00:20Z"],
    tooltipWhen: [
      undefined,
      null,
      false,
      0,
      "",
      " ",
      "not-a-time",
      "2026-01-01T08:00:20.987654+02:00",
    ],
    clock: [undefined, null, false, 0, "", "not-a-time", "2026-01-01T08:00:20Z"],
  };
  for (const timeZone of ["UTC", "Europe/Berlin", "America/New_York"])
    inTimeZone(timeZone, () => {
      const current = loadHelper(new EventTarget()).timestampFunctions;
      const frozen = loadHelper(
        new EventTarget(),
        frozenPanelSource,
      ).timestampFunctions;
      for (const [name, values] of Object.entries(formatterInputs))
        for (const value of values)
          assert.equal(current[name](value), frozen[name](value), `${name}: ${value}`);
    });

  const cases = [
    measurement(),
    measurement({ raw_value: 0, measured_at: null }),
    measurement({
      raw_value: undefined,
      value: 12.3456789,
      received_at: "2026-07-01T08:00:20.987654+02:00",
      measured_at: false,
    }),
  ];
  for (const timeZone of ["UTC", "Europe/Berlin", "America/New_York"])
    for (const source of cases) {
      const expected = inTimeZone(
        timeZone,
        () => tooltipFor(source, "measurement", frozenPanelSource).tooltip.textContent,
      );
      const actual = inTimeZone(timeZone, () => tooltipFor(source).tooltip.textContent);
      assert.equal(actual, expected);
    }
});

test("tooltip formatters and source-point text are reused within each fixed local zone", () => {
  for (const timeZone of ["UTC", "Europe/Berlin", "America/New_York"])
    inTimeZone(timeZone, () => {
      const tracker = trackingIntl();
      const { tooltip, interaction } = tooltipFor(
        measurement(), "measurement", panelSource, tracker.intl,
      );
      const text = tooltip.textContent,
        nodes = [...tooltip.children],
        initialConstructions = tracker.constructions;
      interaction.hover({ clientX: 600, clientY: 100 });
      assert.equal(tooltip.textContent, text);
      assert.deepEqual(tooltip.children, nodes);
      assert.equal(
        tracker.constructions,
        initialConstructions + 1,
        "one zone lookup; timestamp formatters and source-point text are reused",
      );
    });
});

test("formatter cache separates explicit zones with equal current offsets", () => {
  const current = loadHelper(new EventTarget()).timestampFunctions;
  const past = "1900-01-01T12:00:00.123456Z";
  for (const zone of ["Europe/Berlin", "Europe/Paris", "Europe/Berlin"]) {
    const expected = inTimeZone(zone, () => {
      const frozen = loadHelper(new EventTarget(), frozenPanelSource).timestampFunctions;
      return frozen.tooltipWhen(past);
    });
    assert.equal(current.tooltipWhen(past, zone), expected);
  }
});
