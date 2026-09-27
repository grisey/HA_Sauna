"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
});
const catalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/appearance_catalog.json", "utf8"),
);
const clone = (value) => JSON.parse(JSON.stringify(value));
const appearance = (color = "#123456") => ({
  colors: { series_temperature: color },
  scales: {
    temperature: { minimum: 40, maximum: 110 },
    humidity: { minimum: 0, maximum: 60 },
  },
});
const state = (value = appearance()) => ({
  permissions: { admin: true },
  appearance: value,
  appearance_catalog: catalog,
  configuration: { appearance: value, temperature_programs: [] },
});
function panel(api = async () => ({ appearance: appearance() })) {
  return Object.assign(Object.create(Panel.prototype), {
    entry: "first",
    generation: 0,
    isConnected: true,
    state: state(),
    api,
    appearanceDraft: appearance("#654321"),
    appearanceRaw: {},
    shadowRoot: { activeElement: null },
    $: (selector) => (selector === "#history" ? { hidden: true } : null),
    drawCurrent() {},
    drawSettings() {},
    syncAppearanceEditor() {},
    syncNavigation() {},
    applyAppearance() {},
    scheduleHistoryRender() {},
    message(error) {
      if (error) throw error;
    },
  });
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("appearance save is single-flight and preserves drafts on server failure", async () => {
  const request = deferred();
  let calls = 0;
  const p = panel(() => {
    calls++;
    return request.promise;
  });
  const draft = p.appearanceDraft;
  const saving = p.saveAppearance();
  await p.saveAppearance();
  assert.equal(calls, 1);
  assert.ok(p.appearanceRequest);
  request.reject(new Error("Offline"));
  await saving;
  assert.equal(p.appearanceDraft, draft);
  assert.equal(p.appearanceStatus, "Offline");
  assert.equal(p.appearanceRequest, null);
});

test("late save result cannot modify another instance or detached generation", async () => {
  for (const switchInstance of [true, false]) {
    const request = deferred(),
      p = panel(() => request.promise);
    const saving = p.saveAppearance();
    if (switchInstance) p.entry = "second";
    else p.generation++;
    const next = state(appearance("#AABBCC"));
    p.state = next;
    request.resolve({ appearance: appearance("#000000") });
    await saving;
    assert.equal(p.state, next);
    assert.equal(p.state.appearance.colors.series_temperature, "#AABBCC");
  }
});

test("old polls cannot overwrite a confirmed save, later external updates remain authoritative", async () => {
  const poll = deferred();
  const p = panel((path, method) =>
    method === "POST"
      ? Promise.resolve({ appearance: appearance("#654321") })
      : poll.promise,
  );
  const refreshing = p.refresh();
  await p.saveAppearance();
  poll.resolve(state(appearance("#111111")));
  await refreshing;
  assert.equal(p.state.appearance.colors.series_temperature, "#654321");
  assert.equal(p.appearanceDraft, null);
  p.api = async () => state(appearance("#ABCDEF"));
  await p.refresh();
  assert.equal(p.state.appearance.colors.series_temperature, "#ABCDEF");
});

test("local invalid raw entries are retained and never sent", async () => {
  for (const raw of [
    { series_temperature: "oops" },
    { "temperature.minimum": "" },
    { "humidity.maximum": "NaN" },
  ]) {
    let calls = 0;
    const p = panel(async () => {
      calls++;
    });
    p.appearanceRaw = raw;
    await p.saveAppearance();
    assert.equal(calls, 0);
    assert.equal(p.appearanceRaw, raw);
    assert.ok(p.appearanceStatus);
  }
  const p = panel();
  p.appearanceDraft.scales.temperature = { minimum: -1e308, maximum: 1e308 };
  assert.throws(() => p.appearancePayload());
});

test("default preview removes saved hex overrides instead of displaying stale saved values", () => {
  const p = panel();
  p.appearanceDraft = { colors: {}, scales: clone(appearance().scales) };
  const html = p.appearanceSettingsMarkup();
  const temperature = html.match(/<input id="appearance-series_temperature"[^>]*>/)[0];
  assert.match(temperature, /value="#FF6B4A"/);
  assert.doesNotMatch(temperature, /#123456/);
});
