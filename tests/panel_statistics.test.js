"use strict";
// Statistik (B3a): Zeitraum, Laden aus dem Endpunkt statistics und Anzeige der
// gelieferten Anzeigewerte. Das Panel rechnet keine Kennzahl (G19).
const { panelFixture: fixturePanel } = require("./catalog_fixture.cjs");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

let Panel;
const sandbox = {
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
  Intl,
  Infinity,
  clearTimeout,
  setTimeout,
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"),
  sandbox,
);

const shown = (value, display, unit, extra = {}) => ({
  value,
  display,
  unit,
  ...extra,
});
const time = (stamp) => shown(stamp, stamp, "time");
// Gestalt wie GET …/statistics; Anzeigewerte absichtlich ungleich den Rohwerten,
// damit jede angezeigte Zahl erkennbar aus "display" stammt.
const statistics = (groups = null) => ({
  period: {},
  periods: [
    { summer: 2026, label: "Sommer 2026" },
    {
      season: 2026,
      label: "2026/27",
      parts: [
        { part: "autumn", label: "Herbst" },
        { part: "winter", label: "Winter" },
      ],
    },
  ],
  session_ids: ["a", "b"],
  not_evaluated: ["c"],
  settings: { volume_m3: 6, electricity_price_eur_per_kwh: 0.28 },
  figures: {
    sessions: shown(2, 2, "count", { sessions: 2 }),
    operating_time_seconds: shown(36000.4, 600, "h:mm", { sessions: 2 }),
    sauna_time_seconds: shown(1800.2, 31, "h:mm", { sessions: 1 }),
    gangs: shown(3, 3, "count", { sessions: 2 }),
    gangs_per_session: shown(1.5, 1.5, "mean", { sessions: 2 }),
    infusions: shown(7, 7, "count", { sessions: 2 }),
    infusions_per_gang: shown(2.333, 2.3, "mean", { sessions: 2 }),
    infusions_most: shown(4, 4, "count", {
      sessions: 2,
      where: [
        { session_id: "a", gang: 1 },
        { session_id: "a", gang: 2 },
      ],
    }),
    gang_duration: {
      mean_seconds: shown(900.04, 15.1, "min"),
      range_seconds: [shown(600, 10.0, "min"), shown(1200, 20.0, "min")],
    },
    gang_infusions: {
      mean: shown(2.333, 2.3, "mean"),
      range: [shown(1, 1, "count"), shown(4, 4, "count")],
    },
    humidity_rise: {
      mean_gm3: shown(null, null, "g/m³"),
      water_mean_l: shown(null, null, "l"),
    },
    rest: {
      mean_seconds: shown(1200, 20.4, "min"),
      range_seconds: [shown(1200, 20.4, "min"), shown(1200, 20.4, "min")],
      longest: shown(1200, 20.4, "min", { session_id: "b" }),
    },
    habits: {
      sessions: [
        { session_id: "a", weekday: 6, local_start: "17:42:00", gang_count: 2 },
        { session_id: "b", weekday: 1, local_start: "18:00:00", gang_count: 1 },
      ],
      weekday_sessions: [1, 0, 0, 0, 0, 1, 0].map((count) =>
        shown(count, count, "count"),
      ),
      start_quartiles_seconds: [
        shown(63479, 1058, "clock"),
        shown(66001, 1100, "clock"),
      ],
      most_frequent_gang_count: [1, 2],
      most_frequent_gang_count_sessions: shown(1, 1, "count"),
    },
    records: {
      longest_gang: shown(1200, 20.0, "min", { session_id: "a", gang: 2 }),
      hottest_gang: shown(91.04, 91.0, "°C", { session_id: "b", gang: 1 }),
      most_infusions: shown(4, 4, "count", { session_id: "a", gang: 1 }),
      longest_session: shown(20000, 333, "h:mm", { session_id: "a" }),
      coldest_outside: null,
      coldest_outside_sessions: 0,
    },
    energy_kwh: shown(30.04, 30.1, "kWh", { sessions: 2 }),
    cost_eur: shown(8.4, 8.41, "€", { sessions: 2 }),
    energy_per_sauna_hour_kwh: shown(60, 60.2, "kWh/h", { sessions: 1 }),
    heating_share: shown(0.755, 76, "share", { sessions: 2 }),
    by_program: {
      manual: {
        sessions: 1,
        warmup: {
          median_seconds: shown(3600, 61, "h:mm"),
          range_seconds: [shown(3600, 61, "h:mm"), shown(3600, 61, "h:mm")],
          sessions: 1,
        },
        per_session: [],
      },
      constant: {
        sessions: 1,
        warmup: {
          median_seconds: shown(7000, 117, "h:mm"),
          range_seconds: [shown(7000, 117, "h:mm"), shown(7000, 117, "h:mm")],
          sessions: 1,
        },
        per_session: [],
      },
    },
    warmup: {
      median_seconds: shown(5300, 89, "h:mm"),
      range_seconds: [shown(3600, 61, "h:mm"), shown(7000, 117, "h:mm")],
      sessions: 2,
      per_session: [
        {
          session_id: "a",
          started_at: "2026-10-17T15:42:00+00:00",
          program_kind: "manual",
          heating_started_at: time("2026-10-17T15:42:00+00:00"),
          end_at: time("2026-10-17T16:42:00+00:00"),
          kind: "gang",
          seconds: shown(3600, 61, "h:mm"),
          start_temperature_c: shown(12, 12.0, "°C"),
          end_temperature_c: shown(61.44, 61.5, "°C"),
          end_target_c: shown(null, null, "°C"),
          curve: {
            display: [
              [0, 12.0],
              [30, 40.0],
              [60, 61.5],
            ],
            unit: "curve",
          },
        },
        {
          session_id: "b",
          started_at: "2026-11-02T17:00:00+00:00",
          program_kind: "constant",
          heating_started_at: time("2026-11-02T17:00:00+00:00"),
          end_at: time("2026-11-02T18:56:40+00:00"),
          kind: "ready",
          seconds: shown(7000, 117, "h:mm"),
          start_temperature_c: shown(14, 14.0, "°C"),
          end_temperature_c: shown(85, 85.0, "°C"),
          end_target_c: shown(85, 85.0, "°C"),
          curve: {
            display: [
              [0, 14.0],
              [116.7, 85.0],
            ],
            unit: "curve",
          },
        },
      ],
    },
    door_classes: {
      entry: {
        count: shown(2, 2, "count"),
        loss_mean_c: shown(6, 6.1, "°C"),
        loss_max_c: shown(7, 7.0, "°C"),
        open_mean_seconds: shown(60, 1.0, "min"),
      },
      exit: {
        count: shown(2, 2, "count"),
        loss_mean_c: shown(5, 5.2, "°C"),
        loss_max_c: shown(6, 6.0, "°C"),
        open_mean_seconds: shown(90, 1.5, "min"),
      },
      final_ventilation: {
        count: shown(0, 0, "count"),
        loss_mean_c: shown(null, null, "°C"),
        loss_max_c: shown(null, null, "°C"),
        open_mean_seconds: shown(null, null, "min"),
      },
      other: {
        count: shown(1, 1, "count"),
        loss_mean_c: shown(2, 2.3, "°C"),
        loss_max_c: shown(2, 2.0, "°C"),
        open_mean_seconds: shown(30, 0.5, "min"),
      },
      all: {
        count: shown(5, 5, "count"),
        loss_mean_c: shown(4.8, 4.9, "°C"),
        loss_max_c: shown(7, 7.0, "°C"),
        open_mean_seconds: shown(66, 1.1, "min"),
      },
      sessions: 2,
    },
    door_entry_exit: {
      count: shown(4, 4, "count"),
      loss_mean_c: shown(5.55, 5.6, "°C"),
      open_mean_seconds: shown(75, 1.3, "min"),
      largest: {
        loss_c: shown(7, 7.0, "°C"),
        door_class: "exit",
        session_id: "a",
        started_at: "2026-10-17T15:42:00+00:00",
        opened: time("2026-10-17T18:00:00+00:00"),
        open_seconds: shown(294, 4.9, "min"),
      },
      sessions: 2,
    },
    groups,
    per_session: [
      {
        session_id: "a",
        started_at: "2026-10-17T15:42:00+00:00",
        program_kind: "manual",
        duration_seconds: shown(14400, 240, "h:mm"),
        gang_count: shown(2, 2, "count"),
        gangs: [
          {
            number: 1,
            start: time("2026-10-17T16:42:00+00:00"),
            end: time("2026-10-17T16:52:00+00:00"),
            duration_seconds: shown(600, 10.0, "min"),
            infusion_count: shown(4, 4, "count"),
          },
          {
            number: 2,
            start: time("2026-10-17T17:42:00+00:00"),
            end: time(null),
            duration_seconds: shown(null, null, "min"),
            infusion_count: shown(2, 2, "count"),
          },
        ],
        sauna_time_seconds: shown(null, null, "min"),
        infusions_in_gangs: shown(6, 6, "count"),
        energy_kwh: shown(12, 12.1, "kWh"),
        heating_share: shown(0.9, 91, "share"),
        door_loss_entry_exit_mean_c: shown(5, 5.1, "°C"),
      },
      {
        session_id: "b",
        started_at: "2026-11-02T17:00:00+00:00",
        program_kind: "constant",
        duration_seconds: shown(21600, 360, "h:mm"),
        gang_count: shown(1, 1, "count"),
        gangs: [
          {
            number: 1,
            start: time("2026-11-02T19:00:00+00:00"),
            end: time("2026-11-02T19:30:00+00:00"),
            duration_seconds: shown(1800, 30.0, "min"),
            infusion_count: shown(1, 1, "count"),
          },
        ],
        sauna_time_seconds: shown(1800, 30.1, "min"),
        infusions_in_gangs: shown(1, 1, "count"),
        energy_kwh: shown(18, 18.2, "kWh"),
        heating_share: shown(0.6, 61, "share"),
        door_loss_entry_exit_mean_c: shown(4, 4.4, "°C"),
      },
    ],
  },
});

