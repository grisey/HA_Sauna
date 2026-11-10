"use strict";
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

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
    Intl,
  },
);

// BN (Nutzer 10.11.2026): Testnachricht, Anlässe und Werte; das Backend liefert
// Bezeichnungen, Werte und den Teststatus, das Panel formatiert nur.
const data = (overrides = {}) => ({
  entry: "entry-1",
  settings: {
    occasions: { timer_start: true, ready: false },
    timer_runtime_minutes: 240,
    timer_lead_minutes: 15,
  },
  occasions: [
    {
      id: "timer_start",
      label: "Zeitschaltuhr beim Start",
      description: "Beim Start.",
    },
    { id: "ready", label: "Bereit", description: "Bei Bereit." },
  ],
  values: [
    {
      key: "timer_runtime_minutes",
      label: "Laufzeit der Zeitschaltuhr",
      unit: "min",
      description: "Ofenzeit.",
    },
    {
      key: "timer_lead_minutes",
      label: "Erinnerung vor Ablauf",
      unit: "min",
      description: "Vorlauf.",
    },
  ],
  test: null,
  targets: 1,
  can_edit: true,
  can_test: true,
  ...overrides,
});

function render(settings) {
  const nodes = new Map();
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    notificationSettings: settings,
    $: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
      return nodes.get(selector);
    },
    updateMarkup(selector, html) {
      this.$(selector).innerHTML = html;
    },
    clockSeconds: (value) => String(value).slice(11, 19),
  });
  panel.drawNotificationSettings();
  return nodes.get("#notification-settings").innerHTML;
}

const html = render(data());
assert.match(html, /data-action="notification-test" >Testnachricht senden/);
assert.match(html, /data-notification-occasion="timer_start" checked/);
assert.doesNotMatch(html, /data-notification-occasion="ready" checked/);
assert.match(html, /data-notification-value="timer_lead_minutes"[^>]*value="15"/);
assert.match(html, /Noch keine Testnachricht gesendet\./);
assert.match(html, /data-action="notification-save"/);

// Übergeben und Antwort mit Uhrzeit und Benutzer.
const answered = render(
  data({
    test: {
      sent_at: "2026-11-10T18:10:05Z",
      addressed: 2,
      targets: 2,
      accepted: 2,
      replied_at: "2026-11-10T18:10:31Z",
      replied_by_name: "Max",
    },
  }),
);
assert.match(answered, /Gesendet um 18:10:05 an 2 Ziele/);
assert.match(answered, /Übergeben: 2 von 2/);
assert.match(answered, /Antwort empfangen um 18:10:31 von Max/);
const pending = render(
  data({
    test: { sent_at: "2026-11-10T18:10:05Z", addressed: 1, targets: 0, accepted: 0 },
  }),
);
assert.match(pending, /Wird übergeben …/);
assert.match(pending, /Noch keine Antwort/);

// Ohne Administratorrecht nur lesen; ohne Ziel keine Testnachricht.
const reader = render(data({ can_edit: false }));
assert.match(reader, /data-notification-occasion="timer_start" checked disabled/);
assert.doesNotMatch(reader, /notification-save/);
assert.match(render(data({ targets: 0 })), /data-action="notification-test" disabled/);

// Details · Steuerung: Nachrichten mit Empfängern und Antworten aus dem Archiv.
const records = [
  {
    id: 1,
    kind: "notification",
    received_at: "2026-11-10T17:12:00Z",
    payload: {
      event: "message",
      seq: 1,
      at: "2026-11-10T17:12:00Z",
      occasion_label: "Zeitschaltuhr beim Start",
      text: "Sauna gestartet – Zeitschaltuhr aufziehen",
    },
  },
  {
    id: 2,
    kind: "notification",
    received_at: "2026-11-10T17:12:00Z",
    payload: { event: "recipients", seq: 1, count: 2, basis: "anwesende Geräte" },
  },
  {
    id: 3,
    kind: "notification",
    received_at: "2026-11-10T17:12:01Z",
    payload: { event: "handoff", seq: 1, accepted: true },
  },
  {
    id: 4,
    kind: "notification",
    received_at: "2026-11-10T17:13:00Z",
    payload: {
      event: "reply",
      seq: 1,
      at: "2026-11-10T17:13:00Z",
      action: "Uhr ist gestellt",
      outcome_text: "Zeitschaltuhr bestätigt",
      user_id: "u",
    },
    display: { user: "Max" },
  },
];
const panel = Object.assign(fixturePanel(Panel), {
  clockSeconds: (value) => new Date(value).toISOString().slice(11, 19),
});
const entries = panel.controlNotificationEntries(records);
assert.deepEqual(
  entries.map((entry) => [entry.label, entry.detail]),
  [
    [
      "Zeitschaltuhr beim Start",
      "Sauna gestartet – Zeitschaltuhr aufziehen · an 2 Ziele (anwesende Geräte)",
    ],
    ["Antwort „Uhr ist gestellt“", "Zeitschaltuhr bestätigt · Max"],
  ],
);
const card = panel.controlNotificationCard(records);
assert.match(card, /<h2>Benachrichtigungen<\/h2>/);
assert.match(
  card,
  /17:12:00<\/span><span><strong>Zeitschaltuhr beim Start<\/strong>[\s\S]*Antwort „Uhr ist gestellt“ 17:13:00: Zeitschaltuhr bestätigt · Max/,
);
assert.equal(panel.controlNotificationCard([]), "");

(async () => {
  const calls = [];
  const target = Object.assign(fixturePanel(Panel), {
    entry: "entry-1",
    state: { permissions: { admin: true, control: true } },
    notificationSettings: data(),
    api: async (...args) => {
      calls.push(args);
      return args[0].endsWith("/notifications") && args[1] === "GET" ? data() : {};
    },
    drawNotificationSettings() {},
    refresh: async () => {},
    message: () => {},
  });
  await target.action("notification-test");
  assert.deepEqual(
    calls.slice(0, 2).map((call) => [call[0], call[1] || "GET"]),
    [
      ["/entry-1/notifications/test", "POST"],
      ["/entry-1/notifications", "GET"],
    ],
  );
})();
