const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_, value) => (Panel = value) },
});

function fixture(options) {
  const heading = { textContent: "Sauna" },
    select = { options, hidden: options.length < 2 },
    panel = Object.assign(Object.create(Panel.prototype), {
      entry: options[0]?.value,
      $: (selector) => ({ "#instance": select, "#instance-title": heading })[selector],
      reconcileControlCommands() {},
    });
  return { panel, heading, select };
}

test("single-instance title and hidden selector follow accepted state renames", () => {
  const { panel, heading, select } = fixture([
    { value: "garden", textContent: "Gartensauna" },
  ]);
  panel.syncInstanceTitle();
  assert.equal(heading.textContent, "Gartensauna");
  panel.acceptState({ title: "Haus & <Garten>" });
  assert.equal(heading.textContent, "Haus & <Garten>");
  assert.equal(select.options[0].textContent, "Haus & <Garten>");
  assert.equal(select.hidden, true);
  panel.acceptState({ title: "Abendsauna" });
  assert.equal(heading.textContent, "Abendsauna");
});

test("switching instances uses the selected name and leaves other names intact", () => {
  const { panel, heading, select } = fixture([
    { value: "garden", textContent: "Gartensauna" },
    { value: "house", textContent: "Haussauna" },
  ]);
  panel.entry = "house";
  panel.syncInstanceTitle();
  assert.equal(heading.textContent, "Haussauna");
  panel.acceptState({ title: "Innensauna" });
  assert.equal(heading.textContent, "Innensauna");
  assert.equal(select.options[0].textContent, "Gartensauna");
  assert.equal(select.options[1].textContent, "Innensauna");
  assert.equal(select.hidden, false);
});

test("panel retains its generic heading before an instance is available", () => {
  const { panel, heading } = fixture([]);
  panel.syncInstanceTitle();
  assert.equal(heading.textContent, "Sauna");
});

test("state refresh updates unselected names without rebuilding or selecting options", () => {
  const options = [
      { value: "garden", textContent: "Gartensauna" },
      { value: "house", textContent: "Haussauna" },
    ],
    { panel, heading, select } = fixture(options);
  select.value = "garden";
  panel.acceptState({
    title: "Gartensauna",
    instances: [
      { entry_id: "garden", title: "Gartensauna" },
      { entry_id: "house", title: "Haus & <Ruhe>" },
    ],
  });
  assert.equal(heading.textContent, "Gartensauna");
  assert.equal(select.options[1].textContent, "Haus & <Ruhe>");
  assert.equal(select.value, "garden");
  assert.equal(panel.entry, "garden");
  assert.strictEqual(select.options, options);
  assert.strictEqual(select.options[1], options[1]);
  assert.equal(select.innerHTML, undefined);
});
