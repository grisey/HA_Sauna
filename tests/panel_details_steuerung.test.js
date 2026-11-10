"use strict";
// B4d Details · Steuerung nach Brett: Betriebsverlauf mit einer Spur je Frage,
// Zeitpunkt mit „Danach“, Fristen und Regeln aus dem Backend, Protokoll je Typ.
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
let Panel;
vm.runInNewContext(source, {
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
  clearTimeout,
  setTimeout,
});

const T0 = Date.parse("2026-11-02T16:00:00Z");
const iso = (minute) => new Date(T0 + minute * 60000).toISOString();
let id = 0;
const record = (kind, minute, payload) => ({
  id: ++id,
  kind,
  received_at: iso(minute),
  payload,
});
const decision = (minute, heat, reason) =>
  record("decision", minute, { at: iso(minute), created_at: iso(minute), heat, reason });

const shown = () => ({
  session: {
    ended_at: iso(120),
    configuration: { parameters: {}, presence_source: "ha_presence" },
    timeline: {
      session_id: "s1",
      session_started_at: iso(0),
      processed: [
        { event_id: "o", kind: "door_open", effective_at: iso(60), detected_at: iso(60) },
        { event_id: "c", kind: "door_close", effective_at: iso(61), detected_at: iso(61) },
      ],
      completed: [],
      retracted: [],
      active: null,
    },
    heating: {
      intervals: [
        { started_at: iso(0), ended_at: iso(40), end_reason: "feedback_off" },
        { started_at: iso(50), ended_at: iso(70), end_reason: "feedback_off" },
      ],
    },
    target_history: [
      { at: iso(0), target_c: 85 },
      { at: iso(80), target_c: 88 },
    ],
    control_rules: {
      configured: true,
      oven_cooling: [
        {
          started_at: iso(80),
          ended_at: iso(90.9),
          minutes: 10.9,
          gang: 1,
          running: false,
          pending: false,
        },
      ],
      heating_pause_minutes: 5,
      minimum_heating_minutes: 10,
      oven_cooling_minimum_minutes: 5,
      oven_cooling_maximum_minutes: 15,
      minimum_temperature_c: 60,
      band: { below_c: 1, above_c: 1 },
      targets: [85, 88, 90, 93],
      program: { kind: "program", name: "Höhenwanderung" },
      gangs: [
        {
          gang_id: "g1",
          number: 1,
          confirmed: true,
          started_at: iso(61),
          detected_at: iso(62),
          confirmed_at: iso(65),
          ended_at: iso(79),
          end_reason: "ventilation",
          active: false,
        },
      ],
    },
  },
  phase_projection: {
    intervals: [
      { phase: "aufheizen", started_at: iso(0), ended_at: iso(30) },
      { phase: "bereit", started_at: iso(30), ended_at: iso(61) },
      { phase: "saunagang", started_at: iso(61), ended_at: iso(80) },
      { phase: "nachlauf", started_at: iso(80), ended_at: iso(91) },
      { phase: "bereit", started_at: iso(91), ended_at: iso(120) },
    ],
  },
  records: [
    record("measurement", 0, { position: "upper", quantity: "temperature", value: 20 }),
    record("measurement", 35, { position: "upper", quantity: "temperature", value: 86 }),
    decision(0, true, "below_target"),
    decision(30, false, "temperature_reached"),
    decision(36, false, "thermostat_cooldown"),
    decision(41, false, "hysteresis_band"),
    decision(50, true, "below_target"),
    decision(61, true, "gang_heat_demand"),
    decision(80, false, "after_run"),
    record("command", 30, { heat: false, sent_at: iso(30) }),
    record("calibration", 10, { event: "start" }),
    record("diagnostic", 20, { faults: { upper_temperature: "unavailable" }, messages: ["Obere Temperatur fehlt"] }),
    record("diagnostic", 21, { faults: {}, messages: [] }),
  ],
});

const panel = () =>
  Object.assign(fixturePanel(Panel), {
    state: { now: iso(120), permissions: { admin: true }, phase: "bereit" },
    shown: shown(),
    message: () => {},
    updateMarkup(selector, html) {
      this.markup = { ...(this.markup || {}), [selector]: html };
    },
  });
