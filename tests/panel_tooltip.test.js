"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Date,
  Intl,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
});

const tooltipFor = (source, kind = "measurement") => {
  const received =
    typeof source.received_at === "number"
      ? source.received_at
      : Date.parse(source.received_at);
  const tooltip = { style: {} };
  const cursor = { setAttribute: () => {} };
  const session = { configuration: { parameters: { sensor_timeout_seconds: 120 } } };
  const svg = {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 480 }),
    closest: () => ({
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 480 }),
    }),
  };
  const panel = Object.assign(Object.create(Panel.prototype), {
    positions: new Set(["upper"]),
    window: [received - 30000, received + 30000],
    state: { configuration: session.configuration },
    shown: {
      session,
      records: [{ kind, payload: source }],
    },
    historyDetail: true,
    $: (selector) => (selector === "#tooltip" ? tooltip : cursor),
  });
  panel.hoverChart({ svg, target: svg, clientX: 600, clientY: 100 });
  return tooltip.innerHTML;
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

test("hoverChart preserves the original value and both precise time sources", () => {
  const tooltip = inTimeZone("Europe/Berlin", () => tooltipFor(measurement()));

  assert.match(tooltip, /Originalwert 79\.123456 °C/);
  assert.match(tooltip, /Empfangen .*09:00:20\.987654 GMT\+1/);
  assert.match(tooltip, /Gemessen .*09:00:10\.876543 GMT\+1/);
});

test("hoverChart only labels a valid supplied measurement time", () => {
  for (const measured_at of [undefined, null, false, [], {}, "not-a-time"]) {
    const tooltip = tooltipFor(measurement({ measured_at }));

    assert.match(tooltip, /Empfangen/);
    assert.doesNotMatch(tooltip, /Gemessen/);
  }
});

test("hoverChart uses a nullish raw-value fallback and escapes raw strings", () => {
  const zero = tooltipFor(measurement({ value: 0, raw_value: 0 }));
  const derived = tooltipFor(
    measurement({ raw_value: undefined, value: 79.123456 }),
    "source_snapshot",
  );
  const escaped = tooltipFor(measurement({ raw_value: '<raw&"value>' }));

  assert.match(zero, /Originalwert 0 °C/);
  assert.match(derived, /Originalwert 79,123456 °C/);
  assert.match(escaped, /Originalwert &lt;raw&amp;&quot;value&gt; °C/);
  assert.doesNotMatch(escaped, /<raw&"value>/);
});

test("hoverChart distinguishes repeated local DST times through their offset", () => {
  const [beforeFallback, afterFallback] = inTimeZone("Europe/Berlin", () => [
    tooltipFor(measurement({ received_at: "2026-10-25T00:30:00.123456Z" })),
    tooltipFor(measurement({ received_at: "2026-10-25T01:30:00.123456Z" })),
  ]);

  assert.match(beforeFallback, /02:30:00\.123456 GMT\+2/);
  assert.match(afterFallback, /02:30:00\.123456 GMT\+1/);
  assert.notEqual(beforeFallback, afterFallback);
});

test("hoverChart keeps the browser-local zone without a Berlin production rule", () => {
  const utc = inTimeZone("UTC", () => tooltipFor(measurement()));
  const newYork = inTimeZone("America/New_York", () => tooltipFor(measurement()));

  assert.match(utc, /08:00:20\.987654 GMT/);
  assert.match(newYork, /03:00:20\.987654 GMT-5/);
});

test("hoverChart keeps fractions from local ISO times and accepts milliseconds", () => {
  const [localIso, numeric] = inTimeZone("Europe/Berlin", () => [
    tooltipFor(measurement({ received_at: "2026-01-01T08:00:20.987654" })),
    tooltipFor(measurement({ received_at: Date.parse("2026-01-01T08:00:20Z") })),
  ]);

  assert.match(localIso, /08:00:20\.987654 GMT\+1/);
  assert.match(numeric, /09:00:20 GMT\+1/);
});
