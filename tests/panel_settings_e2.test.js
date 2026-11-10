"use strict";
// E2 Einstellungen nach Funktion: Reihenfolge aus dem Katalog, Anlagenwerte als
// Übersicht mit ihrer Einstellungsstelle, Technik eingeklappt, drei Wertstufen aus
// dem Backend (G19). Uses the production catalog and rendering paths.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
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
const live = new Set([
  "target_temperature_c",
  "final_temperature_c",
  "temperature_gangs",
]);
const values = Object.fromEntries(
  catalog.parameters.map((d) => [
    d.key,
    d.default !== null && typeof d.default === "object"
      ? catalog.parameters.find((source) => source.key === d.default.parameter).default
      : d.default,
  ]),
);

function makePanel({ admin = true, fieldStates = {}, applicable = () => true } = {}) {
  const container = { innerHTML: "" },
    controls = new Map(),
    inputs = [],
    statuses = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "test",
    hass: {},
    state: {
      permissions: { admin },
      configuration: { parameters: { ...values }, presence_source: "proxy" },
      parameters: catalog.parameters.map((definition) => ({
        ...definition,
        live_editable: live.has(definition.key),
        applicable: applicable(definition),
      })),
      field_states: fieldStates,
      configuration_model: { presence: { source: "proxy" } },
      frontend_defaults: catalog.frontend,
    },
    selectSettingsSection() {},
    drawAppearanceStatus() {},
    syncAppearanceEditor() {},
    drawArchiveManagement() {},
    drawRecognitionLearning() {},
    drawCalibration() {},
    renderProgramLibrary() {},
    drawButtonProgram() {},
    appearanceSettingsMarkup: () => '<div class="card" id="appearance-settings"></div>',
    shadowRoot: {
      activeElement: null,
      querySelectorAll(selector) {
        if (selector === "#parameters input[data-edited]")
          return inputs.filter((input) => input.dataset.edited);
        if (selector === "[data-settings-drafts]") return statuses;
        return [];
      },
    },
    $(selector) {
      if (selector === "#settings") return container;
      if (selector.startsWith("#parameters input")) return null;
      if (!controls.has(selector)) controls.set(selector, { addEventListener() {} });
      return controls.get(selector);
    },
  });
  return { panel, container, inputs, statuses };
}

const group = (id) => catalog.frontend.settings_groups.find((item) => item.id === id);

test("navigation follows the functional catalog order", () => {
  const { panel, container } = makePanel();
  panel.drawSettings();
  const order = [
    ...container.innerHTML.matchAll(/data-action="settings-section:([a-z_]+)"/g),
  ].map((match) => match[1]);
  assert.deepEqual(
    order,
    catalog.frontend.settings_groups.map((item) => item.id),
  );
  assert.equal(order.at(-1), "technical", "Technik steht ganz hinten");
  assert.ok(order.indexOf("programs") < order.indexOf("light"));
  assert.ok(order.indexOf("light") < order.indexOf("installation"));
  assert.ok(order.indexOf("installation") < order.indexOf("control"));
  assert.ok(order.indexOf("recognition") < order.indexOf("notifications"));
  assert.match(
    container.innerHTML,
    /settings-section:technical"[^>]*data-settings-collapsed/,
  );
  assert.match(container.innerHTML, /id="notification-settings"/);
});

test("an area without content or applicable fields is left out", () => {
  const { panel, container } = makePanel({
    applicable: (d) => d.settings_group !== "monitoring",
  });
  panel.drawSettings();
  assert.doesNotMatch(container.innerHTML, /data-settings-section="monitoring"/);
  assert.match(container.innerHTML, /data-settings-section="control"/);
});

test("normal users keep programs, notifications and personal without settings values", () => {
  const { panel, container } = makePanel({ admin: false });
  panel.drawSettings();
  const order = [
    ...container.innerHTML.matchAll(/data-action="settings-section:([a-z_]+)"/g),
  ].map((match) => match[1]);
  assert.deepEqual(order, ["programs", "notifications", "personal"]);
  for (const item of catalog.frontend.settings_groups)
    assert.equal(panel.settingsParameterGroup(item), "", item.id);
});

