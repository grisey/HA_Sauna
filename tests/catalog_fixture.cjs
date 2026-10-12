"use strict";

// Test inputs consume the same editable catalogue as the application. This
// helper supplies data and metadata only; it makes no control decisions.
const fs = require("node:fs");
const path = require("node:path");

const rawCatalog = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "../custom_components/ha_sauna/defaults.json"),
    "utf8",
  ),
);
const rawDefinitions = new Map(rawCatalog.parameters.map((item) => [item.key, item]));

function resolve(value, trail = []) {
  if (Array.isArray(value)) return value.map((item) => resolve(item, trail));
  if (!value || typeof value !== "object") return value;
  if (Object.keys(value).length === 1 && (value.parameter || value.preference)) {
    const kind = value.parameter ? "parameter" : "preference";
    const key = value[kind], identity = `${kind}:${key}`;
    if (trail.includes(identity)) throw new Error(`Cyclic fixture reference: ${identity}`);
    const definition = kind === "parameter" ? rawDefinitions.get(key) : rawCatalog.appearance.preferences[key];
    if (!definition) throw new Error(`Unknown fixture reference: ${identity}`);
    return resolve(definition.default, [...trail, identity]);
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolve(item, trail)]));
}

const catalog = resolve(rawCatalog);
const definitions = new Map(catalog.parameters.map((item) => [item.key, item]));

function defaultValue(key) {
  const value = definitions.get(key).default;
  return value && typeof value === "object" ? defaultValue(value.parameter) : value;
}

function parameterValues(overrides = {}) {
  return {
    ...Object.fromEntries(
      [...definitions.keys()].map((key) => [key, defaultValue(key)]),
    ),
    ...overrides,
  };
}

function parameterDefinition(key, values = parameterValues()) {
  const definition = definitions.get(key);
  if (!definition) throw new Error(`Unknown fixture parameter: ${key}`);
  const minimum = definition.minimum_parameter
    ? values[definition.minimum_parameter]
    : definition.minimum;
  return { ...definition, default: defaultValue(key), minimum, value: values[key] };
}

function parameterDefinitions(values = parameterValues()) {
  return [...definitions.keys()].map((key) => parameterDefinition(key, values));
}

function preferences(overrides = {}) {
  return { ...Object.fromEntries(Object.entries(catalog.appearance.preferences).map(([key, item]) => [key, item.default])), ...overrides };
}

function panelFixture(panelOrClass) {
  const panel = typeof panelOrClass === "function" ? Object.create(panelOrClass.prototype) : panelOrClass;
  panel.panel = { config: { appearance_catalog: structuredClone(catalog.appearance), frontend_defaults: structuredClone(catalog.frontend) } };
  return panel;
}

module.exports = {
  catalog,
  rawCatalog,
  resolve,
  preferences,
  panelFixture,
  parameterValues,
  parameterDefinition,
  parameterDefinitions,
};
