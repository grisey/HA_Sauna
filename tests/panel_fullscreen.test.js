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
  CustomEvent: class {
    constructor(type, options) {
      this.type = type;
      Object.assign(this, options);
    }
  },
  customElements: { get: () => undefined, define: (_name, value) => (Panel = value) },
  clearTimeout,
});

function fullscreenPanel({ homeAssistant = true, kioskMode = false } = {}) {
  const nodes = new Map(),
    listeners = new Map(),
    events = [],
    kioskEvents = [],
    windowListeners = new Map(),
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
        });
      return nodes.get(selector);
    },
    panel = Object.assign(new Panel(), {
      state: { permissions: { admin: true } },
      _hass: { kioskMode },
      start() {},
      $: node,
      // The HA sidebar is outside the custom panel, across shadow boundaries.
      parentNode: homeAssistant
        ? {
            host: {
              localName: "ha-panel-custom",
              parentNode: {
                host: { localName: "home-assistant-main" },
              },
            },
          }
        : null,
      dispatchEvent: (event) => events.push(event),
      message() {},
      refresh: async () => {},
      fitInstrumentReadouts() {},
      cancelHistoryFrame() {},
      cancelProgramDrag() {},
      cancelTemperatureDrag() {},
      applyAppearance() {},
      selectMenu: { connect() {}, disconnect() {} },
      requestCount: 0,
      exitCount: 0,
    });
  panel.ownerDocument = {
    defaultView: {
      addEventListener(kind, callback) {
        windowListeners.set(kind, callback);
      },
      removeEventListener(kind, callback) {
        if (windowListeners.get(kind) === callback) windowListeners.delete(kind);
      },
      dispatchEvent(event) {
        kioskEvents.push(event.detail.enable);
        panel._hass = { ...panel._hass, kioskMode: event.detail.enable };
        windowListeners.get(event.type)?.(event);
      },
    },
    fullscreenEnabled: true,
    fullscreenElement: null,
    documentElement: {
      async requestFullscreen() {
        panel.requestCount++;
        panel.ownerDocument.fullscreenElement = this;
        listeners.get("fullscreenchange")?.();
      },
    },
    addEventListener(kind, callback) {
      listeners.set(kind, callback);
    },
    removeEventListener(kind, callback) {
      if (listeners.get(kind) === callback) listeners.delete(kind);
    },
    async exitFullscreen() {
      panel.exitCount++;
      this.fullscreenElement = null;
      listeners.get("fullscreenchange")?.();
    },
  };
  panel.requestFullscreen = () =>
    assert.fail("Panel-only fullscreen excludes HA navigation");
  panel.connectedCallback();
  return { panel, node, listeners, events, kioskEvents, windowListeners };
}

test("fullscreen includes HA and keeps internal navigation visible for both roles", async () => {
  for (const admin of [true, false]) {
    const { panel, node, events } = fullscreenPanel();
    panel.state.permissions.admin = admin;
    assert.equal(node('[data-action="menu"]').hidden, true);
    assert.equal(node(".main-tabs").hidden, false);
    await panel.action("menu");
    assert.equal(events.length, 0);

    panel.ownerDocument.fullscreenElement = { localName: "another-panel" };
    panel.syncFullscreenNavigation();
    assert.equal(node('[data-action="menu"]').hidden, true);

    await panel.action("fullscreen");
    assert.equal(panel.requestCount, 1);
    assert.equal(
      panel.ownerDocument.fullscreenElement,
      panel.ownerDocument.documentElement,
    );
    assert.equal(panel.isPanelFullscreen(), true);
    assert.equal(node('[data-action="menu"]').hidden, false);
    assert.equal(node(".main-tabs").hidden, false);
    assert.equal(
      node('[data-action="fullscreen"]').attributes["aria-label"],
      "Vollbild verlassen",
    );
    assert.ok("hidden" in node('[data-fullscreen-icon="enter"]').attributes);
    assert.ok(!("hidden" in node('[data-fullscreen-icon="exit"]').attributes));

    await panel.action("menu");
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "hass-toggle-menu");
    assert.equal(events[0].bubbles, true);
    assert.equal(events[0].composed, true);
    assert.equal(node(".main-tabs").hidden, false);
    await panel.action("history");
    assert.equal(panel.view, "history");
    assert.equal(node(".main-tabs").hidden, false);
    assert.equal(node('.main-tabs [data-action="details"]').hidden, !admin);
    assert.equal(events.length, 1);

    await panel.action("fullscreen");
    assert.equal(panel.exitCount, 1);
    assert.equal(node('[data-action="menu"]').hidden, true);
    assert.equal(node(".main-tabs").hidden, false);
  }
});

test("standalone preview has fullscreen but no nonfunctional HA menu", async () => {
  const { panel, node, events, kioskEvents } = fullscreenPanel({
    homeAssistant: false,
  });
  await panel.action("fullscreen");
  assert.equal(panel.isPanelFullscreen(), true);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  await panel.action("menu");
  assert.equal(events.length, 0);
  assert.deepEqual(kioskEvents, []);
});

