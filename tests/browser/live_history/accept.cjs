const { settings } = require("../../settings_fixture.cjs");
const { catalog: fixtureCatalog } = require("../../catalog_fixture.cjs");
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
const defaults = structuredClone(fixtureCatalog);
let runningBrowser;
fs.mkdirSync(output, { recursive: true });
(async () => {
  const engine = process.env.ENGINE || settings.browser.engine;
  const browser = await (engine === "webkit" ? webkit : chromium).launch({
    headless: settings.browser.headless,
    ...(process.env.CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {}),
  });
  runningBrowser = browser;
  const results = [];
  async function fixture(options = {}, pageOptions = {}) {
    const page = await browser.newPage({
      viewport: settings.browser.viewports.history,
      deviceScaleFactor: settings.browser.device_scale_factor,
      ...pageOptions,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
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
      assert(f.records.length === f.initialRecordCount, "four-hour fixture count");
      f.setDelay(f.inputs.delayed_response_ms);
      f.reset();
      const retainedWindow = JSON.stringify(f.panel.window);
      for (let i = 0; i < f.inputs.accept_updates; i++) {
        const before = f.counts["curves.paintMain"] || 0;
        await f.tick();
        assert(
          (f.counts["curves.paintMain"] || 0) - before <= 1,
          "at most one measurement paint per response; offscreen appends need no repaint",
        );
        assert(
          JSON.stringify(f.panel.window) === retainedWindow,
          "appends preserve the chosen time window",
        );
      }
      assert(
        f.panel.shown.records.length ===
          f.initialRecordCount + f.inputs.accept_updates * f.recordsPerTick,
        "all eight records retained per update",
      );
      assert(
        Object.values(f.identity()).every(Boolean),
        "same chart and interaction nodes",
      );
      const painted = f.counts["curves.paintMain"] || 0,
        previousPaths = [...f.panel.historyChart.curves.mainPaths.values()].map(
          (item) => item.path,
        );
      const at = f.iso(f.origin + f.inputs.late_sample_seconds * 1000);
      f.addRecord({
        kind: "measurement",
        received_at: at,
        payload: {
          position: "upper",
          quantity: "temperature",
          value: f.inputs.late_temperature,
          raw_value: String(f.inputs.late_temperature),
          received_at: at,
          measured_at: at,
        },
      });
      await f.refresh();
      assert(
        f.counts["curves.paintMain"] === painted + 1,
        "a late measurement inside the retained window updates the visible curve",
      );
      assert(
        f.panel.chartDataIndex.series
          .get("upper:temperature")
          .some(
            (point) =>
              point.time === Date.parse(at) &&
              point.value === f.inputs.late_temperature,
          ),
        "the late value is available at its original measurement time",
      );
      assert(
        [...f.panel.historyChart.curves.mainPaths.values()].some(
          (item, index) => item.path !== previousPaths[index],
        ),
        "the visible curve path incorporates the late value",
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
  await page.mouse.move(0, 0);
  await page.evaluate(() => f.settle());
  assert.equal(
    await page.evaluate(() => f.panel.$("#tooltip").hidden),
    true,
    "ordinary hover still disappears on leaving the chart without a pin",
  );
  await page.setViewportSize(settings.browser.viewports.tablet);
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

  // A separate, existing short fixture keeps the live/measurement proof above
  // unchanged. Mouse and touchscreen input exercise the real panel handlers.
  const selectionPage = await fixture(
    { seconds: settings.fixtures.history.detail_duration_seconds },
    { hasTouch: true },
  );
  const selectionPanel = selectionPage.locator("ha-sauna-panel"),
    selectionSurface = selectionPanel.locator("svg.session-chart"),
    selectionTooltip = selectionPanel.locator("#tooltip"),
    readout = selectionPanel.locator(".history-readout"),
    humidityButton = selectionPanel.locator('[data-action="history-humidity"]');
  const settleSelection = async () => {
    await selectionPage.evaluate(() => f.settle());
    await selectionPage.waitForFunction(() => f.panel.historyFrame == null, null, {
      timeout: settings.browser.timeouts_ms.interaction,
    });
  };
  const chartPoint = (fraction, time) =>
    selectionPage.evaluate(
      ({ fraction, time }) => {
        const svg = f.panel.$("svg.session-chart"),
          rect = svg.getBoundingClientRect(),
          model = f.panel.historyChart.model,
          x =
            time == null
              ? model.left + (model.right - model.left) * fraction
              : model.x(time);
        return {
          x: rect.left + (x / model.width) * rect.width,
          y: rect.top + ((model.top + model.bottom) / 2 / model.height) * rect.height,
        };
      },
      { fraction, time },
    );
  const selectedValues = () =>
    selectionPage.evaluate(() => ({
      selection: structuredClone(f.panel.historyValueSelection),
      values: [
        ...f.panel.$("#tooltip").querySelectorAll(".history-tooltip-values"),
      ].map((node) => node.textContent),
    }));
  const humidityState = () =>
    selectionPage.evaluate(() => {
      const p = f.panel,
        labels = [...p.$("#tooltip").querySelectorAll(".history-tooltip-values")]
          .filter((node) => !node.hidden)
          .flatMap((node) => [
            ...node.querySelectorAll("div:not([hidden]) > span[data-quantity]"),
          ]),
        values = (quantity) =>
          labels
            .filter((node) => node.dataset.quantity === quantity)
            .map((node) => node.nextElementSibling.textContent),
        button = p.$('[data-action="history-humidity"]'),
        axis = [...p.$("[data-history-axes]").querySelectorAll(".axis-humidity")].at(
          -1,
        );
      return {
        mode: p.historyHumidityMode,
        label: button.textContent,
        pressed: button.getAttribute("aria-pressed"),
        axis: axis.textContent,
        humidity: values("humidity"),
        labels: labels.map((node) => node.textContent),
        curveValues: [
          ...new Set(
            p.historyChart.model.series
              .get(`${[...p.positions][0]}:humidity`)
              .values.filter((point) => point.value != null)
              .map((point) => point.value),
          ),
        ],
        focus: p.shadowRoot.activeElement === button,
        retained: {
          selection: structuredClone(p.historyValueSelection),
          window: [...p.window],
          zoom: p.zoom,
          inspected: p.inspectedHistoryEventId,
          inspection: p.$("#history-event-inspection").textContent,
          heading: p.$(".history-tooltip-time")?.textContent,
          temperature: values("temperature"),
        },
        identity: f.identity(),
      };
    });
  const assertHumidity = async (mode, before) => {
    const after = await humidityState(),
      absolute = mode === "absolute",
      label = absolute ? "Absolute Feuchte (g/m³)" : "Relative Feuchte (%)";
    assert.equal(after.mode, mode);
    assert.equal(after.label, label);
    assert.equal(after.pressed, String(absolute));
    assert.equal(after.axis, label);
    assert.ok(after.humidity.length > 0, "the selected humidity readout is visible");
    assert.ok(
      after.humidity.every((value) => value.endsWith(absolute ? " g/m³" : " %")),
      "the readout uses the selected humidity unit",
    );
    if (absolute) {
      assert.deepEqual(after.curveValues, [settings.fixtures.history.temperature]);
      assert.ok(
        after.humidity.every(
          (value) => value === `${settings.fixtures.history.temperature} g/m³`,
        ),
        "absolute readouts consume the opaque transport display value",
      );
    }
    assert.doesNotMatch(after.labels.join(" "), /\b(?:oben|unten)\b/i);
    assert.equal(after.focus, true, "the rebuilt humidity control retains focus");
    assert.deepEqual(after.retained, before.retained);
    assert.ok(Object.values(after.identity).every(Boolean));
  };
  const selectionGuides = () =>
    selectionPage.evaluate(() => {
      const p = f.panel,
        selection = p.historyValueSelection,
        model = p.historyChart.model;
      return ["start", "end"].map((boundary) => {
        const node = p.$(`[data-history-selection-${boundary}]`),
          time = selection[boundary];
        return {
          time,
          x: Number(node.getAttribute("x1")),
          expectedX: model.x(time),
          visibility: getComputedStyle(node).visibility,
          expectedVisibility:
            (boundary === "start" || selection.kind === "range") &&
            time >= model.start &&
            time <= model.end
              ? "visible"
              : "hidden",
        };
      });
    });
  const assertSelectionCleared = async () => {
    const cleared = await selectionPage.evaluate(() => {
      const p = f.panel,
        nodes = [
          p.$("[data-history-selection]"),
          ...p.$("[data-history-selection]").children,
          p.$("#cursor"),
          p.$("[data-history-snap-halo]"),
        ];
      return {
        selection: p.historyValueSelection,
        preview: p.historyHoverPreview,
        inspected: p.inspectedHistoryEventId,
        tooltipHidden: p.$("#tooltip").hidden,
        inspectionHidden: p.$("#history-event-inspection").hidden,
        visibility: nodes.map((node) => getComputedStyle(node).visibility),
        markers: [...p.shadowRoot.querySelectorAll("[data-history-event-id]")].map(
          (node) => node.getAttribute("aria-pressed"),
        ),
        snaps: nodes.map((node) => node.getAttribute("data-snap")),
        clearDisabled: p.$('[data-action="clear-history-selection"]').disabled,
      };
    });
    assert.equal(cleared.selection, null);
    assert.equal(cleared.preview, null);
    assert.equal(cleared.inspected, null);
    assert.equal(cleared.tooltipHidden, true);
    assert.equal(cleared.inspectionHidden, true);
    assert.ok(
      cleared.visibility.every((value) => value === "hidden"),
      "clearing hides every SVG child, including the range end guide",
    );
    assert.ok(cleared.markers.every((value) => value === "false"));
    assert.ok(cleared.snaps.every((value) => value !== "true"));
    assert.equal(cleared.clearDisabled, true);
  };
  await selectionPage.evaluate(async () => {
    const original = f.panel.api;
    // An opaque backend display value exercises transport consumption only.
    // The Python projection tests own the temperature/RH physics calculation.
    f.panel.api = async (path) => {
      const response = await original(path);
      if (!response.records) return response;
      return {
        ...response,
        records: response.records.map((record) =>
          ["measurement", "source_snapshot"].includes(record.kind)
            ? {
                ...record,
                display: {
                  absolute_humidity: {
                    value: f.inputs.temperature,
                    received_at: record.payload.received_at,
                    valid_until: f.state.now,
                  },
                },
              }
            : record,
        ),
      };
    };
    f.panel.cache.clear();
    f.panel.shown = null;
    f.panel.historySessionId = null;
    const boundary = f.iso(f.origin + f.inputs.detail_event_seconds * 1000),
      infusionAt = f.iso(f.origin + f.inputs.detail_selected_seconds * 1000),
      events = [
        {
          event_id: "door-proof",
          kind: "door_open",
          effective_at: boundary,
          detected_at: infusionAt,
        },
        {
          event_id: "infusion-proof",
          kind: "infusion",
          effective_at: infusionAt,
          detected_at: infusionAt,
        },
      ];
    f.session.timeline.processed.push(...events);
    for (const event of events)
      f.addRecord({
        kind: "detection",
        received_at: infusionAt,
        payload: { event, trace_at: infusionAt, channels: ["upper"] },
      });
    f.state.phase_projection = {
      intervals: [
        {
          phase: "bereit",
          started_at: f.iso(f.origin),
          ended_at: boundary,
          complete: true,
        },
        {
          phase: "saunagang",
          started_at: boundary,
          ended_at: f.state.now,
          complete: false,
        },
      ],
    };
    await f.refresh();
    f.assertClean();
  });
  const initialHumidity = await humidityState();
  assert.equal(initialHumidity.mode, "relative");
  assert.equal(initialHumidity.label, "Relative Feuchte (%)");
  assert.equal(initialHumidity.pressed, "false");
  assert.equal(initialHumidity.axis, "Relative Feuchte (%)");
  const legend = selectionPanel.locator("#history-legends");
  assert.deepEqual(await legend.locator(".history-legend > strong").allTextContents(), [
    "Phasen",
    "Ereignisse",
  ]);
  assert.doesNotMatch(
    await legend.innerText(),
    /\b(?:Phasengrenze|Messwerte|Temperatur|oben|unten)\b/i,
  );
  for (const [phase, shape, label] of [
    ["bereit", "ready", "Bereit"],
    ["saunagang", "gang", "Saunagang"],
  ]) {
    assert.equal(
      await legend.locator(`[data-legend-phase="${phase}"]`).innerText(),
      label,
    );
    assert.equal(
      await legend.locator(`[data-legend-phase="${phase}"] svg rect.${shape}`).count(),
      1,
    );
    assert.equal(
      await selectionPanel.locator(`.history-background rect.${shape}`).count(),
      1,
    );
  }
  for (const [glyph, count] of [
    ["line.history-event-guide", 1],
    ["g.history-event-marker ellipse", 2],
    ["rect.door", 1],
    ["line.infusion", 1],
  ]) {
    assert.equal(await legend.locator(`svg ${glyph}`).count(), count);
    assert.ok(await selectionPanel.locator(`.history-stack ${glyph}`).count());
  }
  await selectionSurface.scrollIntoViewIfNeeded();
  const selectionTimes = await selectionPage.evaluate(() => {
    const p = f.panel,
      model = p.historyChart.model,
      rect = p.$("svg.session-chart").getBoundingClientRect(),
      phase = p.shown.phase_projection.intervals.find(
        (item) => item.phase === "saunagang",
      ),
      phaseStart = Math.max(model.start, Date.parse(phase.started_at)),
      phaseEnd = Math.min(model.end, Date.parse(phase.ended_at)),
      latestEvent = Math.max(
        phaseStart,
        ...f.session.timeline.processed
          .filter((event) =>
            ["door_open", "door_close", "infusion"].includes(event.kind),
          )
          .map((event) => Date.parse(event.effective_at)),
      ),
      timePerPixel =
        (model.end - model.start) /
        (((model.right - model.left) * rect.width) / model.width),
      clearance =
        2 *
        Math.max(
          p.preference("history_phase_snap_radius_px"),
          p.preference("event_marker_size_px") / 2,
        ) *
        timePerPixel,
      usableStart = latestEvent + clearance,
      usableEnd = phaseEnd - clearance;
    if (!((usableEnd - usableStart) / 3 > timePerPixel))
      throw Error(
        "selection fixture needs a visible phase section beyond its events and snap/marker clearance",
      );
    return {
      first: usableStart + (usableEnd - usableStart) / 3,
      second: usableStart + (2 * (usableEnd - usableStart)) / 3,
      latestEvent,
    };
  });
  // Real pointer input establishes the absolute hover time. Keyboard viewport
  // changes then run without another mouse movement or a coordinate replay.
  await selectionSurface.focus();
  const hoverPoint = await chartPoint(null, selectionTimes.first);
  await selectionPage.mouse.move(hoverPoint.x, hoverPoint.y);
  await settleSelection();
  const hoverState = () =>
    selectionPage.evaluate(() => {
      const p = f.panel,
        model = p.historyChart.model,
        preview = p.historyHoverPreview;
      return {
        time: preview?.time,
        x: Number(p.$("#cursor").getAttribute("x1")),
        expectedX: preview && model.x(preview.time),
        visibility: getComputedStyle(p.$("#cursor")).visibility,
        tooltipHidden: p.$("#tooltip").hidden,
        text: p.$("#tooltip").innerText,
      };
    });
  const hovered = await hoverState();
  assert.equal(hovered.visibility, "visible");
  const keyboardSteps = await selectionPage.evaluate(() => {
    const p = f.panel,
      model = p.historyChart.model,
      roomAfterHover = model.end - p.historyHoverPreview.time;
    return {
      zoom:
        Math.ceil(
          Math.log((model.end - model.start) / roomAfterHover) /
            Math.log(p.preference("history_zoom_factor")),
        ) + 1,
      pan: Math.ceil(1 / p.preference("history_keyboard_pan_fraction")) + 1,
    };
  });
  await selectionSurface.press("+");
  await settleSelection();
  const hoverZoomed = await hoverState();
  assert.equal(hoverZoomed.time, hovered.time);
  assert.equal(hoverZoomed.text, hovered.text);
  assert.equal(hoverZoomed.x, hoverZoomed.expectedX);
  assert.notEqual(hoverZoomed.x, hovered.x, "zoom reprojects the absolute hover time");
  await selectionSurface.press("ArrowRight");
  await settleSelection();
  const hoverPanned = await hoverState();
  assert.equal(hoverPanned.time, hovered.time);
  assert.equal(hoverPanned.text, hovered.text);
  assert.equal(hoverPanned.x, hoverPanned.expectedX);
  assert.notEqual(hoverPanned.x, hoverZoomed.x, "pan moves the guide with its time");
  for (let index = 1; index < keyboardSteps.zoom; index++) {
    await selectionSurface.press("+");
    await settleSelection();
  }
  for (let index = 0; index < keyboardSteps.pan; index++) {
    if ((await hoverState()).visibility === "hidden") break;
    await selectionSurface.press("ArrowRight");
    await settleSelection();
  }
  const outsideHover = await hoverState();
  assert.equal(outsideHover.time, hovered.time);
  assert.equal(outsideHover.visibility, "hidden");
  assert.equal(outsideHover.tooltipHidden, true);
  await selectionSurface.press("Home");
  await settleSelection();
  const restoredHover = await hoverState();
  assert.equal(restoredHover.time, hovered.time);
  assert.equal(restoredHover.visibility, "visible");
  assert.equal(restoredHover.x, restoredHover.expectedX);
  results.push({
    case: "absolute-hover-through-keyboard-zoom-and-pan",
    time: hovered.time,
  });
  const firstPoint = await chartPoint(null, selectionTimes.first);
  await selectionPage.mouse.click(firstPoint.x, firstPoint.y);
  await settleSelection();
  const pinned = await selectedValues();
  assert.equal(pinned.selection.kind, "point", "first mouse click pins a point");
  assert.ok(
    pinned.selection.start > selectionTimes.latestEvent,
    "the pinned point lies after the events inspected separately",
  );
  assert.match(
    await selectionTooltip.innerText(),
    /Punkt.*Temperatur:.*°C.*Luftfeuchte:.*%/s,
  );
  assert.equal(
    await selectionPanel.locator("[data-history-selection]").getAttribute("visibility"),
    "visible",
  );
  await selectionPage.mouse.move(0, 0);
  await settleSelection();
  assert.equal(
    await selectionTooltip.isVisible(),
    true,
    "pinned values survive mouseleave",
  );
  assert.deepEqual(await selectedValues(), pinned);
  const pointGuides = await selectionGuides();
  for (const guide of pointGuides) {
    assert.equal(guide.x, guide.expectedX);
    assert.equal(guide.visibility, guide.expectedVisibility);
  }
  const pointHumidity = await humidityState();
  for (const mode of ["absolute", "relative"]) {
    await humidityButton.focus();
    await humidityButton.click();
    await settleSelection();
    await assertHumidity(mode, pointHumidity);
  }
  assert.deepEqual(
    await selectedValues(),
    pinned,
    "returning to relative restores pin values",
  );
  await selectionSurface.scrollIntoViewIfNeeded();
  await settleSelection();
  await selectionPage.evaluate(async () => {
    const projection = structuredClone(f.state.phase_projection);
    await f.tick();
    projection.intervals.at(-1).ended_at = f.state.now;
    f.state.phase_projection = projection;
    f.panel.state = structuredClone(f.state);
    f.panel.showHistoryCache();
    f.panel.drawHistory("status");
  });
  await settleSelection();
  assert.deepEqual(
    await selectedValues(),
    pinned,
    "a live append keeps the pinned time and values",
  );
  assert.deepEqual(
    await selectionGuides(),
    pointGuides,
    "the pinned guide stays at its absolute time",
  );
  const secondPoint = await chartPoint(null, selectionTimes.second);
  await selectionPage.mouse.click(secondPoint.x, secondPoint.y);
  await settleSelection();
  const range = await selectedValues();
  assert.equal(range.selection.kind, "range", "second mouse click completes the range");
  assert.equal(range.selection.start, pinned.selection.start);
  assert.ok(range.selection.end > range.selection.start);
  assert.match(await selectionTooltip.innerText(), /Saunagang.*Beginn.*Ende/s);

  // Inspect an actual event outside the selected interval. Its facts are visible
  // independently; marker activation must not become a third chart selection.
  const infusionMarker = selectionPanel.locator(
    '.history-event-marker[data-history-event-id="infusion-proof"]',
  );
  await infusionMarker.click();
  await settleSelection();
  assert.equal(await infusionMarker.getAttribute("aria-pressed"), "true");
  assert.match(
    await selectionPanel.locator("#history-event-inspection").innerText(),
    /Aufguss/,
  );
  assert.match(await selectionTooltip.innerText(), /Aufguss/);
  assert.deepEqual(
    await selectedValues(),
    range,
    "marker inspection preserves range and boundary values",
  );
  const rangeHumidity = await humidityState();
  await humidityButton.press("Enter");
  await settleSelection();
  await assertHumidity("absolute", rangeHumidity);
  assert.equal(await infusionMarker.getAttribute("aria-pressed"), "true");
  await humidityButton.press("Space");
  await settleSelection();
  await assertHumidity("relative", rangeHumidity);
  assert.deepEqual(
    await selectedValues(),
    range,
    "keyboard return restores range values",
  );
  await infusionMarker.focus();
  await selectionPage.evaluate(async () => {
    const at = f.iso(
        f.origin +
          (f.inputs.detail_selected_seconds + f.inputs.detail_trace_step_seconds) *
            1000,
      ),
      event = {
        event_id: "door-close-proof",
        kind: "door_close",
        effective_at: at,
        detected_at: f.state.now,
      };
    f.session.timeline.processed.push(event);
    f.addRecord({
      kind: "detection",
      received_at: f.state.now,
      payload: { event, trace_at: at, channels: ["upper"] },
    });
    await f.tick();
    f.assertClean();
  });
  await settleSelection();
  assert.equal(
    await infusionMarker.evaluate((node) => node.getRootNode().activeElement === node),
    true,
    "a new event append retains focus on the same logical history marker",
  );
  assert.equal(await infusionMarker.getAttribute("aria-pressed"), "true");
  assert.deepEqual(await selectedValues(), range);
  results.push({
    case: "history-marker-focus-with-new-event-append",
    marker: "infusion-proof",
    appendedEvent: "door-close-proof",
  });
  await selectionPage.mouse.move(0, 0);
  await settleSelection();
  assert.equal(await selectionTooltip.isVisible(), true);

  await readout.scrollIntoViewIfNeeded();
  const readoutBox = await readout.boundingBox(),
    scrollBefore = await selectionPage.evaluate(() => {
      const readout = f.panel.$(".history-readout"),
        history = f.panel.$("#history"),
        outer = f.outer.querySelector("#scroll"),
        target = [readout, history, outer].find(
          (node) => node.scrollHeight > node.clientHeight,
        );
      if (!target) throw Error("selection fixture has no actual scroll path");
      const up = target.scrollTop,
        down = target.scrollHeight - target.clientHeight - target.scrollTop;
      return {
        readout: readout.scrollTop,
        history: history.scrollTop,
        outer: outer.scrollTop,
        wheel: down >= up ? down : -up,
      };
    });
  await selectionPage.mouse.move(
    readoutBox.x + readoutBox.width / 2,
    readoutBox.y + readoutBox.height / 2,
  );
  await selectionPage.mouse.wheel(0, scrollBefore.wheel);
  await selectionPage.waitForFunction(
    (before) =>
      f.panel.$(".history-readout").scrollTop !== before.readout ||
      f.panel.$("#history").scrollTop !== before.history ||
      f.outer.querySelector("#scroll").scrollTop !== before.outer,
    scrollBefore,
    { timeout: settings.browser.timeouts_ms.interaction },
  );
  await selectionPage.waitForFunction(
    (frames) => {
      const position = `${f.panel.$(".history-readout").scrollTop}:${f.panel.$("#history").scrollTop}:${f.outer.querySelector("#scroll").scrollTop}`,
        previous = window.selectionScrollProbe;
      window.selectionScrollProbe = {
        position,
        stable: previous?.position === position ? previous.stable + 1 : 0,
      };
      return window.selectionScrollProbe.stable >= frames;
    },
    settings.browser.settle_frames,
    { timeout: settings.browser.timeouts_ms.interaction, polling: "raf" },
  );
  const scrollAfter = await selectionPage.evaluate(() => ({
    readout: f.panel.$(".history-readout").scrollTop,
    history: f.panel.$("#history").scrollTop,
    outer: f.outer.querySelector("#scroll").scrollTop,
  }));
  await selectionPage.mouse.move(0, 0);
  await settleSelection();
  assert.equal(await selectionTooltip.isVisible(), true);
  assert.deepEqual(
    await selectionPage.evaluate(() => ({
      readout: f.panel.$(".history-readout").scrollTop,
      history: f.panel.$("#history").scrollTop,
      outer: f.outer.querySelector("#scroll").scrollTop,
    })),
    scrollAfter,
  );
  assert.deepEqual(
    await selectedValues(),
    range,
    "scrolling pinned values keeps both endpoints",
  );

  const selectionZoom = selectionPanel.locator('[data-action="zoom-in"]');
  const rangeGuidesBeforeZoom = await selectionGuides();
  await selectionZoom.click();
  await settleSelection();
  assert.deepEqual(
    (await selectedValues()).selection,
    range.selection,
    "zoom preserves both absolute range times",
  );
  const rangeGuidesZoomed = await selectionGuides();
  for (const guide of rangeGuidesZoomed) {
    assert.equal(guide.x, guide.expectedX);
    assert.equal(guide.visibility, guide.expectedVisibility);
  }
  assert.ok(
    rangeGuidesZoomed.some(
      (guide, index) => guide.x !== rangeGuidesBeforeZoom[index].x,
    ),
    "range guides move to the new projection while their absolute times stay fixed",
  );
  await selectionZoom.focus();
  await selectionPage.evaluate(() => {
    window.selectionBeforeAppend = {
      window: [...f.panel.window],
      zoom: f.panel.zoom,
      selection: structuredClone(f.panel.historyValueSelection),
      focus: f.panel.shadowRoot.activeElement,
      outerScroll: f.outer.querySelector("#scroll").scrollTop,
      historyScroll: f.panel.$("#history").scrollTop,
      readoutScroll: f.panel.$(".history-readout").scrollTop,
    };
  });
  await selectionPage.evaluate(() => f.tick());
  await settleSelection();
  const appended = await selectionPage.evaluate(() => {
    const before = selectionBeforeAppend,
      p = f.panel;
    return {
      selection: p.historyValueSelection,
      beforeSelection: before.selection,
      window: p.window,
      beforeWindow: before.window,
      zoom: p.zoom,
      beforeZoom: before.zoom,
      focusRetained: p.shadowRoot.activeElement === before.focus,
      outerScrollRetained:
        f.outer.querySelector("#scroll").scrollTop === before.outerScroll,
      historyScrollRetained: p.$("#history").scrollTop === before.historyScroll,
      readoutScrollRetained: p.$(".history-readout").scrollTop === before.readoutScroll,
      identity: f.identity(),
      tooltipVisible: !p.$("#tooltip").hidden,
    };
  });
  assert.deepEqual(appended.selection, appended.beforeSelection);
  assert.deepEqual(appended.window, appended.beforeWindow);
  assert.equal(appended.zoom, appended.beforeZoom);
  assert.equal(appended.focusRetained, true);
  assert.equal(appended.outerScrollRetained, true);
  assert.equal(appended.historyScrollRetained, true);
  assert.equal(appended.readoutScrollRetained, true);
  assert.equal(appended.tooltipVisible, true);
  assert.ok(Object.values(appended.identity).every(Boolean));
  assert.deepEqual((await selectedValues()).values, range.values);
  assert.deepEqual(
    await selectionGuides(),
    rangeGuidesZoomed,
    "same-session appends keep the range's absolute guide positions",
  );

  await selectionPanel.locator('[data-action="reset-zoom"]').click();
  await settleSelection();
  await selectionSurface.scrollIntoViewIfNeeded();
  const thirdClick = await chartPoint(null, selectionTimes.first);
  await selectionPage.mouse.click(thirdClick.x, thirdClick.y);
  await settleSelection();
  await assertSelectionCleared();
  assert.equal(
    (await selectedValues()).selection,
    null,
    "the third plot click clears completely",
  );

  // The fixture's tick projects one ready interval. Restore the same source
  // transition before inspecting the nearby event and the phase snap feedback.
  await selectionPage.evaluate(() => {
    f.state.phase_projection.intervals = [
      {
        phase: "bereit",
        started_at: f.iso(f.origin),
        ended_at: f.iso(f.origin + f.inputs.detail_event_seconds * 1000),
        complete: true,
      },
      {
        phase: "saunagang",
        started_at: f.iso(f.origin + f.inputs.detail_event_seconds * 1000),
        ended_at: f.state.now,
        complete: false,
      },
    ];
    f.panel.state = structuredClone(f.state);
    f.panel.showHistoryCache();
  });
  // Event inspection without a pin still enables the actual clear control.
  await infusionMarker.click();
  await settleSelection();
  const clearSelection = selectionPanel.locator(
    '[data-action="clear-history-selection"]',
  );
  assert.equal(await clearSelection.isEnabled(), true);
  assert.equal(await infusionMarker.getAttribute("aria-pressed"), "true");
  const eventReadout = await selectionPage.evaluate(() => {
    const p = f.panel,
      at = f.origin + f.inputs.detail_selected_seconds * 1000,
      cursor = p.$("#cursor");
    return {
      selection: p.historyValueSelection,
      inspected: p.inspectedHistoryEventId,
      heading: p.$("#tooltip").firstElementChild.innerText,
      expectedHeading: p.tooltipWhen(at),
      x: Number(cursor.getAttribute("x1")),
      expectedX: p.historyChart.model.x(at),
      visibility: getComputedStyle(cursor).visibility,
      snap: cursor.getAttribute("data-snap"),
      halo: getComputedStyle(p.$("[data-history-snap-halo]")).visibility,
    };
  });
  assert.equal(eventReadout.selection, null);
  assert.equal(eventReadout.inspected, "infusion-proof");
  assert.equal(
    eventReadout.heading,
    eventReadout.expectedHeading,
    "an event near a phase boundary shows its exact event time",
  );
  assert.equal(eventReadout.x, eventReadout.expectedX);
  assert.equal(eventReadout.visibility, "visible");
  assert.equal(eventReadout.snap, "false");
  assert.equal(eventReadout.halo, "hidden");
  await clearSelection.click();
  await settleSelection();
  await assertSelectionCleared();
  await selectionSurface.scrollIntoViewIfNeeded();
  const nextPinPoint = await chartPoint(null, selectionTimes.first);
  await selectionPage.mouse.click(nextPinPoint.x, nextPinPoint.y);
  await settleSelection();
  const nextPin = (await selectedValues()).selection;
  assert.equal(nextPin.kind, "point");
  const snapPoint = await selectionPage.evaluate(() => {
    const p = f.panel,
      model = p.historyChart.model,
      rect = p.$("svg.session-chart").getBoundingClientRect(),
      at = f.origin + f.inputs.detail_event_seconds * 1000;
    return {
      at,
      radius: p.preference("history_phase_snap_radius_px"),
      boundaryX: rect.left + (model.x(at) / model.width) * rect.width,
      x:
        rect.left +
        (model.x(at) / model.width) * rect.width -
        p.preference("history_phase_snap_radius_px") / 2,
      y: rect.top + ((model.top + model.bottom) / 2 / model.height) * rect.height,
    };
  });
  const previewHeading = await selectionTooltip.locator("strong").first().innerText();
  const boundaryValues = () =>
    selectionTooltip
      .locator(".history-tooltip-values > div > span[data-quantity]")
      .evaluateAll((labels) => labels.map((label) => label.parentElement.textContent));
  const valuesBeforeSnap = await boundaryValues();
  assert.equal(
    valuesBeforeSnap.length,
    settings.fixtures.history.positions.length *
      settings.fixtures.history.quantities.length,
    "the comparison includes every measurement row",
  );
  await selectionPage.mouse.move(snapPoint.x, snapPoint.y);
  await settleSelection();
  const snapPreview = await selectionPage.evaluate(() => {
    const p = f.panel,
      hint = p.$(".history-tooltip-snap"),
      cursor = p.$("#cursor"),
      halo = p.$("[data-history-snap-halo]");
    return {
      time: p.historyHoverPreview.time,
      cursorSnap: cursor.getAttribute("data-snap"),
      cursorVisibility: getComputedStyle(cursor).visibility,
      cursorX: Number(cursor.getAttribute("x1")),
      haloVisibility: getComputedStyle(halo).visibility,
      haloX: Number(halo.getAttribute("cx")),
      expectedX: p.historyChart.model.x(p.historyHoverPreview.time),
      hint: hint.innerText,
      expectedHint: `Saunagang · ${p.tooltipWhen(p.historyHoverPreview.time)}`,
      hintVisible: !hint.hidden && getComputedStyle(hint).display !== "none",
    };
  });
  assert.equal(snapPreview.time, snapPoint.at);
  assert.equal(snapPreview.cursorSnap, "true");
  assert.equal(snapPreview.cursorVisibility, "visible");
  assert.equal(snapPreview.haloVisibility, "visible");
  assert.equal(snapPreview.cursorX, snapPreview.expectedX);
  assert.equal(snapPreview.haloX, snapPreview.expectedX);
  assert.equal(snapPreview.hint, snapPreview.expectedHint);
  assert.equal(
    snapPreview.hintVisible,
    true,
    "snap feedback is visible before pointerup",
  );
  assert.deepEqual((await selectedValues()).selection, nextPin);
  assert.equal(
    await selectionTooltip.locator("strong").first().innerText(),
    previewHeading,
  );
  assert.deepEqual(
    await boundaryValues(),
    valuesBeforeSnap,
    "previewing a boundary preserves the pinned values",
  );
  await selectionPage.mouse.move(
    snapPoint.boundaryX - snapPoint.radius * 2,
    snapPoint.y,
  );
  await settleSelection();
  assert.deepEqual(
    await selectionPage.evaluate(() => {
      const p = f.panel,
        hint = p.$(".history-tooltip-snap");
      return {
        snap: p.$("#cursor").getAttribute("data-snap") === "true",
        halo: getComputedStyle(p.$("[data-history-snap-halo]")).visibility,
        hint: !!hint && !hint.hidden,
      };
    }),
    { snap: false, halo: "hidden", hint: false },
    "moving beyond the catalogue radius removes snap feedback",
  );
  await selectionPage.mouse.move(snapPoint.x, snapPoint.y);
  await selectionPage.mouse.down();
  await settleSelection();
  assert.equal(
    await selectionPanel.locator("#cursor").getAttribute("data-snap"),
    "true",
  );
  assert.deepEqual(
    (await selectedValues()).selection,
    nextPin,
    "pointerdown keeps the existing pin until completion",
  );
  await selectionPage.mouse.up();
  await settleSelection();
  const snappedRange = await selectedValues();
  assert.equal(
    snappedRange.selection.kind,
    "range",
    "the second click completes a range at the previewed phase boundary",
  );
  assert.equal(
    snappedRange.selection.start,
    snapPoint.at,
    "nearby click snaps to the real source phase boundary",
  );
  assert.equal(
    await selectionPanel
      .locator("[data-history-selection-start]")
      .getAttribute("data-snap"),
    "true",
  );
  assert.equal(
    await selectionPanel
      .locator("[data-history-selection-start-snap]")
      .evaluate((node) => getComputedStyle(node).visibility),
    "visible",
  );
  await selectionSurface.press("Escape");
  await settleSelection();
  await assertSelectionCleared();
  results.push({
    case: "mouse-pin-range-marker-scroll-append-third-clear-snap-escape",
    identity: appended.identity,
  });

  // A genuine touchscreen tap, followed by genuine browser multitouch on the
  // configured Chromium engine. Other engines do not claim this CDP proof.
  let touchPoint = await chartPoint(2 / 5);
  await selectionPage.touchscreen.tap(touchPoint.x, touchPoint.y);
  await settleSelection();
  assert.equal(
    (await selectedValues()).selection.kind,
    "point",
    "real touch tap pins a point",
  );
  const touchHumidity = await humidityState();
  for (const mode of ["absolute", "relative"]) {
    await humidityButton.focus();
    await humidityButton.scrollIntoViewIfNeeded();
    const bounds = await humidityButton.boundingBox();
    await selectionPage.touchscreen.tap(
      bounds.x + bounds.width / 2,
      bounds.y + bounds.height / 2,
    );
    await settleSelection();
    await assertHumidity(mode, touchHumidity);
  }
  await selectionSurface.scrollIntoViewIfNeeded();
  await settleSelection();
  await selectionSurface.press("Escape");
  await settleSelection();
  let pinchVerified = false;
  if (engine === "chromium") {
    const cdp = await selectionPage.context().newCDPSession(selectionPage),
      left = await chartPoint(2 / 5),
      right = await chartPoint(3 / 5),
      wideLeft = await chartPoint(1 / 4),
      wideRight = await chartPoint(3 / 4),
      widthBefore = await selectionPage.evaluate(
        () => f.panel.window[1] - f.panel.window[0],
      );
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [
        { ...left, id: 1 },
        { ...right, id: 2 },
      ],
    });
    await settleSelection();
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [
        { ...wideLeft, id: 1 },
        { ...wideRight, id: 2 },
      ],
    });
    await settleSelection();
    assert.ok(
      (await selectionPage.evaluate(() => f.panel.window[1] - f.panel.window[0])) <
        widthBefore,
      "real two-finger input zooms the chart",
    );
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchCancel",
      touchPoints: [],
    });
    await settleSelection();
    assert.deepEqual(
      await selectionPage.evaluate(() => ({
        selection: f.panel.historyValueSelection,
        pointers: f.panel.chartPointers.size,
        inputMode: f.panel.historyInputMode,
      })),
      { selection: null, pointers: 0, inputMode: null },
      "pinch cancellation does not create a pin or retain an active gesture",
    );
    await cdp.detach();
    pinchVerified = true;
  }
  touchPoint = await chartPoint(2 / 5);
  await selectionPage.touchscreen.tap(touchPoint.x, touchPoint.y);
  await settleSelection();
  const touchPin = (await selectedValues()).selection;
  assert.equal(
    touchPin.kind,
    "point",
    "touch remains usable after gesture cancellation",
  );
  const mouseAfterTouch = await chartPoint(3 / 5);
  await selectionPage.mouse.click(mouseAfterTouch.x, mouseAfterTouch.y);
  await settleSelection();
  assert.equal(
    (await selectedValues()).selection.kind,
    "range",
    "mouse continues the touch selection on a hybrid surface",
  );
  results.push({
    case: "touch-tap-and-hybrid-selection",
    pinchVerified,
    pinchInput: pinchVerified
      ? "Chromium CDP Input.dispatchTouchEvent with real browser pointer capture and touchCancel"
      : `not exercised on ${engine}; no CDP multitouch coverage claimed`,
  });

  await selectionPage.setViewportSize(settings.browser.viewports.mobile);
  await settleSelection();
  const narrowSelection = await selectedValues();
  await readout.scrollIntoViewIfNeeded();
  assert.equal(await selectionTooltip.isVisible(), true);
  assert.ok(
    await readout.evaluate((node) => node.scrollWidth <= node.clientWidth),
    "narrow value box has no clipped horizontal content",
  );
  const narrowMetrics = await selectionPage.evaluate(() => {
    const box = f.panel.$(".history-readout"),
      value = f.panel.$(
        ".history-tooltip-values > div:not([hidden]) > span:last-child",
      );
    return {
      clientWidth: box.clientWidth,
      scrollWidth: box.scrollWidth,
      clientHeight: box.clientHeight,
      scrollHeight: box.scrollHeight,
      tooltipFontPx: parseFloat(getComputedStyle(f.panel.$("#tooltip")).fontSize),
      valueFontPx: parseFloat(getComputedStyle(value).fontSize),
    };
  });
  assert.equal(
    narrowMetrics.valueFontPx,
    await selectionPage.evaluate(() => f.panel.preference("font_size_large_px")),
    "narrow values retain the catalogue's actual text size",
  );
  for (const action of [
    "reset-zoom",
    "history-to-selection",
    "clear-history-selection",
  ]) {
    const button = selectionPanel.locator(`[data-action="${action}"]`);
    await button.scrollIntoViewIfNeeded();
    const bounds = await button.boundingBox();
    assert.ok(
      bounds &&
        bounds.width > 0 &&
        bounds.x >= 0 &&
        bounds.x + bounds.width <= settings.browser.viewports.mobile.width,
      `${action} remains within the narrow viewport`,
    );
  }
  await selectionPanel.locator('[data-action="history-to-selection"]').click();
  await settleSelection();
  assert.deepEqual((await selectedValues()).selection, narrowSelection.selection);
  const overviewTab = selectionPanel.locator('.main-tabs [data-action="overview"]');
  await overviewTab.click();
  await selectionPanel.locator('.main-tabs [data-action="history"]').click();
  await settleSelection();
  assert.deepEqual(
    (await selectedValues()).selection,
    narrowSelection.selection,
    "narrow main navigation preserves the selected values",
  );
  await selectionPage.screenshot({
    path: path.join(output, `${engine}-selection-mobile.png`),
    fullPage: true,
  });
  await selectionSurface.scrollIntoViewIfNeeded();
  const narrowPoint = await chartPoint(1 / 2);
  await selectionPage.touchscreen.tap(narrowPoint.x, narrowPoint.y);
  await settleSelection();
  assert.equal(
    (await selectedValues()).selection,
    null,
    "a third touch tap clears the range",
  );
  await assertSelectionCleared();
  await selectionPage.touchscreen.tap(narrowPoint.x, narrowPoint.y);
  await settleSelection();
  assert.equal((await selectedValues()).selection.kind, "point");
  assert.equal(await selectionTooltip.isVisible(), true);
  assert.ok(
    await readout.evaluate((node) => node.scrollWidth <= node.clientWidth),
    "narrow pinned point has no clipped horizontal content",
  );
  results.push({
    case: "narrow-selection-values-and-navigation",
    viewport: settings.browser.viewports.mobile,
    values: narrowMetrics,
  });

  // Reuse the fixture transport for a second session, as the existing lifecycle
  // proof does. Selection changes still enter through the real session menu.
  await selectionPage.evaluate(async () => {
    f.close();
    const p = f.panel,
      original = p.api,
      nextState = structuredClone(f.state),
      nextSession = structuredClone(f.session);
    nextSession.ended_at = null;
    nextSession.timeline.session_id = "synthetic-next";
    nextState.session = nextSession;
    nextState.operation_enabled = true;
    p.api = async (path) => {
      if (path.split("?")[0].endsWith("/state")) return nextState;
      if (path.endsWith("/archive"))
        return [
          {
            session_id: "synthetic-next",
            started_at: nextSession.timeline.session_started_at,
            ended_at: null,
          },
          {
            session_id: "synthetic",
            started_at: f.session.timeline.session_started_at,
            ended_at: f.session.ended_at,
          },
        ];
      const response = await original(path);
      if (
        new URL(path, "http://fixture").searchParams.get("session_id") ===
        "synthetic-next"
      )
        response.session = structuredClone(nextSession);
      return response;
    };
    p.sessions = null;
    await f.refresh();
    f.assertClean();
  });
  await settleSelection();
  assert.equal(
    await selectionPage.evaluate(() => f.panel.historyValueSelection),
    null,
    "a new live session clears the previous pin/range",
  );
  assert.equal(
    await selectionPage.evaluate(() => f.panel.inspectedHistoryEventId),
    null,
    "a new live session clears independent event inspection",
  );
  await selectionSurface.scrollIntoViewIfNeeded();
  const newSessionPoint = await chartPoint(1 / 2);
  await selectionPage.touchscreen.tap(newSessionPoint.x, newSessionPoint.y);
  await settleSelection();
  assert.equal((await selectedValues()).selection.kind, "point");
  const archivedLabel = await selectionPanel
    .locator('#session option[value="synthetic"]')
    .textContent();
  await selectionPanel.locator("#session + .sauna-select-trigger").click();
  await selectionPanel
    .locator(".sauna-select-menu")
    .getByRole("option", { name: archivedLabel, exact: true })
    .click();
  await selectionPage.evaluate(async () => {
    await f.panel.historyLoad?.promise;
    await f.settle();
    f.assertClean();
  });
  assert.equal(await selectionPage.evaluate(() => f.panel.selected), "synthetic");
  assert.equal(
    await selectionPage.evaluate(() => f.panel.shown.session.timeline.session_id),
    "synthetic",
  );
  assert.equal(
    await selectionPage.evaluate(() => f.panel.historyValueSelection),
    null,
    "the real archive selector clears the other session's pin",
  );
  assert.equal(
    await selectionPanel.locator("[data-history-selection]").getAttribute("visibility"),
    "hidden",
  );
  results.push({ case: "new-live-session-and-archive-menu-clear-selection" });
  assert.deepEqual(selectionPage.errors, []);
  await selectionPage.close();
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
  const lifecycle = await fixture({
    seconds: settings.fixtures.history.lifecycle_duration_seconds,
  });
  results.push(
    await lifecycle.evaluate(async () => {
      const p = f.panel;
      f.setDelay(f.inputs.lifecycle_response_delay_ms);
      f.setPageSize(f.inputs.page_size);
      p.cache.clear();
      p.shown = null;
      p.historySessionId = null;
      const original = p.api;
      let failSecond = true;
      p.api = async (path) => {
        if (path.includes(`after=${f.inputs.page_size}`) && failSecond) {
          failSecond = false;
          throw Error("synthetic second page");
        }
        return original(path);
      };
      await p.refresh();
      const load = p.historyLoad?.promise;
      await new Promise((resolve) =>
        setTimeout(resolve, testSettings.browser.settle_ms),
      );
      if (p.busy || p.cache.get("synthetic").records.length < f.inputs.page_size)
        throw Error("partial data not usable");
      await load;
      if (p.cache.get("synthetic").after !== f.inputs.page_size)
        throw Error("failed page cursor");
      await p.refresh();
      await p.historyLoad?.promise;
      await f.settle();
      if (
        p.shown.records.length !== f.initialRecordCount ||
        new Set(p.shown.records.map((r) => r.id)).size !== f.initialRecordCount
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
        path.split("?")[0].endsWith("/state")
          ? Promise.resolve(newState)
          : original(path);
      p.selected = "synthetic";
      p.historySelectionGeneration = (p.historySelectionGeneration || 0) + 1;
      await p.refresh();
      await p.historyLoad?.promise;
      await f.settle();
      if (
        !p.shown.session.ended_at ||
        !p.cache.get("synthetic").finalSynced ||
        p.shown.records.length !== f.records.length
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
      p.api = (path) =>
        Promise.resolve(path.split("?")[0].endsWith("/state") ? p.state : []);
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
        partial: f.inputs.page_size,
        complete: f.initialRecordCount,
        closedOld: f.records.length,
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
