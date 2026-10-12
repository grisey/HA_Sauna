"use strict";

// Test inputs consume the same editable catalogue as the application. This
// helper supplies data and metadata only; it makes no control decisions.
const fs = require("node:fs");
const path = require("node:path");

const catalog = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "../custom_components/ha_sauna/defaults.json"),
    "utf8",
  ),
);
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
  let minimum = definition.minimum ?? 0;
  if (
    [
      "target_temperature_c",
      "final_temperature_c",
      "standard_temperature_c",
      "preset_start_c",
    ].includes(key)
  )
    minimum = values.sauna_min_temperature_c;
  if (key === "oven_cooling_max_minutes") minimum = values.after_run_minutes;
  return { ...definition, default: defaultValue(key), minimum, value: values[key] };
}

function parameterDefinitions(values = parameterValues()) {
  return [...definitions.keys()].map((key) => parameterDefinition(key, values));
}

module.exports = {
  catalog,
  parameterValues,
  parameterDefinition,
  parameterDefinitions,
};
