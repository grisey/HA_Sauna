const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const { parameterValues } = require("./catalog_fixture.cjs");
let Panel;
vm.runInNewContext(fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"), {
  HTMLElement: class {},
  customElements: { get: () => null, define: (_name, value) => (Panel = value) },
});
const iso = (second) => new Date(Date.UTC(2026, 9, 12, 10, 0, second)).toISOString();
const record = (id, kind, payload) => ({ id, kind, received_at: iso(id), payload });
const panel = () =>
  Object.assign(Object.create(Panel.prototype), {
    state: {
      permissions: { admin: true },
      configuration: { parameters: parameterValues() },
    },
  });
const light = (id, brightness, extra = {}) =>
  record(id, "light_command", {
    phase: "aufheizen",
    purpose: "aufheizen",
    brightness_pct: brightness,
    service: "turn_on",
    sent_at: iso(id),
    completed_at: iso(id),
    service_error: null,
    ...extra,
  });

test("diagnostic snapshots count incident starts, retain status changes and partial clearances", () => {
  const records = [
    record(1, "diagnostic", { faults: {}, messages: [] }),
    record(2, "diagnostic", {
      faults: { heater: "pending" },
      messages: ["Heizung wird geprüft"],
    }),
    record(3, "diagnostic", {
      faults: { heater: "confirmed" },
      messages: ["Heizung bestätigt"],
    }),
    record(4, "diagnostic", {
      faults: { heater: "confirmed", light: "failed" },
      messages: ["Heizung bestätigt", "Lichtfehler"],
    }),
    record(5, "diagnostic", { faults: { light: "failed" }, messages: ["Lichtfehler"] }),
    record(6, "diagnostic", { faults: {}, messages: [] }),
    record(7, "diagnostic", {
      faults: { heater: "pending" },
      messages: ["Heizung erneut geprüft"],
    }),
  ];
  const original = JSON.stringify(records),
    p = panel();
  const result = p.historyFaultEvents(records);
  assert.equal(result.count, 3);
  assert.deepEqual(
    Array.from(result.events, (event) => event.label),
    [
      "Fehler aufgetreten",
      "Fehlerstatus geändert",
      "Fehler aufgetreten",
      "Entwarnung",
      "Entwarnung",
      "Fehler aufgetreten",
    ],
  );
  assert.equal(new Set(result.events.map((event) => event.id)).size, 6);
  assert.equal(JSON.stringify(records), original);
  assert.match(p.historyFaultMarkup(records.slice(0, 1)), /data-fault-count="0"/);
  assert.doesNotMatch(
    p.historyFaultMarkup(records.slice(0, 1)),
    /Historisch|Keine Störung gemeldet/,
  );
  p.state.permissions.admin = false;
  assert.equal(
    p.historyFaultMarkup([]),
    "",
    "public history is not evidence of fault freedom",
  );
});

test("light ramps keep recorded boundaries, manual interruptions and every failure", () => {
  const records = [
    light(1, 10),
    light(2, 30),
    light(3, 50),
    record(4, "manual_light", { value: 20 }),
    light(5, 20),
    light(6, 20, { service_error: "TimeoutError" }),
    light(7, 20),
    record(8, "phase", { phase: "bereit" }),
    light(9, 60, { phase: "bereit", purpose: "bereit" }),
    light(10, null, {
      service: "turn_off",
      phase: "session_light",
      purpose: "session_end",
    }),
  ];
  const p = panel(),
    original = JSON.stringify(records);
  const activity = p.historyLightActivity(records);
  assert.equal(activity.length, 6);
  assert.equal(activity[0].records.length, 3);
  assert.equal(activity[0].first, records[0]);
  assert.equal(activity[0].last, records[2]);
  assert.match(activity[0].detail, /letzter belegter Wert/);
  assert.equal(activity.filter((event) => event.error).length, 1);
  assert.equal(activity.at(-1).label, "Lichtbefehl Aus");
  assert.doesNotMatch(activity.at(-1).label, /ausgeschaltet/);
  assert.equal(JSON.stringify(records), original);
  const open = p.historyLightActivity([light(1, 10), light(2, 20)]);
  assert.match(open[0].detail, /kein Abschluss belegt/);
  const appended = p.historyLightActivity([light(1, 10), light(2, 20), light(3, 30)]);
  assert.equal(open[0].id, appended[0].id);
  const late = p.historyLightActivity([
    light(1, 10),
    light(2, 20),
    light(3, 5, { sent_at: iso(0) }),
  ]);
  assert.equal(
    late[0].id,
    open[0].id,
    "earlier sent command arriving later preserves section identity",
  );
  assert.equal(late[0].first.payload.brightness_pct, 5);
  assert.equal(late[0].last.payload.brightness_pct, 20);
  assert.match(
    p.historyLightActivity([light(1, null)])[0].detail,
    /Helligkeit nicht aufgezeichnet/,
  );
  assert.equal(p.historyLightValue(light(1, 0)), "0 %");
});

