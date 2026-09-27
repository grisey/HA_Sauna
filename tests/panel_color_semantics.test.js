"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const catalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/appearance_catalog.json", "utf8"),
);
const color = (role) => catalog.colors.find((item) => item.id === role);

test("active selection is orange, actions inherit neutral HA colors and danger is muted red", () => {
  assert.equal(color("ui_accent").default, "#E58A55");
  assert.equal(color("ui_command").default, null);
  assert.equal(color("ui_danger").default, "#A34029");
  assert.match(source, /button\[aria-current="page"\][\s\S]*?\{[^}]*var\(--accent\)/);
  assert.match(source, /button\.primary,\s*button\.confirm\s*\{[^}]*var\(--confirm\)/);
  assert.match(source, /button\.stop\s*\{[^}]*var\(--danger\)/);
});

test("chart, gauges and phase card share semantic role colors", () => {
  assert.match(source, /appearanceColor\("series_temperature"\)/);
  assert.match(source, /appearanceColor\("series_humidity"\)/);
  assert.match(source, /--sauna-color-phase-warmup\) 28%/);
  assert.match(source, /--sauna-main-background/);
  assert.match(source, /\.control-main\s*\{[^}]*sauna-main-background/);
  assert.equal(color("series_temperature").default, "#FF6B4A");
  assert.equal(color("series_humidity").default, "#42A5FF");
});
