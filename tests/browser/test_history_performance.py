"""Chromium cold history load through the real authenticated HA archive API."""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

import test_panel as panel_fixture
from playwright.async_api import expect
from custom_components.ha_sauna import archive as archive_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_performance_fixture import (
    BACKGROUND_RECORD_COUNT, ORIGINAL_RECORD_COUNT, old_page_query, seed_volume,
)
from harness import retain_session

# Performance-fixture contract with ArchiveView's projection=history page size.
# A full final page requires one additional empty page to close pagination.
HISTORY_PAGE_LIMIT = 5000


class HistoryPerformanceTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup and cleanup, without inheriting/collecting BrowserTests' tests.
    # Both setups register addAsyncCleanup; neither defines asyncTearDown.
    with_recorder = True
    set_source = panel_fixture.BrowserTests.set_source
    asyncSetUp = panel_fixture.BrowserTests.asyncSetUp
    cleanup_browser = panel_fixture.BrowserTests.cleanup_browser

    async def test_cold_selection_starts_during_pending_status_and_reports_loading(self):
        identities = []
        for number in range(2):
            self.now = self.base + timedelta(hours=number * 8)
            # Refresh observations after the clock jump before the production
            # start guard checks their validity.
            for position in ("upper", "lower"):
                await self.set_source(f"{position}_temperature", 70 + number)
                await self.set_source(f"{position}_humidity", 20 + number)
            await self.runtime.set_operation(True)
            retain_session(self.runtime)
            identity = self.runtime.session.session_id
            identities.append(identity)
            # Use the production archive writer and real admin projection with
            # different record kinds, including the normal-view marker evidence.
            self.runtime.archive.append("detector_trace", self.now, {
                "at": self.now, "signals": [], "channels": ["upper"],
                "metrics": {"upper": {"door_temperature_slope": -1.0}},
                "conditions": {}, "checks": {}, "holds": {},
            }, identity)
            self.runtime.archive.append("diagnostic", self.now, {"faults": {}}, identity)
            self.now += timedelta(hours=6)
            await self.runtime.set_operation(False)
            self.now += timedelta(minutes=20)
            await self.runtime.tick()
            await self.runtime.archive.flush()
            self.assertIsNone(self.runtime.session)

        await self.panel.evaluate("p => p.refresh(true)")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await expect(self.panel.locator("canvas.history-curves")).to_be_visible(timeout=15000)
        await self.panel.evaluate("async p => { if (p.historyLoad) await p.historyLoad.promise; }")
        self.assertFalse(await self.panel.evaluate("(p, id) => p.cache.has(id)", identities[0]))

        state_started, release_state = asyncio.Event(), asyncio.Event()
        archive_started, release_archive = asyncio.Event(), asyncio.Event()

        async def hold_state(route):
            state_started.set()
            await release_state.wait()
            await route.continue_()

        async def hold_archive(route):
            archive_started.set()
            await release_archive.wait()
            await route.continue_()

        await self.page.route("**/api/ha_sauna/*/state", hold_state)
        await self.page.route(f"**/archive?session_id={identities[0]}&**", hold_archive)
        try:
            await self.panel.evaluate("p => { void p.refresh(true); }")
            await asyncio.wait_for(state_started.wait(), timeout=10)
            # select_option dispatches the native input/change events consumed
            # by the same handler as the custom dropdown; no direct loader call.
            await self.panel.locator("#session").select_option(identities[0], force=True)
            await asyncio.wait_for(archive_started.wait(), timeout=10)
            self.assertFalse(release_state.is_set(), "archive starts independently of status")
            await expect(self.panel.locator("#plots")).not_to_contain_text("Noch keine Sitzungsdaten")
            await expect(self.panel.locator('#history [role="status"]').filter(has_text="Lade").first).to_be_visible()
            release_archive.set()
            await expect(self.panel.locator("canvas.history-curves")).to_be_visible(timeout=15000)
            await self.panel.evaluate("async p => { if (p.historyLoad) await p.historyLoad.promise; }")
            kinds = await self.panel.evaluate("p => [...new Set(p.shown.records.map(r => r.kind))]")
            self.assertIn("measurement", kinds)
            self.assertIn("detector_trace", kinds)
            self.assertIn("diagnostic", kinds)
            self.assertEqual(await self.panel.evaluate("p => p.shown.session.timeline.session_id"), identities[0])
            self.assertTrue(await self.panel.evaluate("(p, id) => p.cache.get(id).finalSynced", identities[0]))
        finally:
            release_state.set()
            release_archive.set()
            await self.page.unroute("**/api/ha_sauna/*/state", hold_state)
            await self.page.unroute(f"**/archive?session_id={identities[0]}&**", hold_archive)
        self.assertEqual(self.errors, [])

    async def test_chromium_cold_archive_before_after(self):
        await self.runtime.set_operation(True)
        retain_session(self.runtime)
        template_id = self.runtime.session.session_id
        self.now = self.base + timedelta(hours=6)
        await self.runtime.set_operation(False)
        self.now += timedelta(minutes=20)
        await self.runtime.tick()
        await self.runtime.archive.flush()
        self.assertIsNone(self.runtime.session)
        stored = await asyncio.to_thread(self.runtime.archive.read, template_id, now=self.now)
        self.assertIsNotNone(stored)
        # Keep the real session model, but remove setup measurements before
        # seeding so neither variant gains extra context from fixture startup.
        await self.runtime.archive.erase(reset=True)
        # Existing integration bindings provide all four temperature/humidity
        # sources. Seed only synthetic records, off HA's event loop.
        sources = await asyncio.to_thread(
            seed_volume, self.runtime.archive, stored["session"], self.base,
        )
        connect = sqlite3.connect
        start = self.base

        class BeforeConnection(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                sql, parameters = old_page_query(sql, parameters, sources, start)
                return super().execute(sql, parameters)

        def before_connect(*args, **kwargs):
            kwargs.setdefault("factory", BeforeConnection)
            return connect(*args, **kwargs)

        measurements = {}
        for mode in ("before", "after"):
            # A document reload destroys the panel's cached records and Path2D
            # objects. Both variants use the same server/database and role.
            await self.page.reload()
            self.panel = self.page.locator("ha-sauna-panel")
            await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            with patch.object(archive_module.sqlite3, "connect", before_connect if mode == "before" else connect):
                await self.panel.evaluate("""(p, expectedRecords) => {
                    performance.clearResourceTimings();
                    performance.setResourceTimingBufferSize(1000);
                    const m = window.historyBenchmark = {
                        renderMilliseconds: 0, curveMilliseconds: 0,
                        renderCalls: 0, curveCalls: 0, longTasks: [],
                        fullDrawAt: null, fullFrameAt: null
                    };
                    m.observer = new PerformanceObserver(list => {
                        for (const entry of list.getEntries())
                            m.longTasks.push({start: entry.startTime, duration: entry.duration});
                    });
                    m.observer.observe({type: 'longtask', buffered: false});
                    const render = p.renderHistory;
                    p.renderHistory = function(...args) {
                        const began = performance.now();
                        try { return render.apply(this, args); }
                        catch (error) { m.error = error.stack || String(error); throw error; }
                        finally {
                            m.renderMilliseconds += performance.now() - began;
                            m.renderCalls++;
                            const curves = this.historyChart?.curves;
                            if (curves && !curves.benchmarkWrapped) {
                                // Creation returns before the initial-render
                                // RAF. The constructor never calls update, so
                                // this hook includes the very first curve draw.
                                curves.benchmarkWrapped = true;
                                const update = curves.update;
                                curves.update = function(...args) {
                                    const began = performance.now();
                                    try { return update.apply(this, args); }
                                    finally {
                                        m.curveMilliseconds += performance.now() - began;
                                        m.curveCalls++;
                                    }
                                };
                            }
                            if (m.fullDrawAt == null && !this.historyLoad &&
                                this.cache.get('volume')?.finalSynced &&
                                this.chartDataIndex?.indexedCount === expectedRecords &&
                                curves?.mainPaths.size > 0) {
                                m.fullDrawAt = performance.now();
                                requestAnimationFrame(() => requestAnimationFrame(() => {
                                    m.fullFrameAt = performance.now();
                                }));
                            }
                        }
                    };
                    p.selected = 'volume';
                    p.historySelectionGeneration = (p.historySelectionGeneration || 0) + 1;
                    p.setPanelView('history');
                    Promise.resolve(p.startHistoryLoad()).then(() => {
                        if (p.messages?.history) m.error = String(p.messages.history);
                        // Loader finalization is synchronous after scheduling
                        // the final archive RAF. Allow chart creation plus its
                        // initial RAF, then fail with state instead of waiting
                        // two minutes when a hook or record count is wrong.
                        requestAnimationFrame(() => requestAnimationFrame(() => {
                            requestAnimationFrame(() => {
                                if (m.fullDrawAt == null) m.error = JSON.stringify({
                                    message: 'loader settled without full curve draw',
                                    records: p.shown?.records.length,
                                    indexed: p.chartDataIndex?.indexedCount,
                                    final: p.cache.get('volume')?.finalSynced,
                                    paths: p.historyChart?.curves.mainPaths.size,
                                    loading: !!p.historyLoad
                                });
                            });
                        }));
                    });
                }""", ORIGINAL_RECORD_COUNT)
                await self.page.wait_for_function(
                    "window.historyBenchmark?.fullFrameAt != null || window.historyBenchmark?.error", timeout=120000,
                )
                self.assertIsNone(await self.page.evaluate("window.historyBenchmark.error || null"))
                result = await self.panel.evaluate("""async p => {
                    const m = window.historyBenchmark;
                    m.observer.disconnect();
                    const pages = performance.getEntriesByType('resource').filter(e =>
                        e.name.includes('/archive?') && e.name.includes('session_id=volume'));
                    const first = Math.min(...pages.map(e => e.startTime));
                    const canvas = p.shadowRoot.querySelector('canvas.history-curves');
                    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                    let paintedPixels = 0;
                    for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) paintedPixels++;
                    const encoded = new TextEncoder().encode(JSON.stringify(p.shown.records));
                    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', encoded))]
                        .map(v => v.toString(16).padStart(2, '0')).join('');
                    return {
                        requestToFullDrawMilliseconds: m.fullDrawAt - first,
                        requestToPaintOpportunityMilliseconds: m.fullFrameAt - first,
                        responseSpanMilliseconds: Math.max(...pages.map(e => e.responseEnd)) - first,
                        renderMilliseconds: m.renderMilliseconds,
                        curveMilliseconds: m.curveMilliseconds,
                        renderCalls: m.renderCalls, curveCalls: m.curveCalls,
                        longTasks: m.longTasks.filter(t => t.start >= first && t.start <= m.fullFrameAt),
                        pages: pages.length,
                        transferBytes: pages.reduce((n,e) => n + e.transferSize, 0),
                        encodedBodyBytes: pages.reduce((n,e) => n + e.encodedBodySize, 0),
                        decodedBodyBytes: pages.reduce((n,e) => n + e.decodedBodySize, 0),
                        records: p.shown.records.length, digest, paintedPixels,
                        indexedRecords: p.chartDataIndex.indexedCount,
                        coldCache: pages[0]?.name.endsWith('after=0')
                    };
                }""")
            self.assertTrue(result["coldCache"])
            self.assertEqual(result["records"], ORIGINAL_RECORD_COUNT)
            self.assertEqual(result["indexedRecords"], ORIGINAL_RECORD_COUNT)
            self.assertEqual(result["pages"], ORIGINAL_RECORD_COUNT // HISTORY_PAGE_LIMIT + 1)
            self.assertGreater(result["paintedPixels"], 100)
            self.assertGreater(result["curveCalls"], 0)
            self.assertGreater(result["decodedBodyBytes"], 1_000_000)
            self.assertGreater(result["requestToFullDrawMilliseconds"], 0)
            measurements[mode] = result
        self.assertEqual(measurements["before"]["digest"], measurements["after"]["digest"])
        self.assertEqual(measurements["before"]["decodedBodyBytes"], measurements["after"]["decodedBodyBytes"])
        self.assertEqual(self.errors, [])
        print("CHROMIUM_HISTORY_COLD_BENCHMARK " + json.dumps({
            "background_records": BACKGROUND_RECORD_COUNT,
            "original_records": ORIGINAL_RECORD_COUNT,
            "environment": "Linux CI Chromium + real HA HTTP/API; synthetic archive, no physical device",
            "measurement": measurements,
        }, sort_keys=True), flush=True)
