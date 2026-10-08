"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const defaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
);
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: {
    get: () => null,
    define: (_, panel) => {
      Panel = panel;
    },
  },
});
const panel = () =>
  Object.assign(Object.create(Panel.prototype), {
    state: {
      frontend_defaults: defaults.frontend,
      appearance_catalog: defaults.appearance,
      configuration: { parameters: {} },
      parameters: [{ key: "target_temperature_c", minimum: 60, maximum: 100 }],
    },
  });

test("brightness reports distinguish feedback, a plan and unknown independently of draft", () => {
  const p = panel();
  p.manualLightDraft = "35";
  p.state.manual_controls = {
    light: {
      manual: 60,
      automatic: 20,
      observation: { available: true, brightness_percent: 47 },
    },
  };
  assert.equal(p.lightFeedback(), "Licht 47 %");
  p.state.manual_controls.light.observation = {
    available: false,
    brightness_percent: null,
  };
  assert.equal(p.lightFeedback(), "Lichtvorgabe 60 % · Rückmeldung nicht verfügbar");
  p.state.manual_controls.light.manual = null;
  assert.equal(p.lightFeedback(), "Lichtvorgabe 20 % · Rückmeldung nicht verfügbar");
  p.state.manual_controls.light.automatic = null;
  assert.equal(p.lightFeedback(), "Licht unbekannt · Rückmeldung nicht verfügbar");
  p.state.manual_controls.light.observation = {
    available: true,
    brightness_percent: 0,
  };
  assert.equal(p.lightFeedback(), "Licht 0 %");
});

test("temperature controls retain whole degrees even with legacy fractional steps", () => {
  const p = panel();
  p.state.frontend_defaults = {
    ...defaults.frontend,
    temperature_step_c: 0.25,
    temperature_dial_step_c: 2,
  };
  assert.equal(p.temperatureStep(), 1);
  assert.equal(p.clampTemperature(80.3), 80);
  assert.equal(p.clampTemperature(80.5), 81);
  assert.equal(p.clampArcTemperature(83), 84);
  p.state.frontend_defaults.temperature_dial_step_c = 0.1;
  assert.equal(p.clampArcTemperature(80.5), 81);
  p.state.frontend_defaults = {};
  assert.equal(p.temperatureStep(), null);
  assert.equal(p.targetArcBounds(), null);
  assert.equal(p.clampTemperature(80), null);
  p.state.appearance_catalog = { colors: [], scales: {} };
  assert.equal(p.appearanceScale("temperature"), null);
  assert.equal(p.appearanceScale("humidity"), null);
});

test("settings round target temperatures without changing fractional control parameters", async () => {
  const p = panel();
  const calls = [];
  p.entry = "entry";
  p.generation = 0;
  p.api = async (...args) => {
    calls.push(args);
    return { parameters: args[2] };
  };
  p.waitForConfiguration = async () => {};
  p.refresh = async () => {};
  p.shadowRoot = { querySelectorAll: () => [] };
  await p.updateParameters({
    target_temperature_c: 80.5,
    final_temperature_c: 90.4,
    preset_start_c: 70.5,
    preset_step_c: 0.5,
    readiness_hysteresis_c: 1.5,
    heating_hysteresis_c: 2.5,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), [
    "/entry/parameters",
    "POST",
    {
      target_temperature_c: 81,
      final_temperature_c: 90,
      preset_start_c: 71,
      preset_step_c: 0.5,
      readiness_hysteresis_c: 1.5,
      heating_hysteresis_c: 2.5,
    },
  ]);
});

test("featured quantity colors precede advanced colors and legacy height values stay saved", () => {
  const p = panel();
  p.state.appearance = {
    colors: { series_upper: "#00FF00", series_lower: "#CC00FF" },
    scales: {},
  };
  const html = p.appearanceSettingsMarkup();
  assert.doesNotMatch(
    html,
    /Eine Farbe je Messgröße|Messhöhe oben:|Farben und Anzeigeskalen dieser Sauna/,
  );
  assert.doesNotMatch(html, /data-appearance-(picker|color)="series_(upper|lower)"/);
  for (const role of ["series_temperature", "series_humidity"])
    assert.ok(
      html.indexOf(`data-appearance-color="${role}"`) <
        html.indexOf("appearance-advanced"),
    );
  assert.equal(p.savedAppearance().colors.series_upper, "#00FF00");
  assert.equal(
    p.historyCurveStyles()["upper:temperature"].stroke,
    p.historyCurveStyles()["lower:temperature"].stroke,
  );
  assert.equal(
    p.historyCurveStyles().overview.stroke,
    p.historyCurveStyles()["upper:temperature"].stroke,
  );
});

test("history legend groups matching curve, phase and event symbols without height controls", () => {
  const p = panel();
  p.historyTitle = () => "Sitzung";
  p.historyDetail = true;
  const markup = p.historyMarkup({});
  assert.doesNotMatch(markup, /history-detail|position-upper|position-lower|Messhöhen/);
  for (const label of ["Messkurven", "Phasen", "Ereignisse"])
    assert.ok(markup.includes(`aria-label="${label}"`));
  for (const kind of ["temperature", "humidity", "infusion"])
    assert.ok(markup.includes(`<line class="${kind}"`));
  for (const kind of ["heat", "ready", "after", "gang", "door"])
    assert.ok(markup.includes(`<rect class="${kind}"`));
  assert.doesNotMatch(markup, /class="vent"|lüften|Lüftung/);
  const styles = p.historyCurveStyles();
  assert.deepEqual(Array.from(styles["upper:temperature"].lineDash), []);
  assert.deepEqual(Array.from(styles["upper:humidity"].lineDash), [4, 4]);
  assert.equal(
    styles["upper:temperature"].stroke,
    defaults.appearance.colors.find((item) => item.id === "series_temperature").default,
  );
  assert.equal(
    styles["upper:humidity"].stroke,
    defaults.appearance.colors.find((item) => item.id === "series_humidity").default,
  );
});
