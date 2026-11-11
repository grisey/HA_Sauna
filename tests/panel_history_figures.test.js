"use strict";
// Verlauf nach Entwurf B (B3b): Unteransichten Sitzung und Anlage, Kennzahlen aus
// dem Endpunkt key-figures, glatte Achsen (V17), Ereignisbahnen (G18), Sollinie
// (V12), Auswahl und Programmart (K-S25). Das Panel rechnet keine Kennzahl (G19).
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
  JSON,
  Intl,
  Infinity,
  encodeURIComponent,
  decodeURIComponent,
  clearTimeout,
  setTimeout,
};
vm.runInNewContext(
  fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8"),
  sandbox,
);

const zone = "Europe/Berlin";
// Die Zone gilt bis zum Ende des (auch asynchronen) Rückrufs; die Runner laufen in UTC.
const inBerlin = async (callback) => {
  const previous = process.env.TZ;
  process.env.TZ = zone;
  try {
    return await callback();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
};
const shown = (value, display, unit) => ({ value, display, unit });
const time = (at) => shown(at, at, "time");
const at = (clock) => `2026-11-02T${clock}+01:00`;
const celsius = (value) => shown(value, value, "°C");
const minutes = (display) => shown(display * 60, display, "min");
const count = (value) => shown(value, value, "count");
// Werte wie im Brett „Verlauf · Sitzung/Anlage“ (Sitzung 02.11., G9b). Rohwerte
// absichtlich ungleich den Anzeigewerten: angezeigt wird nur "display".
const gang = (number, start, end, values) => ({
  number,
  start: time(at(start)),
  end: end ? time(at(end)) : time(null),
  duration_seconds: minutes(values.duration),
  entry_opening: time(at(values.entry)),
  exit_opening: time(values.exit ? at(values.exit) : null),
  entry_temperature_c: celsius(values.entryC),
  exit_temperature_c: celsius(values.exitC),
  highest_temperature_c: celsius(values.highest),
  infusions: values.infusions.map((clock) => time(at(clock))),
  infusion_count: count(values.infusions.length),
  humidity_rise_mean_gm3: shown(1, values.rise, "g/m³"),
  water_mean_l: shown(1, values.water, "l"),
  inner_door_openings: count(values.inner),
  highest_humidity_percent: shown(1, values.humidity, "%"),
  absolute_humidity_at_highest_gm3: shown(1, values.absolute, "g/m³"),
  target_c: celsius(values.target),
  target_deviation: { mean_c: celsius(values.deviation ?? null) },
});
const figures = (kind = "program") => {
  const manual = kind === "manual";
  return {
    session_id: "s1",
    started_at: "2026-11-02T16:13:38+00:00",
    program: manual
      ? { kind: "manual" }
      : { kind: "program", name: "Höhenwanderung", steps: [85, 88, 90, 93, 95] },
    duration_seconds: shown(18840, 314, "h:mm"),
    gangs: [
      gang(1, "19:19:49", "19:33:39", {
        duration: 13.8,
        entry: "19:18:55",
        exit: "19:32:57",
        entryC: 85.2,
        exitC: 80.2,
        highest: 81.6,
        infusions: ["19:27:14", "19:31:42", "19:32:11"],
        rise: 15.5,
        water: 0.09,
        inner: 2,
        humidity: 22.9,
        absolute: 69.9,
        target: manual ? null : 85,
        deviation: manual ? null : -4.4,
      }),
      gang(2, "20:19:37", "20:35:02", {
        duration: 15.4,
        entry: "20:19:00",
        exit: "20:34:10",
        entryC: 88.7,
        exitC: 84.5,
        highest: 85.9,
        infusions: ["20:29:05", "20:31:13", "20:32:40", "20:33:20"],
        rise: 24.7,
        water: 0.15,
        inner: 3,
        humidity: 23.1,
        absolute: 78.7,
        target: manual ? null : 88,
        deviation: manual ? null : -3.3,
      }),
      gang(3, "21:46:00", "22:00:42", {
        duration: 14.7,
        entry: "21:45:30",
        exit: "22:00:00",
        entryC: 88.9,
        exitC: 88.5,
        highest: 88.5,
        infusions: ["21:50:00", "21:52:00", "21:54:00", "21:56:00"],
        rise: 18.8,
        water: 0.11,
        inner: 0,
        humidity: 20,
        absolute: 70,
        target: manual ? null : 90,
        deviation: manual ? null : -3.7,
      }),
    ],
    gang_count: count(3),
    sauna_time_seconds: minutes(44.0),
    gang_duration_mean_seconds: minutes(14.7),
    infusions_in_gangs: count(11),
    infusions_total: count(11),
    maximum_temperature_c: celsius(92.6),
    maximum_temperature_at: time(at("21:06:27")),
    maximum_temperature_position: { relation: "after", gang: 2 },
    maximum_temperature_target_c: celsius(manual ? null : 88),
    rests_seconds: [minutes(46.0), minutes(71.0)],
    rest_mean_seconds: minutes(58.5),
    heating_started_at: time(at("17:13:38")),
    start_temperature_c: celsius(14.4),
    warmup_seconds: shown(6960, 116, "h:mm"),
    warmup_end_at: time(at("19:09:53")),
    warmup_end_kind: manual ? "gang" : "ready",
    warmup_end_temperature_c: celsius(85.0),
    warmup_end_target_c: celsius(manual ? null : 85),
    targets_c: manual ? [] : [celsius(85), celsius(88), celsius(90)],
    heating_time_seconds: shown(14160, 236, "h:mm"),
    heating_share: shown(0.75, 75, "share"),
    energy_kwh: shown(17.7, 17.7, "kWh"),
    energy_measured_kwh: shown(0, 0, "kWh"),
    energy_upper_bound: manual,
    nominal_power_kw: shown(4.5, 4.5, "kW"),
    cost_eur: shown(4.956, 4.95, "€"),
    energy_warmup_kwh: shown(8.7, 8.7, "kWh"),
    energy_after_warmup_kwh: shown(9.0, 9.0, "kWh"),
    heating_phases_after_warmup: count(6),
    heating_in_gangs: shown(1, 100, "share"),
    oven_cooling: [{ duration_seconds: minutes(10.9), gang: 1 }],
    control_in_ready: {
      window_seconds: minutes(49.8),
      mean_c: celsius(0.5),
      below: shown(0.09, 9, "share"),
      within: shown(0.53, 53, "share"),
      above: shown(0.38, 38, "share"),
      highest_c: celsius(2.3),
      highest_at: time(at("20:55:00")),
      lowest_c: celsius(-1.3),
      lowest_at: time(at("21:43:00")),
      hysteresis_c: celsius(1.0),
      offset_c: celsius(1.0),
    },
    outside_temperature_c: celsius(6.8),
    door_classes: {
      entry: {
        count: count(3),
        loss_mean_c: celsius(4.5),
        open_mean_seconds: minutes(0.9),
      },
      exit: {
        count: count(3),
        loss_mean_c: celsius(5.0),
        open_mean_seconds: minutes(2.0),
      },
      other: {
        count: count(14),
        loss_mean_c: celsius(1.0),
        open_mean_seconds: minutes(0.4),
      },
      final_ventilation: {
        count: count(1),
        loss_mean_c: celsius(12.9),
        open_mean_seconds: minutes(11.8),
      },
      all: {
        count: count(21),
        recovered_count: count(10),
        recovery_checked_count: count(20),
        recovered_mean_seconds: minutes(3.7),
      },
    },
    doors: [{ door_class: "entry" }, { door_class: null }],
    door_loss_entry_exit_mean_c: celsius(4.8),
    door_openings_warmup: count(5),
    rise_per_kwh: shown(8.1, 8.1, "°C/kWh"),
    // Nur frühere Sitzungen derselben Programmart (Vergleiche nach Programmart).
    earlier_warmup: {
      program_kind: manual ? "manual" : "program",
      median_seconds: shown(5340, 89, "h:mm"),
      kinds: ["gang"],
      all_manual: manual,
      sessions: 5,
    },
  };
};
const text = (html) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replaceAll(" ", " ")
    .replace(/\s+/g, " ");

const historyPanel = ({ admin = true, kind = "program", ended = true } = {}) => {
  const nodes = {};
  const node = (selector) => (nodes[selector] ||= { innerHTML: "", hidden: false });
  const calls = [];
  const gangs = [1, 2, 3].map((number) => ({
    gang_id: `g${number}`,
    recognition_kind: "presence_confirmed",
  }));
  const panel = Object.assign(fixturePanel(Panel), {
    entry: "entry",
    navigation: { main: "history", detail: "detail" },
    state: {
      now: at("22:30:00"),
      permissions: { admin },
      operation_enabled: !ended,
      session: ended ? null : { timeline: { session_id: "s1" } },
    },
    shown: {
      records: [],
      session: {
        ended_at: ended ? at("22:27:10") : null,
        target_history: [
          { at: at("17:13:38"), target_c: 85 },
          { at: at("20:10:00"), target_c: 88 },
        ],
        timeline: {
          session_id: "s1",
          session_started_at: at("17:13:38"),
          completed: gangs,
          active: null,
          processed: [],
          retracted: [],
        },
      },
      phase_projection: {
        intervals: [
          {
            phase: "saunagang",
            source_id: "g2",
            started_at: at("20:19:37"),
            ended_at: at("20:35:02"),
          },
        ],
      },
    },
    historyTimelineRevision: 1,
    message: () => {},
    $: node,
    scheduleHistoryRender: (reason) => calls.push(["render", reason]),
    api: async (path) => {
      calls.push(["api", path]);
      return {
        session_id: "s1",
        settings: { volume_m3: 6, electricity_price_eur_per_kwh: 0.28 },
        figures: figures(kind),
      };
    },
  });
  return { panel, node, calls };
};

test("V17: both axes share smooth gridlines; the worse used curve gets the most height", () => {
  // Datenspannen und Teilungszahl wie im Erzeuger des Entwurfs (charts.py, info.json).
  const panel = fixturePanel(Panel);
  const scale = (height, temperature, humidity) => {
    panel.historyGeometry = () => ({ top: 0, bottom: height, height });
    return panel.historyValueScale(
      ...temperature,
      ...(humidity || [null, null]),
      height,
      1,
    );
  };
  const session = scale(6 * 44, [14.527, 92.53], [5.148, 41.741]);
  assert.deepEqual(Array.from(session.temperature), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(Array.from(session.humidity), [0, 10, 20, 30, 40, 50]);
  assert.equal(session.temperatureDigits, 0);
  const gang = scale(4 * 44, [79.222, 89.618], [6.298, 22.303]);
  assert.deepEqual(Array.from(gang.temperature), [75, 80, 85, 90]);
  assert.deepEqual(Array.from(gang.humidity), [0, 10, 20, 30]);
  const touch = scale(3 * 44, [79.244, 89.605], [6.314, 21.969]);
  assert.deepEqual(Array.from(touch.temperature), [75, 80, 85, 90]);
  assert.deepEqual(Array.from(touch.humidity), [0, 10, 20, 30]);
  // Anlage: eine Achse, Soll eingeschlossen (bis 93 °C).
  const plant = scale(5 * 44, [14.527, 93], null);
  assert.deepEqual(Array.from(plant.temperature), [0, 20, 40, 60, 80, 100]);
  assert.equal(plant.humidity, null);
  // Nur 1, 2, 2,5 und 5 × 10ⁿ; 2,5 mit einer Nachkommastelle.
  for (const result of [session, gang, touch, plant])
    for (const values of [result.temperature, result.humidity].filter(Boolean)) {
      const step = values[1] - values[0],
        power = 10 ** Math.floor(Math.log10(step));
      assert.ok([1, 2, 2.5, 5].includes(Number((step / power).toPrecision(6))));
    }
  const quarter = scale(5 * 44, [80.1, 89.9], [10, 12]);
  assert.ok([...quarter.temperature].every((value) => Number.isInteger(value * 10)));
});

test("Sitzungsbild and Gänge im Vergleich show delivered values without doubling", async () => {
  await inBerlin(async () => {
    const { panel, node, calls } = historyPanel();
    await panel.loadHistoryKeyFigures();
    assert.deepEqual(calls[0], ["api", "/entry/key-figures?session_id=s1"]);
    panel.renderHistoryFigures();
    const cards = text(node("#history-figures").innerHTML);
    for (const expected of [
      "SAUNAZEIT|Saunazeit 44,0 min 3 Gänge, je 14,7 min im Mittel",
      "Aufgüsse 11 3, 4 und 4 in den Gängen 1 bis 3",
      "Höchsttemperatur 92,6 °C um 21:06, nach Gang 2, Soll 88 °C",
      "Ruhe zwischen den Gängen 58,5 min im Mittel 46,0 min nach Gang 1, 71,0 min nach Gang 2",
      "Sitzung 5:14 h 17:13 – 22:27, Bereit nach 1:56 h Programm Höhenwanderung",
    ])
      assert.match(cards, new RegExp(expected.split("|").at(-1)));
    // Gleich viele Aufgüsse in und außerhalb der Gänge: keine zweite Zahl.
    assert.doesNotMatch(cards, /ganzen Sitzung/);
    const gangs = text(node("#history-gangs").innerHTML);
    assert.match(
      gangs,
      /Gang 2 · 20:19 15,4 min Soll 88 °C Abweichung −3,3 °C im Mittel Eintritt 88,7 °C Austritt 84,5 °C Höchste 85,9 °C Je Aufguss \+24,7 g\/m³ Feuchte, etwa 0,15 l Wasser/,
    );
    assert.match(gangs, /Ruhe 46,0 min .*Ruhe 71,0 min/);
    assert.equal(
      (node("#history-gangs").innerHTML.match(/class="history-diamond"/g) || []).length,
      11,
    );
    // Gewählter Gang 2: Karte hervorgehoben; die Auswahl zeigt nur, was die Karte nicht hat.
    panel.historyValueSelection = {
      identity: panel.historyValueIdentity(),
      kind: "range",
      start: Date.parse(at("20:19:37")),
      end: Date.parse(at("20:35:02")),
      phaseKey: panel.historyPhaseKey(panel.shown.phase_projection.intervals[0]),
    };
    panel.renderHistoryFigures();
    assert.match(
      node("#history-gangs").innerHTML,
      /data-gang="2" data-selected="true"/,
    );
    assert.match(text(node("#history-gangs").innerHTML), /Gang 2 · 20:19 · gewählt/);
    const phase = panel.shown.phase_projection.intervals[0];
    const content = panel.historyInformationContent(panel.historyValueSelection, phase);
    assert.equal(
      panel.historyInformationHeading(
        panel.historyValueSelection,
        phase,
        zone,
        content,
      ),
      "Saunagang 2 · 20:19:37 – 20:35:02",
    );
    panel.syncHistoryInformation(panel.historyValueSelection, phase, false, content);
    const selection = text(node("[data-history-session-summary]").innerHTML);
    assert.match(selection, /Aufgüsse 20:29 · 20:31 · 20:32 · 20:33/);
    assert.match(selection, /Tür Eintritt 20:19, 3 × kurz offen, Austritt 20:34/);
    assert.match(selection, /Feuchte bis 23,1 % \(78,7 g\/m³\)/);
    assert.doesNotMatch(selection, /88,7|84,5|85,9|24,7/, "keine Werte der Gangkarte");
    // Punkt im Gang: Aufgüsse bis hier (K-Z3) aus der gelieferten Liste.
    const point = {
      kind: "point",
      start: Date.parse(at("20:31:53")),
      end: Date.parse(at("20:31:53")),
    };
    panel.syncHistoryInformation(
      point,
      phase,
      false,
      panel.historyInformationContent(point, phase),
    );
    assert.match(
      text(node("[data-history-session-summary]").innerHTML),
      /2 Aufgüsse bis hier · zuletzt 20:31:13/,
    );
    assert.equal(
      panel.historyInformationHeading(
        point,
        phase,
        zone,
        panel.historyInformationContent(point, phase),
      ),
      "20:31:53 · Saunagang 2",
    );
  });
});

test("Anlagenbild shows warmup, control in ready, energy and door loss from the endpoint", async () => {
  await inBerlin(async () => {
    const { panel, node } = historyPanel();
    panel.historySubview = "plant";
    await panel.loadHistoryKeyFigures();
    panel.renderHistoryFigures();
    const plant = text(node("#history-figures").innerHTML);
    for (const expected of [
      /Aufheizen 1:56 h bis Bereit 17:13 · Beginn 14,4 °C 19:09 · Bereit 85,0 °C, Soll 85 °C erreicht/,
      /Außentemperatur 6,8 °C Frühere Sitzungen 1:29 h bis zum ersten Gang \(Median aus 5 Sitzungen, Programm\)/,
      /Anstieg je kWh 8,1 °C Türöffnungen 5 während des Aufheizens/,
      /Regelung im Bereit 0,5 °C über Soll im Mittel/,
      /9 % darunter 53 % im Regelband 38 % darüber/,
      /Sollwerte 85, 88, 90 °C \(Höhenwanderung\) Regelband Soll ± 1 °C/,
      /Höchster Wert 2,3 °C über Soll, 20:55 Tiefster Wert 1,3 °C unter Soll, 21:43/,
      /Ausgewertet 49,8 min Bereit, ohne Gänge, Ofenkühlung und offene Tür/,
      /Heizen und Energie 17,7 kWh geschätzt Heizzeit 3:56 h, 75 % der Sitzung/,
      /Aufheizen 8,7 kWh in 1:56 h Danach 9,0 kWh, 6 weitere Heizphasen/,
      /In den Gängen Ofen durchgehend an Ofenkühlung einmal, 10,9 min nach Gang 1/,
      /Kosten 4,95 € bei 0,28 €\/kWh/,
      /Schätzung aus Heizzeit × 4,5 kW Nennleistung/,
      /Wärmeverlust durch die Tür 4,8 °C je Ein- oder Austritt im Mittel/,
      /Eintritt 3 4,5 °C 0,9 min Austritt 3 5,0 °C 2,0 min Sonstige 14 1,0 °C 0,4 min Schlusslüften 1 12,9 °C 11,8 min Ohne Art 1/,
      /Türvorgänge 21 in der ganzen Sitzung Wieder erreicht bei 10 von 20, nach 3,7 min im Mittel/,
    ])
      assert.match(plant, expected);
    assert.equal(
      node("#history-gangs").innerHTML,
      "",
      "Gänge nur in der Unteransicht Sitzung",
    );
    // Kein Entwurfshinweis im Panel.
    assert.doesNotMatch(plant, /Entwurf|Vorgabe|Brett/);
  });
});

test("program kind decides setpoint figures: manual sessions show none", async () => {
  await inBerlin(async () => {
    const { panel, node } = historyPanel({ kind: "manual" });
    await panel.loadHistoryKeyFigures();
    panel.renderHistoryFigures();
    const session = text(
      node("#history-figures").innerHTML + node("#history-gangs").innerHTML,
    );
    assert.doesNotMatch(session, /Soll|Abweichung/);
    assert.match(session, /um 21:06, nach Gang 2 /);
    assert.match(session, /erster Gang nach 1:56 h Handbetrieb/);
    panel.historySubview = "plant";
    panel.renderHistoryFigures();
    const plant = text(node("#history-figures").innerHTML);
    assert.doesNotMatch(plant, /Regelung im Bereit|Sollwerte|Soll /);
    assert.match(plant, /1:56 h bis zum ersten Gang/);
    assert.match(plant, /Erster Gang 85,0 °C/);
    assert.match(plant, /Im Handbetrieb eine obere Grenze/);
    assert.match(plant, /\(Median aus 5 Sitzungen, Handbetrieb\)/);
    // Keine frühere Sitzung derselben Programmart: die Zeile entfällt.
    panel.keyFigureData.result.figures.earlier_warmup = {
      program_kind: "manual",
      median_seconds: shown(null, null, "h:mm"),
      kinds: [],
      all_manual: null,
      sessions: 0,
    };
    panel.historyFiguresKey = null;
    panel.renderHistoryFigures();
    assert.doesNotMatch(text(node("#history-figures").innerHTML), /Frühere Sitzungen/);
  });
});

test("running and missing sessions load nothing and say why", async () => {
  await inBerlin(async () => {
    const running = historyPanel({ ended: false });
    await running.panel.loadHistoryKeyFigures();
    assert.deepEqual(running.calls, []);
    running.panel.renderHistoryFigures();
    assert.match(
      text(running.node("#history-figures").innerHTML),
      /Sitzung läuft seit 17:13 3 Gänge abgeschlossen Die Kennzahlen stehen hier nach dem Ende der Sitzung/,
    );
    const missing = historyPanel();
    missing.panel.api = async () => {
      throw { status_code: 404 };
    };
    await missing.panel.loadHistoryKeyFigures();
    missing.panel.renderHistoryFigures();
    assert.match(missing.node("#history-figures").innerHTML, /keine Kennzahlen vor/);
    const failing = historyPanel();
    failing.panel.api = async () => {
      throw { status_code: 503 };
    };
    await failing.panel.loadHistoryKeyFigures();
    failing.panel.renderHistoryFigures();
    assert.match(failing.node("#history-figures").innerHTML, /role="alert"/);
  });
});

test("subviews: plant shows temperature against a dashed setpoint, oven and door lanes", async () => {
  const { panel, node, calls } = historyPanel();
  const nodes = {};
  panel.$ = (selector) =>
    (nodes[selector] ||= {
      innerHTML: "",
      hidden: false,
      setAttribute() {},
      toggleAttribute() {},
    });
  const buttons = ["session", "plant"].map((name) => ({
    dataset: { action: `history-subview:${name}` },
    setAttribute(key, value) {
      this[key] = value;
    },
  }));
  panel.shadowRoot = {
    querySelector: () => null,
    querySelectorAll: (selector) =>
      selector === ".history-subviews [data-action]" ? buttons : [],
  };
  // Standard: Sitzung, nur Aufgüsse eingeblendet (V8).
  assert.equal(panel.historyPlantView(), false);
  assert.deepEqual(
    Array.from(panel.historyEventLanes(), (lane) => lane.kind),
    ["infusion"],
  );
  await panel.action("history-subview:plant");
  assert.equal(panel.historyPlantView(), true);
  assert.deepEqual(calls.at(-1), ["render", "subview"]);
  assert.equal(buttons[1]["aria-pressed"], "true");
  assert.equal(nodes["#history-gangs"].hidden, true);
  assert.equal(nodes["#history-figures"].hidden, false);
  assert.deepEqual(
    Array.from(panel.historyEventLanes(), (lane) => lane.kind),
    ["heater", "door"],
  );
  const start = Date.parse(at("17:13:38")),
    end = Date.parse(at("22:27:10"));
  assert.deepEqual(
    Array.from(panel.historyTargetSteps(start, end), (step) => step.value),
    [85, 88],
  );
  const model = {
    plant: true,
    start,
    end,
    left: 0,
    right: 100,
    top: 0,
    bottom: 100,
    valueScale: { intervals: 5 },
    x: (time) => ((time - start) / (end - start)) * 100,
    yT: (value) => 100 - value,
  };
  const annotations = panel.historyAnnotations(model, panel.shown.session, []);
  assert.match(
    annotations,
    /<polyline class="history-target-line" points="0\.00,15\.00 56\.25,15\.00 56\.25,12\.00 100\.00,12\.00"/,
  );
  assert.match(
    panel.historyChartHeadMarkup(model),
    /Temperatur.*Soll.*Ofen heizt.*Tür offen/,
  );
  assert.doesNotMatch(panel.historyChartHeadMarkup(model), /data-action/);
  await panel.action("history-subview:session");
  const session = panel.historyChartHeadMarkup({
    plant: false,
    humidityMode: "relative",
  });
  assert.match(session, /data-action="history-humidity" aria-pressed="false"/);
  assert.match(
    session,
    /data-action="history-event-category:infusion" aria-pressed="true"/,
  );
  assert.match(
    session,
    /data-action="history-event-category:door" aria-pressed="false"/,
  );
  // Details zeigen weder Unteransichten noch Kennzahlen (B4 unverändert).
  panel.navigation = { main: "details", detail: "detail-history" };
  panel.syncNavigation();
  for (const selector of [
    ".history-subviews",
    "#history-figures",
    "#history-chart-head",
    "#history-gangs",
  ])
    assert.equal(nodes[selector].hidden, true, selector);
  assert.equal(panel.historyPlantView(), false);
});

test("events are lanes with catalogue symbols; the navigation target is the lane event", () => {
  const { panel } = historyPanel();
  const infusion = { event_id: "i1", kind: "infusion", effective_at: at("20:29:05") };
  const open = { event_id: "d1", kind: "door_open", effective_at: at("20:19:00") };
  const close = { event_id: "d2", kind: "door_close", effective_at: at("20:19:30") };
  panel.shown.session.timeline.processed = [open, close, infusion];
  panel.shown.session.door_cycles = [
    {
      id: "c1",
      event_ids: ["d1", "d2"],
      started_at: open.effective_at,
      ended_at: close.effective_at,
      role: "entry",
    },
  ];
  panel.historyEventVisibility.set("door", true);
  const start = Date.parse(at("20:00:00")),
    end = Date.parse(at("21:00:00")),
    model = {
      start,
      end,
      left: 0,
      right: 100,
      width: 100,
      x: (time) => ((time - start) / (end - start)) * 100,
    };
  panel.historyStripEventGroups = () =>
    panel.historyEventGroups().map((group) => ({ ...group, at: group.start }));
  const lanes = panel.historyStripEvents(model);
  assert.match(lanes, /data-history-lane="infusion"[\s\S]*data-history-lane="door"/);
  assert.match(lanes, /data-history-event-kind="infusion" [^>]*data-point="true"/);
  assert.match(
    lanes,
    /data-history-event-kind="door" [^>]*data-point="false"[^>]*aria-label="Eintritt/,
  );
  assert.equal(panel.historyEventMarkers(model), "", "keine zweite Linie je Ereignis");
  // „Ereignis im Verlauf“ (aus der Erkennungskontrolle) wählt das Ereignis im Band.
  const inspected = [];
  panel.inspectHistoryEvent = (id) => inspected.push(id);
  panel.highlightEvent = () => {};
  const target = {
    dataset: { historyEventMembers: encodeURIComponent(JSON.stringify(["i1"])) },
    scrollIntoView() {},
    focus() {},
  };
  panel.shadowRoot = { querySelectorAll: () => [target] };
  panel.view = "history";
  panel.pendingEventFocus = { kind: "row", eventId: "i1", sessionId: "s1" };
  panel.revealEventTarget("row");
  assert.deepEqual(inspected, ["i1"]);
  assert.equal(panel.pendingEventFocus, null);
});

test("a running session names the selected gang's confirmation and documented start", async () => {
  await inBerlin(() => {
    const { panel, node } = historyPanel({ ended: false });
    const session = panel.shown.session;
    session.timeline.completed = [];
    session.timeline.active = { gang_id: "g2", started_at: at("20:19:37") };
    session.gang_intervals = [
      { gang_id: "g2", started_at: at("20:19:37"), ended_at: null, complete: false },
    ];
    const phase = panel.shown.phase_projection.intervals[0],
      selection = {
        identity: panel.historyValueIdentity(),
        kind: "range",
        start: Date.parse(at("20:19:37")),
        end: Date.parse(at("20:35:02")),
        phaseKey: panel.historyPhaseKey(phase),
      },
      content = panel.historyInformationContent(selection, phase);
    assert.equal(
      panel.historyInformationHeading(selection, phase, zone, content),
      "Saunagang · 20:19:37 – Ende nicht belegt",
    );
    panel.syncHistoryInformation(selection, phase, false, content);
    const html = node("[data-history-session-summary]").innerHTML;
    assert.match(html, /data-history-selected-gang data-gang-id="g2"/);
    assert.match(text(html), /Vorläufig · Beginn 20:19/);
    assert.match(html, /<time datetime="2026-11-02T20:19:37\+01:00"/);
  });
});
