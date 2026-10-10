"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const context = {
  HTMLElement: class {},
  customElements: { get: () => null, define() {} },
  Event: class {
    constructor(type) {
      this.type = type;
    }
  },
  MutationObserver: class {
    observe() {}
    disconnect() {}
  },
};
vm.runInNewContext(
  `${fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8")}\nglobalThis.SelectMenu = SaunaSelectMenu;`,
  context,
);

function eventTarget() {
  return {
    listeners: new Map(),
    addEventListener(type, handler) {
      this.listeners.set(type, handler);
    },
    removeEventListener(type) {
      this.listeners.delete(type);
    },
  };
}

function fixture({ disabled = false } = {}) {
  const root = { ...eventTarget(), activeElement: null };
  function element(tagName) {
    const attributes = new Map();
    return {
      tagName,
      className: "",
      textContent: "",
      isConnected: true,
      getAttribute: (name) => attributes.get(name) ?? null,
      hasAttribute: (name) => attributes.has(name),
      setAttribute: (name, value) => attributes.set(name, String(value)),
      removeAttribute: (name) => attributes.delete(name),
      focus() {
        root.activeElement = this;
      },
      remove() {
        this.isConnected = false;
      },
      getClientRects: () => [{}],
    };
  }
  const select = Object.assign(element("SELECT"), {
    size: 0,
    disabled,
    labels: [],
    options: [{ label: "Erste" }, { label: "Zweite" }],
    selectedIndex: 0,
    after(trigger) {
      this.nextSibling = trigger;
    },
    events: [],
    dispatchEvent(event) {
      this.events.push(event.type);
    },
  });
  Object.defineProperty(select, "selectedOptions", {
    get() {
      return [this.options[this.selectedIndex]];
    },
  });
  select.setAttribute("aria-label", "Programm");
  select.setAttribute("tabindex", "3");
  root.contains = (node) => node === select;
  root.querySelectorAll = () => [select];
  const ownerDocument = {
    ...eventTarget(),
    defaultView: eventTarget(),
    createElement: (tag) => element(tag.toUpperCase()),
  };
  const menu = new context.SelectMenu({ shadowRoot: root, ownerDocument });
  let opened = 0;
  // Real enhancement and event routing; browser tests cover popup geometry.
  menu.open = (target) => {
    menu.select = target;
    menu.menu = { id: "menu", remove() {} };
    menu.active = target.selectedIndex;
    menu.syncAttributes(target);
    opened++;
  };
  menu.connect();
  const trigger = menu.models.get(select).trigger;
  function dispatch(type, values = {}) {
    const event = {
      button: 0,
      defaultPrevented: false,
      composedPath: () => [trigger, root],
      preventDefault() {
        this.defaultPrevented = true;
      },
      stopImmediatePropagation() {},
      ...values,
    };
    root.listeners.get(type)(event);
    return event;
  }
  return { menu, select, trigger, root, dispatch, opened: () => opened };
}

test("enhancement uses a button and restores the native model on disconnect", () => {
  const { menu, select, trigger } = fixture();
  assert.equal(trigger.tagName, "BUTTON");
  assert.equal(trigger.getAttribute("role"), "combobox");
  assert.equal(trigger.getAttribute("aria-label"), "Programm");
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
  assert.equal(trigger.textContent, "Erste");
  assert.equal(select.hasAttribute("data-sauna-select-model"), true);
  assert.equal(select.getAttribute("aria-hidden"), "true");
  assert.equal(select.getAttribute("tabindex"), "-1");
  menu.disconnect();
  assert.equal(select.hasAttribute("data-sauna-select-model"), false);
  assert.equal(select.getAttribute("aria-hidden"), null);
  assert.equal(select.getAttribute("tabindex"), "3");
  assert.equal(trigger.isConnected, false);
});

test("button click and F4 toggle one custom menu without native activation", () => {
  const { menu, trigger, root, dispatch, opened } = fixture();
  dispatch("pointerdown");
  assert.equal(dispatch("click").defaultPrevented, true);
  assert.equal(root.activeElement, trigger);
  assert.equal(opened(), 1);
  assert.equal(trigger.getAttribute("aria-expanded"), "true");
  assert.equal(dispatch("keydown", { key: "F4" }).defaultPrevented, true);
  assert.equal(menu.select, null);
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
  assert.equal(trigger.getAttribute("aria-controls"), null);
  assert.equal(dispatch("keydown", { key: "F4" }).defaultPrevented, true);
  assert.equal(opened(), 2);
});

test("selection updates the hidden model, existing events, and trigger focus", () => {
  const { menu, select, trigger, root, dispatch } = fixture();
  dispatch("click");
  menu.choose(1);
  assert.equal(select.selectedIndex, 1);
  assert.deepEqual(select.events, ["input", "change"]);
  assert.equal(trigger.textContent, "Zweite");
  assert.equal(root.activeElement, trigger);
  assert.equal(trigger.getAttribute("aria-expanded"), "false");
});

test("disabled models stay enhanced with a disabled nonnative trigger", () => {
  const { select, trigger, dispatch, opened } = fixture({ disabled: true });
  assert.equal(select.getAttribute("tabindex"), "-1");
  assert.equal(trigger.hasAttribute("disabled"), true);
  dispatch("click");
  assert.equal(opened(), 0);
});
