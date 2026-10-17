const { panelFixture, parameterValues } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: {
    get: () => null,
    define: (_name, value) => {
      Panel = value;
    },
  },
  setTimeout: (callback) => callback(),
});
const copy = (value) => JSON.parse(JSON.stringify(value));
const instrument = {
  id: "appearance.instruments.default",
  label: "Gemeinsame Darstellung",
  group: "appearance",
  factory: "round",
  standard: "linear",
  active: "round",
  has_standard: true,
};
function fixture() {
  const fields = [copy(instrument)],
    calls = [];
  const inputs = new Map(),
    dialog = { showModal() {}, close() {} };
  const p = Object.assign(panelFixture(Panel), {
    entry: "one",
    generation: 0,
    state: {
      permissions: { admin: true },
      configuration: {
        parameters: parameterValues(),
        appearance: { instruments: { default: "round" } },
      },
    },
    appearanceDraft: {
      instruments: { default: "linear" },
      colors: { series_temperature: "#123456" },
    },
    appearanceRaw: { series_temperature: "#invalid" },
    progressionDraft: { "progression-end": "95" },
    $: (selector) => (selector === "#standards-dialog" ? dialog : inputs.get(selector)),
    drawStandardDialog() {},
    applyAppearance() {},
    drawCurrent() {},
    scheduleHistoryRender() {},
    refresh: async () => {
      calls.push(["refresh"]);
    },
    api: async (path, method = "GET", body) => {
      calls.push([path, method, copy(body || {})]);
      if (method === "GET" && path.endsWith("/standards")) return { fields };
      if (method === "GET" && path.includes("/state")) return p.state;
      const target =
        body.action === "overwrite_standard"
          ? fields[0].active
          : body.action === "restore_factory"
            ? fields[0].factory
            : fields[0].standard;
      const preview = {
        action: body.action,
        scope: { field: fields[0].id },
        confirmation: "reviewed-token",
        fields: [
          {
            ...fields[0],
            target,
            target_standard:
              body.action === "overwrite_standard" ? body.value : fields[0].standard,
            target_has_standard: true,
          },
        ],
      };
      if (body.confirmation)
        p.state.configuration.appearance.instruments.default = target;
      return { ...preview, ...(body.confirmation ? { applied: true } : {}) };
    },
  });
  return { p, calls, fields, inputs };
}

test("a field standard previews then confirms the reviewed draft without changing active or foreign drafts", async () => {
  const { p, calls } = fixture();
  const appearance = p.appearanceDraft,
    raw = p.appearanceRaw,
    program = p.progressionDraft;
  await p.openStandardMenu({ field: instrument.id });
  assert.equal(calls.length, 1, "opening the menu only reads the inventory");
  await p.previewStandardAction("overwrite_standard");
  assert.equal(p.state.configuration.appearance.instruments.default, "round");
  assert.equal(p.standardContext.preview.fields[0].target_standard, "linear");
  assert.equal(calls.filter(([, , body]) => body?.confirmation).length, 0);
  await p.confirmStandardAction();
  assert.deepEqual(calls.find(([, , body]) => body?.confirmation)[2], {
    action: "overwrite_standard",
    field: instrument.id,
    value: "linear",
    confirmation: "reviewed-token",
  });
  assert.equal(p.state.configuration.appearance.instruments.default, "round");
  assert.equal(p.appearanceDraft, appearance);
  assert.equal(p.appearanceRaw, raw);
  assert.equal(p.progressionDraft, program);
});

test("restore waits for loaded active values and replaces only the affected draft", async () => {
  const { p, calls, inputs } = fixture();
  const field = { value: "linear", dataset: {} };
  inputs.set('[data-appearance-instrument="default"]', field);
  await p.openStandardMenu({ field: instrument.id });
  await p.previewStandardAction("restore_factory");
  assert.equal(
    p.appearanceDraft.instruments.default,
    "linear",
    "preview has no draft side effect",
  );
  await p.confirmStandardAction();
  assert.equal(field.value, "round");
  assert.equal(p.appearanceDraft.instruments.default, "round");
  assert.equal(p.appearanceDraft.colors.series_temperature, "#123456");
  assert.equal(p.appearanceRaw.series_temperature, "#invalid");
  assert.equal(p.progressionDraft["progression-end"], "95");
  assert.ok(
    calls.find(([path]) => path?.includes("/state")),
    "reload uses the existing status reader",
  );
});

test("stale 409 keeps drafts and requires explicit confirmation of the new preview", async () => {
  const { p, calls } = fixture();
  await p.openStandardMenu({ field: instrument.id });
  await p.previewStandardAction("restore_standard");
  const before = copy(p.appearanceDraft),
    preview = { ...p.standardContext.preview, confirmation: "new-token" };
  const api = p.api;
  let reject = true;
  p.api = async (...args) => {
    if (args[2]?.confirmation && reject) {
      reject = false;
      throw { status_code: 409, body: { error: "Quellen geändert", preview } };
    }
    return api(...args);
  };
  await p.confirmStandardAction();
  assert.deepEqual(copy(p.appearanceDraft), before);
  assert.equal(p.standardContext.preview.confirmation, "new-token");
  assert.equal(
    calls.filter(([, , body]) => body?.confirmation).length,
    0,
    "no automatic retry",
  );
  await p.confirmStandardAction();
  assert.equal(
    calls.find(([, , body]) => body?.confirmation)[2].confirmation,
    "new-token",
  );
});

test("locks do not partially replace drafts; late completions cannot touch a different sauna", async () => {
  const { p } = fixture();
  await p.openStandardMenu({ field: instrument.id });
  await p.previewStandardAction("restore_standard");
  p.api = async () => {
    throw { status_code: 409, body: { error: "Sitzung läuft" } };
  };
  const before = copy(p.appearanceDraft);
  await p.confirmStandardAction();
  assert.deepEqual(copy(p.appearanceDraft), before);
  assert.equal(p.standardContext.preview, null);
  assert.equal(p.standardContext.message, "Sitzung läuft");
  const next = fixture();
  await next.p.openStandardMenu({ field: instrument.id });
  await next.p.previewStandardAction("restore_standard");
  let complete;
  next.p.api = () =>
    new Promise((resolve) => {
      complete = resolve;
    });
  const pending = next.p.confirmStandardAction();
  next.p.entry = "two";
  next.p.generation++;
  next.p.appearanceDraft = { colors: { series_temperature: "#654321" } };
  complete({ action: "restore_standard", fields: [] });
  await pending;
  assert.equal(next.p.appearanceDraft.colors.series_temperature, "#654321");
  assert.equal(next.calls.filter(([path]) => path === "refresh").length, 0);
});

test("global inventory includes hidden fields, program fields have no standard menu", async () => {
  const { p, fields } = fixture();
  fields.push({
    id: "parameters.sensor_timeout_seconds",
    group: "sensors",
    applicable: false,
    active: 60,
    standard: 60,
    factory: 60,
    label: "Gültigkeit",
  });
  await p.openStandardMenu({ all: true });
  assert.equal(p.standardContext.fields.length, 2);
  assert.equal(p.standardContext.fields[1].applicable, false);
  p.state.permissions.admin = false;
  p.standardContext = null;
  await p.openStandardMenu({ all: true });
  assert.equal(p.standardContext, null);
  p.state.frontend_defaults = p.panel.config.frontend_defaults;
  assert.equal(p.settingsParameterGroup({ id: "programs" }), "");
});
