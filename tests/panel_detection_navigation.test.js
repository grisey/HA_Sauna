// The detector view works without HA or a browser; the DOM here records real
// click actions and the stable event-id mapping used by both directions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let Panel;
const sandbox = {
  HTMLElement: class {},
  customElements: {
    get: () => undefined,
    define: (_n, value) => {
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
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"),
  sandbox,
);

const iso = (second) => new Date(1_700_000_000_000 + second * 1000).toISOString();
const processed = [
  {
    event_id: "door-7",
    kind: "door_open",
    effective_at: iso(10),
    detected_at: iso(11),
  },
  {
    event_id: "infusion-8",
    kind: "infusion",
    effective_at: iso(10),
    detected_at: iso(12),
  },
];
const session = {
  ended_at: iso(60),
  timeline: { session_started_at: iso(0), processed, completed: [] },
  configuration: { parameters: { person_step_seconds: 5 } },
};
const parameters = {
  door_open_slope: -1,
  door_heating_slope: -1,
  door_close_slope: 1,
  door_open_humidity_upper: 1,
  door_open_humidity_lower: 1,
  vent_drop_upper: 3,
  vent_drop_lower: 3,
  vent_absolute_humidity_loss_percent: 30,
};

// Equal timestamps do not collapse: IDs remain navigation keys, while each
// event marker is tied to the archived detector value in its primary curve.
{
  const plots = {};
  const traces = [
    {
      at: iso(10),
      metrics: { upper: { door_temperature_slope: -2 }, lower: {} },
      conditions: {},
      holds: { candidate: 0, door_open: 2 },
      signals: ["door_open"],
      channels: ["upper"],
    },
    {
      at: iso(10),
      metrics: { upper: { infusion_humidity_delta: 8 }, lower: {} },
      conditions: {},
      holds: { infusion: 0 },
      signals: ["infusion"],
      channels: ["upper"],
    },
  ];
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session,
      records: traces.map((payload) => ({ kind: "detector_trace", payload })),
    },
    state: { permissions: { admin: true }, configuration: { parameters } },
    window: [Date.parse(iso(0)), Date.parse(iso(60))],
    $: (selector) => (selector === "#detection-plots" ? plots : null),
  });
  p.drawDiagnostics();
  assert.deepEqual(
    p.eventNavigation().map((e) => e.event_id),
    ["door-7", "infusion-8"],
  );
  assert.equal(
    (plots.innerHTML.match(/class="event-marker diagnostic-marker"/g) || []).length,
    2,
  );
  assert.match(plots.innerHTML, /event-marker:door-7/);
  assert.match(plots.innerHTML, /event-marker:infusion-8/);
  assert.doesNotMatch(plots.innerHTML, /event-strip/);
  assert.match(plots.innerHTML, /event-marker-link/);
}

// Same signal kinds bind only to their own effective timestamp; an event with
// no archived trace receives no marker at all.

// Door onset can precede the grid point that completed its hold. The archive
// detection record identifies that producing point by event ID.
{
  const plots = {};
  const door = {
    event_id: "door-produced",
    kind: "door_open",
    effective_at: iso(25),
    detected_at: iso(35),
  };
  const traces = [
    {
      at: iso(25),
      metrics: { upper: { door_temperature_slope: -12 } },
      conditions: {},
      holds: { door_open: 1 },
      signals: [],
      channels: ["upper"],
    },
    {
      at: iso(30),
      metrics: { upper: { door_temperature_slope: -15 } },
      conditions: { door_open: true },
      holds: { door_open: 2 },
      signals: ["door_open"],
      channels: ["upper"],
    },
  ];
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: { ...session, timeline: { ...session.timeline, processed: [door] } },
      records: [
        ...traces.map((payload) => ({ kind: "detector_trace", payload })),
        {
          kind: "detection",
          payload: { event: door, trace_at: iso(30), channels: ["upper"] },
        },
      ],
    },
    state: { permissions: { admin: true }, configuration: { parameters } },
    window: [Date.parse(iso(0)), Date.parse(iso(60))],
    $: (selector) => (selector === "#detection-plots" ? plots : null),
  });
  assert.equal(p.diagnosticTraceForEvent(traces, door), traces[1]);
  p.drawDiagnostics();
  assert.match(plots.innerHTML, /event-marker:door-produced/);
  assert.match(plots.innerHTML, /2 Prüfpunkte/);
}

