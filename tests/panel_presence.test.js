const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");
const assert = require("node:assert/strict");
const { parameterValues: fixtureValues } = require("./catalog_fixture.cjs");
const fs = require("node:fs");
const test = require("node:test");
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
    valueScale: {intervals: 4},
    x: (time) => left + ((time - start) / (finish - start)) * (right - left),
  };
};
const panel = Object.assign(fixturePanel(Panel), {
  state: {
    now: end,
    configuration: { parameters: fixtureValues() },
    presence: {
      configured_source: "ha_presence",
      current: {
        report_id: "proxy-retracted",
        available: true,
        occupancy: "unknown",
        assertion: "proxy_retraction",
        source: "proxy",
        source_ref: "gang-retracted",
        effective_at: t,
        received_at: t,
        reason: "gang_retracted",
      },
      external: {
        "<sensor>": {
          report_id: "sensor-unavailable",
          available: false,
          occupancy: "unknown",
          assertion: "direct_presence",
          source: "<sensor>",
          source_ref: "sensor-state",
          effective_at: t,
          received_at: t,
          reason: "unavailable",
        },
      },
    },
    rule_inputs: { gang_heat_demand: true, temporary_door_heat: true },
    configuration_model: {
      presence: { source: "proxy", direct: false, observer: true },
      regulation_causes: ["gang_heat", "provisional_door_heat", "cooling"],
    },
  },
  historyRecords: () => [],
});
const detail = panel.presenceDetails();
assert.match(
  detail,
  /Indirekter Hinweis zurückgenommen; keine beobachtete Abwesenheit/,
);
assert.doesNotMatch(detail, /Nicht verfügbar|Beobachtete Quelle|&lt;sensor&gt;/);
assert.match(detail, /Temperatur und Feuchte/);
assert.doesNotMatch(detail, /Proxy/);
assert.equal(panel.state.presence.external["<sensor>"].report_id, "sensor-unavailable");
panel.state.configuration.bindings = { presence: "<sensor>" };
panel.state.configuration_model.presence = {
  source: "ha_presence",
  direct: true,
  observer: false,
};
panel.state.configuration_model.regulation_causes = [
  "gang_heat",
  "provisional_door_heat",
  "cooling",
];
panel.state.presence.external["<sensor>"].reason = "unavailable";
panel.state.presence.current = panel.state.presence.external["<sensor>"];
const directDetail = panel.presenceDetails();
assert.match(directDetail, /Präsenzsensor mit Türereignis/);
assert.doesNotMatch(directDetail, /Beobachtete Quelle/);
assert.match(directDetail, /Vorläufige Heizanforderung nach Türschluss[\s\S]*Aktiv/);
assert.match(directDetail, /Sensor nicht verfügbar/);
assert.doesNotMatch(directDetail, /unavailable/);
assert.doesNotMatch(detail, /Aktivierungsregeln sind offen|keine Wiedergabe/);
assert.match(detail, /Heizanforderung im Saunagang[\s\S]*Aktiv/);
assert.match(detail, /Vorläufige Heizanforderung nach Türschluss[\s\S]*Aktiv/);

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
  const refreshing = Object.assign(fixturePanel(Panel), panel, {
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
      if (request === "/entry/state?include_catalog=false") return apiState;
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
const operational = Object.assign(fixturePanel(Panel), {
  state: { now: end, configuration: { bindings: { presence: "binary_sensor.demo" } } },
  shown: {
    session: {
      configuration: {
        presence_source: "ha_presence",
        bindings: { presence: "binary_sensor.demo" },
        parameters: fixtureValues(),
      },
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
        received_at: t,
        payload: {
          report_id: "direct-present",
          assertion: "direct_presence",
          source: "binary_sensor.demo",
          source_ref: "sensor-on",
          occupancy: "present",
          available: true,
          effective_at: t,
          received_at: t,
          reason: null,
        },
      },
      {
        kind: "presence",
        received_at: end,
        payload: {
          report_id: "direct-unavailable",
          assertion: "direct_presence",
          source: "binary_sensor.demo",
          source_ref: "sensor-unavailable",
          occupancy: "unknown",
          available: false,
          effective_at: end,
          received_at: end,
          reason: "unavailable",
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
assert.match(operational.html, /<dt>Präsenz<\/dt><dd>Nicht verfügbar<\/dd>/);
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
assert.match(operational.html, /<dt>Präsenz<\/dt><dd>Anwesend<\/dd>/);
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
  assert.ok(
    !operational.html.split('id="detail-records"')[0].includes(reason),
    `${reason} is not shown as an ID outside explicit archive fields`,
  );
}

const at = (second) => new Date(Date.parse(t) + second * 1000).toISOString();
const presenceReport = (id, source, assertion, occupancy, second, extra = {}) => ({
  report_id: id,
  source,
  source_ref: `input:${id}`,
  assertion,
  occupancy,
  effective_at: at(second),
  received_at: at(second),
  available: true,
  reason: null,
  ...extra,
});
const presenceRecord = (id, payload) => ({
  id,
  kind: "presence",
  received_at: payload.received_at,
  payload,
});
const sourceRecord = (id, second, source, previous, snapshot = {}) => ({
  id,
  kind: "presence_source",
  received_at: at(second),
  payload: {
    configured_source: "ha_presence",
    effective_source: source,
    entity_id: "binary_sensor.saved",
    ...(previous
      ? { previous_source: previous, reason: "availability_qualified" }
      : { external_snapshot: snapshot }),
  },
});
const presenceHistoryPanel = (records, finish) =>
  Object.assign(fixturePanel(Panel), {
    state: { now: at(finish), configuration: { parameters: fixtureValues() } },
    shown: {
      session: {
        ended_at: at(finish),
        configuration: {
          presence_source: "ha_presence",
          bindings: { presence: "binary_sensor.saved" },
          parameters: fixtureValues(),
        },
        timeline: {
          session_id: "source-history",
          session_started_at: at(0),
          processed: [],
          completed: [],
          active: null,
        },
      },
      phase_projection: { intervals: [] },
      records,
    },
    updateMarkup(_selector, html) {
      this.html = html;
    },
  });

test("control history follows source markers through repeated fallback and paged appends", () => {
  const parameters = fixtureValues(),
    firstProxyAt = 4 + parameters.presence_fallback_delay_seconds,
    sensorAt = firstProxyAt + 4 + parameters.presence_recovery_delay_seconds,
    secondProxyAt = sensorAt + 4 + parameters.presence_fallback_delay_seconds,
    finish = secondProxyAt + 4;
  const sensor = presenceReport(
      "saved-sensor",
      "binary_sensor.saved",
      "direct_presence",
      "absent",
      0,
    ),
    ignored = presenceReport(
      "foreign-sensor",
      "binary_sensor.other",
      "direct_presence",
      "present",
      0,
    ),
    proxy = presenceReport(
      "first-proxy",
      "proxy",
      "provisional_proxy",
      "present",
      firstProxyAt + 1,
    ),
    recovered = presenceReport(
      "sensor-recovered",
      "binary_sensor.saved",
      "direct_presence",
      "absent",
      firstProxyAt + 4,
    );
  const records = [
      sourceRecord(1, 0, "ha_presence", null, {
        "binary_sensor.saved": sensor,
        "binary_sensor.other": ignored,
      }),
      sourceRecord(2, firstProxyAt, "proxy", "ha_presence"),
      presenceRecord(
        3,
        presenceReport(
          "first-unknown",
          "proxy",
          "provisional_proxy",
          "unknown",
          firstProxyAt,
          { reason: "no_proxy_evidence" },
        ),
      ),
      presenceRecord(4, proxy),
      presenceRecord(5, recovered),
      sourceRecord(6, sensorAt, "ha_presence", "proxy"),
    ],
    history = presenceHistoryPanel(records, finish);
  const original = JSON.stringify(records);
  const expectOccupancy = (second, label) => {
    history.renderControlHistory();
    history.controlHistoryCursor = Date.parse(at(second));
    history.renderControlHistory();
    assert.ok(history.html.includes(`<dt>Präsenz</dt><dd>${label}</dd>`));
  };
  expectOccupancy(1, "Abwesend");
  expectOccupancy(firstProxyAt, "Unbekannt");
  expectOccupancy(firstProxyAt + 2, "Anwesend");
  expectOccupancy(sensorAt, "Abwesend");

  // Runtime owns qualification; elapsed UI time alone cannot select a source.
  expectOccupancy(secondProxyAt + 1, "Abwesend");
  assert.equal(JSON.stringify(records), original);
  records.push(sourceRecord(7, secondProxyAt, "proxy", "ha_presence"));
  expectOccupancy(secondProxyAt + 1, "Unbekannt");
  const secondUnknown = presenceReport(
    "second-unknown",
    "proxy",
    "provisional_proxy",
    "unknown",
    secondProxyAt,
    { reason: "no_proxy_evidence" },
  );
  records.push(presenceRecord(8, secondUnknown));
  const raw = JSON.stringify(records);
  expectOccupancy(secondProxyAt + 1, "Unbekannt");
  const effective = history.historyPresenceReports(
    Date.parse(t),
    Date.parse(at(finish)),
  );
  assert.equal(
    effective.filter(({ report }) => report?.report_id === sensor.report_id).length,
    1,
  );
  assert.equal(
    effective.some(({ report }) => report?.report_id === ignored.report_id),
    false,
  );
  assert.equal(effective.at(-1).report.report_id, secondUnknown.report_id);
  const ordinary = history.html.split('id="detail-records"')[0];
  assert.doesNotMatch(ordinary, /binary_sensor\.other|foreign-sensor/);
  assert.match(history.renderHistoryRecords(records), /foreign-sensor/);
  assert.equal(
    JSON.stringify(records),
    raw,
    "display filtering preserves raw evidence",
  );

  // An older archive has its own saved source even while a different session is live.
  history.state.session = { timeline: { session_id: "current" } };
  history.state.configuration_model = {
    presence: { source: "ha_presence", direct: true },
  };
  history.state.configuration = {
    presence_source: "ha_presence",
    bindings: { presence: "binary_sensor.other" },
    parameters,
  };
  history.state.presence = { current: ignored };
  history.shown = {
    ...history.shown,
    records: [presenceRecord(1, proxy)],
    session: {
      ...history.shown.session,
      configuration: { presence_source: "proxy", parameters },
      timeline: { ...history.shown.session.timeline, session_id: "old-proxy" },
    },
  };
  expectOccupancy(finish, "Anwesend");
  assert.equal(history.historyConfigurationModel().presence.source, "proxy");
  history.shown.session.timeline.processed = [
    {
      event_id: "legacy-person",
      session_id: "old-proxy",
      kind: "person_strong",
      effective_at: proxy.effective_at,
      detected_at: proxy.received_at,
      booking_at: proxy.effective_at,
    },
  ];
  history.renderControlHistory();
  assert.match(
    history.html.split('id="detail-records"')[0],
    /data-record-id="legacy-person"/,
  );
  delete history.shown.session.configuration;
  history.renderControlHistory();
  assert.equal(history.historyConfigurationModel(), null);
  assert.match(history.html, /<dt>Präsenz<\/dt><dd>Unbekannt<\/dd>/);
  assert.match(
    history.html.split('id="detail-records"')[0],
    /data-record-id="legacy-person"/,
  );
  // Even an initial owner marker requires the person's original archive binding.
  history.shown.records = [sourceRecord(1, 0, "proxy", null), presenceRecord(2, proxy)];
  history.renderControlHistory();
  assert.doesNotMatch(
    history.html.split('id="detail-records"')[0],
    /data-record-id="legacy-person"/,
  );
});

test("the final proxy evidence before a same-time recovery marker remains historical evidence", () => {
  const parameters = fixtureValues(),
    recovery = parameters.presence_recovery_delay_seconds + 2,
    report = presenceReport(
      "presence:proxy:last-person",
      "proxy",
      "provisional_proxy",
      "present",
      recovery - 1,
      { received_at: at(recovery), source_ref: "last-person" },
    ),
    sensor = presenceReport(
      "returning-sensor",
      "binary_sensor.saved",
      "direct_presence",
      "absent",
      1,
    ),
    event = {
      event_id: "last-person",
      session_id: "source-history",
      kind: "person_strong",
      effective_at: report.effective_at,
      detected_at: report.received_at,
      booking_at: report.effective_at,
    },
    consumer = {
      event_id: report.report_id,
      kind: "occupancy",
      session_id: "source-history",
      effective_at: report.effective_at,
      received_at: report.received_at,
      source_ref: report.source_ref,
      gang_id: null,
      presence: report,
      delivery: "live",
    },
    records = [
      sourceRecord(1, 0, "proxy", null),
      presenceRecord(2, sensor),
      { id: 3, kind: "consumer_event", received_at: at(recovery), payload: consumer },
      presenceRecord(4, report),
      {
        id: 5,
        kind: "detection",
        received_at: at(recovery),
        payload: { event, channels: ["upper"], trace_at: report.effective_at },
      },
      {
        id: 6,
        kind: "detector_trace",
        received_at: at(recovery),
        payload: {
          at: report.effective_at,
          channels: ["upper"],
          metrics: { upper: { strong_humidity_slope: 2 } },
          checks: { strong: true },
          conditions: { strong: true },
          holds: { strong: 1 },
          signals: ["person_strong"],
        },
      },
      sourceRecord(7, recovery, "ha_presence", "proxy"),
    ],
    history = presenceHistoryPanel(records, recovery + 1);
  history.shown.session.timeline.processed = [event];
  history.renderControlHistory();
  history.controlHistoryCursor = Date.parse(report.effective_at);
  history.renderControlHistory();
  assert.match(history.html, /<dt>Präsenz<\/dt><dd>Anwesend<\/dd>/);
  const ordinary = history.html.split('id="detail-records"')[0];
  assert.match(ordinary, /data-record-id="last-person"/);
  assert.match(ordinary, /data-record-id="record-3"/);
  assert.ok(
    history
      .historyPresenceReports(Date.parse(t), Date.parse(at(recovery + 1)))
      .some((item) => item.report?.report_id === report.report_id),
  );
  assert.equal(
    history.diagnosticMarkers(
      history.diagnosticData().traces,
      "strong_humidity_slope",
      Date.parse(t),
      Date.parse(at(recovery + 1)),
    )[0].event.event_id,
    event.event_id,
  );
  history.controlHistoryCursor = Date.parse(at(recovery));
  history.renderControlHistory();
  assert.match(history.html, /<dt>Präsenz<\/dt><dd>Abwesend<\/dd>/);
  // A blocked direct-era input may remain in processed without a presence record.
  history.shown.session.timeline.processed.push({
    event_id: "blocked-person",
    session_id: "source-history",
    kind: "person_strong",
    effective_at: at(recovery + 0.5),
    detected_at: at(recovery + 0.5),
    booking_at: at(recovery + 0.5),
  });
  history.renderControlHistory();
  assert.doesNotMatch(
    history.html.split('id="detail-records"')[0],
    /data-record-id="blocked-person"/,
  );
  assert.equal(
    history.shown.session.timeline.processed.at(-1).event_id,
    "blocked-person",
  );
});
