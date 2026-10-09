"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const defaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
);
const clone = (value) => JSON.parse(JSON.stringify(value));
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
});
const instrumentDefaults = () =>
  Object.fromEntries(
    Object.entries(defaults.appearance.instruments).map(([name, spec]) => [
      name,
      spec.default,
    ]),
  );
function panel() {
  const appearance = {
    colors: {},
    scales: Object.fromEntries(
      Object.entries(defaults.appearance.scales).map(([name, spec]) => [
        name,
        clone(spec.default),
      ]),
    ),
  };
  return Object.assign(Object.create(Panel.prototype), {
    entry: "instrument-test",
    generation: 0,
    state: {
      permissions: { admin: true, temperature: true },
      appearance,
      appearance_catalog: clone(defaults.appearance),
      frontend_defaults: clone(defaults.frontend),
      configuration: { appearance, parameters: {} },
    },
    $: () => null,
    shadowRoot: { activeElement: null },
    drawCurrent() {},
    drawSettings() {},
    syncAppearanceEditor() {},
    applyAppearance() {},
    scheduleHistoryRender() {},
  });
}
const edit = (p, name, value) =>
  p.updateAppearanceField({
    dataset: { appearanceInstrument: name },
    value,
  });
const readout = (html, attribute) =>
  html.match(
    new RegExp(`<[^>]*\\b${attribute}[^>]*>([\\s\\S]*?)<\\/(?:div|output)>`),
  )?.[1];

test("legacy appearance inherits catalog defaults and individual styles override the shared choice", () => {
  const p = panel();
  for (const [name, value] of Object.entries(instrumentDefaults()))
    assert.equal(p.instrumentSelection(name), value);
  p.state.appearance.instruments = { default: "linear", temperature: "round" };
  assert.equal(p.instrumentStyle("temperature"), "round");
  assert.equal(p.instrumentStyle("humidity"), "linear");
  assert.equal(p.instrumentStyle("light"), "linear");
  p.state.appearance.instruments.default = "round";
  assert.equal(p.instrumentStyle("humidity"), "round");
});

test("instrument edits preview separately, submit with colors and scales, and reset only the draft", async () => {
  const p = panel(),
    saved = clone(p.state.appearance),
    calls = [];
  p.api = async (...args) => {
    calls.push(clone(args));
    return { appearance: clone(args[2]) };
  };
  edit(p, "default", "linear");
  edit(p, "temperature", "round");
  assert.deepEqual(p.state.appearance, saved);
  assert.equal(p.instrumentStyle("temperature"), "round");
  assert.equal(p.instrumentStyle("humidity"), "linear");
  edit(p, "light", "invalid");
  assert.equal(p.instrumentSelection("light"), instrumentDefaults().light);
  await p.saveAppearance();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0, 2), ["/instrument-test/appearance", "POST"]);
  assert.deepEqual(calls[0][2].instruments, {
    ...instrumentDefaults(),
    default: "linear",
    temperature: "round",
  });
  assert.deepEqual(calls[0][2].scales, saved.scales);
  assert.deepEqual(calls[0][2].colors, saved.colors);
  assert.equal(p.appearanceDraft, null);
  assert.equal(p.state.configuration.appearance, p.state.appearance);
  const persisted = clone(p.state.appearance);
  p.resetAppearanceDraft(true);
  assert.deepEqual(clone(p.appearanceDraft.instruments), instrumentDefaults());
  assert.deepEqual(p.state.appearance, persisted);
  p.resetAppearanceDraft();
  assert.equal(p.appearanceDraft, null);
  assert.equal(p.instrumentStyle("humidity"), "linear");
});

test("linear readings preserve out-of-scale measurements while target control stays separate", () => {
  const p = panel(),
    bounds = { minimum: 40, maximum: 100 };
  const target = p.linearTargetControl(75, bounds);
  for (const reading of [12, 135]) {
    const html = p.linearInstrument({
      key: "temperature",
      reading,
      unit: "°C",
      bounds,
      valid: true,
      control: target,
    });
    assert.ok(html.includes(`${reading} <span>°C</span>`));
    assert.ok(html.includes(`width:${reading < bounds.minimum ? 0 : 100}%`));
    assert.ok(html.includes('value="75"'));
    assert.ok(html.includes("data-linear-target>75 °C</output>"));
  }
  p.state.permissions.temperature = false;
  assert.match(p.linearTargetControl(75, bounds), /<input[^>]*disabled/);
  assert.equal(p.linearTargetControl(75, null), "");
});

