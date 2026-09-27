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
assert.match(detail, /Proxy zurückgenommen; keine beobachtete Abwesenheit/);
assert.match(detail, /Externe Präsenz \(vorbereitet\)/);
assert.match(detail, /Nicht verfügbar/);
assert.match(detail, /&lt;sensor&gt;/);
assert.match(detail, /keine Wiedergabe/);
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
  1,
  "actual heating remains separate",
);
panel.shown = { records };
annotations = panel.historyAnnotations(annotationModel(), session, gangs);
assert.match(annotations, /class="heat"/, "legacy archive keeps its fallback");
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
    "#history-loading": {},
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