{
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: {
        ...session,
        timeline: {
          ...session.timeline,
          processed: [
            {
              event_id: "door-a",
              kind: "door_open",
              effective_at: iso(10),
              detected_at: iso(11),
            },
            {
              event_id: "door-b",
              kind: "door_open",
              effective_at: iso(20),
              detected_at: iso(21),
            },
            {
              event_id: "door-missing",
              kind: "door_open",
              effective_at: iso(30),
              detected_at: iso(31),
            },
          ],
        },
      },
      records: [],
    },
  });
  const traces = [
    {
      at: iso(10),
      metrics: { upper: { door_temperature_slope: -2 } },
      signals: ["door_open"],
    },
    {
      at: iso(20),
      metrics: { upper: { door_temperature_slope: -4 } },
      signals: ["door_open"],
    },
  ];
  assert.deepEqual(
    p
      .diagnosticMarkers(
        traces,
        "door_temperature_slope",
        Date.parse(iso(0)),
        Date.parse(iso(60)),
      )
      .map((m) => [m.event.event_id, m.value]),
    [
      ["door-a", -2],
      ["door-b", -4],
    ],
  );
}

// Ventilation losses are archived as fractions, but the diagnostics display
// them as percentages and retain the archived 30 % threshold (25 % is below,
// 35 % above).  Earlier traces without these metrics render no false zero line.
{
  const plots = {};
  const traces = [
    {
      at: iso(20),
      metrics: {
        upper: {
          ventilation_temperature_loss: 2.5,
          ventilation_absolute_humidity_loss: 0.25,
        },
        lower: {
          ventilation_temperature_loss: 3.1,
          ventilation_absolute_humidity_loss: 0.25,
        },
      },
      conditions: {},
      holds: {},
      signals: [],
    },
    {
      at: iso(25),
      metrics: {
        upper: {
          ventilation_temperature_loss: 3.5,
          ventilation_absolute_humidity_loss: 0.35,
        },
        lower: {
          ventilation_temperature_loss: 3.4,
          ventilation_absolute_humidity_loss: 0.35,
        },
      },
      conditions: {},
      holds: {},
      signals: [],
    },
  ];
  const archived = {
    ...session,
    configuration: {
      parameters: { ...parameters, vent_absolute_humidity_loss_percent: 30 },
    },
  };
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: archived,
      records: traces.map((payload) => ({ kind: "detector_trace", payload })),
    },
    state: {
      permissions: { admin: true },
      configuration: {
        parameters: { ...parameters, vent_absolute_humidity_loss_percent: 99 },
      },
    },
    window: [Date.parse(iso(0)), Date.parse(iso(60))],
    $: (selector) => (selector === "#detection-plots" ? plots : null),
  });
  p.drawDiagnostics();
  assert.match(
    plots.innerHTML.replace(/<[^>]*>/g, ""),
    /Lüftungserkennung · Temperaturverlust · °C/,
  );
  assert.match(
    plots.innerHTML.replace(/<[^>]*>/g, ""),
    /Lüftungserkennung · Absoluter Feuchteverlust · %/,
  );
  assert.match(
    plots.innerHTML,
    /data-series="detector_ventilation_absolute_humidity_loss_upper" d="M[\d.]+,66\.62 L[\d.]+,28\.78 "/,
  );
  assert.match(plots.innerHTML, /Schwelle oben: 30/);
  assert.doesNotMatch(plots.innerHTML, /Schwelle oben: 99/);
}

// Ventilation uses its serialized event kind as signal and the effective
// sample time, while a trace without the matching metric stays unmarked.
{
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: {
        ...session,
        timeline: {
          ...session.timeline,
          processed: [
            {
              event_id: "vent-ok",
              kind: "ventilation_confirmed",
              effective_at: iso(25),
              detected_at: iso(40),
            },
            {
              event_id: "vent-none",
              kind: "ventilation_confirmed",
              effective_at: iso(30),
              detected_at: iso(41),
            },
          ],
        },
      },
      records: [],
    },
  });
  const traces = [
    {
      at: iso(25),
      signals: ["ventilation_confirmed"],
      metrics: { upper: { ventilation_temperature_loss: 3.5 } },
    },
    { at: iso(30), signals: ["ventilation_confirmed"], metrics: { upper: {} } },
  ];
  assert.deepEqual(
    p
      .diagnosticMarkers(
        traces,
        "ventilation_temperature_loss",
        Date.parse(iso(0)),
        Date.parse(iso(60)),
      )
      .map((m) => m.event.event_id),
    ["vent-ok"],
  );
}

