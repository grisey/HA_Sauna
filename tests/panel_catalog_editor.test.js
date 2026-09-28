"use strict";

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
  crypto: { randomUUID: () => "new-program" },
  clearInterval,
  clearTimeout,
});

const programs = [
  { id: "a", name: "A", start_c: 80, end_c: 90, distribution_gangs: 3 },
  { id: "b", name: "B", start_c: 85, end_c: 95, distribution_gangs: 2 },
  { id: "c", name: "C", start_c: 75, end_c: 85, distribution_gangs: 1 },
];
const ids = (panel) =>
  JSON.parse(JSON.stringify(panel.currentProgramDraft().map((program) => program.id)));
const makePanel = () => {
  const library = { dataset: {}, innerHTML: "" };
  const panel = Object.assign(Object.create(Panel.prototype), {
    entry: "sauna-1",
    generation: 0,
    state: {
      permissions: { program: true },
      configuration_locked: false,
      configuration: {
        temperature_programs: programs,
        selected_program_id: "a",
        button_program: "b",
      },
      parameters: [
        { key: "target_temperature_c", minimum: 60, maximum: 110 },
        { key: "temperature_gangs", minimum: 1, maximum: 8 },
      ],
    },
    $: (selector) => (selector === "#program-library" ? library : null),
    shadowRoot: { querySelectorAll: () => [] },
    refresh: async () => {},
  });
  return { panel, library };
};

test("closed rows are compact and a single editor retains its inputs during polling", () => {
  const { panel, library } = makePanel();
  panel.renderProgramLibrary();
  assert.equal((library.innerHTML.match(/class="program-row"/g) || []).length, 3);
  assert.doesNotMatch(library.innerHTML, /data-program-field=/);
  panel.openProgramEditor("c");
  assert.equal((library.innerHTML.match(/data-program-field="name"/g) || []).length, 1);
  panel.openProgramEditor("a");
  assert.equal(panel.programEditor.id, "c");
  panel.updateProgramEditorField({
    matches: (selector) => (selector === "[data-program-step]" ? false : true),
    dataset: { programField: "name" },
    value: "C neu",
  });
  const before = library.innerHTML;
  panel.renderProgramLibrary();
  assert.equal(library.innerHTML, before, "polling does not rebuild an open editor");
  panel.finishProgramEditor();
  assert.equal(panel.programDraft[2].name, "C neu");
  assert.equal(panel.programEditor, null);
  assert.match(library.innerHTML, /C neu/);
});

test("cancel, new program, validation, and deletion protection", () => {
  const { panel } = makePanel();
  panel.openProgramEditor("c");
  panel.programEditor.values.name = "";
  assert.throws(() => panel.finishProgramEditor(), /Programmnamen/);
  assert.equal(panel.programEditor.id, "c");
  panel.cancelProgramEditor();
  assert.equal(panel.programDraft, undefined);
  panel.addProgram();
  assert.equal(panel.programEditor.isNew, true);
  panel.cancelProgramEditor();
  assert.deepEqual(ids(panel), ["a", "b", "c"]);
  panel.openProgramEditor("a");
  panel.removeProgram("a");
  assert.equal(
    panel.currentProgramDraft().length,
    3,
    "selected program cannot be removed",
  );
  panel.cancelProgramEditor();
  panel.openProgramEditor("c");
  panel.removeProgram("c");
  assert.deepEqual(ids(panel), ["a", "b"]);
});

test("edited number input strings become numeric steps without losing invalid drafts", async () => {
  const { panel } = makePanel();
  panel.message = () => {};
  panel.openProgramEditor("c");
  for (const [field, value] of [
    ["start_c", "80"],
    ["end_c", "90"],
    ["distribution_gangs", "3"],
  ])
    panel.updateProgramEditorField({
      matches: (selector) => selector === "[data-program-field]",
      dataset: { programField: field },
      value,
    });
  await panel.action("catalog-kind:steps:c");
  assert.deepEqual(
    JSON.parse(JSON.stringify(panel.programEditor.values.temperature_steps)),
    [80, 85, 90],
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(panel.distributedSteps(80, 90, 1))),
    [80, 90],
  );
  panel.programEditor.kind = "even";
  panel.programEditor.values.start_c = "";
  await assert.rejects(() => panel.action("catalog-kind:steps:c"), /Programmwerte/);
  assert.equal(panel.programEditor.values.start_c, "");
  assert.deepEqual(
    JSON.parse(JSON.stringify(panel.distributedSteps("abc", "90", 3))),
    [],
  );
});

test("server catalog IDs retain colons through the rendered editor actions", async () => {
  for (const id of ["custom:one", "custom:one:two"]) {
    const { panel, library } = makePanel();
    panel.message = () => {};
    panel.state.configuration.temperature_programs = [{ ...programs[2], id }];
    panel.renderProgramLibrary();
    const edit = library.innerHTML.match(/data-action="(program-edit:[^"]+)"/)[1];
    await panel.action(edit);
    assert.equal(panel.programEditor.id, id);
    const steps = library.innerHTML.match(/data-action="(catalog-kind:steps:[^"]+)"/)[1];
    await panel.action(steps);
    assert.equal(panel.programEditor.kind, "steps");
    assert.deepEqual(Array.from(panel.programEditor.values.temperature_steps), [75, 85]);
    const even = library.innerHTML.match(/data-action="(catalog-kind:even:[^"]+)"/)[1];
    await panel.action(even);
    panel.finishProgramEditor();
    assert.equal(panel.currentProgramDraft()[0].id, id);
  }
});

