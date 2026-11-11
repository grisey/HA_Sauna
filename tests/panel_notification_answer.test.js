"use strict";
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// BN-b (Nutzer 11.11.2026): Antippen einer Sauna-Nachricht öffnet das Panel mit
// Titel, Text und den Antwortknöpfen; die Antwort geht an die Schnittstelle.
const timers = [];
let Panel;
vm.runInNewContext(
  fs.readFileSync(
    path.join(__dirname, "../custom_components/ha_sauna/panel.js"),
    "utf8",
  ),
  {
    HTMLElement: class {},
    customElements: {
      get: () => undefined,
      define: (_name, value) => {
        Panel = value;
      },
    },
    Date,
    Map,
    Set,
    Math,
    Number,
    String,
    Object,
    Array,
    Infinity,
    URL,
    atob,
    TextDecoder,
    setTimeout: (callback, delay) => timers.push({ callback, delay }),
    clearTimeout: () => {},
  },
);

const ACTION = "HA_SAUNA_NOTIFY_abc_3_END_GANG";
const question = {
  e: "entry-2",
  t: "Sauna",
  m: "Gang 1 läuft laut Sauna noch – bist du draußen?",
  a: [{ action: ACTION, title: "Gang beenden" }],
};
const token = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");

function setup(href, reply = { outcome: "effect", result: "applied" }) {
  const dialog = {
    innerHTML: "",
    open: false,
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      this.onclose?.();
    },
  };
  const replaced = [],
    calls = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    $: (selector) => (selector === "#answer-dialog" ? dialog : null),
    api: async (...args) => {
      calls.push(args);
      if (reply instanceof Error) throw reply;
      return reply;
    },
  });
  panel.ownerDocument = {
    defaultView: {
      location: { href },
      history: {
        state: { panel: "kept" },
        replaceState: (...args) => replaced.push(args),
      },
    },
  };
  return { panel, dialog, replaced, calls };
}

(async () => {
  // Dialog aus dem Parameter; der Parameter wird entfernt, der Verlauf behält seinen Zustand.
  const href = `https://ha.local/ha-sauna?external_auth=1&nachricht=${token(question)}#x`;
  const { panel, dialog, replaced, calls } = setup(href);
  panel.openAnswerFromLocation();
  assert.equal(dialog.open, true);
  assert.deepEqual(replaced, [[{ panel: "kept" }, "", "/ha-sauna?external_auth=1#x"]]);
  assert.match(dialog.innerHTML, /<h2 id="answer-title">Sauna<\/h2>/);
  assert.match(
    dialog.innerHTML,
    /<p>Gang 1 läuft laut Sauna noch – bist du draußen\?<\/p>/,
  );
  assert.match(
    dialog.innerHTML,
    /<button type="button" data-action="answer:0" >Gang beenden<\/button><button type="button" data-action="answer-close">Schließen<\/button>/,
  );

  // Antwort: die native Kennung an die Instanz aus dem Link; Ergebnis, dann schließen.
  await panel.action("answer:0");
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["/entry-2/notifications/answer", "POST", { action: ACTION }],
  ]);
  assert.match(dialog.innerHTML, /<p role="status">„Gang beenden“ übernommen\.<\/p>/);
  assert.doesNotMatch(dialog.innerHTML, /answer:0/);
  assert.equal(timers.at(-1).delay, panel.preference("program_saved_feedback_ms"));
  await panel.action("answer:0");
  assert.equal(calls.length, 1);
  timers.at(-1).callback();
  assert.equal(dialog.open, false);
  assert.equal(panel.answer, null);

  // Neu laden ohne Parameter: kein Dialog.
  const reload = setup("https://ha.local/ha-sauna?external_auth=1");
  reload.panel.openAnswerFromLocation();
  assert.equal(reload.dialog.open, false);
  assert.deepEqual(reload.replaced, []);

  // Schon erledigt, ohne Berechtigung, unbekannt.
  for (const [result, text] of [
    ["done", "Schon erledigt."],
    ["denied", "Ohne Steuerberechtigung, die Antwort bleibt ohne Wirkung."],
    ["unknown", "Diese Nachricht ist nicht mehr bekannt."],
  ]) {
    const item = setup(href, { outcome: "x", result });
    item.panel.openAnswerFromLocation();
    await item.panel.action("answer:0");
    assert.match(item.dialog.innerHTML, new RegExp(`<p role="status">${text}</p>`));
  }

  // Fehler der Anfrage: Meldung im Dialog, Knöpfe bleiben; ohne Instanz im Link die aktuelle.
  const failed = setup(
    `https://ha.local/ha-sauna?nachricht=${token({ ...question, e: undefined })}`,
    Object.assign(new Error("Response error: 503"), { status_code: 503 }),
  );
  failed.panel.openAnswerFromLocation();
  await failed.panel.action("answer:0");
  assert.equal(failed.calls[0][0], "/entry-1/notifications/answer");
  assert.match(failed.dialog.innerHTML, /wird gerade neu geladen/);
  assert.match(failed.dialog.innerHTML, /data-action="answer:0" >Gang beenden/);
  assert.equal(failed.dialog.open, true);
  await failed.panel.action("answer-close");
  assert.equal(failed.dialog.open, false);

  // Unlesbarer Link: Parameter entfernt, kein Dialog.
  const broken = setup("https://ha.local/ha-sauna?nachricht=%%%");
  broken.panel.openAnswerFromLocation();
  assert.equal(broken.dialog.open, false);
  assert.deepEqual(broken.replaced, [[{ panel: "kept" }, "", "/ha-sauna"]]);

  // Navigation innerhalb von Home Assistant setzt route: der Dialog öffnet ebenso.
  const routed = setup(`https://ha.local/ha-sauna?nachricht=${token(question)}`);
  Object.defineProperty(routed.panel, "isConnected", { value: true });
  routed.panel.route = { prefix: "/ha-sauna", path: "" };
  assert.equal(routed.dialog.open, true);
  assert.deepEqual(routed.panel.route, { prefix: "/ha-sauna", path: "" });
})();