// The event-list lookup omits optional route arguments.  It must still reject
// a same-time trace for another signal or one without the primary metric.
{
  const p = Object.create(Panel.prototype);
  const event = {
    event_id: "door",
    kind: "door_open",
    effective_at: iso(10),
    detected_at: iso(20),
  };
  assert.equal(
    p.diagnosticTraceForEvent(
      [
        {
          at: iso(10),
          signals: ["infusion"],
          metrics: { upper: { door_temperature_slope: -2 } },
        },
      ],
      event,
    ),
    null,
  );
  assert.equal(
    p.diagnosticTraceForEvent(
      [{ at: iso(10), signals: ["door_open"], metrics: { upper: {} } }],
      event,
    ),
    null,
  );
  assert.equal(
    p.diagnosticTraceForEvent(
      [
        {
          at: iso(10),
          signals: ["door_open"],
          metrics: { upper: { door_temperature_slope: -2 } },
        },
      ],
      event,
    ).at,
    iso(10),
  );
}

// Old detector traces still show their available signals, without inventing a
// ventilation measurement or emitting NaN coordinates.
{
  const plots = {};
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session,
      records: [
        {
          kind: "detector_trace",
          payload: {
            at: iso(20),
            metrics: {
              upper: { door_temperature_slope: -2 },
              lower: { door_temperature_slope: -1 },
            },
            conditions: {},
            holds: {},
            signals: [],
          },
        },
      ],
    },
    state: { permissions: { admin: true }, configuration: { parameters } },
    window: [Date.parse(iso(0)), Date.parse(iso(60))],
    $: (selector) => (selector === "#detection-plots" ? plots : null),
  });
  p.drawDiagnostics();
  assert.doesNotMatch(plots.innerHTML, /Lüftungserkennung/);
  assert.doesNotMatch(plots.innerHTML, /NaN/);
}

// The strip contains only events in the current viewport.  Marker labels keep
// their index from the complete timeline, while nearby rendered positions use
// separate lanes even when their timestamps differ.
{
  const plots = {};
  const allEvents = [
    { event_id: "door-7", kind: "door_open", effective_at: iso(10) },
    { event_id: "infusion-8", kind: "infusion", effective_at: iso(11) },
    { event_id: "door-9", kind: "door_close", effective_at: iso(40) },
    { event_id: "future-10", kind: "infusion", effective_at: iso(120) },
  ];
  const p = Object.assign(Object.create(Panel.prototype), {
    shown: {
      session: { ...session, timeline: { ...session.timeline, processed: allEvents } },
      records: [],
    },
    state: { permissions: { admin: true }, configuration: { parameters } },
    window: [Date.parse(iso(0)), Date.parse(iso(60))],
    $: (selector) => (selector === "#detection-plots" ? plots : null),
  });
  p.drawDiagnostics();
  assert.equal(
    (plots.innerHTML.match(/class="event-marker diagnostic-marker"/g) || []).length,
    0,
  );
  assert.doesNotMatch(plots.innerHTML, /event-marker:door-9/);
  assert.doesNotMatch(plots.innerHTML, /future-10/);
  assert.doesNotMatch(plots.innerHTML, /event-strip/);

  p.window = [Date.parse(iso(20)), Date.parse(iso(60))];
  p.drawDiagnostics();
  assert.equal(
    (plots.innerHTML.match(/class="event-marker diagnostic-marker"/g) || []).length,
    0,
  );
}