const statisticsPanel = ({ admin = true } = {}) => {
  const nodes = { "#statistics": { innerHTML: "" } };
  const calls = [];
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry",
    archiveRevision: 1,
    state: { permissions: { admin } },
    message: () => {},
    $: (selector) => nodes[selector] || null,
    api: async (path) => {
      calls.push(path);
      return statistics();
    },
  });
  return { panel, nodes, calls };
};

test("statistics view follows the main navigation for administrators and users", async () => {
  for (const admin of [true, false]) {
    const nodes = Object.fromEntries(
      [
        "#current",
        "#details",
        "#history",
        "#statistics",
        "#settings",
        ".detail-tabs",
      ].map((key) => [key, { hidden: false }]),
    );
    const buttons = ["overview", "history", "statistics", "details", "settings"].map(
      (action) => ({
        dataset: { action },
        setAttribute(name, value) {
          this[name] = value;
        },
      }),
    );
    nodes['.main-tabs [data-action="details"]'] = buttons[3];
    const calls = [];
    const panel = Object.assign(fixturePanel(Panel), {
      entry: "entry",
      archiveRevision: 3,
      state: { permissions: { admin } },
      message: () => {},
      $: (selector) => nodes[selector] || null,
      shadowRoot: {
        querySelectorAll: (selector) =>
          selector === ".main-tabs button" ? buttons : [],
      },
      api: async (path) => {
        calls.push(path);
        return statistics();
      },
      cancelHistoryFrame: () => {},
    });
    await panel.action("statistics");
    assert.equal(panel.view, "statistics");
    assert.equal(nodes["#statistics"].hidden, false);
    for (const other of ["#current", "#details", "#history", "#settings"])
      assert.equal(nodes[other].hidden, true, `${other} hidden for admin=${admin}`);
    assert.deepEqual(
      buttons
        .filter((button) => button["aria-current"] === "page")
        .map((b) => b.dataset.action),
      ["statistics"],
    );
    assert.equal(buttons[3].hidden, !admin, "Details stays admin-only");
    assert.deepEqual(calls, ["/entry/statistics"]);
    assert.match(nodes["#statistics"].innerHTML, /Saunagänge[\s\S]*Anlage/);
  }
});

