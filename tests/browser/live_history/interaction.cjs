#!/usr/bin/env node
const { settings } = require("../../settings_fixture.cjs");
const { catalog: fixtureCatalog } = require("../../catalog_fixture.cjs");
/*
 * Real-browser interaction benchmark for the live history view.
 *
 * The fixture is deliberately the existing synthetic transport only: all
 * rendering, hit-testing, browser input and refresh paths are product code.
 * Run from the HA_Sauna repository against its current panel.js.
 * See the protocol printed by --help.
 */
const fs = require("node:fs");
const path = require("node:path");
const { webkit, chromium } = require("playwright");

const root = path.resolve(process.env.HA_SAUNA_ROOT || process.cwd());
const defaults = structuredClone(fixtureCatalog);
const engine = process.env.ENGINE || settings.browser.engine;
const steps = Number(
  process.env.INPUT_STEPS || settings.fixtures.history.interaction_steps,
);
const tickEvery = Number(
  process.env.TICK_EVERY || settings.fixtures.history.tick_every,
);
const output = path.resolve(
  process.env.INTERACTION_EVIDENCE ||
    path.join(require("node:os").tmpdir(), "ha-sauna-smooth", "interaction.json"),
);

if (process.argv.includes("--help")) {
  console.log(`
Real-browser live-history interaction benchmark

  HA_SAUNA_ROOT=$PWD \\
  NODE_PATH=/path/to/node_modules \\
  PLAYWRIGHT_BROWSERS_PATH=/path/to/browsers \\
  ENGINE=webkit node ${path.basename(process.argv[1])}

Measures the current working-tree panel.js with the declared history dataset
in tests/settings.json; the history view selects its leading position.
INPUT_STEPS must be a positive integer (default ${settings.fixtures.history.interaction_steps}). It sends real
browser pointer moves, Ctrl-wheel zoom in/out and chart drag moves, while
calling fixture.tick() after every ${tickEvery} inputs. Results are JSON at
INTERACTION_EVIDENCE (or ${output}).
`);
  process.exit(0);
}
if (!Number.isInteger(steps) || steps < 1)
  throw Error(`INPUT_STEPS must be a positive integer; got ${steps}`);
if (!Number.isInteger(tickEvery) || tickEvery < 1)
  throw Error(`TICK_EVERY must be a positive integer; got ${tickEvery}`);
if (!["webkit", "chromium"].includes(engine))
  throw Error(`ENGINE must be webkit or chromium; got ${engine}`);
for (const required of [
  path.join(root, "custom_components/ha_sauna/panel.js"),
  path.join(root, "tests/browser/live_history/fixture.js"),
])
  if (!fs.existsSync(required))
    throw Error(`Missing product benchmark input: ${required}`);

const numericSummary = (numbers) => {
  const values = numbers.filter(Number.isFinite).sort((a, b) => a - b);
  const percentile = (fraction) =>
    values.length
      ? values[Math.min(values.length - 1, Math.ceil(values.length * fraction) - 1)]
      : null;
  const sum = values.reduce((total, value) => total + value, 0);
  return {
    count: values.length,
    minMs: values.length ? values[0] : null,
    medianMs: percentile(0.5),
    p95Ms: percentile(0.95),
    maxMs: values.length ? values.at(-1) : null,
    meanMs: values.length ? sum / values.length : null,
  };
};

async function addFixture(page) {
  await page.setContent(
    '<!doctype html><html lang="de"><body style="margin:0"></body></html>',
  );
  await page.addScriptTag({
    path: path.join(root, "custom_components/ha_sauna/panel.js"),
  });
  await page.evaluate((value) => {
    window.testSettings = value;
  }, settings);
  await page.addScriptTag({
    path: path.join(root, "tests/browser/live_history/fixture.js"),
  });
  await page.evaluate(async (defaults) => {
    window.f = await window.installHistoryFixture({ defaults });
    window.f.assertClean();
    if (window.f.records.length !== f.initialRecordCount)
      throw Error(
        `fixture expected ${f.initialRecordCount} records, got ${window.f.records.length}`,
      );
  }, defaults);
}