test("light feedback never substitutes a selected value or a draft for unavailable observation", () => {
  for (const style of ["linear", "round"]) {
    const p = panel(),
      calls = [];
    p.state.appearance.instruments = { light: style };
    p.state.manual_controls = {
      light: {
        manual: 68,
        automatic: 24,
        observation: { available: false, brightness_percent: 91 },
      },
    };
    p.manualLightDraft = "43";
    const dial = (...args) => {
      calls.push(args);
      return `<svg data-round-test>${args[6]}</svg>`;
    };
    const html = p.lightInstrument(dial, true);
    assert.ok(html.includes("Rückmeldung fehlt"));
    if (style === "linear") {
      assert.ok(html.includes("data-light-target>43 %</output>"));
      assert.ok(html.includes('value="43"'));
      assert.match(readout(html, "data-light-observation"), /^– /);
    } else {
      assert.equal(calls[0][0], null);
      assert.equal(calls[0][5], false);
      assert.match(html, /data-light-arc[^>]*role="slider"[^>]*aria-valuenow="43"/);
      assert.match(html, /data-light-target[^>]*>43 %<\/text>/);
      assert.doesNotMatch(html, /<input|manual-light-value-overview/);
    }
    p.state.manual_controls.light.observation = {
      available: true,
      brightness_percent: 17,
    };
    const observed = p.lightInstrument(dial, false);
    assert.ok(!observed.includes("Rückmeldung fehlt"));
    if (style === "linear") {
      assert.match(observed, /<input[^>]*disabled/);
      assert.match(readout(observed, "data-light-observation"), /^17 /);
      assert.ok(observed.includes("data-light-target>43 %</output>"));
    } else {
      assert.equal(calls[1][0], 17);
      assert.match(observed, /data-light-arc[^>]*aria-disabled="true"/);
      assert.match(observed, /data-light-target[^>]*>43 %<\/text>/);
    }
  }
});

function lightPanel() {
  const p = panel(),
    calls = [],
    captures = new Set();
  p.state.permissions.light = true;
  p.state.configuration.control_mode = "manual";
  p.state.manual_controls = {
    light: {
      manual: 20,
      observation: { available: true, brightness_percent: 20 },
    },
  };
  p.api = async (...args) => calls.push(clone(args));
  p.refresh = async () => {};
  p.svgCoordinates = (_svg, x, y) => ({ x, y });
  const svg = {
    setPointerCapture(id) {
      captures.add(id);
    },
    hasPointerCapture(id) {
      return captures.has(id);
    },
    releasePointerCapture(id) {
      captures.delete(id);
    },
  };
  return { p, calls, svg, captures };
}
const pointer = (pointerId, clientX, clientY) => ({
  pointerId,
  clientX,
  clientY,
  preventDefault() {},
});

test("light arc shares endpoint geometry and sends only the final pointer selection", async () => {
  const { p, calls, svg, captures } = lightPanel();
  for (const [x, y, fraction] of [
    [75.75, 204.25, 0],
    [150, 25, 0.5],
    [224.25, 204.25, 1],
  ]) {
    assert.ok(Math.abs(p.instrumentArcFractionAt(svg, x, y) - fraction) < 1e-9);
    assert.equal(p.lightValueAt(svg, x, y), fraction * 100);
  }
  p.beginLightDrag(pointer(3, 150, 25), svg);
  assert.ok(captures.has(3));
  assert.equal(p.manualLightDraft, "50");
  p.updateLightDrag(pointer(3, 224.25, 204.25));
  assert.equal(p.manualLightDraft, "100");
  assert.equal(calls.length, 0);
  await p.endLightDrag({ pointerId: 3 });
  assert.deepEqual(calls, [["/instrument-test/light", "POST", { value: 100 }]]);
  assert.equal(p.lightInteraction, null);
  assert.equal(p.manualLightDraft, null);
  assert.equal(captures.size, 0);
  p.beginLightDrag(pointer(4, 150, 25), svg);
  p.cancelLightDrag();
  assert.equal(calls.length, 1, "cancel releases capture without submitting");
  assert.equal(captures.size, 0);
  assert.equal(p.manualLightDraft, null);
  p.state.permissions.light = false;
  p.beginLightDrag(pointer(5, 150, 25), svg);
  assert.equal(p.lightInteraction, null);
});