test("period choice offers seasons, their parts and summers and reuses a loaded period", async () => {
  const { panel, nodes, calls } = statisticsPanel();
  await panel.loadStatistics();
  let html = nodes["#statistics"].innerHTML;
  const choice = (action, label, pressed) =>
    new RegExp(
      `data-action="statistics-period:${action}" aria-pressed="${pressed}">${label}<`,
    );
  assert.match(html, choice("all", "Gesamt", true));
  assert.match(html, choice("summer:2026", "Sommer 2026", false));
  assert.match(html, choice("season:2026", "2026/27", false));
  assert.doesNotMatch(html, /Herbst/, "season parts appear with their season");
  await panel.action("statistics-period:season:2026");
  html = nodes["#statistics"].innerHTML;
  assert.match(html, choice("season:2026", "2026/27", true));
  assert.match(html, choice("season:2026:autumn", "Herbst", false));
  assert.match(html, choice("season:2026:winter", "Winter", false));
  await panel.action("statistics-period:season:2026:winter");
  assert.match(
    nodes["#statistics"].innerHTML,
    choice("season:2026:winter", "Winter", true),
  );
  await panel.action("statistics-period:summer:2026");
  assert.deepEqual(calls, [
    "/entry/statistics",
    "/entry/statistics?season=2026",
    "/entry/statistics?season=2026&part=winter",
    "/entry/statistics?summer=2026",
  ]);
  await panel.loadStatistics({ redraw: false });
  assert.equal(calls.length, 4, "status polling keeps the loaded period");
  panel.archiveRevision = 2;
  await panel.loadStatistics({ redraw: false });
  assert.deepEqual(calls.at(-1), "/entry/statistics?summer=2026");
  assert.equal(calls.length, 5, "a changed archive reloads the shown period");
  panel.api = async () => {
    throw Object.assign(Error("Response error: 503"), { status_code: 503 });
  };
  await panel.action("statistics-period:all");
  assert.match(nodes["#statistics"].innerHTML, /role="alert"[\s\S]*neu geladen/);
});

