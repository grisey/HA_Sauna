const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const panelPath = process.env.PANEL_PATH || "custom_components/ha_sauna/panel.js";
let Panel;
vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
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
  setTimeout,
});

const t = "2026-01-01T12:00:00Z";
const end = "2026-01-01T12:10:00Z";
const annotationModel = () => {
  const start = Date.parse(t),
    finish = Date.parse(end),
    left = 65,
    right = 1135,
    top = 18,
    bottom = 435;
  return {
    start,
    end: finish,
    left,
    right,
    top,
    bottom,
    x: (time) => left + ((time - start) / (finish - start)) * (right - left),
  };
};
const panel = Object.assign(Object.create(Panel.prototype), {
  state: {
    now: end,
    configuration: { parameters: { sensor_timeout_seconds: 60 } },
    presence: {
      configured_source: "ha_presence",
      current: {
        available: true,
        occupancy: "unknown",
        assertion: "proxy_retraction",
        effective_at: t,
      },
      external: {
        "<sensor>": {
          available: false,
          assertion: "direct_presence",
          effective_at: t,
        },
      },
    },
    rule_inputs: { gang_heat_demand: true, temporary_door_heat: true },
  },
  historyRecords: () => [],
});
const detail = panel.presenceDetails();
assert.match(
  detail,
  /Indirekter Hinweis zurückgenommen; keine beobachtete Abwesenheit/,
);
assert.match(detail, /Präsenzsensor/);
assert.match(detail, /Nicht verfügbar/);
assert.match(detail, /Beobachtete Quelle · &lt;sensor&gt;/);
assert.match(detail, /Temperatur und Feuchte/);
assert.doesNotMatch(detail, /Proxy/);
panel.state.configuration.bindings = { presence: "<sensor>" };
panel.state.presence.effective_source = "ha_presence";
panel.state.presence.external["<sensor>"].reason = "unavailable";
const directDetail = panel.presenceDetails();
assert.match(directDetail, /Führender Präsenzsensor · &lt;sensor&gt;/);
assert.match(directDetail, /Sensor nicht verfügbar/);
assert.doesNotMatch(directDetail, /unavailable/);
assert.doesNotMatch(detail, /Aktivierungsregeln sind offen|keine Wiedergabe/);
assert.match(detail, /Heizanforderung im Saunagang[\s\S]*Aktiv/);
assert.match(detail, /Temporäres Heizen nach Türschluss[\s\S]*Aktiv/);

const session = {
  ended_at: end,
  timeline: { processed: [] },
  heating: { intervals: [{ started_at: t, ended_at: end }] },
};
const gangs = [{ gang_id: "g", started_at: t, ended_at: end, infusion_events: [{}] }];
const records = [{ kind: "phase", received_at: t, payload: { phase: "aufheizen" } }];
panel.shown = {
  records,
  phase_projection: {
    complete: true,
    intervals: [{ started_at: t, ended_at: end, phase: "saunagang", source_id: "g" }],
  },
};
let annotations = panel.historyAnnotations(annotationModel(), session, gangs);
assert.equal(
  (annotations.match(/class="gang"/g) || []).length,
  1,
  "one projected main phase, no duplicate gang overlay",
);
assert.equal(
  (annotations.match(/class="heat"/g) || []).length,
  0,
  "raw phase not rendered over projection",
);
assert.equal(
  (annotations.match(/class="heat actual-heat"/g) || []).length,
  0,
  "actual heating belongs to technical details",
);
panel.shown = { records };
annotations = panel.historyAnnotations(annotationModel(), session, gangs);
assert.doesNotMatch(
  annotations,
  /class="(?:heat|gang)"/,
  "phases are only consumed from the backend projection",
);
console.log("presence and phase projection panel tests passed");

