"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
});
const environment = () => ({
  configured: true,
  station: { name: "Beispielstation", id: "demo" },
  condition: "rainy",
  source: { mode: "mixed_data", interpolated: true },
  measurement_time: "2026-10-08T10:00:00Z",
  forecast_time: "2026-10-08T11:00:00Z",
  values: [
    {
      key: "temperature",
      label: "Temperatur",
      value: 16.46,
      unit: "°C",
      available: true,
    },
    {
      key: "wind_direction",
      label: "Windrichtung",
      value: "W",
      unit: "°",
      available: true,
    },
    {
      key: "precipitation",
      label: "Niederschlag",
      value: 0,
      unit: "mm/h",
      available: true,
    },
    {
      key: "humidity",
      label: "Relative Luftfeuchte",
      value: 64,
      unit: "%",
      available: false,
    },
    { key: "dew_point", label: "Taupunkt", value: null, unit: "°C", available: true },
  ],
});
const panel = (admin, data = environment()) =>
  Object.assign(Object.create(Panel.prototype), {
    state: { permissions: { admin }, environment: data },
    _hass: { localize: (key) => (key.endsWith(".rainy") ? "Regnerisch" : key) },
  });

test("environment is optional and renders in the existing control area for both roles", () => {
  for (const admin of [true, false]) {
    const p = panel(admin);
    const html = p.renderControlView({});
    assert.match(html, /data-environment aria-label="Umgebung"/);
    assert.match(html, /Beispielstation/);
    assert.equal(html.includes("Umgebung zuordnen"), admin);
    p.state.environment = { configured: false };
    assert.doesNotMatch(p.renderControlView({}), /data-environment/);
    delete p.state.environment;
    assert.doesNotMatch(p.renderControlView({}), /data-environment/);
  }
});

test("environment preserves units, compass directions and genuine zero while showing missing values", () => {
  const html = panel(false).renderEnvironment();
  assert.match(html, /data-environment-value="temperature">16,5 °C<\/dd>/);
  assert.match(html, /data-environment-value="wind_direction">W<\/dd>/);
  assert.match(html, /data-environment-value="precipitation">0 mm\/h<\/dd>/);
  assert.match(html, /data-environment-value="humidity">—<\/dd>/);
  assert.match(html, /data-environment-value="dew_point">—<\/dd>/);
  assert.match(html, /Messwerte und Vorhersage · interpoliert/);
  assert.match(html, /Messwerte: .*<br>Vorhersage: /);
  assert.match(html, /Regnerisch/);
  assert.doesNotMatch(html, /rainy/);
});

test("environment escapes source text and only displays localized weather conditions", () => {
  const data = environment();
  data.station.name = '<img src=x onerror="fail()">';
  data.values[1].value = "<W>";
  data.condition = "unknown_condition";
  const p = panel(false, data);
  assert.match(p.renderEnvironment(), /&lt;img/);
  assert.match(p.renderEnvironment(), /&lt;W&gt;/);
  assert.doesNotMatch(p.renderEnvironment(), /<img|unknown_condition/);
  delete p._hass;
  assert.doesNotMatch(p.renderEnvironment(), /unknown_condition/);
  data.values[0].value = Number.NaN;
  assert.match(p.renderEnvironment(), /data-environment-value="temperature">—<\/dd>/);
});

test("environment times are compact today and retain the full timestamp", () => {
  const data = environment();
  const p = panel(false, data);
  p.state.now = data.measurement_time;
  let html = p.renderEnvironment();
  assert.match(
    html,
    /Messwerte: <time datetime="2026-10-08T10:00:00Z" title="2026-10-08T10:00:00Z">\d{2}:\d{2}<\/time>/,
  );
  p.state.now = "2026-10-10T10:00:00Z";
  html = p.renderEnvironment();
  assert.match(html, /Messwerte: <time[^>]+>\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}<\/time>/);
});