// This instrumentation only observes calls and DOM events. It never invokes a
// panel handler, draw function, or paint function itself.
async function installInstrumentation(page) {
  await page.evaluate(() => {
    const bench = (window.__historyInteractionBench = {
      calls: {},
      events: [],
      renders: [],
      rafGaps: [],
      ticks: [],
      nextEventId: 0,
      previousFrame: null,
      monitor: null,
    });
    const append = (key, duration) => {
      const call = (bench.calls[key] ||= { count: 0, durations: [] });
      call.count++;
      call.durations.push(duration);
    };
    const wrap = (owner, name, label, after) => {
      if (!owner || typeof owner[name] !== "function") return;
      const original = owner[name];
      if (original.__interactionBenchWrapped) return;
      function wrapped(...args) {
        const began = performance.now();
        try {
          const result = original.apply(this, args);
          if (result && typeof result.then === "function") {
            return result.finally(() => {
              const completed = performance.now();
              append(label, completed - began);
              after?.(completed, args, completed - began);
            });
          }
          const completed = performance.now();
          append(label, completed - began);
          after?.(completed, args, completed - began);
          return result;
        } catch (error) {
          const completed = performance.now();
          append(label, completed - began);
          after?.(completed, args, completed - began);
          throw error;
        }
      }
      wrapped.__interactionBenchWrapped = true;
      owner[name] = wrapped;
    };
    const completeEvents = (completed, args, durationMs) => {
      const reasons = args?.[0] instanceof Set ? [...args[0]] : [];
      const render = { completed, reasons, durationMs };
      bench.renders.push(render);
      for (const event of bench.events)
        if (event.completed == null && event.at <= completed) {
          event.completed = completed;
          event.latencyMs = completed - event.at;
          event.reasons = render.reasons;
          event.renderDurationMs = durationMs;
        }
    };
    const panel = window.f.panel;
    window.f.reset();
    bench.initialNodes = {
      chart: panel.historyChart,
      tooltip: panel.$("#tooltip"),
      surface: panel.$("svg.session-chart"),
      canvas: panel.$("canvas.history-curves"),
      timeAxis: panel.$("[data-history-time-axis]"),
    };
    wrap(panel, "renderHistory", "panel.renderHistory", completeEvents);
    for (const name of [
      "historyIndex",
      "historyDisplayValues",
      "historyModel",
      "drawHistory",
      "hoverChart",
      "setHistoryWindow",
      "wheelHistoryGesture",
      "updateHistoryGesture",
    ])
      wrap(panel, name, `panel.${name}`);
    const chart = panel.historyChart;
    wrap(chart, "render", "chart.render");
    wrap(chart?.curves, "update", "curves.update");
    wrap(chart?.curves, "buildMainPath", "curves.buildMainPath");
    wrap(chart?.curves, "paintMain", "curves.paintMain");
    wrap(chart?.interaction, "hover", "interaction.hover");
    const root = panel.shadowRoot;
    const record = (event) => {
      if (event.type === "wheel" && !(event.ctrlKey || event.metaKey)) return;
      const nodes = [event.target, ...(event.composedPath?.() || [])];
      const contains = (selector) =>
        nodes.some((node) => node?.matches?.(selector) || node?.closest?.(selector));
      const inChart = contains("svg.session-chart");
      if (!inChart) return;
      const panning = event.type === "pointermove" && !!(event.buttons & 1);
      bench.events.push({
        id: ++bench.nextEventId,
        type: event.type,
        stream: panning ? "pan" : event.type === "wheel" ? "wheel" : "pointer",
        at: performance.now(),
      });
    };
    root.addEventListener("pointermove", record, true);
    root.addEventListener("wheel", record, true);
    bench.latestId = () => bench.nextEventId;
    bench.waitForRender = (
      afterId,
      type,
      timeoutMs = testSettings.browser.timeouts_ms.render,
    ) =>
      new Promise((resolve, reject) => {
        const deadline = performance.now() + timeoutMs;
        const check = () => {
          const event = bench.events.find(
            (item) => item.id > afterId && item.type === type,
          );
          if (event?.completed != null) return resolve({ ...event });
          if (performance.now() >= deadline)
            return reject(Error(`no completed render after ${type} input ${afterId}`));
          setTimeout(check, testSettings.browser.render_poll_ms);
        };
        check();
      });
    bench.tick = async () => {
      const started = performance.now();
      const renderCount = bench.renders.length;
      await window.f.tick();
      const completed = performance.now();
      const tick = {
        durationMs: completed - started,
        renderCompletions: bench.renders
          .slice(renderCount)
          .map((render) => completed - render.completed),
      };
      bench.ticks.push(tick);
      return tick;
    };
    const frame = (now) => {
      if (bench.previousFrame != null) bench.rafGaps.push(now - bench.previousFrame);
      bench.previousFrame = now;
      bench.monitor = requestAnimationFrame(frame);
    };
    bench.monitor = requestAnimationFrame(frame);
    bench.stop = () => {
      cancelAnimationFrame(bench.monitor);
      bench.monitor = null;
    };
    bench.summary = () => {
      bench.stop();
      const f = window.f;
      const panel = f.panel;
      const tooltip = panel.$("#tooltip");
      const surface = panel.$("svg.session-chart");
      const canvas = panel.$("canvas.history-curves");
      const timeAxis = panel.$("[data-history-time-axis]");
      // The fixture's upper temperature is always 69..75 °C and humidity
      // 25..31 %. This oracle does not call the product's lookup/formatter.
      const values = [...tooltip.querySelectorAll(".history-tooltip-values")]
        .filter((container) => !container.hidden)
        .flatMap((container) => [...container.children].filter((row) => !row.hidden))
        .flatMap((row) => [...row.querySelectorAll(".history-tooltip-value")].filter((cell) => !cell.hidden))
        .map((cell) => `${cell.querySelector(".history-tooltip-number").textContent} ${cell.querySelector(".history-tooltip-unit").textContent}`);
      const valueIn = (unit, minimum, maximum) =>
        values.some((text) => {
          const match = text.match(/^([0-9]+(?:[.,][0-9]+)?)\s*(°C|%)$/);
          const value = match ? Number(match[1].replace(",", ".")) : NaN;
          return match?.[2] === unit && value >= minimum && value <= maximum;
        });
      return {
        calls: bench.calls,
        events: bench.events,
        renders: bench.renders,
        rafGaps: bench.rafGaps,
        ticks: bench.ticks,
        fixtureCounts: { ...f.counts },
        fixtureMeasurements: structuredClone(f.measurements),
        records: panel.shown?.records?.length,
        zoom: panel.zoom,
        tooltip: {
          visible: !!tooltip && !tooltip.hidden,
          selection: tooltip.getAttribute("data-selection"),
          text: tooltip?.textContent || "",
          values,
          valuesCorrect:
            values.length === 2 && valueIn("°C", 69, 75) && valueIn("%", 25, 31),
        },
        persistentNodes: {
          fixtureIdentity: f.identity(),
          chart: bench.initialNodes.chart === panel.historyChart,
          tooltip: bench.initialNodes.tooltip === tooltip,
          surface: bench.initialNodes.surface === surface,
          canvas: bench.initialNodes.canvas === canvas,
          timeAxis: !!timeAxis && bench.initialNodes.timeAxis === timeAxis,
        },
      };
    };
  });
}