const html = (p) => p.markup["#control-history"];

test("the operation history has one lane per question in the board order", () => {
  const p = panel();
  p.renderControlHistory();
  const lanes = [...html(p).matchAll(/<div class="control-track" data-detail-key="lane-[^"]*"><span>([^<]+)</g)].map(
    (match) => match[1],
  );
  assert.deepEqual(lanes, [
    "Phase",
    "Temperatur",
    "Heizanforderung",
    "Ofen (Rückmeldung)",
    "Ofenkühlung",
    "Tür",
    "Präsenzsensor",
  ]);
  assert.doesNotMatch(html(p), /Ofenschalter<\/span>|Heizrückmeldung<\/span>|Saunagang<\/span>/);
  const demand = html(p).match(/data-detail-key="lane-Heizanforderung">(.*?)<i /)[1];
  assert.match(demand, /background:var\(--sauna-color-ui-heater-on\)" title="Heizanforderung: Ein · Unter Einschaltgrenze/);
  assert.match(demand, /background:color-mix\(in srgb, var\(--sauna-color-phase-ready\) \d+%, [^"]+\)" title="Heizanforderung: Aus · Obere Regeltemperatur erreicht/);
  assert.match(html(p), /class="control-temperature"[^]*class="control-temperature-target"[^]*class="control-temperature-curve"/);
  const legend = [...html(p).matchAll(/<span class="control-legend-item"><i[^>]*><\/i>([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(legend, [
    "Unter Einschaltgrenze, Mindestheizzeit",
    "Aktiver Gang",
    "Obere Regeltemperatur erreicht",
    "Regelband, Heizpause",
    "Ofenkühlung",
  ]);
  assert.match(html(p), /kräftig = heizen, blass = nicht heizen/);
});

test("the moment names reason, phase, temperature at the setpoint, oven, door and the next changes", () => {
  const p = panel();
  p.controlHistorySession = "s1";
  p.controlHistoryEnd = Date.parse(iso(120));
  p.controlHistoryCursor = Date.parse(iso(35));
  p.renderControlHistory();
  const moment = html(p).match(/<section class="card control-moment"[^]*?<\/section>/)[0];
  assert.match(moment, /Zeitpunkt [0-9:]+<\/p><p class="control-moment-title">Ofen aus: Obere Regeltemperatur erreicht<\/p>/);
  assert.match(moment, /<dt>Phase<\/dt><dd>Bereit seit [0-9:]+<\/dd>/);
  assert.match(moment, /<dt>Temperatur<\/dt><dd>86 °C bei Soll 85 °C<\/dd>/);
  assert.match(moment, /<dt>Ofen<\/dt><dd>Befehl Aus, Rückmeldung Ein<\/dd>/);
  assert.match(moment, /<dt>Tür, Präsenz<\/dt><dd>geschlossen, unbekannt<\/dd>/);
  const following = [...moment.matchAll(/<li><button type="button" data-control-time="(\d+)">.*?<span>([^<]+)<\/span><\/button>/g)].map(
    (m) => [Number(m[1]), m[2]],
  );
  assert.deepEqual(following, [
    [Date.parse(iso(36)), "Heizpause"],
    [Date.parse(iso(41)), "Temperatur im Regelband"],
    [Date.parse(iso(50)), "Unter Einschaltgrenze: Ofen ein"],
    [Date.parse(iso(61)), "Aktiver Gang"],
  ]);
  p.controlHistoryCursor = Date.parse(iso(70));
  p.renderControlHistory();
  assert.match(html(p), /<dt>Gang<\/dt><dd>Gang 1 seit [0-9:]+, bestätigt [0-9:]+<\/dd>/);
});

test("deadlines and rules are formatted from the backend once", () => {
  const p = panel();
  p.renderControlHistory();
  const rules = html(p).match(/<section class="card control-rules"[^]*?<\/section>/)[0];
  assert.match(rules, /<dt>Ofenkühlung<\/dt><dd>[0-9:]+ – [0-9:]+, 10,9 min nach Gang 1<\/dd>/);
  assert.match(rules, /<dt>Dauer der Ofenkühlung<\/dt><dd>5 min bis 15 min je nach Heizzeit<\/dd>/);
  assert.match(rules, /<dt>Heizpause<\/dt><dd>5 min nach dem Abschalten<\/dd>/);
  assert.match(rules, /<dt>Mindestheizzeit<\/dt><dd>10 min<\/dd>/);
  assert.match(rules, /<dt>Regelband<\/dt><dd>Soll ± 1 °C<\/dd>/);
  assert.match(rules, /<dt>Mindesttemperatur<\/dt><dd>60 °C für Gänge<\/dd>/);
  assert.match(rules, /<dt>Soll<\/dt><dd>85 → 88 → 90 → 93 °C \(Höhenwanderung\)<\/dd>/);
  assert.match(rules, /Werte aus der Konfiguration der Sitzung\./);
  assert.equal(html(p).match(/Mindestheizzeit<\/dt>/g).length, 1, "the rules stand once");
  p.shown.session.control_rules.band = { below_c: 1, above_c: 2 };
  p.shown.session.control_rules.configured = false;
  p.renderControlHistory();
  assert.match(html(p), /Soll − 1 °C bis Soll \+ 2 °C/);
  assert.match(html(p), /keine Konfiguration gespeichert/);
  delete p.shown.session.control_rules;
  p.renderControlHistory();
  assert.match(html(p), /keine Fristen und Regeln vor/);
});

test("the protocol is closed, filterable per type with counts and without calibration rows", async () => {
  const p = panel();
  p.renderControlHistory();
  assert.doesNotMatch(html(p), /<table>/);
  const chips = [...html(p).matchAll(/data-action="control-protocol-type:([a-z]+)" aria-pressed="true"><strong>(\d+)<\/strong> ([^<]+)</g)].map(
    (m) => [m[1], Number(m[2]), m[3]],
  );
  assert.deepEqual(chips, [
    ["decision", 7, "Heizentscheidungen"],
    ["command", 1, "Ofenbefehle"],
    ["gang", 1, "Gänge"],
    ["recognition", 2, "Erkennungen"],
    ["fault", 2, "Fehler"],
  ]);
  assert.match(html(p), /Fehler im Sitzungsverlauf · <span data-fault-count="1">1<\/span>/, "V-30");
  await p.action("control-protocol");
  assert.match(html(p), /<th>Uhrzeit<\/th>/);
  assert.match(html(p), />Gang 1 gebucht<\/td><td>Beginn [0-9:]+ · bestätigt [0-9:]+ · Ende [0-9:]+ \(Durchlüften\)/);
  assert.doesNotMatch(html(p), /Kalibrierung gestartet/);
  assert.equal((html(p).match(/>Heizanforderung (Ein|Aus)<\/td>/g) || []).length, 7);
  await p.action("control-protocol-type:decision");
  assert.match(html(p), /data-action="control-protocol-type:decision" aria-pressed="false"/);
  assert.equal((html(p).match(/>Heizanforderung (Ein|Aus)<\/td>/g) || []).length, 0);
  assert.match(html(p), /<td><button data-control-time="\d+" aria-label="Zeitpunkt [0-9:]+ wählen">/);
});

test("raw session data stay closed and render only after opening", async () => {
  const p = panel();
  let plots = 0;
  p.renderHistoryValues = () => {
    plots++;
    return "<p>Messreihen</p>";
  };
  p.renderControlHistory();
  const rows = [...html(p).matchAll(/<div class="detail-raw-row"><span>([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(rows, [
    "Heizzeit und Energie",
    "Ofenkühlung und Fristen",
    "Temperaturprogramm",
    "Gänge und Präsenz",
    "Gespeicherte Konfiguration der Sitzung",
    "Messwerte und Lichtverlauf",
    `Einzelbelege · ${p.shown.records.length} Datensätze, seitenweise`,
  ]);
  assert.equal(plots, 0);
  assert.doesNotMatch(html(p), /detail-raw-body|id="detail-records"/);
  await p.action("control-raw:values");
  assert.equal(plots, 1);
  await p.action("control-raw:records");
  assert.match(html(p), /id="detail-records"/);
  assert.match(html(p), /Seite 1 von 1/);
});