test("keyboard order and pointer insertion preserve IDs and can be discarded", () => {
  const { panel, library } = makePanel();
  panel.moveProgram("c", -1);
  assert.deepEqual(ids(panel), ["a", "c", "b"]);
  assert.match(library.innerHTML, /C, Position 2 von 3/);
  panel.moveProgram("a", 0, 2);
  assert.deepEqual(ids(panel), ["c", "b", "a"]);
  panel.openProgramEditor("c");
  panel.moveProgram("a", -1);
  assert.deepEqual(ids(panel), ["c", "b", "a"]);
  panel.cancelProgramEditor();
  panel.discardPrograms();
  assert.deepEqual(ids(panel), ["a", "b", "c"]);
});

test("pointer drag inserts at the indicated row and cancel keeps the order", () => {
  const { panel } = makePanel();
  const rows = programs.map((program, index) => ({
    dataset: { programId: program.id },
    getBoundingClientRect: () => ({ top: index * 40, height: 40 }),
    removeAttribute() {
      delete this.dataset.dropPosition;
    },
    closest() {
      return this;
    },
  }));
  panel.shadowRoot = {
    querySelectorAll: (selector) => (selector === ".program-row" ? rows : []),
    elementFromPoint: (_x, y) => rows[Math.min(2, Math.floor(y / 40))],
  };
  panel.getBoundingClientRect = () => ({ top: 0, bottom: 400 });
  const handle = {
    dataset: { programDrag: "a" },
    disabled: false,
    setPointerCapture() {},
    releasePointerCapture() {},
  };
  const down = { target: { closest: () => handle }, pointerId: 1, preventDefault() {} };
  panel.beginProgramDrag(down);
  panel.updateProgramDrag({
    pointerId: 1,
    clientX: 20,
    clientY: 105,
    preventDefault() {},
  });
  assert.equal(rows[2].dataset.dropPosition, "after");
  panel.finishProgramDrag({ pointerId: 1 });
  assert.deepEqual(ids(panel), ["b", "c", "a"]);
  handle.dataset.programDrag = "c";
  panel.beginProgramDrag(down);
  panel.cancelProgramDrag();
  assert.deepEqual(ids(panel), ["b", "c", "a"]);
});

test("save blocks duplicate submissions and retains draft on failure", async () => {
  const { panel, library } = makePanel();
  panel.moveProgram("c", -1);
  let fail;
  const held = new Promise((_resolve, reject) => {
    fail = reject;
  });
  const calls = [];
  panel.api = async (...args) => {
    calls.push(args);
    return held;
  };
  const request = panel.savePrograms();
  panel.savePrograms();
  panel.moveProgram("a", 1);
  assert.equal(calls.length, 1);
  assert.match(
    library.innerHTML,
    /data-action="program-save" class="confirm" disabled/,
  );
  fail(Error("rejected"));
  await assert.rejects(request, /rejected/);
  assert.deepEqual(ids(panel), ["a", "c", "b"]);
  assert.match(
    library.innerHTML,
    /data-action="program-save" class="confirm" (?!disabled)/,
  );
});

test("a rejected save from a previous sauna does not surface in the new one", async () => {
  const { panel } = makePanel();
  panel.moveProgram("c", -1);
  let rejectSave;
  panel.api = () =>
    new Promise((_resolve, reject) => {
      rejectSave = reject;
    });
  const request = panel.savePrograms();
  panel.entry = "sauna-2";
  panel.generation++;
  panel.programSavePending = null;
  rejectSave(Error("old sauna failed"));
  await assert.doesNotReject(request);
});

test("a confirmed catalog save releases its draft for a newer server catalog", async () => {
  const { panel } = makePanel();
  panel.moveProgram("c", -1);
  const saved = panel.currentProgramDraft();
  const newer = [{ ...saved[0], name: "Von anderem Client geändert" }, ...saved.slice(1)];
  panel.api = async () => ({ programs: saved });
  panel.refresh = async () => {
    panel.state.configuration.temperature_programs = newer;
  };
  await panel.savePrograms();
  assert.equal(panel.programDraft, null);
  assert.equal(panel.currentProgramDraft()[0].name, "Von anderem Client geändert");
});

test("disconnect releases drag and transient save states while retaining a draft", () => {
  const { panel } = makePanel();
  panel.moveProgram("c", -1);
  let released = 0;
  panel.programDrag = {
    handle: {
      releasePointerCapture() {
        released++;
      },
    },
    pointerId: 3,
  };
  panel.programSavePending = { entry: panel.entry };
  panel.programSaveState = "saving";
  panel.cancelHistoryFrame = () => {};
  panel.chartPointers = new Map();
  panel.disconnectedCallback();
  assert.equal(released, 1);
  assert.equal(panel.programSavePending, null);
  assert.equal(panel.programSaveState, null);
  assert.equal(panel.programDrag, null);
  assert.deepEqual(ids(panel), ["a", "c", "b"]);
});