test("lists are grouped by season and summer or by season part as delivered", async () => {
  const group = (label, extra, sessions, sauna, energy, loss) => ({
    ...extra,
    label,
    sessions: shown(sessions, sessions, "count"),
    sauna_time_seconds: shown(sauna * 60, sauna, "h:mm", { sessions: 1 }),
    gangs: shown(3, 3, "count"),
    infusions: shown(9, 9, "count"),
    energy_kwh: shown(energy, energy, "kWh", { sessions }),
    heating_share: shown(0.8, 80, "share"),
    door_loss_entry_exit_mean_c: shown(loss, loss, "°C"),
  });
  const { panel, nodes } = statisticsPanel();
  panel.api = async () =>
    statistics([
      group("Sommer 2026", { summer: 2026 }, 1, 40, 12.5, 6.2),
      group("2026/27", { season: 2026 }, 2, 80, 30.1, 4.4),
    ]);
  await panel.loadStatistics();
  let html = nodes["#statistics"].innerHTML;
  assert.match(html, /<h3>Saisons im Vergleich<\/h3>/);
  assert.match(
    html,
    /<span class="stat-session-label">Sommer 2026<\/span><div class="stat-track"[^>]*><span class="stat-group-bar" style="width:50%"><\/span><span class="stat-group-text">1 Sitzung · 3 Gänge<\/span><\/div><span class="stat-session-value">0:40\u00a0h · 9<\/span>/,
  );
  assert.match(html, /title="Saunazeit aus 1 von 2 Sitzungen">1:20\u00a0h · 9</);
  assert.doesNotMatch(html, /Sitzungen im Vergleich|class="stat-gang/);
  assert.match(
    html,
    /<h3>Energie je Saison<\/h3>[\s\S]*<span class="muted">2026\/27<\/span>/,
  );
  assert.match(
    html,
    /Mittlerer Verlust je Ein- oder Austritt und Saison: Sommer 2026 6,2\u00a0°C; 2026\/27 4,4\u00a0°C/,
  );
  panel.archiveRevision = 9;
  panel.api = async () =>
    statistics([group("Herbst", { season: 2026, part: "autumn" }, 2, 80, 30.1, 4.4)]);
  await panel.loadStatistics();
  html = nodes["#statistics"].innerHTML;
  assert.match(html, /<h3>Jahreszeiten im Vergleich<\/h3>/);
  assert.match(html, /<h3>Energie je Jahreszeit<\/h3>/);
});

test("a late answer for another instance or period is dropped", async () => {
  const { panel, nodes } = statisticsPanel();
  let release;
  panel.api = () => new Promise((resolve) => (release = () => resolve(statistics())));
  const pending = panel.loadStatistics();
  panel.entry = "other";
  release();
  await pending;
  assert.equal(panel.statisticsData, undefined);
  assert.doesNotMatch(nodes["#statistics"].innerHTML, /Saunagänge/);
});

test("statistics shows delivered display values only, missing values as missing", async () => {
  const { panel, nodes } = statisticsPanel({ admin: false });
  await panel.loadStatistics();
  const html = nodes["#statistics"].innerHTML;
  // Summen: Anzeigewerte, h:mm, Mittel mit fester Stelle, Bezug „aus n von m“.
  assert.match(
    html,
    /<p class="stat-key">Sitzungen<\/p><p class="stat-value">2<\/p><p class="stat-line">10:00\u00a0h in Betrieb/,
  );
  assert.match(
    html,
    /Saunazeit<\/p><p class="stat-value">0:31<small>h<\/small><\/p><p class="stat-line">Summe aller Gänge<\/p><p class="stat-line"><span class="stat-basis">aus 1 von 2 Sitzungen/,
  );
  assert.match(html, /Ø 1,5 je Sitzung/);
  assert.match(html, /Ø 2,3 je Gang, höchstens 4/);
  assert.match(html, /1 Sitzung ohne Auswertung/);
  // Sitzungen im Vergleich: Gang ohne belegtes Ende bleibt offen, Saunazeit fehlt.
  assert.match(
    html,
    /class="stat-gang open"[^>]*title="Gang 2: Ende nicht belegt, 2 Aufgüsse">2</,
  );
  assert.match(html, /<span class="stat-session-value">–\u0020·\u00206<\/span>/);
  assert.match(html, /<span class="stat-session-value">30,1\u00a0min · 1<\/span>/);
  // Typischer Gang: fehlender Feuchteanstieg als fehlend, ohne Wassermenge.
  assert.match(
    html,
    /Feuchte je Aufguss<\/p><p class="stat-value stat-value-small">–<\/p><p class="stat-line">mittlerer Anstieg der absoluten Feuchte<\/p>/,
  );
  // Ruhe mit längster Pause und ihrer Sitzung.
  assert.match(html, /20,4 bis 20,4\u00a0min, längste am Mo 02\.11\.</);
  // Gewohnheiten: Sitzungen je Wochentag und Beginn aus dem Endpunkt, je Sitzung ein Punkt.
  assert.match(
    html,
    /aria-label="Sitzungen je Wochentag: Mo 1, Di 0, Mi 0, Do 0, Fr 0, Sa 1, So 0"/,
  );
  assert.equal(
    html.match(/<i style="height:100%"><\/i><span class="muted">(Mo|Sa)</g).length,
    2,
  );
  assert.equal(html.match(/class="stat-habit-dot"/g).length, 2);
  assert.match(
    html,
    /Meist 1 oder 2 Gänge \(1 von 2 Sitzungen\), Beginn meist zwischen 17:38 und 18:20\u00a0Uhr\./,
  );
  // Rekorde mit Bezug; ohne Außentemperatur fehlt der Kälterekord.
  assert.match(
    html,
    /Meiste Aufgüsse in einem Gang<\/dt><dd><strong>4<\/strong><span>Sa 17\.10\., Gang 1 und 2</,
  );
  assert.match(
    html,
    /Kältester Saunatag<\/dt><dd><strong>–<\/strong><span>keine Sitzung mit Außentemperatur/,
  );
  // Anlage: Kosten mit Strompreis, Heizanteil ganze Prozent.
  assert.match(html, /8,41\u00a0€ bei 0,28 €\/kWh/);
  assert.match(html, /Heizanteil<\/p><p class="stat-value">76<small>%<\/small>/);
  // Türverlust nach Art, Schlusslüften ohne Vorgang mit fehlenden Werten.
  assert.match(
    html,
    /<span>Eintritt<\/span><span>2<\/span><span>6,1\u00a0°C<\/span><span>1,0\u00a0min<\/span>/,
  );
  assert.match(
    html,
    /<span>Schlusslüften<\/span><span>0<\/span><span>–<\/span><span>–<\/span>/,
  );
  assert.match(html, /5,6<small>°C je Ein- oder Austritt im Mittel/);
  assert.match(
    html,
    /Größter Verlust<\/dt><dd><span>7,0\u00a0°C beim Austritt am Sa 17\.10\., Tür 4,9\u00a0min offen<\/span>/,
  );
  assert.match(html, /<h3>Sitzungen im Vergleich<\/h3>/);
  assert.match(html, /<h3>Energie je Sitzung<\/h3>/);
  assert.doesNotMatch(html, /Entwurf|Brett|Entwurfsnotiz/);
});

test("warmup comparisons are separated by program kind with the endpoint curve", async () => {
  const { panel, nodes } = statisticsPanel();
  await panel.loadStatistics();
  let html = nodes["#statistics"].innerHTML;
  // Karte: Median aller Sitzungen, darunter je Programmart.
  assert.match(
    html,
    /Aufheizen<\/p><p class="stat-value">1:29<small>h Median<\/small><\/p><p class="stat-line">1:01 bis 1:57\u00a0h bis Bereit oder zum ersten Gang<\/p><p class="stat-line">Handbetrieb – 1:01\u00a0h<\/p><p class="stat-line">Konstant – 1:57\u00a0h<\/p>/,
  );
  // Bild: Programmart der jüngsten Sitzung vorgewählt, nur deren Kurven.
  assert.match(
    html,
    /data-action="statistics-kind:manual" aria-pressed="false">Handbetrieb \(1\)/,
  );
  assert.match(
    html,
    /data-action="statistics-kind:constant" aria-pressed="true">Konstant \(1\)/,
  );
  assert.equal(html.match(/<polyline class="stat-curve latest"/g).length, 1);
  assert.equal(html.match(/<polyline/g).length, 1);
  assert.match(html, /Konstant – Median 1:57\u00a0h, 1:57 bis 1:57\u00a0h, 1 Sitzung/);
  assert.match(html, /Mo 02\.11\. \d\d:\d\d · Bereit nach 1:57\u00a0h, 85,0\u00a0°C/);
  await panel.action("statistics-kind:manual");
  html = nodes["#statistics"].innerHTML;
  assert.equal(html.match(/<polyline class="stat-curve"/g).length, 1);
  assert.match(html, /Sa 17\.10\. \d\d:\d\d · Gang nach 1:01\u00a0h, 61,5\u00a0°C/);
  // Glatte Temperaturachse und Uhrteilung der Zeitachse.
  assert.match(
    html,
    /<div class="stat-y-axis" aria-hidden="true">(<span style="top:[\d.]+%">\d+(?: °C)?<\/span>)+<\/div>/,
  );
  assert.match(html, /<span[^>]*>1:00 h<\/span>/);
});

test("smooth axes use 1, 2, 2.5 and 5 steps and give the curve the most height", () => {
  const { smoothAxis, clockAxis, clockLabel } = vm.runInNewContext(
    `${fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8")};({ smoothAxis, clockAxis, clockLabel })`,
    { ...sandbox, customElements: { get: () => undefined, define: () => {} } },
  );
  assert.deepEqual(smoothAxis(9.4, 91.4, 2, 5).values, [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(smoothAxis(78, 90, 2, 5).values, [77.5, 80, 82.5, 85, 87.5, 90]);
  assert.deepEqual(
    smoothAxis(0.3, 2.2, 2, 8).values,
    [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25],
  );
  assert.deepEqual(
    smoothAxis(0.3, 2.2, 2, 2).values,
    [0, 2, 4],
    "next decade when flooring needs it",
  );
  for (const [low, high] of [
    [9.4, 91.4],
    [78, 90],
    [0.3, 2.2],
    [13, 93],
  ]) {
    const axis = smoothAxis(low, high, 2, 8),
      step = axis.values[1] - axis.values[0],
      mantissa = step / 10 ** Math.floor(Math.log10(step));
    assert.ok(
      [1, 2, 2.5, 5].some((value) => Math.abs(value - mantissa) < 1e-9),
      `${step}`,
    );
    assert.ok(axis.first <= low && axis.last >= high);
  }
  assert.deepEqual(
    clockAxis(144 * 60, 7).values.map((value) => clockLabel(value, 1800)),
    ["0:00 h", "0:30 h", "1:00 h", "1:30 h", "2:00 h", "2:30 h"],
  );
  assert.deepEqual(
    clockAxis(5.2 * 3600, 7).values.map((value) => clockLabel(value, 3600)),
    ["0", "1 h", "2 h", "3 h", "4 h", "5 h", "6 h"],
  );
});
