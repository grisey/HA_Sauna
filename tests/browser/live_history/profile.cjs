const fs = require("node:fs"),
  assert = require("node:assert/strict");
const { webkit, chromium } = require("playwright");
const root = process.cwd(),
  output =
    process.env.HISTORY_EVIDENCE ||
    require("node:path").join(require("node:os").tmpdir(), "ha-sauna-live-history");
const { execFileSync } = require("node:child_process");
let runningBrowser;
fs.mkdirSync(output, { recursive: true });
(async () => {
  const engine = process.env.ENGINE || "webkit";
  const b = await (engine === "webkit" ? webkit : chromium).launch({
    headless: true,
    ...(engine === "chromium"
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  });
  runningBrowser = b;
  const results = [];
  for (const variant of (process.env.VARIANTS || "main,aa,fix").split(","))
    for (const both of [false, true]) {
      const page = await b.newPage({
        viewport: { width: 1440, height: 1100 },
        deviceScaleFactor: 2,
      });
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.setContent(
        '<!doctype html><html lang="de"><body style="margin:0"></body></html>',
      );
      await page.addScriptTag({
        content:
          variant === "fix"
            ? fs.readFileSync(`${root}/custom_components/ha_sauna/panel.js`, "utf8")
            : execFileSync(
                "git",
                [
                  "show",
                  `${variant === "main" ? "becfdb5464e1219e42f75f03f9c5feea723c0d61" : "aa1681de874e9dcd1d27802a152418a6213e9b93"}:custom_components/ha_sauna/panel.js`,
                ],
                { cwd: root, encoding: "utf8" },
              ),
      });
      await page.addScriptTag({
        path: `${root}/tests/browser/live_history/fixture.js`,
      });
      const result = await page.evaluate(async (both) => {
        window.f = await installHistoryFixture({ both });
        f.assertClean();
        const cold = f.coldMs;
        f.setDelay(100);
        f.reset();
        const ticks = [],
          frameIntervals = [],
          hoverDurations = [];
        let previous, monitor;
        const watch = (now) => {
          if (previous) frameIntervals.push(now - previous);
          previous = now;
          monitor = requestAnimationFrame(watch);
        };
        monitor = requestAnimationFrame(watch);
        const inputRect = f.panel.$("svg.session-chart").getBoundingClientRect();
        let ctmCalls = 0,
          rectCalls = 0;
        const svg = f.panel.$("svg.session-chart");
        for (const name of ["getScreenCTM", "getBoundingClientRect"]) {
          const old = svg[name];
          svg[name] = function (...args) {
            if (name === "getScreenCTM") ctmCalls++;
            else rectCalls++;
            return old.apply(this, args);
          };
        }
        const hover = () => {
          const began = performance.now();
          f.panel.scheduleHover({
            svg,
            clientX: inputRect.left + inputRect.width * 0.6,
            clientY: inputRect.top + 180,
          });
          return began;
        };
        hover();
        await f.settle();
        for (let i = 0; i < 20; i++) {
          const t = performance.now();
          await f.tick();
          ticks.push(performance.now() - t);
          f.assertClean();
          const ht = hover();
          await f.settle();
          hoverDurations.push(performance.now() - ht);
        }
        cancelAnimationFrame(monitor);
        const counts = { ...f.counts },
          measurements = structuredClone(f.measurements);
        f.panel.zoom = 2;
        const [a, z] = f.panel.historyDomain();
        f.panel.setHistoryWindow(a + (z - a) / 4, z - (z - a) / 4);
        f.panel.drawHistory();
        await f.settle();
        f.reset();
        await f.status();
        await f.status();
        const phaseCounts = { ...f.counts };
        const rect = f.panel.$("svg.session-chart").getBoundingClientRect();
        for (let i = 0; i < 10; i++) {
          f.panel.scheduleHover({
            svg: f.panel.$("svg.session-chart"),
            clientX: rect.left + rect.width * (0.4 + i * 0.01),
            clientY: rect.top + 150,
          });
          await f.settle();
        }
        const hoverCounts = { ...f.counts };
        return {
          coldMs: cold,
          ticks,
          frameIntervals,
          hoverDurations,
          geometryReads: { ctmCalls, rectCalls },
          counts,
          measurements,
          phaseCounts,
          hoverCounts,
          identity: f.identity(),
          records: f.panel.shown.records.length,
          tooltip: f.panel.$("#tooltip").textContent,
          canvasBitmap: f.panel.$("canvas.history-curves")?.width,
          cssWidth: rect.width,
          ua: navigator.userAgent,
        };
      }, both);
      result.engine = engine;
      result.version = b.version();
      result.variant = variant;
      result.both = both;
      result.errors = errors;
      results.push(result);
      await page.screenshot({
        path: `${output}/${engine}-${variant}-${both ? "both" : "upper"}.png`,
        fullPage: true,
      });
      await page.close();
      console.log(
        JSON.stringify({
          engine,
          variant,
          both,
          counts: result.counts,
          phase: result.phaseCounts,
          identity: result.identity,
          errors,
        }),
      );
    }
  fs.writeFileSync(
    `${output}/${engine}-profiles.json`,
    JSON.stringify(results, null, 2),
  );
  await b.close();
})().catch(async (e) => {
  console.error(e);
  await runningBrowser?.close();
  process.exitCode = 1;
});
