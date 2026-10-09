// Runs without Home Assistant or a browser: the temperature arc keeps its
// geometry and uses only the parameter definition for its limits.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const frontendDefaults = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).frontend;

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {
    get: () => undefined,
    define: (_name, value) => {
      Panel = value;
    },
  },
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
  setTimeout,
  clearTimeout,
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const appearanceCatalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
).appearance;
vm.runInNewContext(source, sandbox);

assert.match(source, /data-target-arc/);
assert.match(source, /role="slider"/);
assert.doesNotMatch(source, /foreignObject[^`]*id="target"/);
assert.match(source, /Temperaturautomatik/);

const state = {
  frontend_defaults: frontendDefaults,
  appearance_catalog: appearanceCatalog,
  permissions: { temperature: true },
  target_temperature: 80,
  configuration: { parameters: { sauna_min_temperature_c: 60 } },
  parameters: [
    { key: "target_temperature_c", minimum: 60, maximum: 100 },
    { key: "temperature_gangs", minimum: 1, maximum: 8 },
  ],
};
const calls = [];
const panel = Object.assign(Object.create(Panel.prototype), {
  entry: "entry-1",
  state,
  api: async (...args) => calls.push(args),
  refresh: async () => {},
  drawCurrent: () => {},
  $: () => null,
  svgCoordinates: (_svg, x, y) => ({ x, y }),
});

assert.deepEqual(JSON.parse(JSON.stringify(panel.temperatureBounds())), {
  minimum: 60,
  maximum: 100,
});
assert.equal(
  panel.temperatureValueAt({}, 75.75, 204.25),
  60,
  "the arc starts at the configured minimum",
);
assert.equal(
  panel.temperatureValueAt({}, 150, 25),
  75,
  "the top of the dial is the middle of the display range",
);
assert.equal(
  panel.temperatureValueAt({}, 224.25, 204.25),
  100,
  "the arc ends at the configured maximum",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(panel.temperatureTickValues())),
  [40, 50, 60, 70, 80, 90, 100, 110],
  "reference ticks derive from the display range",
);
assert.equal(
  panel.clampTemperature(60.2, { minimum: 60.2, maximum: 100 }),
  61,
  "a fractional minimum exposes the next valid whole degree",
);
assert.equal(
  panel.clampArcTemperature(60.2, { minimum: 61, maximum: 100 }),
  61,
  "the dial exposes only whole degrees inside its bounds",
);
assert.equal(panel.clampArcTemperature(80.6, { minimum: 60, maximum: 100 }), 81);
assert.match(
  source,
  /\.target-temperature-track:focus(?:\s*,[^{}]+)?\s*\{[^}]*opacity:\s*0\.35/s,
  "keyboard focus no longer turns the complete target arc opaque",
);

{
  const inputs = {
    "#progression-start": { value: "60" },
    "#progression-end": { value: "100" },
    "#progression-gangs": { value: "8" },
  };
  const p = Object.assign(Object.create(Panel.prototype), {
    state,
    $: (selector) => inputs[selector],
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(p.progressionValues())),
    { start: 60, end: 100, gangs: 8 },
    "free progression accepts the catalog's dynamic bounds",
  );
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(p.progressionValues({ start: "80.5", end: "89.4", gangs: "3" })),
    ),
    { start: 81, end: 89, gangs: 3 },
    "completed fractional endpoints become the actual whole-degree commands",
  );
  assert.throws(
    () => p.progressionValues({ start: "59.9", end: "89", gangs: "3" }),
    /zulässigen Grenzen/,
    "rounding must not admit a raw value below the allowed minimum",
  );
  assert.throws(
    () => p.roundTargetTemperature(60.3, { minimum: 60.2, maximum: 100 }),
    /zulässigen Grenzen/,
    "the rounded command must also remain within the bounds",
  );
  inputs["#progression-start"].value = "0";
  assert.throws(
    () => p.progressionValues(),
    /zulässigen Grenzen/,
    "free progression rejects an out-of-range temperature before the request",
  );
  inputs["#progression-start"].value = "60";
  inputs["#progression-gangs"].value = "21";
  assert.throws(
    () => p.progressionValues(),
    /zulässigen Grenzen/,
    "free progression rejects an out-of-range distribution before the request",
  );
}

(async () => {
  await panel.changeTarget(80.5);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), [
    "/entry-1/temperature",
    "POST",
    { target_temperature_c: 81 },
  ]);
  assert.equal(panel.state.target_temperature, 81);
  const svg = { setPointerCapture: () => {}, releasePointerCapture: () => {} };
  panel.beginTemperatureDrag(
    { pointerId: 4, clientX: 224.25, clientY: 204.25, preventDefault: () => {} },
    svg,
  );
  await panel.endTemperatureDrag({ pointerId: 4 });
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), [
    "/entry-1/temperature",
    "POST",
    { target_temperature_c: 100 },
  ]);

  let prevented = false;
  await panel.keyTemperatureTarget({
    key: "ArrowUp",
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, true, "slider keys suppress page scrolling");
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), [
    "/entry-1/temperature",
    "POST",
    { target_temperature_c: 100 },
  ]);
  assert.equal(
    panel.programSelectionDraft,
    null,
    "the confirmed direct target clears its temporary constant draft",
  );

  let releaseFirst;
  const sent = [];
  panel.api = async (...args) => {
    sent.push(args);
    if (sent.length === 1)
      await new Promise((resolve) => {
        releaseFirst = resolve;
      });
    return { parameters: { target_temperature_c: args[2].target_temperature_c } };
  };
  panel.beginTemperatureDrag(
    { pointerId: 10, clientX: 150, clientY: 25, preventDefault() {} },
    svg,
  );
  const first = panel.endTemperatureDrag({ pointerId: 10 });
  await Promise.resolve();
  panel.beginTemperatureDrag(
    { pointerId: 11, clientX: 224.25, clientY: 204.25, preventDefault() {} },
    svg,
  );
  const secondInteraction = panel.temperatureInteraction;
  releaseFirst();
  await first;
  assert.equal(panel.temperatureInteraction, secondInteraction);
  await panel.endTemperatureDrag({ pointerId: 11 });
  assert.deepEqual(
    sent.map(([, , body]) => body.target_temperature_c),
    [75, 100],
  );

  let rejectOlder;
  const followup = [];
  panel.api = async (...args) => {
    followup.push(args);
    if (followup.length === 1)
      await new Promise((_resolve, reject) => {
        rejectOlder = reject;
      });
    return { parameters: { target_temperature_c: args[2].target_temperature_c } };
  };
  const older = panel.changeTarget(80);
  const olderFailure = assert.rejects(older, /older choice failed/);
  await Promise.resolve();
  const newer = panel.changeTarget(90);
  rejectOlder(Error("older choice failed"));
  await olderFailure;
  await newer;
  assert.deepEqual(
    followup.map(([, , body]) => body.target_temperature_c),
    [80, 90],
    "a rejected predecessor cannot suppress the newer valid target",
  );
  assert.equal(panel.state.target_temperature, 90);
  assert.equal(panel.temperatureChange, null);

  console.log("panel temperature arc regressions passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