async function waitForInputRender(page, beforeId, type) {
  return page.evaluate(
    ({ before, eventType }) =>
      window.__historyInteractionBench.waitForRender(before, eventType),
    { before: beforeId, eventType: type },
  );
}

async function latestInputId(page) {
  return page.evaluate(() => window.__historyInteractionBench.latestId());
}

async function tick(page) {
  return page.evaluate(() => window.__historyInteractionBench.tick());
}

async function runInputSequence(page) {
  const surface = page.locator("ha-sauna-panel").locator("svg.session-chart");
  const surfaceBox = await surface.boundingBox();
  if (!surfaceBox) throw Error("history surface has no bounding box");
  // Delay belongs only to the fixture's synthetic archive transport. It leaves
  // actual panel refresh, rendering and input code untouched, while ensuring
  // users' inputs overlap a live-response interval.
  await page.evaluate(() => window.f.setDelay(40));
  const inputLatency = [];
  const doPointerMove = async (x, y, stream) => {
    const before = await latestInputId(page);
    await page.mouse.move(x, y);
    const event = await waitForInputRender(page, before, "pointermove");
    inputLatency.push({ ...event, stream });
  };
  const doWheel = async (deltaY, stream) => {
    const before = await latestInputId(page);
    // WebKit's automation backend does not retain keyboard Control on its
    // mouse.wheel protocol command. Dispatch the same bubbling, cancelable
    // browser WheelEvent that the product's shadow-root listener receives;
    // do not invoke that listener or any render method directly.
    await page.evaluate((delta) => {
      const svg = window.f.panel.$("svg.session-chart");
      const rect = svg.getBoundingClientRect();
      svg.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          composed: true,
          cancelable: true,
          ctrlKey: true,
          deltaY: delta,
          clientX: rect.left + rect.width / 2,
          clientY: rect.top + rect.height / 2,
        }),
      );
    }, deltaY);
    const event = await waitForInputRender(page, before, "wheel");
    inputLatency.push({ ...event, stream });
  };
  const pendingTicks = [];
  const interleaveTick = (index) => {
    if ((index + 1) % tickEvery === 0) {
      const pending = tick(page);
      // Keep the pending promise observed now, then await all live updates at
      // the end of the stream. Browser input remains live during this delay.
      pendingTicks.push(pending);
    }
  };

  // A pointer stream deliberately moves across a broad interior line so each
  // native event targets the real SVG rather than any control chrome.
  for (let index = 0; index < steps; index++) {
    const fraction = 0.12 + (0.76 * index) / (steps - 1);
    await doPointerMove(
      surfaceBox.x + surfaceBox.width * fraction,
      surfaceBox.y + surfaceBox.height * (0.36 + 0.08 * (index % 3)),
      "pointer",
    );
    interleaveTick(index);
  }

  // One 24-step zoom phase contains an actual zoom-in and zoom-out portion.
  // Its in/out imbalance leaves a narrowed window for the chart pan.
  const zoomInSteps = Math.ceil((steps * 2) / 3);
  for (let index = 0; index < steps; index++) {
    await doWheel(
      index < zoomInSteps ? -35 : 25,
      index < zoomInSteps ? "wheel-in" : "wheel-out",
    );
    interleaveTick(index);
  }
  const panBox = await surface.boundingBox();
  if (!panBox) throw Error("history chart has no bounding box");
  const startX = panBox.x + panBox.width / 2;
  const startY = panBox.y + panBox.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (let index = 0; index < steps; index++) {
    const before = await latestInputId(page);
    await page.mouse.move(
      startX + 42 * Math.sin((Math.PI * index) / (steps - 1)),
      startY,
    );
    const event = await waitForInputRender(page, before, "pointermove");
    inputLatency.push({ ...event, stream: "pan" });
    interleaveTick(index);
  }
  await page.mouse.up();
  const ticks = await Promise.all(pendingTicks);
  // Re-enter the real chart after releasing the chart capture. This gives
  // the endpoint assertion a sampled chart position rather than the pointerout
  // state that restores the session summary during chart-pan interactions.
  const finalBox = await surface.boundingBox();
  if (!finalBox) throw Error("history surface lost its bounding box");
  const finalBefore = await latestInputId(page);
  await page.mouse.move(
    finalBox.x + finalBox.width * 0.51,
    finalBox.y + finalBox.height * 0.42,
  );
  await waitForInputRender(page, finalBefore, "pointermove");
  await page.evaluate(() => window.f.settle());
  return { inputLatency, ticks };
}

