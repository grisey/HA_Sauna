"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const catalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).appearance;
const color = (role) => catalog.colors.find((item) => item.id === role);

test("selection and commands share a warm accent; danger remains muted red", () => {
  assert.equal(color("ui_accent").default, "#A86C45");
  assert.equal(color("ui_command").default, "#A86C45");
  assert.equal(color("ui_danger").default, "#A3453C");
  assert.match(source, /button\[aria-current="page"\][\s\S]*?\{[^}]*var\(--accent\)/);
  assert.match(source, /button\.primary,\s*button\.confirm\s*\{[^}]*var\(--confirm\)/);
  assert.match(source, /button\.stop\s*\{[^}]*var\(--danger\)/);
});

test("measurements share semantic colors and control surface uses a soft phase tint", () => {
  assert.match(source, /appearanceColor\("series_temperature"\)/);
  assert.match(source, /appearanceColor\("series_humidity"\)/);
  assert.match(source, /--sauna-color-phase-warmup\) 28%/);
  assert.match(source, /--sauna-main-background/);
  assert.match(source, /\.control-main\s*\{[^}]*sauna-main-background/);
  assert.equal(color("series_temperature").default, "#CD895F");
  assert.equal(color("series_humidity").default, "#6BA4BF");
});