test("browser exit restores embedded controls without hiding internal navigation", async () => {
  const { panel, node, listeners } = fullscreenPanel();
  await panel.action("fullscreen");
  await panel.action("menu");
  panel.ownerDocument.fullscreenElement = null;
  listeners.get("fullscreenchange")();
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(node('[data-action="fullscreen"]').attributes["aria-label"], "Vollbild");
  assert.ok(!("hidden" in node('[data-fullscreen-icon="enter"]').attributes));
  assert.ok("hidden" in node('[data-fullscreen-icon="exit"]').attributes);
});

test("pending, declined, or unconfirmed requests never invent fullscreen state", async () => {
  const { panel, node } = fullscreenPanel();
  let resolveRequest;
  panel.ownerDocument.documentElement.requestFullscreen = () =>
    new Promise((resolve) => {
      panel.requestCount++;
      resolveRequest = resolve;
    });
  const request = panel.action("fullscreen");
  await panel.action("fullscreen");
  assert.equal(panel.requestCount, 1);
  assert.equal(node('[data-action="fullscreen"]').disabled, true);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node(".main-tabs").hidden, false);
  resolveRequest();
  await request;
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node('[data-action="fullscreen"]').disabled, false);

  panel.ownerDocument.documentElement.requestFullscreen = async () => {
    throw Error("NotAllowedError");
  };
  await assert.rejects(panel.action("fullscreen"), /Vollbildansicht/);
  assert.equal(node('[data-action="menu"]').hidden, true);
  assert.equal(node('[data-action="fullscreen"]').disabled, false);
});

test("unavailable fullscreen leaves embedded navigation usable", async () => {
  for (const unsupported of ["policy", "request", "exit"]) {
    const { panel, node } = fullscreenPanel();
    if (unsupported === "policy") panel.ownerDocument.fullscreenEnabled = false;
    if (unsupported === "request")
      panel.ownerDocument.documentElement.requestFullscreen = undefined;
    if (unsupported === "exit") panel.ownerDocument.exitFullscreen = undefined;
    panel.syncFullscreenNavigation();
    assert.equal(node('[data-action="fullscreen"]').hidden, true, unsupported);
    assert.equal(node('[data-action="menu"]').hidden, true, unsupported);
    assert.equal(node(".main-tabs").hidden, false, unsupported);
    await panel.action("fullscreen");
    assert.equal(panel.requestCount, 0, unsupported);
  }
});

test("fullscreen is independent of API state and listener lifecycle preserves HA navigation", async () => {
  const { panel, node, listeners } = fullscreenPanel();
  panel.state = null;
  await panel.action("fullscreen");
  await panel.action("menu");
  assert.equal(node(".main-tabs").hidden, false);
  assert.equal(listeners.has("fullscreenchange"), true);
  panel.disconnectedCallback();
  assert.equal(listeners.has("fullscreenchange"), false);
  assert.equal(panel.exitCount, 0);
  panel.connectedCallback();
  assert.equal(listeners.has("fullscreenchange"), true);
  assert.equal(node(".main-tabs").hidden, false);
});

test("HA kiosk follows actual fullscreen and restores on browser exit or disconnect", async () => {
  for (const exit of ["browser", "disconnect"]) {
    const { panel, listeners, kioskEvents, windowListeners } = fullscreenPanel();
    await panel.action("fullscreen");
    assert.deepEqual(kioskEvents, [true]);
    panel.syncFullscreenNavigation();
    assert.deepEqual(kioskEvents, [true]);
    if (exit === "browser") {
      panel.ownerDocument.fullscreenElement = null;
      listeners.get("fullscreenchange")();
    } else {
      panel.disconnectedCallback();
      assert.equal(windowListeners.has("hass-kiosk-mode"), false);
      assert.equal(panel.exitCount, 0);
    }
    assert.deepEqual(kioskEvents, [true, false]);
    assert.equal(panel._hass.kioskMode, false);
  }
});

test("preexisting kiosk and later app commands retain control of the HA frame", async () => {
  const existing = fullscreenPanel({ kioskMode: true });
  await existing.panel.action("fullscreen");
  await existing.panel.action("fullscreen");
  assert.deepEqual(existing.kioskEvents, []);
  assert.equal(existing.panel._hass.kioskMode, true);

  for (const enable of [true, false]) {
    const { panel, kioskEvents } = fullscreenPanel();
    await panel.action("fullscreen");
    panel.ownerDocument.defaultView.dispatchEvent({
      type: "hass-kiosk-mode",
      detail: { enable },
    });
    panel.syncFullscreenNavigation();
    await panel.action("fullscreen");
    assert.deepEqual(kioskEvents, [true, enable]);
    assert.equal(panel._hass.kioskMode, enable);
  }
});