function summarizeCase(raw, input) {
  const calls = Object.fromEntries(
    Object.entries(raw.calls).map(([name, call]) => [
      name,
      { count: call.count, duration: numericSummary(call.durations) },
    ]),
  );
  const fixtureMethods = Object.fromEntries(
    Object.entries(raw.fixtureMeasurements).map(([name, values]) => [
      name,
      {
        count: raw.fixtureCounts[name] || values.length,
        duration: numericSummary(values),
      },
    ]),
  );
  const latencyByStream = Object.fromEntries(
    ["pointer", "wheel-in", "wheel-out", "pan"].map((stream) => [
      stream,
      numericSummary(
        input.inputLatency
          .filter((event) => event.stream === stream)
          .map((event) => event.latencyMs),
      ),
    ]),
  );
  const raf = numericSummary(raw.rafGaps);
  return {
    records: raw.records,
    zoom: raw.zoom,
    inputLatency: latencyByStream,
    renderDurationByStream: Object.fromEntries(
      ["pointer", "wheel-in", "wheel-out", "pan"].map((stream) => [
        stream,
        numericSummary(
          input.inputLatency
            .filter((event) => event.stream === stream)
            .map((event) => event.renderDurationMs),
        ),
      ]),
    ),
    tickDuration: numericSummary(input.ticks.map((item) => item.durationMs)),
    renderDuration: calls["panel.renderHistory"]?.duration || numericSummary([]),
    methods: calls,
    fixtureMethods,
    rafGaps: {
      ...raf,
      over33ms: raw.rafGaps.filter((gap) => gap > 33.4).length,
      over50ms: raw.rafGaps.filter((gap) => gap > 50).length,
    },
    tooltip: raw.tooltip,
    persistentNodes: raw.persistentNodes,
    eventCount: raw.events.length,
    renderCount: raw.renders.length,
    raw: {
      inputs: input.inputLatency,
      tickCompletions: raw.ticks,
      methods: raw.calls,
      renders: raw.renders,
      rafGaps: raw.rafGaps,
    },
  };
}

