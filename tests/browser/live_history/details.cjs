// Real panel methods and browser input; only archive/status transport is synthetic.
const { chromium, webkit } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DETAILS_EVIDENCE || "/tmp/ha-sauna-details";
fs.mkdirSync(output, { recursive: true });

(async () => {
  const engine = process.env.ENGINE || "chromium";
  const browser = await (engine === "webkit" ? webkit : chromium).launch({
    headless: true,
  });
  const results = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.setContent(
      '<!doctype html><html lang="de"><body style="margin:0"></body></html>',
    );
    await page.addScriptTag({
      path: path.resolve("custom_components/ha_sauna/panel.js"),
    });
    await page.addScriptTag({
      path: path.resolve("tests/browser/live_history/fixture.js"),
    });
    const defaults = JSON.parse(
      fs.readFileSync("custom_components/ha_sauna/defaults.json", "utf8"),
    );
    await page.evaluate(async (defaults) => {
      window.f = await installHistoryFixture({ seconds: 1200, defaults });
      f.state.permissions = { admin: true };
      f.panel.state.permissions = { admin: true };
      const at = f.iso(f.origin + 305000);
      const events = [
        {
          event_id: "door-proof",
          kind: "door_open",
          effective_at: f.iso(f.origin + 300000),
          detected_at: at,
        },
        {
          event_id: "infusion-proof",
          kind: "infusion",
          effective_at: at,
          detected_at: at,
        },
      ];
      f.session.timeline.processed.push(...events);
      for (let second = 0; second < 1200; second += 5) {
        const selected = second === 305;
        f.addRecord({
          kind: "detector_trace",
          received_at: f.iso(f.origin + second * 1000),
          payload: {
            at: f.iso(f.origin + second * 1000),
            metrics: {
              upper: {
                door_temperature_slope: selected ? -12 : -1,
                door_humidity_delta: -6,
                infusion_humidity_delta: selected ? 8 : 0,
                infusion_temperature_delta: 0,
              },
              lower: { door_temperature_slope: -0.8 },
            },
            checks: { door_open: true, person_strong: false },
            conditions: { door_open: selected, person_strong: false },
            holds: { door_open: selected ? 2 : 0, person_strong: 0 },
            signals: selected ? ["door_open", "infusion"] : [],
            channels: ["upper"],
          },
        });
      }
      for (const event of events)
        f.addRecord({
          kind: "detection",
          received_at: at,
          payload: { event, trace_at: at, channels: ["upper"] },
        });
      f.panel.setPanelView("diagnostics");
      await f.refresh();
      f.assertClean();
    }, defaults);

    const panel = page.locator("ha-sauna-panel");
    const marker = panel
      .locator('.diagnostic-marker[data-event-id="door-proof"]')
      .first();
    await marker.click();
    await panel.locator("#detection-plots").waitFor({ state: "visible" });
    assert.equal(await page.evaluate(() => f.panel.navigation.detail), "diagnostics");
    assert.equal(await marker.getAttribute("data-selected"), "true");
    await marker.press("Enter");
    await marker.press("Space");
    assert.equal(await page.evaluate(() => f.panel.navigation.main), "details");
    await panel.locator("#diagnostic-selection").waitFor({ state: "visible" });
    assert.match(await panel.locator("#diagnostic-selection").innerText(), /Tür/);
    results.push({
      check:
        "Pointer and keyboard select the producing trace without leaving diagnostics",
      passed: true,
    });

    await page.evaluate(() => {
      const p = f.panel;
      const details = p.$("#detection-plots details");
      details.open = true;
      const text = details.querySelector("p").firstChild;
      const selection = document.getSelection();
      selection.setBaseAndExtent(text, 0, text, Math.min(24, text.length));
      f.outer.querySelector("#scroll").scrollTop = 100;
      window.beforeUpdate = {
        details,
        chart: p.$('[data-diagnostic-metric="door_temperature_slope"]'),
        marker: p.$('.diagnostic-marker[data-event-id="door-proof"]'),
        focus: p.shadowRoot.activeElement,
        window: [...p.window],
        zoom: p.zoom,
        selected: selection.toString(),
        scroll: f.outer.querySelector("#scroll").scrollTop,
      };
    });
    const stable = await page.evaluate(async () => {
      const p = f.panel;
      f.setDelay(80);
      const loading = [];
      const observer = new MutationObserver(() =>
        loading.push(!p.$("#history-loading").hidden),
      );
      observer.observe(p.$("#history-loading"), {
        attributes: true,
        childList: true,
        subtree: true,
      });
      for (let i = 0; i < 3; i++) await f.tick();
      observer.disconnect();
      return {
        details: p.$("#detection-plots details") === beforeUpdate.details,
        open: beforeUpdate.details.open,
        chart:
          p.$('[data-diagnostic-metric="door_temperature_slope"]') ===
          beforeUpdate.chart,
        marker:
          p.$('.diagnostic-marker[data-event-id="door-proof"]') === beforeUpdate.marker,
        focus: p.shadowRoot.activeElement === beforeUpdate.focus,
        window: JSON.stringify(p.window) === JSON.stringify(beforeUpdate.window),
        zoom: p.zoom === beforeUpdate.zoom,
        textSelection:
          document.getSelection().toString() === beforeUpdate.selected &&
          beforeUpdate.selected.length > 0,
        scroll: f.outer.querySelector("#scroll").scrollTop === beforeUpdate.scroll,
        loading: !loading.includes(true),
        marked:
          p.$('.diagnostic-marker[data-event-id="door-proof"]').dataset.selected ===
          "true",
      };
    });
    assert.deepEqual(
      Object.values(stable),
      Object.values(stable).map(() => true),
      JSON.stringify(stable),
    );
    results.push({
      check:
        "Warm live appends preserve nodes, disclosure, focus, selection, viewport and scroll",
      ...stable,
    });

    const polling = await page.evaluate(async () => {
      f.setDelay(0);
      const requests = f.requests.length;
      for (let i = 0; i < 3; i++) {
        f.state.now = f.iso(Date.parse(f.state.now) + 2000);
        await f.panel.refresh();
        await f.panel.historyLoad?.promise;
      }
      return { before: requests, after: f.requests.length };
    });
    assert.equal(
      polling.after,
      polling.before,
      "status polls do not reload the archive every two seconds",
    );
    results.push({ check: "Background archive requests are throttled", ...polling });

    const outage = await page.evaluate(async () => {
      const p = f.panel,
        chart = p.$('[data-diagnostic-metric="door_temperature_slope"]');
      const windowBefore = JSON.stringify(p.window);
      f.setFail();
      await f.refresh();
      const failed = {
        errorVisible: !!p.messages?.history,
        viewPreserved: p.navigation.detail === "diagnostics",
        chartPreserved:
          p.$('[data-diagnostic-metric="door_temperature_slope"]') === chart,
        windowPreserved: JSON.stringify(p.window) === windowBefore,
      };
      await f.refresh();
      f.assertClean();
      return { ...failed, recovered: !p.messages?.history };
    });
    assert.ok(Object.values(outage).every(Boolean), JSON.stringify(outage));
    results.push({
      check: "Archive outage retains the visible state and retries cleanly",
      ...outage,
    });

    const chartSurface = panel.locator(
      '[data-diagnostic-metric="door_temperature_slope"] svg',
    );
    await chartSurface.hover({ position: { x: 100, y: 90 } });
    const mouse = await chartSurface.boundingBox();
    const pointer = { x: mouse.x + 100, y: mouse.y + 90 };
    const cursorAt = (point) => {
      let target = document.elementFromPoint(point.x, point.y);
      while (target?.shadowRoot) {
        const child = target.shadowRoot.elementFromPoint(point.x, point.y);
        if (!child || child === target) break;
        target = child;
      }
      return { cursor: getComputedStyle(target).cursor, tag: target.tagName };
    };
    const cursorBefore = await page.evaluate(cursorAt, pointer);
    await page.evaluate(async () => {
      await f.tick();
    });
    const cursorAfter = await page.evaluate(cursorAt, pointer);
    assert.deepEqual(cursorAfter, cursorBefore);
    assert.equal(cursorAfter.cursor, "default");
    results.push({
      check: "Stationary pointer retains its cursor on the updated diagram",
      ...cursorAfter,
    });

    const fullscreen = panel.locator('[data-action="fullscreen"]');
    await fullscreen.click();
    await page.waitForFunction(() => f.panel.isPanelFullscreen());
    await page.evaluate(async () => {
      await f.tick();
    });
    assert.equal(await page.evaluate(() => f.panel.highlightedEventId), "door-proof");
    assert.deepEqual(
      await page.evaluate(() => f.panel.window),
      await page.evaluate(() => beforeUpdate.window),
    );
    await fullscreen.click();
    await page.waitForFunction(() => !f.panel.isPanelFullscreen());
    results.push({
      check: "Fullscreen and live updates retain the selected section",
      passed: true,
    });

    await page.screenshot({
      path: path.join(output, "diagnostics-wide.png"),
      fullPage: true,
    });
    await page.evaluate(() => {
      f.host.style.width = "390px";
      f.host.style.maxWidth = "100%";
    });
    await page.waitForTimeout(150);
    const narrow = await page.evaluate(() => {
      const p = f.panel,
        element = p.$("#detection-plots"),
        plot = p.$('[data-diagnostic-metric="door_temperature_slope"]');
      return {
        container: element.getBoundingClientRect().width,
        plot: plot.getBoundingClientRect().width,
        scroll: element.scrollWidth,
        client: element.clientWidth,
        window: [...p.window],
        expectedWindow: beforeUpdate.window,
        selected: p.highlightedEventId,
      };
    });
    assert.ok(narrow.plot <= narrow.container + 1, JSON.stringify(narrow));
    assert.ok(narrow.scroll <= narrow.client + 1, JSON.stringify(narrow));
    assert.deepEqual(narrow.window, narrow.expectedWindow);
    assert.equal(narrow.selected, "door-proof");
    await page.screenshot({
      path: path.join(output, "diagnostics-narrow-container.png"),
      fullPage: true,
    });
    results.push({
      check:
        "Narrow embedded container retains selection and viewport without overflow",
      ...narrow,
    });

    await page.evaluate(async () => {
      f.host.style.width = "";
      const iso = (seconds) => f.iso(f.origin + seconds * 1000);
      f.addRecord({
        kind: "diagnostic",
        received_at: iso(0),
        payload: { faults: {}, messages: [] },
      });
      f.panel.setPanelView("history");
      await f.refresh();
    });
    const faultFree = await panel.locator("#event-list").innerText();
    assert.doesNotMatch(faultFree, /Historische Fehler|Fehler[^\n]*\(1\)/);
    const faultCount = panel.locator("[data-fault-count]");
    if (await faultCount.count())
      assert.equal(await faultCount.first().getAttribute("data-fault-count"), "0");
    await page.evaluate(async () => {
      const iso = (seconds) => f.iso(f.origin + seconds * 1000);
      for (const [index, brightness] of [80, 70, 60].entries()) {
        const at = iso(610 + index * 10);
        f.addRecord({
          kind: "light_command",
          received_at: at,
          payload: {
            phase: "aufheizen",
            purpose: "warmup",
            service: "turn_on",
            brightness_pct: brightness,
            planned_at: at,
            sent_at: at,
            completed_at: at,
            ends_at: iso(1200),
            service_error: null,
          },
        });
      }
      f.addRecord({
        kind: "light_command",
        received_at: iso(640),
        payload: {
          phase: "aufheizen",
          purpose: "warmup",
          service: "turn_on",
          brightness_pct: 50,
          sent_at: iso(640),
          completed_at: iso(640),
          service_error: "fixture-light-failure",
        },
      });
      f.addRecord({
        kind: "source_state",
        received_at: iso(605),
        display: { power_w: 6000 },
        payload: {
          role: "heater_power",
          source: "sensor.fixture_power",
          state: "6000",
          attributes: { unit_of_measurement: "W", device_class: "power" },
        },
      });
      f.addRecord({
        kind: "illuminance",
        received_at: iso(606),
        payload: {
          source: "sensor.fixture_illuminance",
          state: "250",
          attributes: { unit_of_measurement: "lx" },
        },
      });
      f.addRecord({
        kind: "manual_light",
        received_at: iso(650),
        payload: { brightness_pct: 20, source: "panel" },
      });
      f.addRecord({
        kind: "diagnostic",
        received_at: iso(660),
        payload: {
          faults: { session_light: "service_unavailable" },
          messages: ["Saunalicht konnte nicht eingestellt werden."],
        },
      });
      f.panel.setPanelView("detail-history");
      await f.refresh();
      f.outer.querySelector("#scroll").scrollTop = 0;
      f.assertClean();
    });
    await panel.locator("#control-values").waitFor({ state: "visible" });
    const valueText = await panel.locator("#control-values").innerText();
    for (const term of ["Temperatur", "Luftfeuchte", "oben", "unten", "W", "lx"])
      assert.ok(valueText.includes(term), `${term} missing from value history`);
    assert.match(valueText, /6[.\s]000/);
    const grouped = panel
      .locator("[data-light-section]")
      .filter({ hasText: "3 Einzelbefehle" });
    assert.equal(
      await grouped.count(),
      1,
      "three successful light commands are one visible section",
    );
    assert.match(
      await panel.locator(".control-history-events").innerText(),
      /fixture-light-failure/,
    );
    const lightCommands = grouped.locator("details");
    await lightCommands.locator("summary").click();
    assert.match(await lightCommands.innerText(), /80.*%/s);
    assert.match(await lightCommands.innerText(), /60.*%/s);
    const controlSlider = panel.locator("#control-history-cursor");
    const sliderBox = await controlSlider.boundingBox();
    await controlSlider.click({
      position: { x: sliderBox.width * 0.75, y: sliderBox.height / 2 },
    });
    const controlState = await page.evaluate(() => {
      const p = f.panel;
      const slider = p.$("#control-history-cursor");
      window.controlNodes = {
        slider,
        records: p.$("#detail-records"),
        values: p.$("#control-values"),
        cursor: p.controlHistoryCursor,
      };
      return {
        value: slider.value,
        cursor: p.controlHistoryCursor,
        start: f.origin,
        max: Number(slider.max),
        line: Number(p.$("#control-values svg line").getAttribute("x1")),
        status: p.$(".control-history-inspection h3").textContent,
      };
    });
    assert.equal(
      controlState.cursor,
      controlState.start + Number(controlState.value),
      "native range interaction selects the actual time",
    );
    assert.ok(
      Number(controlState.value) > controlState.max * 0.7 &&
        Number(controlState.value) < controlState.max * 0.8,
    );
    assert.ok(
      Math.abs(
        controlState.line -
          (48 + (500 * Number(controlState.value)) / controlState.max),
      ) < 0.01,
      "plot cursor follows the selected time",
    );
    await page.evaluate(async () => {
      await f.tick();
    });
    assert.equal(
      await panel.locator("#control-history-cursor").inputValue(),
      controlState.value,
    );
    assert.equal(
      await page.evaluate(() => f.panel.controlHistoryCursor),
      controlState.cursor,
    );
    assert.equal(
      await panel.locator(".control-history-inspection h3").first().innerText(),
      controlState.status,
    );
    assert.match(await panel.locator("#control-values").innerText(), /6[.\s]000/);
    assert.ok(
      await page.evaluate(() => {
        const p = f.panel;
        return (
          p.$("#control-history-cursor") === controlNodes.slider &&
          p.$("#control-values") === controlNodes.values &&
          p.$("#detail-records") === controlNodes.records &&
          p.shadowRoot.activeElement === controlNodes.slider
        );
      }),
      "control history preserves its value, focus and sections",
    );
    assert.equal(await lightCommands.getAttribute("open"), "");
    await grouped.locator("button[data-control-time]").focus();
    await grouped.evaluate((row) => {
      window.lateRecordNodes = {
        row,
        button: row.querySelector("button"),
        details: row.querySelector("details"),
      };
    });
    const lateInsert = await page.evaluate(async () => {
      const at = f.iso(f.origin + 590000),
        p = f.panel;
      f.addRecord({
        kind: "command",
        received_at: at,
        payload: { heat: true, sent_at: at, completed_at: at, service_error: null },
      });
      await f.refresh();
      return {
        row:
          p.$(`[data-record-id="${lateRecordNodes.row.dataset.recordId}"]`) ===
          lateRecordNodes.row,
        button: p.shadowRoot.activeElement === lateRecordNodes.button,
        details:
          lateRecordNodes.row.querySelector("details") === lateRecordNodes.details,
        open: lateRecordNodes.details.open,
        insertedBefore:
          !!lateRecordNodes.row.previousElementSibling?.textContent.includes(
            "Ofenbefehl",
          ),
      };
    });
    assert.ok(Object.values(lateInsert).every(Boolean), JSON.stringify(lateInsert));
    results.push({
      check:
        "Earlier event inserted later preserves existing row identity, keyboard focus and open commands",
      ...lateInsert,
    });
    const lateLight = await page.evaluate(async () => {
      const at = f.iso(f.origin + 580000),
        p = f.panel;
      // Document endpoints can be retargeted to the outer shadow host while
      // toString still exposes the selected text. Observe the same native
      // shadow selection as the panel, and prove its endpoints before updating.
      const selection = p.shadowRoot.getSelection?.() || document.getSelection(),
        text = lateRecordNodes.details.querySelector("p").firstChild;
      const end = Math.min(text.length, 20);
      selection.setBaseAndExtent(text, 0, text, end);
      const selectedText = selection.toString();
      const observeSelection = () => ({
        anchor: selection.anchorNode === text,
        focus: selection.focusNode === text,
        anchorOffset: selection.anchorOffset,
        focusOffset: selection.focusOffset,
        text: selection.toString(),
        documentAnchorIsText: document.getSelection().anchorNode === text,
        documentText: document.getSelection().toString(),
      });
      const selectionBefore = observeSelection();
      if (
        !selectionBefore.anchor ||
        !selectionBefore.focus ||
        selectionBefore.anchorOffset !== 0 ||
        selectionBefore.focusOffset !== end ||
        !selectedText.length ||
        selectionBefore.documentText !== selectedText
      )
        throw new Error(
          `Native selection was not established before refresh: ${JSON.stringify(selectionBefore)}`,
        );
      f.addRecord({
        kind: "light_command",
        received_at: f.state.now,
        payload: {
          phase: "aufheizen",
          purpose: "warmup",
          service: "turn_on",
          brightness_pct: 90,
          planned_at: at,
          sent_at: at,
          completed_at: f.state.now,
          ends_at: f.iso(f.origin + 1200000),
          service_error: null,
        },
      });
      await f.refresh();
      const selectionAfter = observeSelection();
      return {
        checks: {
          row:
            p.$(`[data-record-id="${lateRecordNodes.row.dataset.recordId}"]`) ===
            lateRecordNodes.row,
          focus: p.shadowRoot.activeElement === lateRecordNodes.button,
          selection:
            selectionAfter.anchor &&
            selectionAfter.focus &&
            selectionAfter.anchorOffset === 0 &&
            selectionAfter.focusOffset === end &&
            selectionAfter.text === selectedText,
          documentSelection: selectionAfter.documentText === selectedText,
          details:
            lateRecordNodes.row.querySelector("details") === lateRecordNodes.details,
          open: lateRecordNodes.details.open,
          allCommands:
            lateRecordNodes.details.querySelectorAll("p[data-record-id]").length === 4,
          earlierStart: lateRecordNodes.row.textContent.includes("90 %"),
        },
        selectionBefore,
        selectionAfter,
      };
    });
    assert.ok(
      Object.values(lateLight.checks).every(Boolean),
      JSON.stringify(lateLight),
    );
    results.push({
      check:
        "Earlier light completion retains section, focus and native selection endpoints",
      ...lateLight.checks,
      selectionBefore: lateLight.selectionBefore,
      selectionAfter: lateLight.selectionAfter,
    });
    await controlSlider.focus();
    // Expand only the synthetic outer harness for complete review images;
    // the retained panel and its real layout remain unchanged.
    await page.evaluate(() => {
      const scroll = f.outer.querySelector("#scroll");
      scroll.style.height = "auto";
      scroll.style.overflow = "visible";
    });
    await panel
      .locator("#control-state-history")
      .screenshot({ path: path.join(output, "control-state-wide.png") });
    await panel
      .locator("#control-values")
      .screenshot({ path: path.join(output, "control-values-wide.png") });
    await page.evaluate(async () => {
      f.panel.setPanelView("history");
      await f.refresh();
    });
    assert.equal(
      await panel
        .locator("#event-list [data-fault-count]")
        .getAttribute("data-fault-count"),
      "1",
    );
    results.push({
      check:
        "Values, compact light sections, raw commands and true fault count stay usable across appends",
      passed: true,
    });

    await page.evaluate(async () => {
      f.close();
      delete f.session.configuration;
      delete f.session.configuration_model;
      f.panel.selected = f.session.timeline.session_id;
      f.panel.setPanelView("diagnostics");
      await f.refresh();
    });
    assert.match(
      await panel.locator("#diagnostic-threshold-status").innerText(),
      /keine historischen Schwellen/,
    );
    const legacy = await panel
      .locator('[data-series="detector_door_temperature_slope_upper"]')
      .evaluate((node) => ({
        path: node.getAttribute("d"),
        cap: getComputedStyle(node).strokeLinecap,
        width: node.getBBox().width,
        height: node.getBBox().height,
      }));
    assert.match(legacy.path, /l0,0/);
    assert.equal(legacy.cap, "round");
    assert.ok(legacy.width > 0 && legacy.height > 0);
    await panel
      .locator('[data-diagnostic-metric="door_temperature_slope"]')
      .screenshot({ path: path.join(output, "legacy-diagnostic-points.png") });
    results.push({
      check:
        "Legacy archive keeps recorded points visible without invented cadence or thresholds",
      passed: true,
    });

    const large = await page.evaluate(async (defaults) => {
      document.body.replaceChildren();
      window.f = await installHistoryFixture({ seconds: 14400, defaults });
      f.state.permissions = { admin: true };
      f.panel.state.permissions = { admin: true };
      f.panel.setPanelView("detail-history");
      await f.refresh();
      // The first later status enables "Bis zum neuesten Stand". Measure
      // subsequent polls once that visible state transition has settled.
      f.state.now = f.iso(Date.parse(f.state.now) + 2000);
      await f.panel.refresh();
      await f.panel.historyLoad?.promise;
      let valuesRenders = 0;
      const render = f.panel.renderHistoryValues;
      f.panel.renderHistoryValues = function (...args) {
        valuesRenders++;
        return render.apply(this, args);
      };
      const polls = [],
        appends = [];
      for (let i = 0; i < 5; i++) {
        f.state.now = f.iso(Date.parse(f.state.now) + 2000);
        const start = performance.now();
        await f.panel.refresh();
        await f.panel.historyLoad?.promise;
        polls.push(performance.now() - start);
      }
      const unchangedRenders = valuesRenders;
      for (let i = 0; i < 5; i++) {
        const start = performance.now();
        await f.tick();
        appends.push(performance.now() - start);
        f.assertClean();
      }
      return {
        records: f.panel.shown.records.length,
        unchangedRenders,
        pollsMs: polls,
        appendsMs: appends,
        plotCount: f.panel.shadowRoot.querySelectorAll(".detail-value-plot").length,
      };
    }, defaults);
    assert.equal(large.records, 57600 + 5 * 8);
    assert.equal(
      large.unchangedRenders,
      0,
      "unchanged status polls do not rebuild four hours of value plots",
    );
    assert.equal(large.plotCount, 4);
    results.push({
      check:
        "Four-hour detail history processes append batches and skips unchanged value plots",
      ...large,
    });

    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, "details.json"),
      JSON.stringify({ engine, results, errors }, null, 2) + "\n",
    );
    console.log(JSON.stringify({ engine, passed: results.length, results }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