(async () => {
  const archivedProjection = {
    complete: false,
    intervals: [{ started_at: t, ended_at: end, phase: "bereit" }],
  };
  const liveProjection = {
    complete: true,
    intervals: [{ started_at: t, ended_at: end, phase: "saunagang", source_id: "g" }],
  };
  const archivedSession = {
    ...session,
    timeline: { ...session.timeline, session_id: "s" },
  };
  let apiState = {
    ...panel.state,
    permissions: { admin: true },
    session: archivedSession,
    phase_projection: liveProjection,
  };
  let archiveCalls = 0;
  let rendered;
  const nodes = {
    "#history": { hidden: false },
    "#session": { innerHTML: "", value: "" },
  };
  const refreshing = Object.assign(Object.create(Panel.prototype), panel, {
    isConnected: true,
    entry: "entry",
    generation: 0,
    selected: "live",
    navigation: { main: "details", detail: "detail-history" },
    cache: new Map(),
    shadowRoot: { activeElement: null, querySelectorAll: () => [] },
    $: (selector) => nodes[selector] || null,
    invalidateHistoryIndex: () => {},
    scheduleRefresh: () => {},
    drawCurrent: () => {},
    drawSettings: () => {},
    message: (error) => {
      if (error) throw error;
    },
    drawHistory() {
      rendered = this.historyAnnotations(annotationModel(), this.shown.session, gangs);
    },
    api: async (request) => {
      if (request === "/entry/state") return apiState;
      if (request === "/entry/archive")
        return [{ session_id: "s", started_at: t, ended_at: end }];
      archiveCalls++;
      return {
        session: archivedSession,
        records:
          archiveCalls === 1
            ? records.map((item, index) => ({ ...item, id: index + 1 }))
            : [],
        phase_projection: archivedProjection,
        next_after: null,
      };
    },
  });
  await refreshing.refresh();
  await refreshing.historyLoad?.promise;
  assert.equal(
    refreshing.shown.phase_projection,
    liveProjection,
    "active session uses current runtime projection",
  );
  assert.equal((rendered.match(/class="gang"/g) || []).length, 1);
  assert.doesNotMatch(
    rendered,
    /class="heat"/,
    "refresh preserves projection into annotations",
  );

  refreshing.selected = "s";
  refreshing.historySelectionGeneration = 1;
  await refreshing.refresh();
  await refreshing.historyLoad?.promise;
  assert.equal(archiveCalls, 1, "closed archive selection reuses the final cache");
  assert.equal(
    refreshing.shown.phase_projection,
    archivedProjection,
    "cached archive keeps its own projection",
  );
  assert.match(rendered, /class="ready"/);
  assert.doesNotMatch(
    rendered,
    /class="gang"/,
    "archive cannot inherit current gang projection",
  );

  refreshing.selected = "live";
  refreshing.historySelectionGeneration = 2;
  apiState = { ...apiState, session: null };
  await refreshing.refresh();
  await refreshing.historyLoad?.promise;
  assert.equal(
    refreshing.shown.phase_projection,
    archivedProjection,
    "last-session view uses archive projection when live session is absent",
  );
  console.log("refresh projection transport tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

// The operational history separates observed occupancy, demand and real feedback.
const operational = Object.assign(Object.create(Panel.prototype), {
  state: { now: end, configuration: { bindings: { presence: "binary_sensor.demo" } } },
  shown: {
    session: {
      timeline: {
        session_id: "direct",
        session_started_at: t,
        processed: [],
        completed: [],
        active: null,
      },
      contactor_history: [
        { at: t, state: true },
        { at: end, state: false },
      ],
    },
    phase_projection: { intervals: [] },
    records: [
      {
        kind: "presence",
        payload: {
          assertion: "direct_presence",
          source: "binary_sensor.demo",
          occupancy: "present",
          available: true,
          effective_at: t,
          received_at: t,
        },
      },
      {
        kind: "presence",
        payload: {
          assertion: "direct_presence",
          source: "binary_sensor.demo",
          occupancy: "unknown",
          available: false,
          effective_at: end,
          received_at: end,
        },
      },
      {
        kind: "decision",
        payload: { at: t, created_at: t, heat: true, reason: "gang_heat_demand" },
      },
      {
        kind: "decision",
        payload: { at: end, created_at: end, heat: false, reason: "after_run" },
      },
    ],
  },
  updateMarkup(_selector, html) {
    this.html = html;
  },
});
operational.renderControlHistory();
assert.match(operational.html, /Betriebsverlauf/);
assert.match(operational.html, /<th>Uhrzeit<\/th>/);
assert.doesNotMatch(operational.html, /Beobachtet|Verarbeitet/);
assert.match(operational.html, /<dt>Präsenzsensor<\/dt><dd>Nicht verfügbar<\/dd>/);
assert.match(operational.html, /<dt>Heizanforderung<\/dt><dd>Aus · Ofenkühlung<\/dd>/);
assert.match(operational.html, /<dt>Ofenschalter<\/dt><dd>Aus<\/dd>/);
assert.doesNotMatch(operational.html, /session-chart|Temperaturkurve/);
operational.shown.phase_projection.intervals = [
  { started_at: t, ended_at: end, phase: "nachlauf" },
];
operational.renderControlHistory();
assert.match(
  operational.html,
  /<dt>Ofenkühlung<\/dt><dd>Aktiv<\/dd>/,
  "the projection cutoff does not fabricate a cooling end",
);
operational.state.session = operational.shown.session;
operational.state.phase = "bereit";
operational.renderControlHistory();
assert.match(
  operational.html,
  /<dt>Ofenkühlung<\/dt><dd>Inaktiv<\/dd>/,
  "a current phase change at the right boundary is reflected",
);
operational.controlHistoryCursor = Date.parse(t) + 1000;
operational.renderControlHistory();
assert.match(operational.html, /<dt>Präsenzsensor<\/dt><dd>Anwesend<\/dd>/);
assert.match(operational.html, /<dt>Ofenschalter<\/dt><dd>Ein<\/dd>/);

// These are the codes emitted by thermostat.evaluate and Controller._evaluate_manual.
operational.controlHistoryCursor = null;
for (const [reason, label] of [
  ["temperature_reached", "Obere Regeltemperatur erreicht"],
  ["thermostat_cooldown", "Heizpause"],
  ["manual_mode", "Manueller Betrieb · Ofen Aus"],
  ["temperature_configuration_required", "Temperatureinstellungen unvollständig"],
  ["upper_temperature_unavailable", "Gültiger Regeltemperaturwert fehlt"],
  ["protection:heater_feedback_mismatch", "Bestätigte technische Störung"],
  ["inhibit:configuration", "Einrichtung unvollständig"],
]) {
  operational.shown.records = [
    {
      kind: "decision",
      payload: { at: end, created_at: end, heat: false, reason },
    },
  ];
  operational.renderControlHistory();
  assert.ok(
    operational.html.includes(`<dt>Heizanforderung</dt><dd>Aus · ${label}</dd>`),
    `${reason} has its German label in the state at the selected time`,
  );
  assert.ok(
    operational.html.includes(`>${label}</td>`),
    `${reason} has its German label in the decision history`,
  );
  assert.ok(!operational.html.includes(reason), `${reason} is not shown as an ID`);
}