test("values retain missing sources and use authoritative power projection without unit assumptions", () => {
  const records = [
    record(1, "measurement", {
      position: "upper",
      quantity: "temperature",
      value: 0,
      received_at: iso(1),
    }),
    record(2, "measurement", {
      position: "upper",
      quantity: "temperature",
      value: null,
      received_at: iso(2),
    }),
    {
      ...record(3, "source_state", {
        role: "heater_power",
        state: "6",
        attributes: { unit_of_measurement: "kW" },
      }),
      display: { power_w: 6000 },
    },
    {
      ...record(4, "source_state", {
        role: "heater_power",
        state: "2",
        attributes: { unit_of_measurement: "MW" },
      }),
      display: { power_w: null },
    },
    record(5, "source_state", { role: "heater_power", state: "2", attributes: {} }),
    record(6, "illuminance", { state: "0", attributes: { unit_of_measurement: "lx" } }),
    record(7, "illuminance", { state: "2", attributes: { unit_of_measurement: "fc" } }),
  ];
  const p = panel();
  const series = p.historyValueSeries(records);
  const power = series.find((item) => item.key === "power");
  assert.deepEqual(
    Array.from(power.values, (point) => point.value),
    [6000, null, null],
  );
  assert.equal(
    series.find((item) => item.key === "upper:temperature").values[0].value,
    0,
  );
  assert.equal(series.filter((item) => item.key.startsWith("illuminance:")).length, 2);
  assert.match(
    p.renderHistoryValues(
      records,
      Date.parse(iso(0)),
      Date.parse(iso(10)),
      Date.parse(iso(2)),
    ),
    /Quelle nicht aufgezeichnet/,
  );
  records.push(
    record(8, "measurement", {
      position: "upper",
      quantity: "temperature",
      value: 42,
      received_at: iso(8),
    }),
  );
  const next = p.historyValueSeries(records);
  assert.equal(
    next.find((item) => item.key === "power"),
    power,
    "unchanged series keeps its identity",
  );
  assert.equal(
    next.find((item) => item.key === "upper:temperature").values.at(-1).value,
    42,
  );
});

test("record access is bounded and all pages remain reachable", () => {
  const p = panel(),
    records = Array.from({ length: 93 }, (_, index) =>
      record(index + 1, "decision", { heat: false, reason: "operation_off" }),
    );
  assert.equal(
    (p.renderHistoryRecords(records).match(/<details data-record-id=/g) || []).length,
    40,
  );
  p.detailRecordPage = 2;
  const last = p.renderHistoryRecords(records);
  assert.equal((last.match(/<details data-record-id=/g) || []).length, 13);
  assert.match(last, /data-record-id="93"/);
  assert.match(last, /Seite 3 von 3/);
  assert.match(p.detailFields({ false_value: false, zero: 0, missing: null }), /Nein/);
  assert.match(
    p.detailFields({ false_value: false, zero: 0, missing: null }),
    /Nicht aufgezeichnet/,
  );
  const events = Array.from({ length: 93 }, (_, index) => ({
    id: `event-${index}`,
    at: Date.parse(iso(index)),
    label: "Ereignis",
    detail: "Beleg",
    source: "Regelung",
    error: index === 90,
  }));
  const activity = p.renderControlEvents(events, null);
  assert.equal((activity.match(/<tr data-record-id=/g) || []).length, 60);
  assert.match(
    activity,
    /data-record-id="error-event-90"/,
    "errors outside the current page remain immediately visible",
  );
});

