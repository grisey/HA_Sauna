"use strict";

// Data only: no product decisions and no acceptance oracles live here.
const fs = require("node:fs");
const path = require("node:path");
const settings = JSON.parse(
  fs.readFileSync(path.join(__dirname, "settings.json"), "utf8"),
);

function setting(name) {
  let value = settings;
  for (const key of name.split(".")) {
    if (!Object.hasOwn(value, key)) throw new Error(`Unknown test setting: ${name}`);
    value = value[key];
  }
  return structuredClone(value);
}

module.exports = { settings, setting };
