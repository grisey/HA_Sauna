"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const catalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/appearance_catalog.json", "utf8"),
);
let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8") +
    ";globalThis.appearanceTest = { appearanceTickValues, appearanceContrastRatio };",
  sandbox,
);
const { appearanceTickValues, appearanceContrastRatio } = sandbox.appearanceTest;
const makePanel = (appearance = { colors: {}, scales: {} }) => {
  const variables = new Map();
  const panel = Object.assign(Object.create(Panel.prototype), {
    state: {
      appearance_catalog: catalog,
      appearance,
      operation_enabled: true,
      configuration: {
        control_mode: "automatic",
        parameters: { sauna_min_temperature_c: 50 },
      },
      parameters: [{ key: "target_temperature_c", minimum: 50, maximum: 100 }],
      phase: "aufheizen",
    },
    style: {
      setProperty: (name, value) => variables.set(name, value),
      removeProperty: (name) => variables.delete(name),
    },
  });
  return { panel, variables };
};

test("finite display scales bound ticks and preserve backend target limits", () => {
  const { panel } = makePanel({
    colors: {},
    scales: {
      temperature: { minimum: 72, maximum: 86 },
      humidity: { minimum: 5, maximum: 55 },
    },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(panel.targetArcBounds())), {
    minimum: 72,
    maximum: 86,
  });
  assert.equal(
    panel.temperatureValueAt(
      {
        viewBox: { baseVal: { width: 300, height: 260 } },
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 300, height: 260 }),
      },
      75.75,
      204.25,
    ),
    72,
  );
  assert.equal(
    panel.temperatureValueAt(
      {
        viewBox: { baseVal: { width: 300, height: 260 } },
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 300, height: 260 }),
      },
      224.25,
      204.25,
    ),
    86,
  );
  assert.ok(appearanceTickValues({ minimum: -1e300, maximum: 1e300 }).length <= 8);
  assert.ok(
    appearanceTickValues({ minimum: 0, maximum: Number.MIN_VALUE }).length <= 8,
  );
  panel.appearanceDraft = {
    colors: {},
    scales: {
      temperature: { minimum: 200, maximum: 210 },
      humidity: { minimum: -5, maximum: 55 },
    },
  };
  assert.equal(
    panel.targetArcBounds(),
    null,
    "a disjoint display range never broadens the actuator range",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(panel.appearanceScale("humidity"))),
    { minimum: 0, maximum: 60 },
    "invalid humidity draft falls back to display defaults",
  );
});

test("shared phase and measurement colors flow to host and curve styles", () => {
  const { panel, variables } = makePanel({
    colors: {
      card_background: "#000000",
      phase_warmup: "#123456",
      series_temperature: "#ABCDEF",
      series_humidity: "#654321",
    },
    scales: {},
  });
  panel.applyAppearance();
  assert.equal(variables.get("--sauna-main-background"), "#010407");
  assert.equal(panel.historyCurveStyles()["upper:temperature"].stroke, "#ABCDEF");
  assert.equal(panel.historyCurveStyles()["lower:humidity"].stroke, "#654321");
  panel.state.configuration.control_mode = "manual";
  panel.applyAppearance();
  assert.equal(variables.has("--sauna-main-background"), false);
  panel.state.configuration.control_mode = "automatic";
  panel.appearanceStale = true;
  panel.applyAppearance();
  assert.equal(variables.has("--sauna-main-background"), false);
});

test("automatic ink keeps 4.5:1 text and 3:1 focus across opposite surfaces", () => {
  const { panel, variables } = makePanel({
    colors: {
      page_background: "#FFFFFF",
      card_background: "#000000",
      text: "#777777",
      muted_text: "#777777",
      focus: "#FFFFFF",
    },
    scales: {},
  });
  panel.applyAppearance();
  for (const [name, background] of [
    ["page", "#FFFFFF"],
    ["card", "#000000"],
  ]) {
    assert.ok(
      appearanceContrastRatio(variables.get(`--sauna-${name}-text`), background) >= 4.5,
    );
    assert.ok(
      appearanceContrastRatio(
        variables.get(`--sauna-${name}-muted-text`),
        background,
      ) >= 4.5,
    );
    assert.ok(
      appearanceContrastRatio(variables.get(`--sauna-${name}-focus`), background) >= 3,
    );
  }
});

test("tinted text uses the same painted surface as its background", () => {
  const { panel, variables } = makePanel({
    colors: {
      card_background: "#000000",
      text: "#757575",
      phase_warmup: "#FFFFFF",
      status_info: "#FFFFFF",
      status_warning: "#FFFFFF",
      status_error: "#FFFFFF",
      status_unknown: "#FFFFFF",
      ui_heater_on: "#FFFFFF",
      ui_heater_off: "#FFFFFF",
    },
    scales: {},
  });
  panel.applyAppearance();
  for (const name of ["info", "warning", "error", "unknown", "heater-on", "heater-off"]) {
    assert.ok(
      appearanceContrastRatio(
        variables.get(`--sauna-tint-${name}-text`),
        variables.get(`--sauna-tint-${name}-background`),
      ) >= 4.5,
      name,
    );
  }
  for (const [ink, background] of [
    ["--sauna-main-text", "--sauna-main-background"],
    ["--sauna-pending-text", "--sauna-pending-background"],
  ]) {
    assert.ok(appearanceContrastRatio(variables.get(ink), variables.get(background)) >= 4.5);
  }
});

test("popup, plot controls, selected event and empty card use their painted surfaces", () => {
  const { panel, variables } = makePanel({
    colors: {
      page_background: "#FFFFFF",
      card_background: "#000000",
      chart_background: "#000000",
      text: "#000000",
      muted_text: "#000000",
      focus: "#000000",
      status_warning: "#FFFFFF",
    },
    scales: {},
  });
  panel.applyAppearance();
  const css = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
  assert.match(css, /\.program-info-popup\s*\{[^}]*color: var\(--sauna-card-text/);
  assert.match(css, /header\s*\{[^}]*color: var\(--sauna-page-text/);
  assert.ok(appearanceContrastRatio(variables.get("--sauna-card-text"), "#000000") >= 4.5);
  assert.match(css, /\.empty\s*\{[^}]*color: var\(--sauna-card-muted-text/);
  assert.ok(appearanceContrastRatio(variables.get("--sauna-card-muted-text"), "#000000") >= 4.5);
  assert.match(css, /\.plot-panel\s*\{[^}]*--sauna-focus-current: var\(--sauna-chart-focus/);
  assert.ok(appearanceContrastRatio(variables.get("--sauna-chart-focus"), "#000000") >= 3);
  assert.match(css, /\.event-row\[data-selected="true"\]\s*\{[^}]*outline: 3px solid var\(--sauna-tint-warning-focus/);
  assert.ok(
    appearanceContrastRatio(
      variables.get("--sauna-tint-warning-focus"),
      variables.get("--sauna-tint-warning-background"),
    ) >= 3,
  );
});
