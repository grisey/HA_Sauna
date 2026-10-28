"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const { panelFixture } = require("./catalog_fixture.cjs");

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const catalog = structuredClone(require("./catalog_fixture.cjs").catalog).appearance;
const color = (role) => catalog.colors.find((item) => item.id === role);
let Panel;
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: {
    get: () => null,
    define: (_, value) => {
      Panel = value;
    },
  },
});
const panel = panelFixture(Panel);
const css = panel.appearanceStyles();

test("selection and commands share a warm accent; danger remains muted red", () => {
  assert.equal(color("ui_accent").default, color("ui_command").default);
  assert.notEqual(color("ui_danger").default, color("ui_command").default);
  assert.match(css, /button\[aria-current="page"\][\s\S]*?\{[^}]*var\(--accent\)/);
  assert.match(css, /button\.primary,\s*button\.confirm\s*\{[^}]*var\(--confirm\)/);
  assert.match(css, /button\.stop\s*\{[^}]*var\(--danger\)/);
});

test("measurements share semantic colors and control surface uses a soft phase tint", () => {
  assert.match(source, /appearanceColor\("series_temperature"\)/);
  assert.match(source, /appearanceColor\("series_humidity"\)/);
  assert.match(css, /--sauna-color-phase-warmup\) 16%/);
  assert.match(source, /--sauna-main-background/);
  assert.match(css, /\.control-main\s*\{[^}]*sauna-main-background/);
  const distinct = [
    "series_temperature",
    "series_humidity",
    "phase_session",
    "event_door",
    "event_infusion",
  ].map((role) => color(role).default);
  assert.equal(new Set(distinct).size, distinct.length);
  assert.deepEqual(
    Array.from(panel.historyCurveStyles()["upper:humidity"].lineDash),
    [],
  );
});