// Marker -> row highlights the exact same event id, never a nearest timestamp.
{
  const rows = [
    {
      dataset: { eventId: "door-7", selected: "false" },
      classList: { contains: () => true },
      scrollIntoView: () => {
        rows[0].scrolled = true;
      },
    },
    {
      dataset: { eventId: "infusion-8", selected: "false" },
      classList: { contains: () => true },
      scrollIntoView: () => {
        rows[1].scrolled = true;
      },
    },
  ];
  const p = Object.assign(Object.create(Panel.prototype), {
    shadowRoot: { querySelectorAll: () => rows },
  });
  p.highlightEvent("infusion-8", true);
  assert.equal(rows[0].dataset.selected, "false");
  assert.equal(rows[1].dataset.selected, "true");
  assert.equal(rows[1].scrolled, true);
}

// Both click actions expose their target before focusing it; nonadmins cannot enter diagnostics.
{
  const calls = [];
  const p = Object.assign(Object.create(Panel.prototype), {
    state: { permissions: { admin: true } },
    message: () => {},
    setPanelView: (view) => calls.push(["view", view]),
    focusEvent: (id, reveal) => calls.push(["focus", id, !!reveal]),
  });
  p.action("event-marker:infusion-8");
  p.action("event-row:door-7");
  assert.deepEqual(calls, [
    ["view", "history"],
    ["focus", "infusion-8", true],
    ["view", "diagnostics"],
    ["focus", "door-7", false],
  ]);
  p.state.permissions.admin = false;
  p.action("event-row:door-7");
  assert.equal(calls.length, 4);
}

// The event list is a chronological sequence with stable ties. Grouping only
// supplies a minute heading; every source event keeps its own navigation row.
{
  const events = [
    { event_id: "late", kind: "infusion", effective_at: iso(60) },
    { event_id: "tie-a", kind: "infusion", effective_at: iso(10) },
    { event_id: "missing", kind: "door_open", effective_at: null },
    { kind: "door_close", effective_at: iso(5) },
    { event_id: "tie-b", kind: "infusion", effective_at: iso(10) },
    { event_id: "early", kind: "presence_confirmed", effective_at: iso(1) },
    { event_id: "invalid", kind: "<custom>", effective_at: "invalid" },
  ];
  const original = JSON.stringify(events);
  for (const admin of [false, true]) {
    const records = [];
    const nodes = new Map();
    const node = (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", hidden: false });
      return nodes.get(selector);
    };
    const p = Object.assign(Object.create(Panel.prototype), {
      entry: "entry",
      state: { now: iso(90), permissions: { admin }, configuration: { parameters } },
      shown: {
        records,
        session: {
          ended_at: iso(90),
          timeline: {
            session_id: "events",
            session_started_at: iso(0),
            completed: [],
            processed: events,
            retracted: [],
          },
        },
      },
      historyChart: { identity: "entry:events", render() {} },
      historyDetail: true,
      historyTimelineRevision: 1,
      window: [Date.parse(iso(0)), Date.parse(iso(90))],
      isConnected: true,
      view: "history",
      $: node,
      shadowRoot: { querySelectorAll: () => [] },
      updateHistoryTimelineRevision() {},
      diagnosticData: () => ({ traces: [] }),
      diagnosticTraceForEvent: (_traces, event) => event.event_id === "tie-a",
      diagnosticsRenderKey: () => "events",
      revealEventTarget() {},
    });
    p.historyIndex(records);
    p.renderHistory(new Set(["status"]));
    const html = node("#event-list").innerHTML;
    assert.deepEqual(
      [...html.matchAll(/class="event-row" data-event-id="([^"]+)"/g)].map(
        (match) => match[1],
      ),
      ["early", "legacy-3", "tie-a", "tie-b", "late", "missing", "invalid"],
    );
    assert.equal((html.match(/class="history-event-group"/g) || []).length, 3);
    assert.equal((html.match(/<span>Aufguss<\/span>/g) || []).length, 3);
    assert.match(html, /class="history-event-groups"/);
    assert.match(html, /class="history-event-time">Ohne Zeit/);
    assert.match(html, /&lt;custom&gt;/);
    assert.equal(html.includes('data-action="event-row:tie-a"'), admin);
    assert.equal(html.includes('data-action="event-row:tie-b"'), false);
    assert.deepEqual(
      p.historyEventsAt(Date.parse(iso(10)), 10000).map((event) => event.event_id),
      ["early", "legacy-3", "tie-a", "tie-b"],
    );
    assert.equal(
      JSON.stringify(events),
      original,
      "sort does not mutate source events",
    );
  }
}

console.log("panel detection navigation regressions passed");
