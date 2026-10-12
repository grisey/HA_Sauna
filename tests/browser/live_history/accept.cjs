// Run from repository root with Playwright installed in NODE_PATH.
// No Home Assistant connection: only the transport supplies synthetic fixtures.
const { webkit, chromium } = require("playwright");
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const root = process.cwd(),
  output =
    process.env.HISTORY_EVIDENCE ||
    require("node:path").join(require("node:os").tmpdir(), "ha-sauna-live-history");
const defaults = JSON.parse(
  fs.readFileSync(path.join(root, "custom_components/ha_sauna/defaults.json"), "utf8"),
);
let runningBrowser;
fs.mkdirSync(output, { recursive: true });
(async () => {
  const engine = process.env.ENGINE || "webkit";
  const browser = await (engine === "webkit" ? webkit : chromium).launch({
    headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  });
  runningBrowser = browser;
  const results = [];
  async function fixture(options = {}) {
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
    await page.evaluate(
      async (options) => {
        window.f = await installHistoryFixture(options);
        f.assertClean();
      },
      { defaults, ...options },
    );
    page.errors = errors;
    return page;
  }
  const page = await fixture();
  results.push(
    await page.evaluate(async () => {
      const assert = (condition, message) => {
        if (!condition) throw Error(message);
      };
      assert(f.records.length === 57600, "four-hour fixture count");
      f.setDelay(100);
      f.reset();
      for (let i = 0; i < 3; i++) {
        const before = f.counts["curves.paintMain"] || 0;
        await f.tick();
        assert(
          f.counts["curves.paintMain"] === before + 1,
          "one measurement paint per response",
        );
      }
      assert(
        f.panel.shown.records.length === 57624,
        "all eight records retained per update",
      );
      assert(
        Object.values(f.identity()).every(Boolean),
        "same chart and interaction nodes",
      );
      const live = { ...f.counts };
      const [a, z] = f.panel.historyDomain();
      f.panel.setHistoryWindow(a + (z - a) / 4, z - (z - a) / 4);
      f.panel.drawHistory();
      await f.settle();
      const index = f.panel.chartDataIndex,
        paths = [...f.panel.historyChart.curves.mainPaths.values()].map((v) => v.path);
      f.reset();
      await f.status();
      await f.status();
      const stateOnly = { ...f.counts };
      assert(
        !f.counts["curves.paintMain"] &&
          !f.counts["curves.buildMainPath"] &&
          !f.counts.historyIndex,
        "clock/phase does not rebuild measurements",
      );
      f.state.phase_projection = {
        intervals: [
          {
            phase: "saunagang",
            started_at: f.iso(f.origin + 4000000),
            ended_at: f.state.now,
            source_id: "g",
            complete: false,
          },
        ],
      };
      f.state.session.timeline.active = {
        gang_id: "g",
        started_at: f.iso(f.origin + 4000000),
        infusion_events: [],
      };
      f.panel.state = structuredClone(f.state);
      f.panel.showHistoryCache();
      f.panel.drawHistory("status");
      await f.settle();
      assert(f.panel.$(".history-background .provisional"), "provisional gang layer");
      f.state.session.timeline.active = null;
      f.state.phase_projection = {
        intervals: [
          {
            phase: "bereit",
            started_at: f.iso(f.origin),
            ended_at: f.state.now,
            complete: false,
          },
        ],
      };
      f.panel.state = structuredClone(f.state);
      f.panel.showHistoryCache();
      f.panel.drawHistory("status");
      await f.settle();
      assert(
        !f.panel.$(".history-background .gang") &&
          f.panel.$(".history-background .ready"),
        "retraction exposes authoritative base phase",
      );
      assert(index === f.panel.chartDataIndex, "original point index retained");
      assert(
        paths.every(
          (v, i) => v === [...f.panel.historyChart.curves.mainPaths.values()][i].path,
        ),
        "paths retained across phase changes",
      );
      return { case: "live-and-phases", live, stateOnly, identity: f.identity() };
    }),
  );
  // Actual browser mouse, zoom button, focus and minimap pointer-capture.
  const surface = page.locator("ha-sauna-panel").locator("svg.session-chart");
  const box = await surface.boundingBox();
  await page.mouse.move(box.x + box.width * 0.55, box.y + 150);
  await page.evaluate(() => f.settle());
  const tooltipBefore = await page.evaluate(() => f.panel.$("#tooltip").textContent);
  assert.match(tooltipBefore, /Temperatur:.*°C/);
  assert.match(tooltipBefore, /Luftfeuchte:.*%/);
  const overview = page
    .locator("ha-sauna-panel")
    .locator("#history-overview [data-history-window]");
  const obox = await overview.boundingBox();
  const windowBeforeDrag = await page.evaluate(() => [...f.panel.window]);
  await page.mouse.move(obox.x + obox.width / 2, obox.y + obox.height / 2);
  await page.mouse.down();
  await page.mouse.move(obox.x + obox.width / 2 + 30, obox.y + obox.height / 2);
  await page.evaluate(() => f.tick());
  await page.mouse.up();
  assert.notDeepEqual(await page.evaluate(() => [...f.panel.window]), windowBeforeDrag);
  const zoomButton = page.locator("ha-sauna-panel").locator('[data-action="zoom-in"]');
  const zoomBefore = await page.evaluate(() => f.panel.zoom);
  await zoomButton.click();
  await page.evaluate(() => f.settle());
  assert.ok((await page.evaluate(() => f.panel.zoom)) > zoomBefore);
  await zoomButton.focus();
  await page.evaluate(() => f.tick());
  assert.equal(
    await zoomButton.evaluate(
      (button) => button.getRootNode().activeElement === button,
    ),
    true,
  );
  // Scroll the actual shadow ancestor, then re-enter the chart with the mouse.
  await surface.scrollIntoViewIfNeeded();
  const currentBox = await surface.boundingBox();
  await page.mouse.move(
    currentBox.x + currentBox.width / 2,
    currentBox.y + currentBox.height / 3,
  );
  await page.evaluate(() => f.settle());
  results.push(
    await page.evaluate(() => {
      const p = f.panel;
      const tip = p.$("#tooltip");
      const geometry = p.historyChart.interaction.geometry;
      if (
        tip.hidden ||
        !tip.textContent.includes("°C") ||
        !tip.textContent.includes("%")
      )
        throw Error("hover values missing after zoom and live append");
      if (
        p.$("canvas.history-curves").width !==
        Math.round(geometry.canvas.cssWidth * devicePixelRatio)
      )
        throw Error("DPR bitmap mismatch");
      if (!Object.values(f.identity()).every(Boolean))
        throw Error("interaction replaced history nodes");
      return {
        case: "interaction",
        identity: f.identity(),
        zoom: p.zoom,
        tooltip: tip.textContent,
      };
    }),
  );
  await page.setViewportSize({ width: 1120, height: 1000 });
  await page.evaluate(() => f.settle());
  await page.evaluate(() => f.reset());
  await page.locator("ha-sauna-panel").locator('[data-action="overview"]').click();
  await page.evaluate(() => f.settle());
  await page.evaluate(() => {
    f.reset();
    f.panel.drawHistory();
  });
  await page.evaluate(() => f.settle());
  assert.equal(
    await page.evaluate(
      () => !!f.counts["curves.paintMain"] || f.panel.historyFrame != null,
    ),
    false,
  );
  await page.locator("ha-sauna-panel").locator('[data-action="history"]').click();
  await page.evaluate(async () => {
    await f.panel.historyLoad?.promise;
    await f.settle();
  });
  results.push(
    await page.evaluate(async () => {
      const canvas = f.panel.$("canvas.history-curves"),
        rect = f.panel.$("svg.session-chart").getBoundingClientRect();
      if (canvas.width !== Math.round(rect.width * devicePixelRatio))
        throw Error("resize bitmap mismatch");
      const oldInteraction = f.panel.historyChart.interaction,
        index = f.panel.chartDataIndex;
      f.panel.remove();
      if (f.panel.historyFrame != null || f.panel.historyChart)
        throw Error("disconnect cleanup");
      oldInteraction.invalidateGeometry();
      if (f.panel.historyFrame != null)
        throw Error("disposed interaction scheduled work");
      // Reconnection retains the panel's single event-handler shell. Disable its
      // automatic poll here so this test owns deterministic refresh timing.
      f.panel._hass = null;
      f.outer.querySelector("#content").append(f.panel);
      f.panel.drawHistory();
      await f.settle();
      if (!f.panel.historyChart || f.panel.chartDataIndex !== index)
        throw Error("reconnect index/lifetime");
      return { case: "resize-hidden-reconnect", bitmap: canvas.width, css: rect.width };
    }),
  );
  await page.screenshot({
    path: path.join(output, `${engine}-acceptance.png`),
    fullPage: true,
  });
  assert.deepEqual(page.errors, []);
  await page.close();
  for (const empty of [false, true]) {
    const p = await fixture({ seconds: 20 });
    results.push(
      await p.evaluate(async (empty) => {
        const cache = f.panel.cache.get("synthetic"),
          before = cache.records.length,
          rev = cache.measurementRevision,
          cursor = cache.after;
        f.close(empty);
        f.setFail();
        await f.refresh();
        if (cache.after !== cursor || cache.finalSynced)
          throw Error("failed completion lost retry cursor");
        await f.refresh();
        f.assertClean();
        if (
          !cache.finalSynced ||
          !cache.session.ended_at ||
          cache.records.length !== before + (empty ? 0 : 4)
        )
          throw Error("final snapshot incomplete");
        if (empty && cache.measurementRevision !== rev)
          throw Error("empty completion invents measurement revision");
        const end = f.panel.historyDomain()[1];
        f.state.now = f.iso(Date.parse(f.state.now) + 120000);
        await f.refresh();
        if (f.panel.historyDomain()[1] !== end)
          throw Error("finished session still moving");
        return {
          case: empty ? "empty-final-page" : "final-records-retry",
          records: cache.records.length,
          after: cache.after,
          ended: cache.session.ended_at,
          measurementRevision: cache.measurementRevision,
        };
      }, empty),
    );
    assert.deepEqual(p.errors, []);
    await p.close();
  }
  const lifecycle = await fixture({ seconds: 750 });
  results.push(
    await lifecycle.evaluate(async () => {
      const p = f.panel;
      f.setDelay(35);
      f.setPageSize(1000);
      p.cache.clear();
      p.shown = null;
      p.historySessionId = null;
      const original = p.api;
      let failSecond = true;
      p.api = async (path) => {
        if (path.includes("after=1000") && failSecond) {
          failSecond = false;
          throw Error("synthetic second page");
        }
        return original(path);
      };
      await p.refresh();
      const load = p.historyLoad?.promise;
      await new Promise((resolve) => setTimeout(resolve, 50));
      if (p.busy || p.cache.get("synthetic").records.length < 1000)
        throw Error("partial data not usable");
      await load;
      if (p.cache.get("synthetic").after !== 1000) throw Error("failed page cursor");
      await p.refresh();
      await p.historyLoad?.promise;
      await f.settle();
      if (
        p.shown.records.length !== 3000 ||
        new Set(p.shown.records.map((r) => r.id)).size !== 3000
      )
        throw Error("retry duplicates/data loss");
      // Keep an old open cached session while the status belongs to a new session.
      f.close();
      const newState = structuredClone(f.state);
      newState.session = {
        ...f.session,
        ended_at: null,
        timeline: { ...f.session.timeline, session_id: "new" },
      };
      p.api = (path) =>
        path.endsWith("/state") ? Promise.resolve(newState) : original(path);
      p.selected = "synthetic";
      p.historySelectionGeneration = (p.historySelectionGeneration || 0) + 1;
      await p.refresh();
      await p.historyLoad?.promise;
      await f.settle();
      if (
        !p.shown.session.ended_at ||
        !p.cache.get("synthetic").finalSynced ||
        p.shown.records.length !== 3004
      )
        throw Error("old selection after new live session not finalized");
      // The replaced-selection response must not overwrite the current cache/view.
      p.cache.clear();
      p.selected = "synthetic";
      p.historySelectionGeneration++;
      let release;
      const stale = new Promise((resolve) => {
        release = resolve;
      });
      p.api = (path) =>
        path.endsWith("/archive")
          ? Promise.resolve([{ session_id: "synthetic" }])
          : stale;
      const previous = p.startHistoryLoad();
      await Promise.resolve();
      await Promise.resolve();
      p.selected = "other";
      p.historySelectionGeneration++;
      p.historyLoad = null;
      release({
        records: [{ id: 9999, kind: "measurement", payload: {} }],
        session: f.session,
        next_after: null,
      });
      await previous;
      if (p.cache.get("synthetic").records.length)
        throw Error("stale response reached cache");
      // Empty archive uses the real no-session rendering path.
      p.selected = "live";
      p.sessions = [];
      p.cache.clear();
      p.state = { ...f.state, session: null };
      p.historySelectionGeneration++;
      p.api = (path) => Promise.resolve(path.endsWith("/state") ? p.state : []);
      await p.refresh();
      await p.historyLoad?.promise;
      await f.settle();
      if (
        p.historyChart ||
        p.$("canvas.history-curves") ||
        !p.$("#plots").textContent.includes("Noch keine Sitzungsdaten")
      )
        throw Error("empty archive cleanup");
      return {
        case: "slow-pages-retry-old-session-stale-empty",
        partial: 1000,
        complete: 3000,
        closedOld: 3004,
      };
    }),
  );
  assert.deepEqual(lifecycle.errors, []);
  await lifecycle.close();
  console.log(JSON.stringify(results, null, 2));
  fs.writeFileSync(
    path.join(output, `${engine}-acceptance.json`),
    JSON.stringify({ engine, version: browser.version(), results }, null, 2),
  );
  await browser.close();
})().catch(async (error) => {
  console.error(error);
  await runningBrowser?.close();
  process.exitCode = 1;
});
