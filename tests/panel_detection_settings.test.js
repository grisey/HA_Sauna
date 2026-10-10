// Uses the production catalog and rendering/save paths without Home Assistant.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  FormData: class {
    constructor(form) {
      return form.entries();
    }
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
});
const catalog = JSON.parse(
  fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
);
const values = Object.fromEntries(catalog.parameters.map((d) => [d.key, d.default]));
const panel = Object.assign(Object.create(Panel.prototype), {
  entry: "test",
  settingsEntry: "test",
  settingsAdmin: true,
  settingsPresenceSource: "proxy",
  hass: {},
  state: {
    permissions: { admin: true },
    configuration: { parameters: { ...values }, presence_source: "proxy" },
    // The configured method must win over stale observational state.
    presence: { effective_source: "ha_presence" },
    parameters: catalog.parameters,
    frontend_defaults: catalog.frontend,
  },
  selectSettingsSection() {},
  drawAppearanceStatus() {},
  drawArchiveManagement() {},
  renderProgramLibrary() {},
  drawButtonProgram() {},
  message() {},
});
const proxyMarkup = panel.settingsParameterGroup({ id: "sensors" });
assert.match(
  panel.sensorSettingsOverview(),
  /<strong>Temperatur und Feuchte<\/strong>/,
);
assert.doesNotMatch(proxyMarkup, /<summary>Experteneinstellungen/);
assert.match(proxyMarkup, /data-action="program-info:parameter-group:door"/);
assert.match(proxyMarkup, /popover="manual"[^>]*hidden/);
for (const group of catalog.frontend.settings_subgroups.filter(
  (g) => g.presence_sources,
)) {
  for (const definition of catalog.parameters.filter(
    (d) => d.settings_subgroup === group.id,
  ))
    assert.ok(proxyMarkup.includes(`name="${definition.key}"`), definition.key);
}
panel.state.configuration.presence_source = "ha_presence";
const directMarkup = panel.settingsParameterGroup({ id: "sensors" });
assert.match(panel.sensorSettingsOverview(), /<strong>Präsenzsensor<\/strong>/);
for (const group of catalog.frontend.settings_subgroups.filter(
  (g) => g.presence_sources,
)) {
  for (const definition of catalog.parameters.filter(
    (d) => d.settings_subgroup === group.id,
  ))
    assert.ok(!directMarkup.includes(`name="${definition.key}"`), definition.key);
}
for (const subgroup of ["signals", "door", "infusion", "ventilation"]) {
  const definitions = catalog.parameters.filter(
    (d) => d.settings_subgroup === subgroup,
  );
  assert.ok(definitions.length, subgroup);
  for (const definition of definitions) {
    assert.ok(proxyMarkup.includes(`name="${definition.key}"`), definition.key);
    assert.ok(directMarkup.includes(`name="${definition.key}"`), definition.key);
  }
  const description = catalog.frontend.settings_subgroups.find(
    (g) => g.id === subgroup,
  ).descriptions;
  if (description) {
    assert.ok(proxyMarkup.includes(description.proxy));
    assert.ok(directMarkup.includes(description.ha_presence));
  }
}
for (const definition of catalog.parameters.filter((d) =>
  ["heater_feedback", "temperature_estimation"].includes(d.settings_subgroup),
)) {
  assert.ok(!directMarkup.includes(`name="${definition.key}"`));
  assert.ok(
    panel
      .settingsParameterGroup({ id: "operation" })
      .includes(`name="${definition.key}"`),
  );
}

// Minimal input/section DOM keeps the real drawSettings update path observable.
const sensorSection = {
  inputs: [],
  writes: 0,
  set innerHTML(html) {
    this.writes++;
    this.html = html;
    this.inputs = [
      ...html.matchAll(/<input\b[^>]*name="([^"]+)"[^>]*value="([^"]*)"[^>]*>/g),
    ].map(([, name, value]) => ({ name, value, dataset: {} }));
  },
  querySelectorAll(selector) {
    return selector === "input[data-edited]"
      ? this.inputs.filter((input) => input.dataset.edited)
      : this.inputs;
  },
};
sensorSection.innerHTML = proxyMarkup;
const outside = { name: "nominal_power_kw", value: "9", dataset: { edited: "true" } };
const form = {
  entries: () =>
    [...sensorSection.inputs, outside].map((input) => [input.name, input.value]),
};
panel.$ = (selector) => {
  if (selector === "#settings-sensors") return sensorSection;
  if (selector === "#settings-parameters") return form;
  const name = selector.match(/input\[name="([^"]+)"\]/)?.[1];
  if (name)
    return [outside, ...sensorSection.inputs].find((input) => input.name === name);
  return {};
};
panel.shadowRoot = { activeElement: null, querySelectorAll: () => [] };
const edit = (name, value) =>
  Object.assign(
    sensorSection.inputs.find((input) => input.name === name),
    {
      value,
      dataset: { edited: "true" },
    },
  );
const shared = catalog.parameters.find((d) => d.settings_subgroup === "door");
const indirect = catalog.parameters.find(
  (d) => d.settings_subgroup === "presence_strong",
);
const sharedDraft = String(
  Math.min(shared.maximum ?? Infinity, shared.default + shared.number_step),
);
const indirectDraft = String(
  Math.min(indirect.maximum ?? Infinity, indirect.default + indirect.number_step),
);
edit(shared.key, sharedDraft);
edit(indirect.key, indirectDraft);
panel.drawSettings();
assert.equal(sensorSection.writes, 2, "method change replaces only the sensor section");
assert.equal(
  sensorSection.inputs.find((input) => input.name === shared.key).value,
  sharedDraft,
);
assert.equal(outside.value, "9", "other sections retain unsaved edits");
assert.ok(!sensorSection.inputs.some((input) => input.name === indirect.key));
panel.state.configuration.parameters[shared.key] = shared.default;
panel.drawSettings();
assert.equal(sensorSection.writes, 2, "ordinary state update preserves the DOM");
assert.equal(
  sensorSection.inputs.find((input) => input.name === shared.key).value,
  sharedDraft,
);
let saved;
panel.updateParameters = async (parameters) => {
  saved = parameters;
};
(async () => {
  await panel.saveSettings();
  assert.equal(saved[shared.key], Number(sharedDraft));
  assert.equal(
    saved[indirect.key],
    values[indirect.key],
    "hidden parameters retain configured values, not hidden drafts",
  );
  assert.equal(saved.nominal_power_kw, 9);
  panel.state.configuration.presence_source = "proxy";
  panel.drawSettings();
  assert.equal(
    sensorSection.inputs.find((input) => input.name === indirect.key).value,
    indirectDraft,
    "switching back restores an unsaved draft",
  );
  panel.state.configuration_locked = true;
  panel.drawSettings();
  assert.ok(sensorSection.inputs.every((input) => input.disabled));
  saved = null;
  await panel.saveSettings();
  assert.equal(saved, null, "locked configuration cannot be saved");
  console.log("detection settings rendering and persistence tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
