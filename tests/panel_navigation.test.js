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
});

const navigationPanel = () => {
  const nodes = Object.fromEntries(
    [
      "#current",
      "#details",
      "#history",
      "#settings",
      "#plots",
      "#detection-plots",
      "#gangs",
      "#event-list",
      ".detail-tabs",
    ].map((key) => [key, { hidden: false }]),
  );
  const button = (action) => ({
    dataset: { action },
    setAttribute(name, value) {
      this[name] = value;
    },
  });
  const main = ["overview", "history", "details", "settings"].map(button);
  const detail = ["detail", "detail-history", "diagnostics"].map(button);
  nodes['.main-tabs [data-action="details"]'] = main[2];
  const panel = Object.assign(Object.create(Panel.prototype), {
    state: { permissions: { admin: true } },
    message: () => {},
    refresh: async () => {},
    drawHistory: () => {},
    drawSettings: () => {},
    cancelHistoryFrame: () => {},
    $: (selector) => nodes[selector] || null,
    shadowRoot: {
      querySelectorAll: (selector) =>
        selector === ".main-tabs button"
          ? main
          : selector === ".detail-tabs button"
            ? detail
            : [],
    },
  });
  return { panel, nodes, main, detail };
};

test("one main selection follows each view, including the shared detail history", async () => {
  const { panel, nodes, main, detail } = navigationPanel();
  for (const [action, selected, view] of [
    ["overview", "overview", "overview"],
    ["history", "history", "history"],
    ["details", "details", "detail"],
    ["detail-history", "details", "history"],
    ["diagnostics", "details", "diagnostics"],
    ["settings", "settings", "settings"],
  ]) {
    await panel.action(action);
    assert.equal(panel.view, view);
    assert.deepEqual(
      main
        .filter((button) => button["aria-current"] === "page")
        .map((button) => button.dataset.action),
      [selected],
    );
    assert.equal(nodes[".detail-tabs"].hidden, selected !== "details");
    assert.equal(nodes["#history"].hidden, !["history", "diagnostics"].includes(view));
    panel.syncNavigation();
    assert.deepEqual(
      main
        .filter((button) => button["aria-current"] === "page")
        .map((button) => button.dataset.action),
      [selected],
    );
  }
  assert.equal(
    detail.find((button) => button.dataset.action === "diagnostics")["aria-current"],
    "page",
  );
  await panel.action("details");
  assert.equal(panel.view, "diagnostics", "returning to details preserves its subpage");
});

test("lost admin permission returns detail views to control", async () => {
  const { panel, nodes, main } = navigationPanel();
  await panel.action("detail-history");
  panel.state.permissions.admin = false;
  panel.syncNavigation();
  assert.equal(panel.view, "overview");
  assert.equal(nodes['.main-tabs [data-action="details"]'].hidden, true);
  assert.equal(main[0]["aria-current"], "page");
  assert.equal(nodes["#current"].hidden, false);
});

test("zoom buttons use the history chart midpoint", async () => {
  const { panel } = navigationPanel();
  panel.window = [0, 100];
  panel.ensureHistoryWindow = () => {};
  const svg = { getBoundingClientRect: () => ({ left: 20, width: 200 }) };
  panel.$ = (selector) => (selector === "svg.session-chart" ? svg : null);
  panel.zoomAt = (factor, x, target) => {
    assert.equal(factor, 2);
    assert.equal(x, 120);
    assert.equal(target, svg);
  };
  await panel.action("zoom-in");
});

test("focused button program keeps its choice while accepting a new lock", () => {
  const attrs = new Map();
  const field = {
    nodeType: 1,
    nodeName: "SELECT",
    value: "program-a",
    get attributes() {
      return [...attrs].map(([name, value]) => ({ name, value }));
    },
    hasAttribute: (name) => attrs.has(name),
    getAttribute: (name) => attrs.get(name) ?? null,
    setAttribute: (name, value) => attrs.set(name, value),
    removeAttribute: (name) => attrs.delete(name),
  };
  const next = {
    nodeType: 1,
    nodeName: "SELECT",
    value: "program-b",
    attributes: [{ name: "disabled", value: "" }],
    hasAttribute: (name) => name === "disabled",
    getAttribute: (name) => (name === "disabled" ? "" : null),
  };
  const panel = Object.assign(Object.create(Panel.prototype), {
    shadowRoot: { activeElement: field },
  });
  panel.patchNode(field, next);
  assert.equal(attrs.has("disabled"), true);
  assert.equal(field.value, "program-a");
});

test("empty history clears the retained overview and every visible sink", () => {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector))
      nodes.set(selector, {
        innerHTML: "old session",
        textContent: "old time",
        replaceChildren() {
          this.innerHTML = "";
        },
      });
    return nodes.get(selector);
  };
  let destroyed = 0;
  const panel = Object.assign(Object.create(Panel.prototype), {
    historyChart: { destroy: () => destroyed++ },
    $: node,
    updateMarkup: (selector, html) => {
      node(selector).innerHTML = html;
    },
  });
  panel.clearHistoryDisplay();
  assert.equal(destroyed, 1);
  assert.equal(node("#history-overview").innerHTML, "");
  assert.equal(node("#range").textContent, "");
  for (const selector of ["#gangs", "#event-list", "#detection-plots"])
    assert.equal(node(selector).innerHTML, "");
  assert.match(node("#plots").innerHTML, /Noch keine Sitzungsdaten/);
});
