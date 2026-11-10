"use strict";
// B4b Details · Erkennung: Erkennungskontrolle aus dem Endpunkt archive/recognition.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

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
  Intl,
  clearTimeout,
  setTimeout,
});

const T0 = Date.parse("2026-11-04T19:00:00Z"),
  at = (seconds) => new Date(T0 + seconds * 1000).toISOString();

const control = (overrides = {}) => ({
  session_id: "s1",
  recorded: true,
  summary: {
    gangs: 1,
    infusions: 2,
    infusions_in_gangs: 1,
    infusions_outside: 1,
    door_operations: 2,
    rejected_candidates: {
      candidates: 3,
      rejected: 2,
      rejected_in_episodes: 1,
      rejected_single: 1,
      rejected_episodes: 1,
      taken_as_door: 1,
      door_episodes: 1,
    },
    retracted_gangs: 1,
  },
  states: [
    { at: at(0), until: at(100), state: "closed", label: "Ruhe", probability: 0.9 },
    { at: at(100), until: at(130), state: "open", label: "offen", probability: 0.8 },
    { at: at(130), until: at(600), state: "recovery", label: "Erholung", probability: 0.7 },
  ],
  events: [
    {
      event_id: "e-open",
      kind: "door_open",
      label: "Tür geöffnet",
      source: "Erkennung",
      effective_at: at(100),
      decided_at: at(112.5),
      decision_seconds: 12.5,
      probability: 0.9984,
      meaning: "Eintritt Gang 1",
    },
    {
      event_id: "e-late",
      kind: "infusion",
      label: "Aufguss",
      source: "Erkennung",
      effective_at: at(5000),
      decided_at: at(5001),
      decision_seconds: 1,
      probability: null,
      meaning: "Aufguss außerhalb eines Gangs",
    },
  ],
  rejected: [
    {
      effective_at: at(300),
      decided_at: at(308),
      decision_seconds: 8,
      reason: "opening_form",
      reason_label: "Öffnungsform passt nicht",
      state: "offen",
      against: "Ruhe",
      ratio: 12.5,
      result: "verworfen in einer Episode",
    },
  ],
  not_taken: [],
  retracted: [
    {
      gang_id: "g0",
      started_at: at(200),
      recognition: "Anwesenheit beim Türschluss",
      retracted_at: at(920),
      reason: "Bestätigungsfrist abgelaufen",
    },
  ],
  ...overrides,
});

const fixture = ({ admin = true, data = control(), records = [] } = {}) => {
  const nodes = { "#recognition-control": {} },
    requests = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "e",
    view: "diagnostics",
    state: { permissions: { admin } },
    shown: { session: { timeline: { session_id: "s1" }, ended_at: at(7200) }, records },
    window: [T0, T0 + 600 * 1000],
    $: (selector) => nodes[selector] || null,
    message: () => {},
    api: async (path) => {
      requests.push(path);
      return data;
    },
  });
  return { panel, nodes, requests, html: () => nodes["#recognition-control"].innerHTML };
};

test("the control loads once per session state and draws when it arrives", async () => {
  const { panel, requests, html } = fixture();
  panel.drawRecognitionControl();
  assert.match(html(), /Erkennungskontrolle wird geladen/);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(requests, ["/e/archive/recognition?session_id=s1"]);
  assert.match(html(), /<h2>Erkennung<\/h2>/);
  await panel.loadRecognitionControl();
  assert.equal(requests.length, 1, "same session state: no second request");
});

test("summary, rejected candidates and retracted gangs come from the endpoint", () => {
  const { panel } = fixture();
  const html = panel.recognitionControlMarkup(control());
  assert.match(html, /<dt>Gänge<\/dt><dd>1<\/dd>/);
  assert.match(html, /<dt>Aufgüsse<\/dt><dd>2 <span class="muted">· 1 in Gängen, 1 außerhalb<\/span>/);
  assert.match(html, /<dt>Verworfene Kandidaten<\/dt><dd>2 <span class="muted">· 1 in Episoden, 1 einzeln; 1 als Türvorgang übernommen/);
  assert.match(html, /offen 12,5-mal so wahrscheinlich wie Ruhe/);
  assert.match(html, /Verworfene Kandidaten<\/h3>/);
  assert.match(html, /Aufgehobene vorläufige Gänge<\/h3>[^]*Anwesenheit beim Türschluss[^]*Bestätigungsfrist abgelaufen/);
  assert.doesNotMatch(html, /data-recognition-recorded/);
});

