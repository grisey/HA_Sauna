"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {
    attachShadow() {
      this.shadowRoot = { querySelectorAll: () => [] };
    }
  },
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  clearTimeout,
});

function fullscreenPanel() {
  const nodes = new Map(),
    listeners = new Map(),
    root = { fullscreenElement: null },
    node = (selector) => {
      if (!nodes.has(selector))
        nodes.set(selector, {
          hidden: false,
          attributes: {},
          setAttribute(name, value) {
            this.attributes[name] = value;
          },
          toggleAttribute(name, enabled) {
            if (enabled) this.attributes[name] = "";
            else delete this.attributes[name];
          },
          focus() {
            this.focused = true;
          },
        });
      return nodes.get(selector);
    },
    panel = Object.assign(new Panel(), {
      state: { permissions: { admin: true } },
      $: node,
      getRootNode: () => root,
      message() {},
      refresh: async () => {},
      fitInstrumentReadouts() {},
      cancelHistoryFrame() {},
      cancelProgramDrag() {},
      cancelTemperatureDrag() {},
      applyAppearance() {},
      requestCount: 0,
      exitCount: 0,
    });
  panel.ownerDocument = {
    fullscreenEnabled: true,
    fullscreenElement: null,
    addEventListener(kind, callback) {
      listeners.set(kind, callback);
    },
    removeEventListener(kind, callback) {
      if (listeners.get(kind) === callback) listeners.delete(kind);
    },
    async exitFullscreen() {
      panel.exitCount++;
      root.fullscreenElement = null;
      this.fullscreenElement = null;
      listeners.get("fullscreenchange")?.();
    },
  };
  panel.requestFullscreen = async () => {
    panel.requestCount++;
    root.fullscreenElement = panel;
    // Home Assistant hosts the panel inside another element's shadow tree.
    panel.ownerDocument.fullscreenElement = { localName: "home-assistant" };
    listeners.get("fullscreenchange")?.();
  };
  panel.connectedCallback();
  return { panel, root, node, listeners };
}

test("only actual panel fullscreen shows the menu, including a nested shadow tree", async () => {
  const { panel, root, node } = fullscreenPanel();
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(node('[data-action="fullscreen"]').hidden, false);
  await panel.action("menu");
  assert.equal(panel.fullscreenMenuOpen, false);

  root.fullscreenElement = { localName: "another-panel" };
  panel.syncFullscreenNavigation();
  assert.equal(node('[data-action="menu"]').hidden, true);

  await panel.action("fullscreen");
  assert.equal(panel.requestCount, 1);
  assert.notEqual(panel.ownerDocument.fullscreenElement, panel);
  assert.equal(panel.isPanelFullscreen(), true);
  assert.equal(node('[data-action="menu"]').hidden, false);
  assert.equal(node(".main-tabs").hidden, true);
  assert.equal(
    node('[data-action="fullscreen"]').attributes["aria-label"],
    "Vollbild verlassen",
  );
  assert.ok("hidden" in node('[data-fullscreen-icon="enter"]').attributes);
  assert.ok(!("hidden" in node('[data-fullscreen-icon="exit"]').attributes));

  await panel.action("menu");
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(node('[data-action="menu"]').attributes["aria-expanded"], "true");
  assert.equal(node('.main-tabs [aria-current="page"]').focused, true);
  await panel.action("history");
  assert.equal(panel.view, "history");
  assert.equal(node(".main-tabs").hidden, true);
  assert.equal(node('[data-action="menu"]').focused, true);

  await panel.action("fullscreen");
  assert.equal(panel.exitCount, 1);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
});

test("browser exit while the fullscreen menu is open restores embedded navigation", async () => {
  const { panel, root, node, listeners } = fullscreenPanel();
  await panel.action("fullscreen");
  await panel.action("menu");
  root.fullscreenElement = null;
  panel.ownerDocument.fullscreenElement = null;
  listeners.get("fullscreenchange")();
  assert.equal(panel.fullscreenMenuOpen, false);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(node('[data-action="fullscreen"]').attributes["aria-label"], "Vollbild");
  assert.ok(!("hidden" in node('[data-fullscreen-icon="enter"]').attributes));
  assert.ok("hidden" in node('[data-fullscreen-icon="exit"]').attributes);
});

test("pending, declined, or unconfirmed fullscreen requests never invent fullscreen state", async () => {
  const { panel, node } = fullscreenPanel();
  let resolveRequest;
  panel.requestFullscreen = () =>
    new Promise((resolve) => {
      resolveRequest = resolve;
    });
  const request = panel.action("fullscreen");
  assert.equal(node('[data-action="fullscreen"]').disabled, true);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  resolveRequest();
  await request;
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node('[data-action="fullscreen"]').disabled, false);

  panel.requestFullscreen = async () => {
    throw Error("NotAllowedError");
  };
  await assert.rejects(panel.action("fullscreen"), /Vollbildansicht/);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node('[data-action="fullscreen"]').disabled, false);
});

test("unavailable or blocked fullscreen hides its button and leaves the tabs usable", async () => {
  for (const unsupported of ["policy", "request", "exit"]) {
    const { panel, node } = fullscreenPanel();
    if (unsupported === "policy") panel.ownerDocument.fullscreenEnabled = false;
    if (unsupported === "request") panel.requestFullscreen = undefined;
    if (unsupported === "exit") panel.ownerDocument.exitFullscreen = undefined;
    panel.syncFullscreenNavigation();
    assert.equal(node('[data-action="fullscreen"]').hidden, true, unsupported);
    assert.equal(node('[data-action="menu"]').hidden, true, unsupported);
    assert.equal(node(".main-tabs").hidden, false, unsupported);
    await panel.action("fullscreen");
    assert.equal(panel.requestCount, 0, unsupported);
  }
});

test("fullscreen remains available without state and removes its listener on disconnect", async () => {
  const { panel, node, listeners } = fullscreenPanel();
  panel.state = null;
  await panel.action("fullscreen");
  await panel.action("menu");
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(listeners.has("fullscreenchange"), true);
  panel.disconnectedCallback();
  assert.equal(listeners.has("fullscreenchange"), false);
  assert.equal(panel.fullscreenMenuOpen, false);
  panel.connectedCallback();
  assert.equal(listeners.has("fullscreenchange"), true);
});