test("live detail applicability follows configuration model while archive values keep their own evidence", () => {
  const p = panel();
  p.state = {
    ...p.state,
    configuration_model: {
      measurement_positions: ["upper"],
      presence: { direct: true },
      regulation_causes: ["gang_heat", "cooling"],
      power_sensor_configured: false,
    },
    measurements: [
      { position: "upper", quantity: "temperature", value: 75 },
      { position: "lower", quantity: "temperature", value: 55 },
    ],
    rule_inputs: { gang_heat_demand: false, temporary_door_heat: true, cooling: false },
    heating_observation: { source: "contactor", heating: false, power_w: null },
  };
  const direct = p.renderLiveEvidence();
  assert.doesNotMatch(
    direct,
    /temporary_door_heat|Türhilfe|power_w|Leistung \(W\)|Untere|>55[< ]/,
  );
  p.state.configuration_model.regulation_causes = [
    "gang_heat",
    "provisional_door_heat",
    "cooling",
  ];
  assert.match(p.renderLiveEvidence(), /Türhilfe/);
  const archived = [
    record(1, "measurement", { position: "lower", quantity: "temperature", value: 55 }),
  ];
  assert.equal(
    p.historyValueSeries(archived)[0].key,
    "lower:temperature",
    "current one-height configuration cannot erase historical evidence",
  );
  p.state.configuration_model.regulation_causes = [];
  const manual = p.renderDetailView({
    targetTemperature: "75",
    thermostatTarget: "77",
    restartThreshold: "73",
    temperatureProgram: "90 → 70 °C",
    nextGangTemperature: "70",
  });
  assert.match(manual, /Konstante Temperaturregelung \(manuell\)/);
  assert.match(manual, /Wiedereinschaltschwelle<\/dt><dd>73 °C/);
  assert.match(manual, /Obere Regeltemperatur<\/dt><dd data-readiness>77 °C/);
  assert.doesNotMatch(manual, /90 → 70|Solltemperatur des nächsten Gangs/);
});

test("status-only counter updates patch evidence at the slower cadence without rebuilding plots", () => {
  const p = panel(),
    patches = [];
  const session = {
    ended_at: null,
    timeline: {
      session_id: "retained",
      session_started_at: iso(0),
      processed: [],
      completed: [],
      retracted: [],
      active: null,
    },
    energy: { measured_kwh: 1 },
    configuration: p.state.configuration,
  };
  p.state = { ...p.state, now: iso(40), session, phase: "aufheizen" };
  p.shown = { session, records: [], phase_projection: { intervals: [] } };
  p.shadowRoot = {};
  p.$ = () => ({});
  p.updateMarkup = (selector, html) => patches.push({ selector, html });
  let plots = 0;
  p.renderHistoryValues = () => {
    plots++;
    return "";
  };
  p.renderControlHistory();
  p.state.now = iso(41);
  p.renderControlHistory(); // Enable the explicit newest-data button once.
  plots = 0;
  patches.length = 0;
  session.energy.measured_kwh = 2;
  p.state.now = iso(46);
  p.renderControlHistory();
  assert.equal(plots, 0);
  assert.deepEqual(
    patches.map((item) => item.selector),
    ["#control-session-evidence"],
  );
  assert.match(patches[0].html, /Gemessene Energie \(kWh\)<\/dt><dd>2<\/dd>/);
  assert.equal(p.controlHistoryEnd, Date.parse(iso(40)));
});
