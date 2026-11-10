const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
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
const catalog = structuredClone(require("./catalog_fixture.cjs").catalog);
const values = Object.fromEntries(
  catalog.parameters.map((d) => [
    d.key,
    d.default !== null && typeof d.default === "object"
      ? catalog.parameters.find((source) => source.key === d.default.parameter).default
      : d.default,
  ]),
);
// Values the API marks as live temperature choice (edited through programs).
const live = new Set([
  "target_temperature_c",
  "final_temperature_c",
  "temperature_gangs",
]);
const panelOwned = (d) =>
  d.configuration_surface === "panel" && !live.has(d.key) && !!d.settings_subgroup;
const makePanel = (admin) => {
  const container = { innerHTML: "" },
    controls = new Map(),
    inputs = new Map(),
    standardsDialog = { addEventListener() {} },
    save = { disabled: false };
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "test",
    hass: {},
    state: {
      permissions: { admin },
      configuration: { parameters: { ...values }, presence_source: "proxy" },
      parameters: catalog.parameters.map((definition) => ({
        ...definition,
        live_editable: live.has(definition.key),
        applicable: true,
      })),
      configuration_model: { presence: { source: "proxy" } },
      frontend_defaults: catalog.frontend,
    },
    selectSettingsSection() {},
    drawAppearanceStatus() {},
    drawArchiveManagement() {},
    renderProgramLibrary() {},
    drawButtonProgram() {},
    appearanceSettingsMarkup: () => "",
    message() {},
    shadowRoot: {
      activeElement: null,
      querySelectorAll: () => [save],
    },
    $(selector) {
      if (selector === "#settings") return container;
      if (selector === "#standards-dialog") return standardsDialog;
      if (selector === "#settings-parameters")
        return {
          entries: () => [...inputs.values()].map((input) => [input.name, input.value]),
        };
      const name = selector.match(/input\[name="([^"]+)"\]/)?.[1];
      if (name) return inputs.get(name);
      if (!controls.has(selector)) controls.set(selector, {});
      return controls.get(selector);
    },
  });
  return { panel, container, inputs, save };
};
const installation = catalog.frontend.settings_groups.filter(
  (group) => group.surface === "integration",
);
const display = catalog.parameters.filter(panelOwned);
const { panel, container, inputs, save } = makePanel(true);
// E2: installation areas are read-only overviews; their values keep their
// editing place in the integration configuration.
for (const group of installation) {
  const markup = panel.settingsParameterGroup(group);
  assert.doesNotMatch(markup, /<input/, group.id);
  assert.match(markup, /Ändern unter Sauna konfigurieren/, group.id);
  assert.match(markup, new RegExp(`standards-open:group:${group.id}"`), group.id);
}
const programs = panel.settingsParameterGroup(
  catalog.frontend.settings_groups.find((group) => group.id === "programs"),
);
assert.equal(panel.settingsParameterGroup({ id: "appearance" }), "");
for (const definition of catalog.parameters) {
  assert.equal(
    programs.includes(`name="${definition.key}"`),
    panelOwned(definition),
    definition.key,
  );
}
panel.drawSettings();
assert.match(container.innerHTML, /Sauna konfigurieren/);
assert.match(
  container.innerHTML,
  /href="\/config\/integrations\/integration\/ha_sauna"/,
);
for (const group of installation)
  assert.ok(container.innerHTML.includes(`settings-section:${group.id}`), group.id);
for (const definition of display) {
  inputs.set(definition.key, {
    name: definition.key,
    value: String(values[definition.key]),
    dataset: {},
  });
}
const edited = inputs.get("preset_step_c");
edited.value = "7";
edited.dataset.edited = "true";
panel.drawSettings();
assert.equal(edited.value, "7", "refresh preserves a display draft");
const normal = makePanel(false);
normal.panel.drawSettings();
assert.doesNotMatch(normal.container.innerHTML, /Sauna konfigurieren/);
assert.doesNotMatch(
  normal.container.innerHTML,
  /settings-section:(appearance|maintenance)/,
);
assert.match(normal.container.innerHTML, /settings-section:programs/);
assert.match(normal.container.innerHTML, /settings-section:personal/);
for (const group of catalog.frontend.settings_groups)
  assert.equal(normal.panel.settingsParameterGroup(group), "");
let saved;
panel.updateParameters = async (parameters, start, partial, method) => {
  assert.equal(method, "PATCH");
  saved = parameters;
};
(async () => {
  await panel.saveSettings();
  assert.equal(saved.preset_step_c, 7);
  for (const definition of catalog.parameters.filter((d) => !panelOwned(d)))
    assert.ok(
      !Object.hasOwn(saved, definition.key),
      `must not overwrite ${definition.key}`,
    );
  panel.state.configuration_locked = true;
  panel.drawSettings();
  assert.ok([...inputs.values()].every((input) => input.disabled));
  assert.equal(save.disabled, true);
  saved = null;
  await panel.saveSettings();
  assert.equal(saved, null, "a session locks display parameter writes");
  let normalWrites = 0;
  normal.panel.updateParameters = async () => {
    normalWrites++;
  };
  await normal.panel.saveSettings();
  assert.equal(normalWrites, 0, "normal users cannot write parameter settings");
  console.log("settings ownership and persistence tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