test("installation areas show values read-only with the editing place", () => {
  const { panel } = makePanel();
  const markup = panel.settingsParameterGroup(group("installation"));
  assert.doesNotMatch(markup, /<input/);
  assert.match(markup, /data-setting-value="nominal_power_kw">4,5 kW</);
  assert.match(markup, /data-setting-value="electricity_price_eur_per_kwh">leer</);
  assert.match(markup, /standards-open:field:parameters\.nominal_power_kw"/);
  assert.match(markup, /Ändern unter Sauna konfigurieren › Anlage und Energie/);
  assert.match(markup, /standards-open:group:installation"/);
  // Subgroups appear in catalog order with their own heading.
  assert.ok(
    markup.indexOf("Ofen, Strompreis und Kabine") < markup.indexOf("Gezählte Heizzeit"),
  );
});

test("inapplicable fields stay hidden like in the integration configuration", () => {
  const { panel } = makePanel({
    applicable: (d) => d.key !== "power_heating_threshold_w",
  });
  const markup = panel.settingsParameterGroup(group("installation"));
  assert.doesNotMatch(markup, /power_heating_threshold_w/);
  assert.match(markup, /heat_reset_minutes/);
});

test("the technical area is collapsed and lists fixed values without menu", () => {
  const { panel } = makePanel();
  const markup = panel.settingsParameterGroup(group("technical"));
  assert.match(markup, /data-settings-layout="collapsed"/);
  assert.match(
    markup,
    /<details class="card settings-card" data-settings-subgroup="warmup">/,
  );
  assert.doesNotMatch(markup, /<details[^>]* open/);
  assert.match(markup, /Fest vorgegeben/);
  for (const definition of catalog.parameters.filter(
    (d) => d.settings_group === "technical",
  )) {
    assert.match(
      markup,
      new RegExp(`data-setting-value="${definition.key}"`),
      definition.key,
    );
    assert.equal(
      markup.includes(`standards-open:field:parameters.${definition.key}"`),
      definition.configuration_surface !== "catalog",
      definition.key,
    );
  }
  assert.match(
    markup,
    /data-setting-value="warmup_rate_relative_tolerance">0,000000001 Anteil</,
  );
  assert.doesNotMatch(markup, /<input/);
});

test("deviations name the standard or the factory value as reference", () => {
  const { panel } = makePanel({
    fieldStates: {
      "parameters.minimum_heating_minutes": {
        reference: "standard",
        reference_value: 12,
        differs: true,
      },
      "parameters.light_output_hysteresis_percent": {
        reference: "factory",
        reference_value: 0.1,
        differs: true,
      },
      "parameters.thermostat_cooldown_minutes": {
        reference: "standard",
        reference_value: 5,
        differs: false,
      },
    },
  });
  const control = panel.settingsParameterGroup(group("control"));
  assert.match(control, /Abweichend vom Standard: 12 min/);
  assert.match(
    control,
    /data-field-state="parameters.thermostat_cooldown_minutes" data-field-unit="min" hidden><\/small>/,
  );
  const light = panel.settingsParameterGroup(group("light"));
  assert.match(light, /Abweichend von der Werkseinstellung: 0,1 Prozentpunkte/);
  // Panel-owned fields use the same marking below the input.
  const { panel: other } = makePanel({
    fieldStates: {
      "parameters.preset_count": {
        reference: "factory",
        reference_value: 6,
        differs: true,
      },
    },
  });
  const programs = other.settingsParameterGroup(group("programs"));
  assert.match(programs, /name="preset_count"/);
  assert.match(programs, /Abweichend von der Werkseinstellung: 6</);
});

test("panel-owned fields can be discarded and show unsaved drafts", () => {
  const { panel, inputs, statuses } = makePanel();
  const draft = { name: "preset_step_c", value: "7", dataset: { edited: "true" } };
  inputs.push(draft, { name: "preset_count", value: "6", dataset: {} });
  const status = { dataset: { settingsDrafts: "" }, textContent: "" };
  statuses.push(status);
  panel.drawSettingsDrafts();
  assert.equal(status.textContent, "Nicht gespeichert: Abstand der Temperaturtasten");
  panel.discardSettingsDrafts();
  assert.equal(draft.value, values.preset_step_c);
  assert.equal(draft.dataset.edited, undefined);
  assert.equal(status.textContent, "");
  const programs = panel.settingsParameterGroup(group("programs"));
  assert.match(programs, /data-action="settings-discard"/);
  assert.match(programs, /Einstellungen speichern/);
  assert.match(programs, /standards-open:group:programs"/);
  for (const key of live) assert.doesNotMatch(programs, new RegExp(`name="${key}"`));
});

test("labels and explanations contain no internal identifiers", () => {
  for (const definition of catalog.parameters) {
    assert.doesNotMatch(
      definition.label,
      /[a-z]+_[a-z]+|Proxy|Katalog/,
      definition.key,
    );
    assert.doesNotMatch(definition.description, /[a-z]+_[a-z]+|Proxy/, definition.key);
  }
  for (const item of [
    ...catalog.frontend.settings_groups,
    ...catalog.frontend.settings_subgroups,
  ])
    assert.doesNotMatch(item.label, /[a-z]+_[a-z]+/, item.id);
});

test("appearance fields use the same deviation marking", () => {
  const { panel } = makePanel({
    fieldStates: {
      "appearance.instruments.temperature": {
        reference: "factory",
        reference_value: "inherit",
        differs: true,
      },
      "appearance.colors.series_temperature": {
        reference: "standard",
        reference_value: "#CD895F",
        differs: true,
      },
      "appearance.scales.humidity.maximum": {
        reference: "factory",
        reference_value: 50,
        differs: false,
      },
    },
  });
  assert.equal(
    panel.fieldStateText("appearance.instruments.temperature"),
    "Abweichend von der Werkseinstellung: Gemeinsame Vorgabe",
  );
  assert.equal(
    panel.fieldStateText("appearance.colors.series_temperature"),
    "Abweichend vom Standard: #CD895F",
  );
  assert.match(
    panel.fieldStateMarkup("appearance.scales.humidity.maximum", "%"),
    /data-field-state="appearance\.scales\.humidity\.maximum" data-field-unit="%" hidden>/,
  );
});

test("notification settings name deviations from the factory value", () => {
  // BN-Einstellungen haben keinen gespeicherten Standard (Ausnahme der drei Stufen).
  const nodes = new Map();
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    notificationSettings: {
      entry: "entry-1",
      settings: {
        occasions: { timer_start: true, ready: false },
        timer_runtime_minutes: 180,
      },
      factory_deviations: { timer_runtime_minutes: 240, occasions: { ready: true } },
      occasions: [
        { id: "timer_start", label: "Zeitschaltuhr beim Start", description: "Start." },
        { id: "ready", label: "Bereit", description: "Bereit." },
      ],
      values: [
        {
          key: "timer_runtime_minutes",
          label: "Laufzeit der Zeitschaltuhr",
          unit: "min",
          description: "Ofenzeit.",
        },
      ],
      test: null,
      targets: 1,
      can_edit: true,
      can_test: true,
    },
    $: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "" });
      return nodes.get(selector);
    },
    updateMarkup(selector, html) {
      this.$(selector).innerHTML = html;
    },
  });
  panel.drawNotificationSettings();
  const html = nodes.get("#notification-settings").innerHTML;
  assert.match(html, /Abweichend von der Werkseinstellung: 240 min/);
  assert.match(
    html,
    /Bereit\.<\/small><small class="settings-field-state">Abweichend von der Werkseinstellung: ein/,
  );
  assert.equal(html.match(/settings-field-state/g).length, 2);
});