async function runCase(browser) {
  const page = await browser.newPage({
    viewport: settings.browser.viewports.history,
    deviceScaleFactor: settings.browser.device_scale_factor,
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  try {
    await addFixture(page);
    await installInstrumentation(page);
    const input = await runInputSequence(page);
    const raw = await page.evaluate(() => window.__historyInteractionBench.summary());
    if (
      raw.records !==
      settings.fixtures.history.duration_seconds *
        settings.fixtures.history.positions.length *
        settings.fixtures.history.quantities.length +
        input.ticks.length *
          settings.fixtures.history.samples_per_tick *
          settings.fixtures.history.positions.length *
          settings.fixtures.history.quantities.length
    )
      throw Error(
        `expected 8 retained measurement records per live tick; got ${raw.records}`,
      );
    if (!raw.tooltip.visible || raw.tooltip.selection !== "" || !raw.tooltip.valuesCorrect)
      throw Error(`tooltip values outside the synthetic series: ${raw.tooltip.text}`);
    if (!Object.values(raw.persistentNodes.fixtureIdentity).every(Boolean))
      throw Error(
        `history nodes were replaced: ${JSON.stringify(raw.persistentNodes.fixtureIdentity)}`,
      );
    if (
      !Object.values(raw.persistentNodes).every(
        (value) => typeof value === "object" || value === true,
      )
    )
      throw Error(
        `persistent node check failed: ${JSON.stringify(raw.persistentNodes)}`,
      );
    if (errors.length) throw Error(`page errors: ${errors.join(" | ")}`);
    return {
      case: "live-history",
      errors,
      ...summarizeCase(raw, input),
    };
  } finally {
    await page.close();
  }
}

(async () => {
  const launchOptions = {
    headless: settings.browser.headless,
    ...(engine === "chromium"
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  };
  const browser = await (engine === "webkit" ? webkit : chromium).launch(launchOptions);
  try {
    const result = await runCase(browser);
    const results = [result];
    console.log(
      JSON.stringify({
        case: result.case,
        records: result.records,
        inputLatency: result.inputLatency,
        tickDuration: result.tickDuration,
        renderDuration: result.renderDuration,
        rafGaps: result.rafGaps,
        tooltip: result.tooltip.valuesCorrect,
      }),
    );
    const report = {
      scope: {
        engine,
        browserVersion: browser.version(),
        panel: path.join(root, "custom_components/ha_sauna/panel.js"),
        fixture: path.join(root, "tests/browser/live_history/fixture.js"),
        recordsPerInitialCase:
          settings.fixtures.history.duration_seconds *
          settings.fixtures.history.positions.length *
          settings.fixtures.history.quantities.length,
        inputStepsPerStream: steps,
        tickEvery,
        notes: [
          "Fixture transport is synthetic; panel rendering, input, refresh and canvas paths are product code.",
          "Pointer and chart-pan inputs use Playwright's native mouse. WebKit automation drops Control from mouse.wheel, so the Ctrl-wheel phase uses bubbling, cancelable DOM WheelEvents with ctrlKey true on the product SVG; no handlers or paints are called directly.",
          "Latency is delivered DOM input to completion of the next actual panel.renderHistory callback; it does not include device or browser event-queue time before DOM delivery.",
          "The 40 ms synthetic archive-response delay permits DOM inputs to overlap live refreshes; it is not a Companion App measurement.",
          "methods are synchronous wall-clock durations of observed product method calls; fixtureMethods are the fixture's existing method observers.",
          "The end timestamp is JS callback completion, not GPU/compositor presentation. This is an automated headless measurement, not validation in the user's Companion App.",
        ],
      },
      results,
    };
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2));
    console.log(`wrote ${output}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
