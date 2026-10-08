#!/usr/bin/env node
/*
 * Real-browser interaction benchmark for the live history view.
 *
 * The fixture is deliberately the existing synthetic transport only: all
 * rendering, hit-testing, browser input and refresh paths are product code.
 * Run from the HA_Sauna repository, after making the candidate available in
 * custom_components/ha_sauna/panel.js.  See the protocol printed by --help.
 */
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { webkit, chromium } = require("playwright");

const root = path.resolve(process.env.HA_SAUNA_ROOT || process.cwd());
const baselineRevision =
  process.env.BASELINE_REVISION || "7de9317f39f5fc39f58c4f4618f16f0ceff5d35e";
const engine = process.env.ENGINE || "webkit";
const steps = Number(process.env.INPUT_STEPS || 24);
const tickEvery = Number(process.env.TICK_EVERY || 4);
const variants = (process.env.VARIANTS || "baseline,candidate")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);
const cases = (process.env.BOTH_CASES || "both")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean)
  .map((value) => {
    if (value === "single") return { name: "single-height", both: false };
    if (value === "both") return { name: "both-heights", both: true };
    throw Error(`BOTH_CASES accepts single,both; got ${value}`);
  });
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

Defaults compare git show ${baselineRevision} with the current working-tree
panel.js, in a 57,600-record four-series both-heights fixture. Set
BOTH_CASES=single,both only when the extra single-height matrix is material.
INPUT_STEPS must be 20..40 (default 24).  It sends real
browser pointer moves, Ctrl-wheel zoom in/out and minimap drag moves, while
calling fixture.tick() after every ${tickEvery} inputs. Results are JSON at
INTERACTION_EVIDENCE (or ${output}).
`);
  process.exit(0);
}
if (!Number.isInteger(steps) || steps < 20 || steps > 40)
  throw Error(`INPUT_STEPS must be an integer from 20 to 40; got ${steps}`);
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

function panelSource(variant) {
  if (variant === "candidate")
    return fs.readFileSync(
      path.join(root, "custom_components/ha_sauna/panel.js"),
      "utf8",
    );
  if (variant === "baseline")
    return execFileSync(
      "git",
      ["show", `${baselineRevision}:custom_components/ha_sauna/panel.js`],
      { cwd: root, encoding: "utf8" },
    );
  throw Error(`VARIANTS accepts baseline,candidate; got ${variant}`);
}

async function addFixture(page, source, both) {
  await page.setContent(
    '<!doctype html><html lang="de"><body style="margin:0"></body></html>',
  );
  await page.addScriptTag({ content: source });
  await page.addScriptTag({
    path: path.join(root, "tests/browser/live_history/fixture.js"),
  });
  await page.evaluate(async (bothValue) => {
    window.f = await window.installHistoryFixture({ seconds: 14400, both: bothValue });
    window.f.assertClean();
    if (window.f.records.length !== 57600)
      throw Error(`fixture expected 57600 records, got ${window.f.records.length}`);
  }, both);
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
      overview: panel.$("#history-overview svg"),
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
      const inMinimap = contains("#history-overview svg");
      if (!inChart && !inMinimap) return;
      // Moving into the minimap before pointerdown has no product gesture and
      // consequently no render to time.  Measure the captured drag moves.
      if (inMinimap && !panel.historyGesture) return;
      bench.events.push({
        id: ++bench.nextEventId,
        type: event.type,
        stream: inMinimap ? "minimap" : event.type === "wheel" ? "wheel" : "pointer",
        at: performance.now(),
      });
    };
    root.addEventListener("pointermove", record, true);
    root.addEventListener("wheel", record, true);
    bench.latestId = () => bench.nextEventId;
    bench.waitForRender = (afterId, type, timeoutMs = 1500) =>
      new Promise((resolve, reject) => {
        const deadline = performance.now() + timeoutMs;
        const check = () => {
          const event = bench.events.find(
            (item) => item.id > afterId && item.type === type,
          );
          if (event?.completed != null) return resolve({ ...event });
          if (performance.now() >= deadline)
            return reject(Error(`no completed render after ${type} input ${afterId}`));
          setTimeout(check, 4);
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
      const overview = panel.$("#history-overview svg");
      const point = panel.lastHistoryPointer;
      let expectedOriginal = null;
      if (point && surface) {
        const coordinate = panel.svgCoordinates(surface, point.clientX, point.clientY);
        const model = panel.historyChart?.model;
        const time =
          model?.start +
          ((coordinate.x - model.left) / (model.right - model.left)) *
            (model.end - model.start);
        const nearest = panel.nearestMeasurement?.("upper", "temperature", time);
        if (nearest?.source?.raw_value != null)
          expectedOriginal = `Originalwert ${nearest.source.raw_value} °C`;
      }
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
          text: tooltip?.textContent || "",
          expectedOriginal,
          originalCorrect:
            !!expectedOriginal && tooltip.textContent.includes(expectedOriginal),
        },
        persistentNodes: {
          fixtureIdentity: f.identity(),
          chart: bench.initialNodes.chart === panel.historyChart,
          tooltip: bench.initialNodes.tooltip === tooltip,
          surface: bench.initialNodes.surface === surface,
          canvas: bench.initialNodes.canvas === canvas,
          overview: bench.initialNodes.overview === overview,
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
  // Its in/out imbalance leaves a narrowed window for the minimap pan.
  const zoomInSteps = Math.ceil((steps * 2) / 3);
  for (let index = 0; index < steps; index++) {
    await doWheel(
      index < zoomInSteps ? -35 : 25,
      index < zoomInSteps ? "wheel-in" : "wheel-out",
    );
    interleaveTick(index);
  }
  const windowRect = page
    .locator("ha-sauna-panel")
    .locator("#history-overview [data-history-window]");
  const minimapBox = await windowRect.boundingBox();
  if (!minimapBox) throw Error("history minimap window has no bounding box");
  const startX = minimapBox.x + minimapBox.width / 2;
  const startY = minimapBox.y + minimapBox.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (let index = 0; index < steps; index++) {
    const before = await latestInputId(page);
    await page.mouse.move(
      startX + 42 * Math.sin((Math.PI * index) / (steps - 1)),
      startY,
    );
    const event = await waitForInputRender(page, before, "pointermove");
    inputLatency.push({ ...event, stream: "minimap" });
    interleaveTick(index);
  }
  await page.mouse.up();
  const ticks = await Promise.all(pendingTicks);
  // Re-enter the real chart after releasing the minimap capture. This gives
  // the endpoint assertion a sampled chart position rather than the pointerout
  // state that deliberately hides the tooltip during minimap interactions.
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
    ["pointer", "wheel-in", "wheel-out", "minimap"].map((stream) => [
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
      ["pointer", "wheel-in", "wheel-out", "minimap"].map((stream) => [
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

async function runCase(browser, variant, variantSource, testCase) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
    deviceScaleFactor: 2,
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  try {
    await addFixture(page, variantSource, testCase.both);
    await installInstrumentation(page);
    const input = await runInputSequence(page);
    const raw = await page.evaluate(() => window.__historyInteractionBench.summary());
    if (raw.records !== 57600 + input.ticks.length * 8)
      throw Error(
        `expected 8 retained measurement records per live tick; got ${raw.records}`,
      );
    if (!raw.tooltip.originalCorrect)
      throw Error(`tooltip lost original value: ${raw.tooltip.text}`);
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
      variant,
      case: testCase.name,
      both: testCase.both,
      errors,
      ...summarizeCase(raw, input),
    };
  } finally {
    await page.close();
  }
}

(async () => {
  const launchOptions = {
    headless: true,
    ...(engine === "chromium"
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  };
  const browser = await (engine === "webkit" ? webkit : chromium).launch(launchOptions);
  try {
    const results = [];
    for (const variant of variants) {
      const source = panelSource(variant);
      for (const testCase of cases) {
        const result = await runCase(browser, variant, source, testCase);
        results.push(result);
        console.log(
          JSON.stringify({
            variant: result.variant,
            case: result.case,
            records: result.records,
            inputLatency: result.inputLatency,
            tickDuration: result.tickDuration,
            renderDuration: result.renderDuration,
            rafGaps: result.rafGaps,
            tooltip: result.tooltip.originalCorrect,
          }),
        );
      }
    }
    const report = {
      scope: {
        engine,
        browserVersion: browser.version(),
        baselineRevision,
        candidate: path.join(root, "custom_components/ha_sauna/panel.js"),
        fixture: path.join(root, "tests/browser/live_history/fixture.js"),
        recordsPerInitialCase: 57600,
        inputStepsPerStream: steps,
        tickEvery,
        notes: [
          "Fixture transport is synthetic; panel rendering, input, refresh and canvas paths are product code.",
          "Pointer and minimap inputs use Playwright's native mouse. WebKit automation drops Control from mouse.wheel, so the Ctrl-wheel phase uses bubbling, cancelable DOM WheelEvents with ctrlKey true on the product SVG; no handlers or paints are called directly.",
          "Latency is delivered DOM input to completion of the next actual panel.renderHistory callback; it does not include device or browser event-queue time before DOM delivery.",
          "The 40 ms synthetic archive-response delay permits DOM inputs to overlap live refreshes; it is not a Companion App measurement.",
          "methods are synchronous wall-clock durations of observed product method calls; fixtureMethods are the fixture's existing method observers.",
          "The end timestamp is JS callback completion, not GPU/compositor presentation. This is an automated headless comparison, not validation in the user's Companion App.",
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
