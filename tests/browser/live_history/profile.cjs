// Current-panel load and live-render profile with native browser pointer input.
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const { webkit, chromium } = require("playwright");
const root = process.cwd();
const defaults = JSON.parse(
  fs.readFileSync(path.join(root, "custom_components/ha_sauna/defaults.json"), "utf8"),
);
const output =
  process.env.HISTORY_EVIDENCE ||
  path.join(require("node:os").tmpdir(), "ha-sauna-live-history");
let runningBrowser;
fs.mkdirSync(output, { recursive: true });
(async () => {
  const engine = process.env.ENGINE || "webkit";
  const browser = await (engine === "webkit" ? webkit : chromium).launch({
    headless: true,
    ...(engine === "chromium" && process.env.CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  });
  runningBrowser = browser;
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
    deviceScaleFactor: 2,
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.setContent(
    '<!doctype html><html lang="de"><body style="margin:0"></body></html>',
  );
  await page.addScriptTag({
    path: path.join(root, "custom_components/ha_sauna/panel.js"),
  });
  await page.addScriptTag({
    path: path.join(root, "tests/browser/live_history/fixture.js"),
  });
  await page.evaluate(async (defaults) => {
    window.f = await installHistoryFixture({ defaults });
    f.assertClean();
    f.setDelay(100);
    f.reset();
    window.profile = {
      ticks: [],
      frameIntervals: [],
      geometryReads: { ctmCalls: 0, rectCalls: 0 },
    };
    const watch = (now) => {
      if (profile.previous != null) profile.frameIntervals.push(now - profile.previous);
      profile.previous = now;
      profile.monitor = requestAnimationFrame(watch);
    };
    profile.monitor = requestAnimationFrame(watch);
    const svg = f.panel.$("svg.session-chart");
    for (const [name, key] of [
      ["getScreenCTM", "ctmCalls"],
      ["getBoundingClientRect", "rectCalls"],
    ]) {
      const original = svg[name];
      svg[name] = function (...args) {
        profile.geometryReads[key]++;
        return original.apply(this, args);
      };
    }
  }, defaults);
  const surface = page.locator("ha-sauna-panel").locator("svg.session-chart");
  for (let index = 0; index < 20; index++) {
    const box = await surface.boundingBox();
    await page.mouse.move(
      box.x + box.width * (0.3 + index / 50),
      box.y + box.height / 3,
    );
    await page.evaluate(async () => {
      const started = performance.now();
      await f.tick();
      profile.ticks.push(performance.now() - started);
      f.assertClean();
    });
  }
  await page.evaluate(() => {
    profile.liveCounts = { ...f.counts };
    profile.liveMeasurements = structuredClone(f.measurements);
  });
  await page.locator("ha-sauna-panel").locator('[data-action="zoom-in"]').click();
  await page.evaluate(async () => {
    await f.settle();
    f.reset();
    await f.status();
    await f.status();
    profile.phaseCounts = { ...f.counts };
  });
  for (let index = 0; index < 10; index++) {
    const box = await surface.boundingBox();
    await page.mouse.move(
      box.x + box.width * (0.4 + index / 100),
      box.y + box.height / 3,
    );
    await page.evaluate(() => f.settle());
  }
  const result = await page.evaluate(() => {
    cancelAnimationFrame(profile.monitor);
    return {
      coldMs: f.coldMs,
      ticks: profile.ticks,
      frameIntervals: profile.frameIntervals,
      geometryReads: profile.geometryReads,
      counts: profile.liveCounts,
      measurements: profile.liveMeasurements,
      phaseCounts: profile.phaseCounts,
      hoverCounts: { ...f.counts },
      identity: f.identity(),
      records: f.panel.shown.records.length,
      tooltip: f.panel.$("#tooltip").textContent,
      canvasBitmap: f.panel.$("canvas.history-curves").width,
      cssWidth: f.panel.$("svg.session-chart").getBoundingClientRect().width,
      ua: navigator.userAgent,
    };
  });
  assert.deepEqual(errors, []);
  assert.equal(result.records, 57600 + 20 * 8);
  assert.ok(Object.values(result.identity).every(Boolean));
  assert.equal(result.phaseCounts["curves.buildMainPath"] || 0, 0);
  assert.match(result.tooltip, /Temperatur:.*°C/);
  assert.match(result.tooltip, /Luftfeuchte:.*%/);
  Object.assign(result, { engine, version: browser.version(), errors });
  await page.screenshot({
    path: path.join(output, `${engine}-profile.png`),
    fullPage: true,
  });
  fs.writeFileSync(
    path.join(output, `${engine}-profiles.json`),
    JSON.stringify([result], null, 2),
  );
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
})().catch(async (error) => {
  console.error(error);
  await runningBrowser?.close();
  process.exitCode = 1;
});
