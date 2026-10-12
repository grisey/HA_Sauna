const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
// Runs without Home Assistant or a browser: the temperature arc keeps its
// geometry and uses only the parameter definition for its limits.
const assert = require("node:assert/strict");
const {
  parameterValues: fixtureValues,
  parameterDefinition: fixtureDefinition,
} = require("./catalog_fixture.cjs");
const fs = require("node:fs");
const vm = require("node:vm");

const frontendDefaults = structuredClone(require("./catalog_fixture.cjs").catalog).frontend;

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
const appearanceCatalog = structuredClone(require("./catalog_fixture.cjs").catalog).appearance;
vm.runInNewContext(source, sandbox);

assert.match(source, /data-target-arc/);
assert.match(source, /role="slider"/);
assert.doesNotMatch(source, /foreignObject[^`]*id="target"/);
assert.match(source, /Temperaturautomatik/);

const state = {
  frontend_defaults: frontendDefaults,
  appearance_catalog: appearanceCatalog,
  appearance: { scales: { temperature: { minimum: 50, maximum: 110 } } },
  permissions: { temperature: true },
  target_temperature: 80,
  temperature_control: {
    scope: "current",
    value: 80,
    current_target: 80,
    next_target: 80,
  },
  configuration: { parameters: fixtureValues() },
  parameters: [
    fixtureDefinition("target_temperature_c"),
    fixtureDefinition("temperature_gangs"),
  ],
};
const calls = [];
const panel = Object.assign(fixturePanel(Panel), {
  entry: "entry-1",
  state,
  api: async (...args) => {
    calls.push(args);
    const value = args[2].target_temperature_c;
    return {
      temperature_control: {
        scope: "current",
        value,
        current_target: value,
        next_target: value,
      },
    };
  },
  refresh: async () => {},
  drawCurrent: () => {},
  $: () => null,
  svgCoordinates: (_svg, x, y) => ({ x, y }),
});

assert.deepEqual(JSON.parse(JSON.stringify(panel.temperatureBounds())), {
  minimum: fixtureDefinition("target_temperature_c").minimum,
  maximum: fixtureDefinition("target_temperature_c").maximum,
});
assert.equal(
  panel.temperatureValueAt({}, 75.75, 204.25),
  fixtureDefinition("target_temperature_c").minimum,
  "the arc starts at the configured minimum",
);
assert.equal(
  panel.temperatureValueAt({}, 150, 25),
  80,
  "the top of the dial is the middle of the display range",
);
assert.equal(
  panel.temperatureValueAt({}, 224.25, 204.25),
  fixtureDefinition("target_temperature_c").maximum,
  "the arc ends at the configured maximum",
);
assert.deepEqual(
  JSON.parse(JSON.stringify(panel.temperatureTickValues())),
  [50, 60, 70, 80, 90, 100, 110],
  "reference ticks derive from the explicitly configured display range",
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
    "#progression-start": {
      value: String(fixtureDefinition("target_temperature_c").minimum),
    },
    "#progression-end": {
      value: String(fixtureDefinition("target_temperature_c").maximum),
    },
    "#progression-gangs": {
      value: String(fixtureDefinition("temperature_gangs").maximum),
    },
  };
  const p = Object.assign(fixturePanel(Panel), {
    state,
    $: (selector) => inputs[selector],
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(p.progressionValues())),
    {
      start: fixtureDefinition("target_temperature_c").minimum,
      end: fixtureDefinition("target_temperature_c").maximum,
      gangs: fixtureDefinition("temperature_gangs").maximum,
    },
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
    () =>
      p.progressionValues({
        start: String(fixtureDefinition("target_temperature_c").minimum - 0.1),
        end: "89",
        gangs: "3",
      }),
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
  inputs["#progression-start"].value = String(
    fixtureDefinition("target_temperature_c").minimum,
  );
  inputs["#progression-gangs"].value = String(
    fixtureDefinition("temperature_gangs").maximum + 1,
  );
  assert.throws(
    () => p.progressionValues(),
    /zulässigen Grenzen/,
    "free progression rejects an out-of-range distribution before the request",
  );
}

(async () => {
  const manualConfiguration = {
    control_mode: "manual",
    program_mode: "program",
    selected_program_id: "saved-program",
    temperature_steps: [75, 80],
    parameters: { target_temperature_c: 80 },
  };
  const manualPanel = Object.assign(fixturePanel(Panel), {
    entry: "manual-entry",
    state: { ...state, configuration: manualConfiguration },
    api: async (...args) => {
      calls.push(args);
      return {
        ...manualConfiguration,
        program_mode: "constant",
        selected_program_id: null,
        temperature_steps: null,
        parameters: { target_temperature_c: 85 },
        temperature_control: {
          scope: "current",
          value: 85,
          current_target: 85,
          next_target: 85,
        },
      };
    },
    refresh: async () => {},
  });
  await manualPanel.changeTarget(84.6);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.shift())), [
    "/manual-entry/temperature",
    "POST",
    { target_temperature_c: 85 },
  ]);
  assert.equal(manualPanel.state.target_temperature, 85);
  assert.deepEqual(
    JSON.parse(JSON.stringify(manualPanel.state.configuration)),
    {
      ...manualConfiguration,
      program_mode: "constant",
      selected_program_id: null,
      temperature_steps: null,
      parameters: { target_temperature_c: 85 },
    },
    "manual instruments use the shared constant temperature choice",
  );

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
    { target_temperature_c: fixtureDefinition("target_temperature_c").maximum },
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
    { target_temperature_c: fixtureDefinition("target_temperature_c").maximum },
  ]);
  assert.equal(
    panel.programSelectionDraft,
    null,
    "the confirmed direct target clears its temporary constant draft",
  );

  {
    const configuration = {
      control_mode: "automatic",
      program_mode: "progressive",
      selected_program_id: "saved-program",
      temperature_steps: [76, 83, 91],
      parameters: { target_temperature_c: 76, final_temperature_c: 91 },
    };
    const selection = { mode: "individual" };
    const progression = { start: "77", end: "92", gangs: "4" };
    const steps = [77, 84, 92];
    const save = { name: "Entwurf" };
    const requests = [];
    const progressivePanel = Object.assign(fixturePanel(Panel), {
      entry: "progressive-entry",
      state: {
        ...state,
        target_temperature: 76,
        next_gang_temperature: 83,
        temperature_control: {
          scope: "next_gang",
          value: 83,
          current_target: 76,
          next_target: 83,
        },
        session: { temperature_program_mode: "progressive" },
        configuration,
      },
      programSelectionDraft: selection,
      progressionDraft: progression,
      freeProgramKind: "steps",
      freeProgramStepsDraft: steps,
      programSaveState: save,
      programChoiceOpen: true,
      api: async (...args) => {
        requests.push(args);
        return {
          ...configuration,
          target_temperature: 76,
          next_gang_temperature: args[2].target_temperature_c,
          temperature_control: {
            scope: "next_gang",
            value: args[2].target_temperature_c,
            current_target: 76,
            next_target: args[2].target_temperature_c,
          },
        };
      },
      refresh: async () => {},
      drawCurrent: () => {},
      $: () => null,
      svgCoordinates: (_svg, x, y) => ({ x, y }),
    });
    const preserveProgram = () => {
      assert.equal(progressivePanel.state.target_temperature, 76);
      assert.deepEqual(
        JSON.parse(JSON.stringify(progressivePanel.state.configuration)),
        configuration,
        "a live instrument change preserves the selected progressive program",
      );
      assert.equal(progressivePanel.programSelectionDraft, selection);
      assert.equal(progressivePanel.progressionDraft, progression);
      assert.equal(progressivePanel.freeProgramStepsDraft, steps);
      assert.equal(progressivePanel.freeProgramKind, "steps");
      assert.equal(progressivePanel.programSaveState, save);
      assert.equal(progressivePanel.programChoiceOpen, true);
    };
    progressivePanel.beginTemperatureDrag(
      { pointerId: 21, clientX: 224.25, clientY: 204.25, preventDefault() {} },
      svg,
    );
    await progressivePanel.endTemperatureDrag({ pointerId: 21 });
    assert.equal(
      progressivePanel.state.next_gang_temperature,
      fixtureDefinition("target_temperature_c").maximum,
    );
    preserveProgram();
    await progressivePanel.commitLinearTarget(88);
    assert.equal(progressivePanel.state.next_gang_temperature, 88);
    preserveProgram();
    await progressivePanel.keyTemperatureTarget({
      key: "ArrowUp",
      preventDefault() {},
    });
    const keyboardTarget = 88 + frontendDefaults.temperature_dial_step_c;
    assert.equal(progressivePanel.state.next_gang_temperature, keyboardTarget);
    preserveProgram();
    assert.deepEqual(
      JSON.parse(JSON.stringify(requests)),
      [fixtureDefinition("target_temperature_c").maximum, 88, keyboardTarget].map(
        (value) => [
          "/progressive-entry/temperature",
          "POST",
          { target_temperature_c: value },
        ],
      ),
      "round, linear and keyboard controls all change the next gang",
    );
  }

  let releaseFirst;
  const sent = [];
  panel.api = async (...args) => {
    sent.push(args);
    if (sent.length === 1)
      await new Promise((resolve) => {
        releaseFirst = resolve;
      });
    return {
      temperature_control: {
        scope: "current",
        value: args[2].target_temperature_c,
        current_target: args[2].target_temperature_c,
        next_target: args[2].target_temperature_c,
      },
    };
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
    [80, 100],
  );

  let rejectOlder;
  const followup = [];
  panel.api = async (...args) => {
    followup.push(args);
    if (followup.length === 1)
      await new Promise((_resolve, reject) => {
        rejectOlder = reject;
      });
    return {
      temperature_control: {
        scope: "current",
        value: args[2].target_temperature_c,
        current_target: args[2].target_temperature_c,
        next_target: args[2].target_temperature_c,
      },
    };
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