test("the event list shows only the window; a selection explains the event", async () => {
  const { panel, html } = fixture();
  panel.recognitionControl = { key: "k", entry: "e", sessionId: "s1", result: control() };
  panel.drawRecognitionControl();
  assert.match(html(), /data-recognition-event="e-open"/);
  assert.doesNotMatch(html(), /data-recognition-event="e-late"/);
  assert.match(html(), /Ein Ereignis in der Liste oder im Diagramm wählen/);
  await panel.action("recognition-event:e-open");
  assert.equal(panel.recognitionSelected, "e-open");
  assert.match(html(), /data-recognition-event="e-open" aria-selected="true"/);
  assert.match(html(), /<dt>Bedeutung<\/dt><dd>Eintritt Gang 1<\/dd>/);
  assert.match(html(), /<dt>Entscheidung<\/dt><dd>12,5 s nach dem Zeitpunkt/);
  // A session without the stored ratio shows the probability, without a note.
  assert.match(html(), /<dt>Sicherheit<\/dt><dd>Wahrscheinlichkeit bei der Entscheidung 99,84 %<\/dd>/);
  assert.doesNotMatch(html(), /Verhältnis zur nächstbesten Erklärung/);
  await panel.action("recognition-event:e-open");
  assert.equal(panel.recognitionSelected, null, "a second press clears the selection");
});

test("B4f: a taken event shows the ratio to the next best explanation like a rejected candidate", async () => {
  const data = control();
  Object.assign(data.events[0], { state: "offen", against: "Ruhe", ratio: 24264.3 });
  const { panel, html } = fixture({ data });
  panel.recognitionControl = { key: "k", entry: "e", sessionId: "s1", result: data };
  await panel.action("recognition-event:e-open");
  assert.match(
    html(),
    /<dt>Sicherheit<\/dt><dd>offen 24\.264,3-mal so wahrscheinlich wie Ruhe<\/dd><dt>Wahrscheinlichkeit<\/dt><dd>99,84 % bei der Entscheidung<\/dd>/,
  );
  assert.match(html(), /<th>Sicherheit<\/th>/);
  assert.match(html(), /<td>offen 24\.264,3-mal so wahrscheinlich wie Ruhe<\/td><\/tr>/);
  assert.doesNotMatch(html(), /nicht gespeichert<\/p>/);
});

test("sessions before the state history are marked, without track and candidates", () => {
  const { panel } = fixture();
  const html = panel.recognitionControlMarkup(
    control({
      recorded: false,
      states: null,
      rejected: null,
      summary: { ...control().summary, rejected_candidates: null },
    }),
  );
  assert.match(html, /data-recognition-recorded="false">Für diese Sitzung ist kein Zustandsverlauf gespeichert/);
  assert.match(html, /<dt>Verworfene Kandidaten<\/dt><dd>nicht gespeichert<\/dd>/);
  assert.match(html, /data-recognition-track="missing">Zustandsspur nicht gespeichert/);
  assert.doesNotMatch(html, /Verworfene Kandidaten<\/h3>/);
  assert.doesNotMatch(html, /recognition-state state-/);
});

test("curves upper and lower, door and infusion marks, state track and legend", () => {
  const records = [];
  for (let second = 0; second <= 600; second += 10)
    for (const [position, offset] of [["upper", 0], ["lower", -20]])
      records.push({
        kind: "measurement",
        payload: { position, quantity: "temperature", value: 80 + offset, received_at: at(second) },
      });
  const { panel } = fixture({ records });
  const html = panel.recognitionCurveMarkup(control());
  assert.match(html, /data-series="recognition_upper_temperature"/);
  assert.match(html, /data-series="recognition_lower_temperature"/);
  assert.match(html, /<g class="recognition-mark door"[^>]*data-action="recognition-event:e-open"/);
  assert.equal((html.match(/class="recognition-rejected"/g) || []).length, 1);
  assert.equal((html.match(/<rect class="recognition-state state-/g) || []).length, 3);
  const legend = html.match(/<strong>Zustand<\/strong>(.*?)<\/p>/)[1];
  assert.deepEqual([...legend.matchAll(/<\/i>([^<]+)</g)].map((m) => m[1]), ["Ruhe", "offen", "Erholung"]);
  assert.match(html, /Keine Messwerte im Ausschnitt/, "no humidity series in this fixture");
});

test("non-administrators get neither the control nor its selection", async () => {
  const { panel, html, requests } = fixture({ admin: false });
  panel.drawRecognitionControl();
  assert.equal(html(), "");
  await panel.action("recognition-event:e-open");
  assert.equal(panel.recognitionSelected, undefined);
  assert.deepEqual(requests, []);
});