test("light keyboard uses configured steps, bounds and actual-value no-op", async () => {
  for (const [key, offset] of [
    ["ArrowRight", 1],
    ["ArrowLeft", -1],
    ["PageUp", 10],
    ["PageDown", -10],
  ]) {
    const { p, calls } = lightPanel();
    p.state.manual_controls.light.manual = 50;
    const step = defaults.frontend.brightness_step_percent;
    await p.keyLightTarget({ key, preventDefault() {} });
    assert.equal(
      calls[0][2].value,
      Math.max(0, Math.min(100, Math.round(50 + offset * step))),
    );
    assert.equal(p.lightInteraction, null);
  }
  const { p, calls } = lightPanel();
  for (const [key, expected] of [
    ["Home", 0],
    ["End", 100],
  ]) {
    await p.keyLightTarget({ key, preventDefault() {} });
    assert.equal(calls.at(-1)[2].value, expected);
  }
  const before = calls.length;
  await p.keyLightTarget({
    key: "Enter",
    preventDefault() {
      assert.fail("unhandled key");
    },
  });
  await p.submitLight(20);
  await p.submitLight(true);
  assert.equal(calls.length, before, "actual brightness and already-on are no-ops");
  await p.submitLight(null);
  assert.equal(calls.at(-1)[2].value, null, "return to automatic remains a command");
});

test("linear target completion cannot clear newer drafts or another entry", async () => {
  for (const change of ["entry", "generation", "revision", "none"]) {
    const p = panel();
    let resolve,
      redraws = 0;
    p.changeTarget = () =>
      new Promise((done) => {
        resolve = done;
      });
    p.drawCurrent = () => {
      redraws++;
    };
    p.linearTargetDraft = 75;
    const request = p.commitLinearTarget(75);
    p.linearTargetDraft = 85;
    if (change === "entry") p.entry = "another";
    if (change === "generation") p.generation++;
    if (change === "revision") p.linearTargetRevision = 1;
    resolve();
    await request;
    assert.equal(p.linearTargetDraft, change === "none" ? null : 85);
    assert.equal(redraws, change === "none" ? 1 : 0);
  }
});

test("weather uses only configured supplied metrics and escapes source strings", () => {
  const p = panel();
  assert.equal(p.weatherMarkup(), "");
  p.state.environment = { configured: false, values: [] };
  assert.equal(p.weatherMarkup(), "");
  p.state.environment = {
    configured: true,
    condition: "partlycloudy",
    station: { name: '<img src=x onerror="bad()">' },
    values: [
      {
        key: "temperature",
        label: "Außen <b>",
        value: 16.4,
        available: true,
        unit: "°C",
      },
      { key: "humidity", label: "Feuchte", value: 88, available: false, unit: "%" },
      {
        key: "wind_speed",
        label: "Wind",
        state: "<script>bad()</script>",
        available: true,
        unit: '"<x>',
      },
    ],
  };
  const html = p.weatherMarkup();
  assert.ok(html.includes('role="img"'));
  assert.ok(html.includes("Leicht bewölkt"));
  assert.ok(html.includes("16,4"));
  assert.ok(html.includes("&lt;img src=x onerror=&quot;bad()&quot;&gt;"));
  assert.ok(html.includes("Außen &lt;b&gt;"));
  assert.ok(html.includes("&lt;script&gt;bad()&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('data-weather-quantity="precipitation"'));
  assert.ok(!html.includes("<dd>88"));
  assert.match(html, /<dd>– <span>%<\/span>/);
});
