"""Chromium inside the real HA frontend; no mock hass object or fake API."""
import asyncio
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import test_device_path as device_tests
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.onboarding import OnboardingStorage
from homeassistant.components.onboarding.const import STEPS
from homeassistant.setup import async_setup_component
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.components.http.config import async_get_and_load_store
from playwright.async_api import async_playwright, expect
from custom_components.ha_sauna.core.timeline import Event, Kind


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    # HA's shell queries recorder/info even on a custom panel. Start the real
    # Recorder before entity platforms; never suppress or stub its WS response.
    with_recorder = True
    set_source = device_tests.DevicePathTests.set_source

    async def asyncSetUp(self):
        await device_tests.DevicePathTests.asyncSetUp(self)
        self.browser = None
        self.playwright = None
        self.page = None
        self.errors = []
        self.console_errors = []
        self.network_errors = []
        self.ws_errors = []
        self.addAsyncCleanup(self.cleanup_browser)
        # This fixture intentionally binds a new ephemeral localhost port.
        # Confirm its working HTTP configuration so HA's own migration dialog
        # does not cover the panel under test.
        http_store = await async_get_and_load_store(self.hass)
        if http_store.pending:
            await http_store.async_promote_pending()
        await OnboardingStorage(self.hass, 4, "onboarding", private=True).async_save({"done": STEPS})
        for component in ("labs", "brands"):
            self.assertTrue(await async_setup_component(self.hass, component, {}))
        self.assertTrue(await async_setup_component(self.hass, "frontend", {}))
        await self.hass.async_block_till_done()
        # The real frontend cannot finish loading without its service catalogue.
        # Surface setup errors here rather than hiding them behind a WS error.
        self.assertTrue(await async_get_all_descriptions(self.hass))
        self.user = await self.hass.auth.async_create_user("Testperson", group_ids=[GROUP_ID_ADMIN])
        self.url = f"http://127.0.0.1:{self.hass.http.server_port}"
        refresh = await self.hass.auth.async_create_refresh_token(self.user, client_id=self.url + "/")
        tokens = {"hassUrl": self.url, "clientId": self.url + "/", "access_token": self.hass.auth.async_create_access_token(refresh),
                  "refresh_token": refresh.token, "expires": (time.time()+1800)*1000, "expires_in": 1800}
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.context = await self.browser.new_context(viewport={"width": 1440, "height": 1080}, color_scheme="dark", accept_downloads=True)
        await self.context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps(tokens)) + ");")
        await self.context.add_init_script("window.testErrors=[];addEventListener('unhandledrejection',e=>window.testErrors.push({code:e.reason?.code,message:e.reason?.message,stack:e.reason?.stack}));")
        self.page = await self.context.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda e: self.console_errors.append(e.text) if e.type == "error" else None)
        self.page.on("requestfailed", lambda r: self.network_errors.append((r.url.split("?")[0], r.failure)))
        self.page.on("response", lambda r: self.network_errors.append((r.url.split("?")[0], r.status)) if r.status >= 400 else None)
        def websocket(socket):
            requests = {}
            def sent(data):
                try:
                    message = json.loads(data)
                    if isinstance(message, dict) and "id" in message:
                        requests[message["id"]] = message.get("type")
                except (ValueError, TypeError):
                    pass
            def received(data):
                try:
                    messages = json.loads(data)
                    for message in messages if isinstance(messages, list) else [messages]:
                        if isinstance(message, dict) and message.get("success") is False:
                            self.ws_errors.append({"request": requests.get(message.get("id")), "error": message.get("error")})
                except (ValueError, TypeError):
                    pass
            socket.on("framereceived", received)
            socket.on("framesent", sent)
        self.page.on("websocket", websocket)
        await self.page.goto(self.url + "/ha-sauna")
        self.panel = self.page.locator("ha-sauna-panel")
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)

    async def cleanup_browser(self):
        try:
            if self.browser and self.page:
                print("BROWSER_ERRORS", self.errors)
                print("BROWSER_CONSOLE", self.console_errors[-20:])
                print("BROWSER_NETWORK", self.network_errors[-20:])
                print("BROWSER_WS_ERRORS", self.ws_errors)
                print("BROWSER_REJECTIONS", await self.page.evaluate("window.testErrors"))
                print("BROWSER_SCRIPTS", await self.page.locator("script[src]").evaluate_all("els=>els.map(e=>e.src)"))
                print("BROWSER_HA_STATE", await self.page.evaluate("()=>{const e=document.querySelector('home-assistant'),h=e?.hass;return h?{...Object.fromEntries(['connected','states','config','services','themes','panels','user'].map(k=>[k,h[k]!=null])),migration:e._databaseMigration}:{element:!!e,defined:!!customElements.get('home-assistant')}}"))
        finally:
            try:
                if self.browser:
                    await self.browser.close()
            finally:
                self.browser = None
                try:
                    if self.playwright:
                        await self.playwright.stop()
                finally:
                    self.playwright = None

    async def emit(self, kind, second):
        self.now = self.base + timedelta(seconds=second)
        await self.runtime.receive(Event(f"browser:{kind}:{second}", self.runtime.session.session_id,
            kind, self.now, self.now))
        await self.hass.async_block_till_done()

    async def test_four_main_tabs_detail_status_and_normal_user_permissions(self):
        tabs = self.panel.locator(".main-tabs")
        for action, section in (("overview", "current"), ("history", "history"),
                                ("details", "details"), ("settings", "settings")):
            await tabs.locator(f'[data-action="{action}"]').click()
            await expect(tabs.locator(f'[data-action="{action}"]')).to_have_attribute(
                "aria-current", "page"
            )
            await expect(self.panel.locator(f"#{section}")).to_be_visible()
        await tabs.locator('[data-action="details"]').click()
        await expect(self.panel.locator('.detail-tabs [data-action="detail"]')).to_have_attribute(
            "aria-current", "page"
        )
        self.assertEqual(await self.panel.locator('#details [data-action="heater:true"]').count(), 0)
        self.assertEqual(await self.panel.locator('#details [data-action="manual-light-overview"]').count(), 0)
        await self.panel.locator('.detail-tabs [data-action="detail-history"]').click()
        await expect(self.panel.locator('#history')).to_be_visible()
        await tabs.locator('[data-action="settings"]').click()
        await tabs.locator('[data-action="details"]').click()
        await expect(self.panel.locator('.detail-tabs [data-action="detail-history"]')).to_have_attribute(
            "aria-current", "page"
        )
        await tabs.locator('[data-action="overview"]').click()
        await self.panel.locator("#current .manual-overrides summary").click()
        await expect(self.panel.locator('#current .manual-overrides [data-action="heater:true"]')).to_be_visible()
        await expect(self.panel.locator('#current .manual-overrides [data-action="manual-light-overview"]')).to_be_visible()
        await expect(self.panel.locator('#current .manual-overrides [data-action="heater:true"]')).to_be_disabled()
        await expect(self.panel.locator('#current .manual-overrides [data-action="manual-light-overview"]')).to_be_disabled()

        user = await self.hass.auth.async_create_user("Normal panel user", group_ids=[GROUP_ID_USER])
        refresh = await self.hass.auth.async_create_refresh_token(user, client_id=self.url + "/")
        tokens = {"hassUrl": self.url, "clientId": self.url + "/",
                  "access_token": self.hass.auth.async_create_access_token(refresh),
                  "refresh_token": refresh.token, "expires": (time.time() + 1800) * 1000,
                  "expires_in": 1800}
        context = await self.browser.new_context(viewport={"width": 390, "height": 844})
        try:
            await context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps(tokens)) + ");")
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(self.url + "/ha-sauna")
            panel = page.locator("ha-sauna-panel")
            await expect(panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            await expect(panel.locator('.main-tabs [data-action="details"]')).to_be_hidden()
            self.assertEqual(await panel.locator('#current .manual-overrides [data-action="heater:true"]').count(), 0)
            self.assertEqual(await panel.locator('#current [data-action="manual-light-overview"]').count(), 0)
            await panel.locator('.main-tabs [data-action="settings"]').click()
            await expect(panel.locator('#program-library [data-program-id]').first).to_be_visible()
            await expect(panel.locator('#button-program')).to_be_visible()
            self.assertEqual(await panel.locator('#parameters').count(), 0)
            self.assertEqual(await panel.locator('[data-action="export"]').count(), 0)
            await panel.locator('.main-tabs [data-action="overview"]').click()
            await panel.locator('#current [data-action="program-mode:constant"]').click()
            await expect(panel.locator('#current .temperature-presets button').first).to_be_enabled()
            await panel.locator('#current [data-action="program-mode:program"]').click()
            first_program = panel.locator('#current .program-named-choice').first
            await expect(first_program).to_be_enabled()
            await first_program.click()
            await expect(first_program).to_have_attribute('aria-pressed', 'true')
            await self.runtime.set_operation(True)
            await self.hass.async_block_till_done()
            await panel.locator('.main-tabs [data-action="history"]').click()
            await expect(panel.locator('#history .history-curves')).to_be_visible(timeout=15000)
            self.assertEqual(errors, [])
        finally:
            await context.close()
        self.assertEqual(self.errors, [])

    async def test_normal_and_admin_views_load_and_navigate_the_same_final_archive(
        self,
    ):
        await self.runtime.set_operation(True)
        identity = self.runtime.session.session_id
        await self.emit(Kind.DOOR_OPEN, 20)
        event = next(
            item
            for item in self.runtime.session.timeline.processed
            if item.kind == Kind.DOOR_OPEN
        )
        self.runtime.archive.append(
            "detector_trace",
            self.now,
            {
                "at": self.now,
                "signals": ["door_open"],
                "channels": ["upper"],
                "metrics": {"upper": {"door_temperature_slope": -1.0}},
                "conditions": {},
                "checks": {},
                "holds": {},
            },
            identity,
        )
        self.runtime.archive.append(
            "detection",
            self.now,
            {
                "event": event,
                "channels": ["upper"],
                "trace_at": self.now,
            },
            identity,
        )
        # Real archive pagination leaves the first page available while the
        # following page is delayed in each fixed permission context.
        for second in range(30, 1030):
            at = self.base + timedelta(seconds=second)
            self.runtime.archive.append(
                "measurement",
                at,
                {
                    "position": "upper",
                    "quantity": "temperature",
                    "value": 72,
                    "received_at": at,
                },
                identity,
            )
        await self.runtime.set_operation(False)
        self.now += timedelta(minutes=20)
        await self.runtime.tick()
        await self.runtime.archive.flush()
        self.assertIsNone(self.runtime.session)
        # Keep the completed archive far from the live status time.
        self.now += timedelta(days=3650)
        user = await self.hass.auth.async_create_user(
            "Normal archive user", group_ids=[GROUP_ID_USER]
        )
        self.assertFalse(user.is_owner)
        self.assertFalse(user.is_admin)
        refresh = await self.hass.auth.async_create_refresh_token(
            user, client_id=self.url + "/"
        )
        tokens = {
            "hassUrl": self.url,
            "clientId": self.url + "/",
            "access_token": self.hass.auth.async_create_access_token(refresh),
            "refresh_token": refresh.token,
            "expires": (time.time() + 1800) * 1000,
            "expires_in": 1800,
        }
        context = await self.browser.new_context(
            viewport={"width": 1440, "height": 1080}
        )
        self.addAsyncCleanup(context.close)
        await context.add_init_script(
            "localStorage.setItem('hassTokens', "
            + json.dumps(json.dumps(tokens))
            + ");"
        )
        page = await context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        await page.goto(self.url + "/ha-sauna")
        normal_panel = page.locator("ha-sauna-panel")
        await expect(
            normal_panel.locator('#current [data-action="operation"]')
        ).to_be_visible(timeout=60000)
        await self.hass.async_block_till_done()
        archive_url = f"**/api/ha_sauna/{self.entry.entry_id}/archive?*"
        for page, panel, admin in (
            (page, normal_panel, False),
            (self.page, self.panel, True),
        ):
            with self.subTest(admin=admin):
                await panel.evaluate("""async p => {
                  while (p.busy) await new Promise(resolve => setTimeout(resolve, 10));
                  clearTimeout(p.timer);
                  await p.refresh();
                }""")
                self.assertEqual(
                    await panel.evaluate("p => p.state.permissions.admin"), admin
                )
                entered, release = asyncio.Event(), asyncio.Event()

                async def delay_archive_page(route):
                    if route.request.url.endswith("after=0"):
                        await route.continue_()
                        return
                    response = await route.fetch()
                    entered.set()
                    await release.wait()
                    await route.fulfill(response=response)

                await page.route(archive_url, delay_archive_page)
                try:
                    await panel.locator('.main-tabs [data-action="history"]').click()
                    await asyncio.wait_for(entered.wait(), 10)
                    await expect(panel.locator("svg.session-chart")).to_be_visible(
                        timeout=15000
                    )
                    self.assertEqual(
                        await panel.evaluate("p => p.historySelectionId()"), identity
                    )
                    self.assertFalse(
                        await panel.evaluate(
                            "p => p.historyCache(p.historySelectionId()).finalSynced"
                        )
                    )
                    chart = await panel.evaluate_handle("p => p.historyChart")
                    await panel.evaluate("""p => {
                      const [a,b] = p.window;
                      p.setHistoryWindow(a, a + (b-a)/2);
                      p.drawHistory();
                    }""")
                    await panel.locator('[data-action="zoom-in"]').click()
                    self.assertEqual(await panel.evaluate("p => p.zoom"), 4)
                    await panel.locator("svg.session-chart").press("ArrowRight")
                    overview = panel.locator("#history-overview [data-history-window]")
                    box = await overview.bounding_box()
                    self.assertIsNotNone(box)
                    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    before_drag = await panel.evaluate("p => [...p.window]")
                    await page.mouse.move(x, y)
                    await page.mouse.down()
                    await page.mouse.move(x + 20, y)
                    await page.mouse.up()
                    self.assertGreater(
                        await panel.evaluate("p => p.window[0]"), before_drag[0]
                    )
                    await panel.locator('[data-action="reset-zoom"]').click()
                    self.assertEqual(await panel.evaluate("p => p.zoom"), 1)
                    await panel.locator('[data-action="zoom-in"]').click()
                    window = await panel.evaluate("p => [...p.window]")
                    session_select = panel.locator("#session")
                    await session_select.click()
                    await panel.evaluate("p => p.refresh()")
                    await expect(session_select).to_be_enabled()
                    await page.keyboard.press("Escape")
                    await session_select.click()
                    await page.keyboard.press("Escape")
                    self.assertTrue(
                        await panel.evaluate("""p => {
                      const [start, end] = p.historyDomain();
                      return p.window[0] >= start && p.window[1] <= end &&
                        end < Date.parse(p.state.now) - 365 * 24 * 3600 * 1000;
                    }""")
                    )
                    release.set()
                    await panel.evaluate(
                        "async p => { if (p.historyLoad) await p.historyLoad.promise; }"
                    )
                finally:
                    release.set()
                    await page.unroute(archive_url, delay_archive_page)
                self.assertTrue(
                    await panel.evaluate(
                        "p => p.historyCache(p.historySelectionId()).finalSynced"
                    )
                )
                chart_box = await panel.locator("svg.session-chart").bounding_box()
                archive_requests = []

                def record_archive(request):
                    if f"/api/ha_sauna/{self.entry.entry_id}/archive" in request.url:
                        archive_requests.append(request.url)

                page.on("request", record_archive)
                try:
                    for _ in range(3):
                        await panel.evaluate("p => p.refresh()")
                    self.assertEqual(archive_requests, [])
                    self.assertEqual(await panel.locator("#history-loading").count(), 0)
                    self.assertEqual(
                        await panel.locator("svg.session-chart").bounding_box(), chart_box
                    )
                finally:
                    page.remove_listener("request", record_archive)
                self.assertTrue(
                    await panel.evaluate(
                        "(p, chart) => p.historyChart === chart", chart
                    )
                )
                self.assertEqual(await panel.evaluate("p => [...p.window]"), window)
                await chart.dispose()
                session_select = panel.locator("#session")
                await session_select.click()
                await page.keyboard.press("Escape")
                await session_select.click()
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                await expect(session_select).to_have_value(identity)
                await session_select.click()
                await page.keyboard.press("Escape")
                await panel.evaluate("""async p => {
                  while (p.busy) await new Promise(resolve => setTimeout(resolve, 10));
                  if (p.historyLoad) await p.historyLoad.promise;
                }""")
                await expect(panel.locator("svg.session-chart")).to_be_visible(
                    timeout=15000
                )
                traces = await panel.evaluate(
                    "p => p.shown.records.filter(r => r.kind === 'detector_trace').length"
                )
                if admin:
                    self.assertGreater(traces, 0)
                    await panel.locator('.main-tabs [data-action="details"]').click()
                    await panel.locator(
                        '.detail-tabs [data-action="diagnostics"]'
                    ).click()
                    await expect(
                        panel.locator(".diagnostic-marker").first
                    ).to_be_visible(timeout=15000)
                else:
                    self.assertEqual(traces, 0)
                    await expect(
                        panel.locator('.main-tabs [data-action="details"]')
                    ).to_be_hidden()
                    self.assertEqual(
                        await panel.locator(".diagnostic-marker").count(), 0
                    )
                    self.assertEqual(
                        await panel.locator('[data-action^="event-row:"]').count(), 0
                    )
                    await expect(panel.locator("#detection-plots")).to_be_empty()
        self.assertEqual(self.errors, [])

    async def test_upper_probe_failure_keeps_lower_readings_and_visible_warning(self):
        await self.set_source("upper_temperature", "unavailable")
        self.now = self.base + timedelta(seconds=31)
        await self.set_source("lower_temperature", 68)
        await self.set_source("lower_humidity", 38)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        await self.panel.evaluate("panel => panel.refresh()")

        current = self.panel.locator("#current")
        await expect(current.locator(".gauges")).to_contain_text("68 °C")
        await expect(current.locator(".gauges")).to_contain_text("38 %")
        await expect(current.locator(".gauges")).not_to_contain_text("Ersatzmessung unten")
        await expect(current.locator('[role="alert"]')).to_contain_text("Temperatur oben")
        self.assertEqual(self.errors, [])

    async def test_empty_history_uses_card_palette_and_clears_overview(self):
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        editor = self.panel.locator('#appearance-settings')
        group = editor.locator('summary').filter(has_text='Grunddarstellung')
        if await group.locator('..').get_attribute('open') is None:
            await group.click()
        await editor.locator('[data-appearance-color="card_background"]').fill('#000000')
        await editor.locator('[data-appearance-color="muted_text"]').fill('#000000')
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        empty = self.panel.locator('#plots .card.empty')
        await expect(empty).to_be_visible()
        await expect(empty).to_have_css('background-color', 'rgb(0, 0, 0)')
        await expect(empty).to_have_css('color', 'rgb(255, 255, 255)')
        await expect(self.panel.locator('#history-overview')).to_be_empty()
        await expect(self.panel.locator('#range')).to_be_empty()

    async def test_instance_switch_waits_for_its_own_status_before_exposing_controls(self):
        second_heater = device_tests.TestHeater()
        second_heater.entity_id = "switch.test_heater_second"
        second_heater._attr_unique_id = "isolated-heater-second"
        second_heater._attr_name = "Second test heater"
        second_heater.respond = False  # No feedback edge on the first sauna's source.
        await self.hass.data["switch"].async_add_entities([second_heater])
        second = await device_tests.create_sauna(self.hass, binding_overrides={
            "heater": second_heater.entity_id,
        }, parameter_overrides={
            "target_temperature_c": 90, "safety_temperature_c": 105,
        })
        await self.page.reload()
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
        await expect(self.panel.locator('#instance')).to_be_visible()
        await self.panel.locator('#instance').select_option(self.entry.entry_id)
        await expect(self.panel.locator('[data-target-arc][role="slider"]')).to_have_attribute("aria-valuenow", "80")
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await expect(self.panel.locator('#parameters')).to_be_visible()
        await self.panel.evaluate("""async p => {
          while (p.busy) await new Promise(resolve => setTimeout(resolve, 10));
          clearTimeout(p.timer);
        }""")
        old_started, new_started = asyncio.Event(), asyncio.Event()
        old_release, new_release = asyncio.Event(), asyncio.Event()
        async def old_status(route):
            response = await route.fetch()
            old_started.set()
            await old_release.wait()
            await route.fulfill(response=response)
        async def new_status(route):
            new_started.set()
            await new_release.wait()
            await route.continue_()
        old_url = f"**/api/ha_sauna/{self.entry.entry_id}/state"
        new_url = f"**/api/ha_sauna/{second.entry_id}/state"
        writes = []
        def record_write(request):
            if f"/api/ha_sauna/{second.entry_id}/" in request.url and request.method == "POST":
                writes.append(request.url)
        self.page.on("request", record_write)
        await self.page.route(old_url, old_status)
        await self.page.route(new_url, new_status)
        try:
            await self.panel.evaluate("p => { p.switchTestPoll = p.refresh(); }")
            await asyncio.wait_for(old_started.wait(), 10)
            await self.panel.locator('#instance').select_option(second.entry_id)
            await expect(self.panel.locator('#settings')).to_contain_text("Lade Saunadaten")
            self.assertEqual(await self.panel.locator('#parameters').count(), 0)
            self.assertEqual(await self.panel.locator('#current [data-action="operation"]').count(), 0)
            await self.panel.evaluate("""async p => {
              await p.action('operation');
              await p.changeTarget(95);
              await p.saveSettings();
              await p.keyTemperatureTarget({key:'ArrowUp', preventDefault(){}});
            }""")
            self.assertEqual(writes, [])
            old_release.set()
            await asyncio.wait_for(new_started.wait(), 10)
            self.assertIsNone(await self.panel.evaluate("p => p.state"))
            await expect(self.panel.locator('#settings')).to_contain_text("Lade Saunadaten")
            new_release.set()
            await expect(self.panel.locator('#parameters')).to_be_visible(timeout=15000)
            await self.panel.locator('.main-tabs [data-action="overview"]').click()
            await expect(self.panel.locator('[data-target-arc][role="slider"]')).to_have_attribute("aria-valuenow", "90")
            self.assertEqual(writes, [])
        finally:
            old_release.set()
            new_release.set()
            await self.page.unroute(old_url, old_status)
            await self.page.unroute(new_url, new_status)
            self.page.remove_listener("request", record_write)
        self.assertEqual(self.errors, [])

    async def test_appearance_preview_validation_persistence_and_display_scales(self):
        artifact_dir = os.environ.get("HA_SAUNA_BROWSER_ARTIFACTS")
        screenshots = (
            Path(artifact_dir) / "appearance"
            if artifact_dir
            else Path(tempfile.mkdtemp(prefix="ha-sauna-appearance-"))
        )
        screenshots.mkdir(parents=True, exist_ok=True)
        print(f"BROWSER_SCREENSHOTS {screenshots}")
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        session_id = self.entry.runtime_data.session.session_id
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await self.page.screenshot(path=str(screenshots / "dark_control_collapsed.png"), full_page=True)
        await self.panel.locator('[data-action="program-toggle"]').click()
        await expect(self.panel.locator("#program-choice-body")).to_be_visible()
        await self.page.screenshot(path=str(screenshots / "dark_control_open.png"), full_page=True)
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await expect(self.panel.locator('canvas.history-curves')).to_be_visible()
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        editor = self.panel.locator("#appearance-settings")
        await expect(editor).to_be_visible()
        for group in ("Bedienung", "Grunddarstellung", "Zustände und Phasen", "Messungen und Ereignisse"):
            await expect(editor.locator("summary").filter(has_text=group)).to_have_count(1)
        for group in ("Grunddarstellung", "Zustände und Phasen", "Messungen und Ereignisse"):
            summary = editor.locator("summary").filter(has_text=group)
            if await summary.locator("..").get_attribute("open") is None:
                await summary.click()
        await editor.locator('[data-appearance-color="card_background"]').fill("#000000")
        temperature = editor.locator('[data-appearance-color="series_temperature"]')
        humidity = editor.locator('[data-appearance-color="series_humidity"]')
        warmup = editor.locator('[data-appearance-color="phase_warmup"]')
        minimum = editor.locator('[data-appearance-scale="temperature.minimum"]')
        await expect(minimum).to_have_value("40")
        await expect(editor.locator('[data-appearance-scale="temperature.maximum"]')).to_have_value("110")
        await expect(editor.locator('[data-appearance-scale="humidity.maximum"]')).to_have_value("60")
        await temperature.fill("#19A6C8")
        await humidity.fill("#9A35C0")
        await warmup.fill("#123456")
        await expect(editor.locator("#appearance-status")).to_contain_text("Vorschau")
        self.assertEqual(await self.panel.evaluate("p=>p.appearanceColor('series_temperature')"), "#19A6C8")
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:temperature'].stroke"), "#19A6C8")
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:humidity'].stroke"), "#9A35C0")
        self.assertEqual(await self.panel.evaluate("p=>p.style.getPropertyValue('--sauna-main-background')"), "#010407")
        await expect(self.panel.locator('#current .dial-temperature path[stroke="#19A6C8"]')).to_have_count(1)
        await expect(self.panel.locator('#current .dial:not(.dial-temperature) path[stroke="#9A35C0"]')).to_have_count(1)
        await expect(self.panel.locator('#history .top-legend span').nth(0).locator('i')).to_have_css('background-color', 'rgb(25, 166, 200)')
        await expect(self.panel.locator('#history .top-legend span').nth(1).locator('i')).to_have_css('background-color', 'rgb(154, 53, 192)')
        self.assertEqual(await self.panel.evaluate("p=>p.style.getPropertyValue('--sauna-color-phase-warmup')"), "#123456")
        await minimum.fill("120")
        await editor.locator('[data-action="appearance-save"]').click()
        await expect(minimum).to_have_value("120")
        await expect(temperature).to_have_value("#19A6C8")
        await expect(editor.locator("#appearance-status")).to_contain_text("Minimum")
        await self.panel.evaluate("p=>p.refresh()")
        await expect(minimum).to_have_value("120")
        await minimum.fill("50")
        appearance_url = f"/api/ha_sauna/{self.entry.entry_id}/appearance"
        async with self.page.expect_response(lambda response: response.url.endswith(appearance_url) and response.request.method == "POST") as saved_wait:
            await editor.locator('[data-action="appearance-save"]').click()
        self.assertEqual((await saved_wait.value).status, 200)
        await expect(editor.locator("#appearance-status")).to_have_text("Darstellung gespeichert")
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        self.assertEqual(self.entry.runtime_data.configuration.appearance["colors"]["series_temperature"], "#19A6C8")
        await self.page.screenshot(path=str(screenshots / "dark_editor_saved.png"), full_page=True)

        await self.page.reload()
        self.panel = self.page.locator("ha-sauna-panel")
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        editor = self.panel.locator("#appearance-settings")
        await editor.locator("summary").filter(has_text="Messungen und Ereignisse").click()
        await expect(editor.locator('[data-appearance-color="series_temperature"]')).to_have_value("#19A6C8")
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value("50")
        await editor.locator('[data-action="appearance-default"]').click()
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value("40")
        await editor.locator('[data-action="appearance-discard"]').click()
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value("50")
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        # The rightmost point of the visible arc is 100 °C at 50–110 °C.
        # Send a real pointer event through the rendered SVG transform.
        arc_point = await self.panel.locator('svg.dial-temperature').evaluate("""svg => {
            const point = svg.createSVGPoint(); point.x = 255; point.y = 130;
            const screen = point.matrixTransform(svg.getScreenCTM());
            return {x: screen.x, y: screen.y};
        }""")
        target_url = f"/api/ha_sauna/{self.entry.entry_id}/temperature"
        async with self.page.expect_response(lambda response: response.url.endswith(target_url) and response.request.method == "POST") as target_wait:
            await self.page.mouse.click(arc_point["x"], arc_point["y"])
        self.assertEqual((await target_wait.value).status, 200)
        await expect(self.panel.locator('[data-target-arc][role="slider"]')).to_have_attribute("aria-valuenow", "100")
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        if not await editor.locator('[data-appearance-scale="temperature.minimum"]').is_visible():
            await editor.locator("summary").filter(has_text="Messungen und Ereignisse").click()
        await editor.locator('[data-appearance-scale="temperature.minimum"]').fill("70")
        await editor.locator('[data-appearance-scale="temperature.maximum"]').fill("80")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        arc = self.panel.locator('[data-target-arc][role="slider"]')
        await expect(arc).to_have_attribute("aria-valuemin", "70")
        await expect(arc).to_have_attribute("aria-valuemax", "80")
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await editor.locator('[data-appearance-scale="temperature.minimum"]').fill("20")
        await editor.locator('[data-appearance-scale="temperature.maximum"]').fill("30")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator('[data-target-arc][role="slider"]')).to_have_count(0)
        await expect(self.panel.locator("#current .scale-hint").filter(has_text="Soll außerhalb der Anzeigeskala")).to_have_count(1)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await editor.locator('[data-action="appearance-discard"]').click()
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        dark_background = await self.panel.evaluate("p=>getComputedStyle(p).backgroundColor")
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.page.screenshot(path=str(screenshots / "dark_mobile_editor.png"), full_page=True)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await self.page.screenshot(path=str(screenshots / "dark_mobile_control_collapsed.png"), full_page=True)
        await self.panel.locator('[data-action="program-toggle"]').click()
        await self.page.screenshot(path=str(screenshots / "dark_mobile_control_open.png"), full_page=True)
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"), 390)

        tokens = await self.page.evaluate("localStorage.getItem('hassTokens')")
        light_context = await self.browser.new_context(viewport={"width": 1440, "height": 1080}, color_scheme="light")
        try:
            await light_context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(tokens) + ");")
            light_page = await light_context.new_page()
            light_errors = []
            light_page.on("pageerror", lambda error: light_errors.append(str(error)))
            await light_page.goto(self.url + "/ha-sauna")
            light_panel = light_page.locator("ha-sauna-panel")
            await expect(light_panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            await expect(light_page.get_by_text("Loading...", exact=True)).to_be_hidden(timeout=30000)
            light_background = await light_panel.evaluate("p=>getComputedStyle(p).backgroundColor")
            self.assertNotEqual(light_background, dark_background, "light screenshot must use a genuinely different HA surface")
            await light_page.screenshot(path=str(screenshots / "light_control_collapsed.png"), full_page=True)
            await light_panel.locator('[data-action="program-toggle"]').click()
            await light_page.screenshot(path=str(screenshots / "light_control_open.png"), full_page=True)
            await light_panel.locator('.main-tabs [data-action="settings"]').click()
            await expect(light_panel.locator("#appearance-settings")).to_be_visible()
            await light_page.screenshot(path=str(screenshots / "light_editor.png"), full_page=True)
            await light_page.set_viewport_size({"width": 390, "height": 844})
            await light_page.screenshot(path=str(screenshots / "light_mobile_editor.png"), full_page=True)
            self.assertLessEqual(await light_panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"), 390)
            self.assertEqual(light_errors, [])
        finally:
            await light_context.close()
        self.assertEqual(self.errors, [])

    async def test_named_individual_id_uses_catalog_requests_and_retains_running_drafts(self):
        identities = ("other", "custom:one", "custom:one:two", "individual")
        programs = [{"id": identity, "name": "Benannt " + identity,
                     "temperature_steps": [76, 83, 90]} for identity in identities]
        await self.panel.evaluate("""(p, programs) => p.api(
          `/${p.entry}/programs`, "POST", {
            programs: [...p.state.configuration.temperature_programs, ...programs],
          })""", programs)
        await self.hass.async_block_till_done()
        await self.panel.evaluate("p => p.refresh()")
        await self.panel.locator('[data-action="program-mode:program"]').click()
        program_url = f"/api/ha_sauna/{self.entry.entry_id}/program"
        for identity in identities:
            choice = self.panel.locator(f'[data-action="program-select:{identity}"]')
            await expect(choice).to_be_enabled()
            async with self.page.expect_response(
                lambda response: response.url.endswith(program_url) and response.request.method == "POST"
            ) as response_wait:
                await choice.click()
            response = await response_wait.value
            self.assertEqual(response.status, 200)
            self.assertEqual(response.request.post_data_json, {"profile": identity})
            self.assertEqual((await response.json())["selected_program_id"], identity)
            await expect(choice).to_have_attribute("aria-pressed", "true")
            self.assertEqual(self.entry.options["selected_program_id"], identity)
            self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, identity)
            self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (76, 83, 90))
        await self.page.reload()
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
        await expect(self.panel.locator('[data-action="program-mode:program"]')).to_have_attribute("aria-pressed", "true")
        named = self.panel.locator('[data-action="program-select:individual"]')
        await expect(named).to_have_attribute("aria-pressed", "true")
        self.assertEqual(await self.panel.locator("#progression-start").count(), 0)

        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('[data-action="program-toggle"]')).to_be_visible()
        session_id = self.entry.runtime_data.session.session_id
        active = self.panel.locator(".program-active-label")
        await expect(active).to_have_text("Aktuell: Benannt individual")
        await self.panel.locator('[data-action="program-toggle"]').click()
        await self.panel.locator('[data-action="program-select:other"]').click()
        await expect(self.panel.locator(".program-pending")).to_contain_text("Benannt other")
        self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, "individual")
        await self.panel.evaluate("p => p.refresh()")
        await expect(self.panel.locator(".program-pending")).to_contain_text("Benannt other")
        await self.panel.locator('[data-action="program-cancel-draft"]').click()
        await expect(active).to_have_text("Aktuell: Benannt individual")

        await self.panel.locator('[data-action="program-toggle"]').click()
        await self.panel.locator('[data-action="program-mode:individual"]').click()
        await expect(self.panel.locator(".program-pending")).to_contain_text("Noch nicht übernommen: Individuell")
        await self.panel.locator('[data-action="program-kind:steps"]').click()
        steps = self.panel.locator("[data-free-step]")
        for index, value in enumerate((80, 86, 92)):
            await steps.nth(index).fill(str(value))
        async with self.page.expect_response(
            lambda response: response.url.endswith(program_url) and response.request.method == "POST"
        ) as response_wait:
            await self.panel.locator('[data-action="program-apply"]').click()
        response = await response_wait.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.request.post_data_json, {"temperature_steps": [80, 86, 92]})
        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)
        self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (80, 86, 92))
        await expect(active).to_have_text("Aktuell: Individuell")
        await self.panel.locator('[data-action="program-mode:constant"]').click()
        async with self.page.expect_response(
            lambda response: response.url.endswith(program_url) and response.request.method == "POST"
        ) as response_wait:
            await self.panel.locator('[data-action="program-apply"]').click()
        response = await response_wait.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.request.post_data_json, {"profile": "constant"})
        self.assertEqual(self.entry.runtime_data.configuration.program_mode, "constant")
        self.assertIsNone(self.entry.runtime_data.configuration.temperature_steps)
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        await expect(active).to_have_text("Aktuell: Konstant")
        self.assertEqual(self.errors, [])

    async def test_running_program_requires_confirmation_and_reports_saved_choice(self):
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await expect(self.panel.locator("#current .control-main .oven-feedback")).to_be_visible()
        await expect(self.panel.locator("#current .control-main .oven-feedback strong")).to_have_text("Ofen an")
        active_program = self.panel.locator('#current [data-action="program-toggle"] .program-active-label')
        await expect(active_program).to_have_text("Aktuell: Individuell")
        overrides = self.panel.locator("#current .manual-overrides")
        await expect(overrides).to_be_visible()
        await expect(overrides).not_to_have_attribute("open", "")
        await overrides.locator("summary").click()
        await expect(overrides).to_have_attribute("open", "")
        await expect(overrides.locator(".override-limit")).to_have_text(
            "Übersteuerung: höchstens 10 Minuten."
        )
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        await self.panel.get_by_role("button", name="Programm ändern").click()
        await expect(self.panel.locator("#program-choice-body")).to_be_visible()
        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)
        await self.panel.locator('[data-action="program-mode:program"]').click()
        choice = self.panel.locator('[data-action="program-select:genusszeit"]')
        await expect(choice).to_be_visible()
        await expect(choice).to_have_attribute("aria-pressed", "false")
        await expect(choice).to_contain_text("Vorgemerkt")
        await expect(choice).to_have_css("box-shadow", "none")
        await expect(choice).to_have_css("outline-style", "none")
        action = self.panel.locator('#program-choice-body [data-action="program-apply"]')
        await expect(action).to_have_text("Programm übernehmen")
        await expect(action).to_be_enabled()
        await expect(self.panel.locator('#current .program-pending')).to_contain_text("Genusszeit")
        await expect(active_program).to_have_text("Aktuell: Individuell")
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator("#program-choice-body")).to_be_visible()
        await expect(active_program).to_have_text("Aktuell: Individuell")
        await expect(overrides).to_have_attribute("open", "")
        await self.panel.locator('[data-action="program-cancel-draft"]').click()
        await expect(action).to_have_count(0)
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await expect(active_program).to_have_text("Aktuell: Individuell")
        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)

        await self.panel.get_by_role("button", name="Programm ändern").click()
        await self.panel.locator('[data-action="program-mode:program"]').click()
        operation_geometry = lambda: self.panel.evaluate("""p => {
          const button = p.$('#current [data-action="operation"]');
          const card = p.$('#current .control-main');
          const a = button.getBoundingClientRect(), b = card.getBoundingClientRect();
          return {x: a.x - b.x, y: a.y - b.y, width: a.width, height: a.height};
        }""")
        operation_box = await operation_geometry()
        entered = asyncio.Event()
        release = asyncio.Event()
        route_finished = asyncio.Event()
        program_url = f"/api/ha_sauna/{self.entry.entry_id}/program"

        async def delay_program(route):
            entered.set()
            await release.wait()
            try:
                await route.continue_()
            finally:
                route_finished.set()

        await self.page.route("**" + program_url, delay_program)
        try:
            async with self.page.expect_response(
                lambda response: response.url.endswith(program_url) and response.request.method == "POST"
            ) as response_wait:
                click = asyncio.create_task(action.click())
                await asyncio.wait_for(entered.wait(), 10)
                await expect(action).to_have_text("Wird übernommen …")
                await expect(action).to_have_class("program-saving program-main")
                await expect(action).to_have_css("border-style", "solid")
                await expect(action).to_be_disabled()
                self.assertEqual(await operation_geometry(), operation_box)
                await expect(self.panel.locator('[data-action="program-cancel-draft"]')).to_be_disabled()
                release.set()
                await click
            saved = await response_wait.value
            self.assertEqual(saved.status, 200)
            self.assertEqual((await saved.json())["selected_program_id"], "genusszeit")
            await expect(action).to_have_text("✓ Übernommen", timeout=15000)
            await expect(action).to_have_class("program-saved program-main")
            self.assertEqual(await operation_geometry(), operation_box)
        finally:
            release.set()
            if entered.is_set():
                await asyncio.wait_for(route_finished.wait(), 10)
            await self.page.unroute("**" + program_url, delay_program)
        self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, "genusszeit")
        await expect(active_program).to_have_text("Aktuell: Genusszeit")
        await expect(choice).to_have_attribute("aria-pressed", "true")
        await expect(choice).not_to_contain_text("Vorgemerkt")
        await expect(choice).to_have_css("background-color", "rgb(229, 138, 85)")
        await self.panel.evaluate("p=>p.refresh()")
        await expect(active_program).to_have_text("Aktuell: Genusszeit")
        await expect(choice).to_have_attribute("aria-pressed", "true")
        await expect(action).to_have_count(0, timeout=5000)
        self.assertEqual(await operation_geometry(), operation_box)
        self.assertEqual(self.errors, [])

    async def test_individual_feedback_preserves_the_entire_open_editor_geometry(self):
        program_url = f"/api/ha_sauna/{self.entry.entry_id}/program"
        for width in (1440, 390):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 1080})
                await self.panel.evaluate(
                    'p => p.api(`/${p.entry}/program`, "POST", {profile: "constant"})'
                )
                await self.page.reload()
                choice = self.panel.locator('[data-action="program-mode:individual"]')
                await expect(choice).to_be_visible(timeout=60000)
                entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

                async def delay_program(route):
                    response = await route.fetch()
                    entered.set()
                    await release.wait()
                    try:
                        await route.fulfill(response=response)
                    finally:
                        finished.set()

                # Coordinates relative to the panel exclude browser scrolling;
                # each existing control and the complete row still has to stay put.
                geometry = lambda: self.panel.evaluate("""p => {
                  const origin = p.getBoundingClientRect();
                  return ['.program-types', '.program-types button:nth-child(1)',
                    '.program-types button:nth-child(2)', '.program-types button:nth-child(3)',
                    '.program-form', '.manual-overrides', '.gauges', '[data-action="operation"]']
                    .map(selector => {
                      const r = p.$('#current ' + selector).getBoundingClientRect();
                      return {selector, x:r.x-origin.x, y:r.y-origin.y, width:r.width, height:r.height};
                    });
                }""")
                await self.page.route("**" + program_url, delay_program)
                try:
                    await choice.click()
                    await asyncio.wait_for(entered.wait(), 10)
                    await expect(self.panel.locator("#progression-end")).to_be_visible()
                    await expect(choice).to_contain_text("Wird übernommen …")
                    saving = await geometry()
                    release.set()
                    await expect(choice).to_contain_text("✓ Übernommen")
                    saved = await geometry()
                    self.assertEqual(saved, saving)
                    await expect(choice).not_to_contain_text("✓ Übernommen", timeout=5000)
                    self.assertEqual(await geometry(), saving)
                    await self.panel.evaluate("p => p.refresh()")
                    self.assertEqual(await geometry(), saving)
                    self.assertEqual(self.entry.runtime_data.configuration.program_mode, "progressive")
                finally:
                    release.set()
                    if entered.is_set():
                        await asyncio.wait_for(finished.wait(), 10)
                    await self.page.unroute("**" + program_url, delay_program)
        self.assertEqual(self.errors, [])

    async def test_individual_kind_switch_persists_only_at_the_required_confirmation(self):
        program_url = f"/api/ha_sauna/{self.entry.entry_id}/program"
        for running in (False, True):
            with self.subTest(running=running):
                await self.panel.evaluate('''p => p.api(`/${p.entry}/program`, "POST",
                  {temperature_steps: [70, 88, 90]})''')
                await self.page.reload()
                await expect(self.panel.locator("[data-free-step]").nth(1)).to_have_value("88", timeout=60000)
                if running:
                    await self.panel.locator('#current [data-action="operation"]').click()
                    await expect(self.panel.locator('[data-action="program-toggle"]')).to_be_visible()
                    await self.panel.locator('[data-action="program-toggle"]').click()
                writes = []

                def record_program(request):
                    if request.method == "POST" and request.url.endswith(("/program", "/temperature")):
                        writes.append(request.post_data_json)

                self.page.on("request", record_program)
                try:
                    if not running:
                        count = self.panel.locator("#free-step-count")
                        too_many = str(int(await count.get_attribute("max")) + 1)
                        for invalid in ("", too_many):
                            await count.fill(invalid)
                            await count.press("Enter")
                            await expect(self.panel.locator("#message")).not_to_be_empty()
                            await count.press("Tab")
                            await self.panel.evaluate("p => p.refresh()")
                            await expect(count).to_have_value(invalid)
                            await expect(self.panel.locator("#message")).not_to_be_empty()
                            self.assertEqual(writes, [])
                            self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (70, 88, 90))
                        for requested_count, expected_steps in ((4, [70, 88, 90, 90]), (3, [70, 88, 90])):
                            async with self.page.expect_response(
                                lambda response: response.url.endswith(program_url)
                                and response.request.method == "POST"
                            ) as count_saved:
                                await count.fill(str(requested_count))
                                await count.press("Enter")
                            self.assertTrue((await count_saved.value).ok)
                            await self.panel.evaluate(
                                "async p => { while (p.programRequest) await new Promise(r => setTimeout(r, 10)); }"
                            )
                            await count.press("Tab")
                            await self.panel.evaluate("p => p.refresh()")
                            self.assertEqual(writes, [{"temperature_steps": expected_steps}])
                            self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, tuple(expected_steps))
                            await expect(self.panel.locator("[data-free-step]")).to_have_count(requested_count)
                            await expect(self.panel.locator("#message")).to_be_empty()
                            writes.clear()
                    if running:
                        await self.panel.locator('[data-action="program-kind:even"]').click()
                        await self.panel.evaluate("p => p.refresh()")
                        self.assertEqual(writes, [])
                        self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (70, 88, 90))
                        await expect(self.panel.locator('[data-action="program-apply"]')).to_be_enabled()
                    async with self.page.expect_response(
                        lambda response: response.url.endswith(program_url)
                        and response.request.method == "POST", timeout=5000
                    ) as saved:
                        await self.panel.locator(
                            '[data-action="program-apply"]' if running else '[data-action="program-kind:even"]'
                        ).click()
                    response = await saved.value
                    self.assertTrue(response.ok)
                    self.assertEqual(writes, [{"target_temperature_c": 70, "final_temperature_c": 90, "temperature_gangs": 3}])
                    configuration = self.entry.runtime_data.configuration
                    self.assertIsNone(configuration.temperature_steps)
                    self.assertEqual(configuration.parameters.values["target_temperature_c"], 70)
                    self.assertEqual(configuration.parameters.values["final_temperature_c"], 90)
                    self.assertEqual(configuration.parameters.values["temperature_gangs"], 3)
                    await self.panel.locator('[data-action="program-info:free"]').click()
                    await expect(self.panel.locator('.program-info-popup')).to_contain_text("70 → 80 → 90 °C")
                finally:
                    self.page.remove_listener("request", record_program)
        self.assertEqual(self.errors, [])

    async def test_individual_autosave_preserves_next_focus_and_newer_input(self):
        for navigation in ("click", "Tab", "failure"):
            with self.subTest(navigation=navigation):
                await self.panel.evaluate('''p => p.api(`/${p.entry}/program`, "POST",
                  {target_temperature_c: 70, final_temperature_c: 90, temperature_gangs: 3})''')
                await self.page.reload()
                end = self.panel.locator("#progression-end")
                gangs = self.panel.locator("#progression-gangs")
                start = self.panel.locator("#progression-start")
                await expect(end).to_have_value("90", timeout=60000)
                entered = [asyncio.Event(), asyncio.Event()]
                release = [asyncio.Event(), asyncio.Event()]
                finished = [asyncio.Event(), asyncio.Event()]
                writes = []
                temperature_url = f"/api/ha_sauna/{self.entry.entry_id}/temperature"

                async def delayed_temperature(route):
                    index = len(writes)
                    writes.append(route.request.post_data_json)
                    if index >= 2:
                        await route.continue_()
                        return
                    response = None if navigation == "failure" else await route.fetch()
                    entered[index].set()
                    await asyncio.sleep(0.5)
                    await release[index].wait()
                    try:
                        if navigation == "failure":
                            await route.fulfill(status=500, content_type="application/json",
                                                body='{"message":"Absichtlicher Speicherfehler"}')
                        else:
                            await route.fulfill(response=response)
                    finally:
                        finished[index].set()

                await self.page.route("**" + temperature_url, delayed_temperature)
                try:
                    await end.fill("91")
                    if navigation == "Tab":
                        await end.press("Tab")
                        await self.page.keyboard.press("Tab")
                    else:
                        await gangs.click()
                    await asyncio.wait_for(entered[0].wait(), 10)
                    await expect(gangs).to_be_focused()
                    await expect(gangs).to_be_enabled()
                    if navigation == "failure":
                        release[0].set()
                        await expect(self.panel.locator("#message")).not_to_be_empty()
                        await expect(end).to_have_value("91")
                        await expect(gangs).to_be_focused()
                        for _ in range(3):
                            await self.panel.evaluate("p => p.refresh()")
                        await self.page.wait_for_timeout(2100)
                        self.assertEqual(writes, [{"final_temperature_c": 91}])
                        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"], 90)
                        await expect(end).to_have_value("91")
                        continue
                    await gangs.fill("4")
                    await start.click()
                    await start.fill("7")
                    self.assertEqual(writes, [{"final_temperature_c": 91}])
                    release[0].set()
                    await asyncio.wait_for(entered[1].wait(), 10)
                    await expect(start).to_be_focused()
                    await expect(start).to_have_value("7")
                    release[1].set()
                    await asyncio.wait_for(finished[1].wait(), 10)
                    await self.panel.evaluate(
                        "async p => { while (p.programRequest) await new Promise(r => setTimeout(r, 10)); }"
                    )
                    await self.panel.evaluate("p => p.refresh()")
                    await expect(start).to_be_focused()
                    await expect(start).to_have_value("7")
                    await expect(gangs).to_have_value("4")
                    self.assertEqual(writes, [{"final_temperature_c": 91}, {"temperature_gangs": 4}])
                    self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 70)
                    self.assertEqual(self.entry.options["parameters"]["final_temperature_c"], 91)
                    self.assertEqual(self.entry.options["parameters"]["temperature_gangs"], 4)
                    completed_writes = []

                    def record_completed_start(request):
                        if request.method == "POST" and request.url.endswith("/program"):
                            completed_writes.append(request.post_data_json)

                    self.page.on("request", record_completed_start)
                    try:
                        async with self.page.expect_response(
                            lambda response: response.url.endswith("/program")
                            and response.request.method == "POST"
                        ) as completed:
                            await start.fill("72")
                            await start.press("Tab")
                        self.assertTrue((await completed.value).ok)
                        await self.panel.evaluate(
                            "async p => { while (p.programRequest) await new Promise(r => setTimeout(r, 10)); }"
                        )
                        await self.panel.evaluate("p => p.refresh()")
                        self.assertEqual(completed_writes, [{"target_temperature_c": 72,
                                                           "final_temperature_c": 91,
                                                           "temperature_gangs": 4}])
                        self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 72)
                        await expect(start).to_have_value("72")
                    finally:
                        self.page.remove_listener("request", record_completed_start)
                finally:
                    for event in release:
                        event.set()
                    for index, event in enumerate(entered):
                        if event.is_set():
                            await asyncio.wait_for(finished[index].wait(), 10)
                    await self.page.unroute("**" + temperature_url, delayed_temperature)
        self.assertEqual(self.errors, [])

    async def test_automatic_light_presets_are_disabled_without_running_operation(self):
        await self.panel.locator("#current .manual-overrides summary").click()
        presets = self.panel.locator('#current .manual-overrides [data-action^="light:"]')
        self.assertEqual(await presets.count(), 4)
        for index in range(await presets.count()):
            with self.subTest(preset=index):
                await expect(presets.nth(index)).to_be_disabled()
        await expect(self.panel.locator("#manual-light-value-overview")).to_be_disabled()
        await expect(self.panel.locator('[data-action="manual-light-overview"]')).to_be_disabled()
        self.assertIsNone(self.runtime.session)
        self.assertFalse(await self.panel.evaluate("p => p.state.operation_enabled"))

    async def test_normal_user_keeps_simple_light_controls_in_running_automatic_mode(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        user = await self.hass.auth.async_create_user("Normal light user", group_ids=[GROUP_ID_USER])
        self.assertFalse(user.is_admin)
        refresh = await self.hass.auth.async_create_refresh_token(user, client_id=self.url + "/")
        tokens = {"hassUrl": self.url, "clientId": self.url + "/",
                  "access_token": self.hass.auth.async_create_access_token(refresh),
                  "refresh_token": refresh.token, "expires": (time.time() + 1800) * 1000,
                  "expires_in": 1800}
        context = await self.browser.new_context(viewport={"width": 390, "height": 844})
        try:
            await context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps(tokens)) + ");")
            page = await context.new_page()
            await page.goto(self.url + "/ha-sauna")
            panel = page.locator("ha-sauna-panel")
            await expect(panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            self.assertEqual(await panel.evaluate("p => [p.state.permissions.admin, p.state.permissions.light]"), [False, True])
            presets = panel.locator('#current [data-action^="light:"]')
            self.assertEqual(await presets.evaluate_all("buttons => buttons.map(b => b.dataset.action)"),
                             ["light:auto", "light:false", "light:true"])
            for index in range(3):
                await expect(presets.nth(index)).to_be_enabled()
            self.assertEqual(await panel.locator(".manual-overrides").count(), 0)
            self.assertEqual(await panel.locator('[data-action^="heater:"]').count(), 0)
            self.assertEqual(await panel.locator("#manual-light-value-overview").count(), 0)
            async with page.expect_response(lambda response: response.url.endswith("/light")
                                            and response.request.method == "POST") as saved:
                await panel.locator('[data-action="light:false"]').click()
            self.assertTrue((await saved.value).ok)
            await expect(panel.locator('[data-action="light:false"]')).to_have_attribute("aria-pressed", "true")
            await self.runtime.set_operation(False)
            await panel.evaluate("p => p.refresh()")
            for index in range(3):
                await expect(presets.nth(index)).to_be_disabled()
        finally:
            await context.close()
        self.assertEqual(self.errors, [])

    async def test_return_from_hidden_discovers_a_whole_finished_session(self):
        await self.runtime.set_operation(True)
        old_id = self.runtime.session.session_id
        await self.runtime.set_operation(False)
        self.now += timedelta(minutes=20)
        await self.runtime.tick()
        await self.runtime.archive.flush()
        self.assertIsNone(self.runtime.session)
        await self.panel.evaluate("p => p.refresh()")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await expect(self.panel.locator("svg.session-chart")).to_be_visible(timeout=15000)
        await self.panel.evaluate("async p => { if (p.historyLoad) await p.historyLoad.promise; }")
        self.assertEqual(await self.panel.evaluate("p => p.historySelectionId()"), old_id)
        old_cache = await self.panel.evaluate_handle("(p, id) => p.cache.get(id)", old_id)
        old_records = await self.panel.evaluate("(p, id) => JSON.stringify(p.cache.get(id).records)", old_id)
        self.assertTrue(await self.panel.evaluate("(p, id) => p.cache.get(id).finalSynced", old_id))
        # Only visibility is simulated; sessions, status and archive responses
        # come from the real runtime and authenticated HA endpoints.
        await self.page.evaluate("""() => {
          Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        await self.panel.evaluate("async p => { while (p.busy) await new Promise(r => setTimeout(r, 10)); }")
        await self.set_source("upper_temperature", 71)
        await self.set_source("lower_temperature", 65)
        await self.runtime.set_operation(True)
        new_id = self.runtime.session.session_id
        self.assertNotEqual(new_id, old_id)
        await self.runtime.set_operation(False)
        self.now += timedelta(minutes=20)
        await self.runtime.tick()
        await self.runtime.archive.flush()
        self.assertIsNone(self.runtime.session)
        requests = []

        def record_archive(request):
            if f"/api/ha_sauna/{self.entry.entry_id}/archive" in request.url:
                requests.append(request.url)

        self.page.on("request", record_archive)
        try:
            await self.page.evaluate("""() => {
              delete document.hidden;
              document.dispatchEvent(new Event('visibilitychange'));
            }""")
            await expect(self.panel.locator(f'#session option[value="{new_id}"]')).to_have_count(1, timeout=15000)
            await self.panel.evaluate("async p => { if (p.historyLoad) await p.historyLoad.promise; }")
            self.assertEqual(await self.panel.evaluate("p => p.historySelectionId()"), new_id)
            self.assertEqual(await self.panel.evaluate("p => p.shown.session.timeline.session_id"), new_id)
            self.assertEqual(sum(url.endswith("/archive") for url in requests), 1)
            self.assertTrue(await self.panel.evaluate("(p, old) => p.cache.get(old.session.timeline.session_id) === old", old_cache))
            self.assertEqual(await self.panel.evaluate("(p, id) => JSON.stringify(p.cache.get(id).records)", old_id), old_records)
            requests.clear()
            for _ in range(3):
                await self.panel.evaluate("p => p.refresh()")
            self.assertEqual(requests, [])
            await self.panel.locator("#session").select_option(old_id)
            await expect(self.panel.locator("svg.session-chart")).to_be_visible()
            self.assertEqual(await self.panel.evaluate("p => p.historySelectionId()"), old_id)
            self.assertEqual(requests, [])
        finally:
            self.page.remove_listener("request", record_archive)
            await old_cache.dispose()
        self.assertEqual(self.errors, [])

    async def test_catalog_editor_sorting_and_persisted_program_ids(self):
        await self.panel.locator('[data-action="program-mode:program"]').click()
        await expect(self.panel.locator('[data-action="program-select:genusszeit"]')).to_have_attribute(
            "aria-pressed", "true", timeout=15000
        )
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.panel.locator('#button-program').select_option('gipfelstuermer')
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options['button_program'], 'gipfelstuermer')
        rows = self.panel.locator('#program-library [data-program-id]')

        async def order():
            return await rows.evaluate_all('elements => elements.map(row => row.dataset.programId)')

        before = await order()
        await rows.first.locator('[data-action^="program-edit:"]').click()
        await expect(rows.first.locator('.program-form')).to_be_visible()
        self.assertEqual(await self.panel.locator('#program-library .program-form').count(), 1)
        await rows.first.locator('[data-program-field="name"]').fill('Nur im Entwurf')
        await rows.first.locator('[data-action="program-cancel"]').click()
        await expect(rows.first.locator('.program-form')).to_have_count(0)
        self.assertEqual(await order(), before)

        handle = rows.first.locator('[data-program-drag]')
        await handle.focus()
        await handle.press('ArrowDown')
        self.assertEqual((await order())[0], before[1])
        await self.panel.locator('[data-action="program-discard"]').click()
        self.assertEqual(await order(), before)

        handle = rows.first.locator('[data-program-drag]')
        target = rows.nth(1)
        source_box, target_box = await handle.bounding_box(), await target.bounding_box()
        await self.page.mouse.move(source_box['x'] + source_box['width'] / 2,
                                   source_box['y'] + source_box['height'] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(target_box['x'] + target_box['width'] / 2,
                                   target_box['y'] + target_box['height'] * .85, steps=8)
        await self.page.mouse.up()
        mouse_order = await order()
        self.assertEqual(mouse_order[0], before[1])
        await self.panel.locator('[data-action="program-save"]').click()
        await self.hass.async_block_till_done()
        self.assertEqual([program['id'] for program in self.entry.options['temperature_programs']], mouse_order)
        await self.page.reload()
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        self.assertEqual(await order(), mouse_order)
        self.assertEqual(self.entry.options['selected_program_id'], 'genusszeit')
        self.assertEqual(self.entry.options['button_program'], 'gipfelstuermer')

        tokens = await self.page.evaluate("localStorage.getItem('hassTokens')")
        context = await self.browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        try:
            await context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(tokens) + ");")
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(self.url + '/ha-sauna')
            panel = page.locator('ha-sauna-panel')
            await expect(panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            await panel.locator('.main-tabs [data-action="settings"]').click()
            mobile_rows = panel.locator('#program-library [data-program-id]')
            touch_before = await mobile_rows.evaluate_all('elements => elements.map(row => row.dataset.programId)')
            touch_handle = mobile_rows.first.locator('[data-program-drag]')
            await touch_handle.scroll_into_view_if_needed()
            start, destination = await touch_handle.bounding_box(), await mobile_rows.nth(1).bounding_box()
            x = start['x'] + start['width'] / 2
            y_start = start['y'] + start['height'] / 2
            y_end = destination['y'] + destination['height'] * .85
            cdp = await context.new_cdp_session(page)
            await cdp.send('Input.dispatchTouchEvent', {
                'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y_start, 'id': 1}]
            })
            for step in range(1, 10):
                await cdp.send('Input.dispatchTouchEvent', {
                    'type': 'touchMove',
                    'touchPoints': [{'x': x, 'y': y_start + (y_end - y_start) * step / 9, 'id': 1}]
                })
                await page.wait_for_timeout(20)
            await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
            touch_order = await mobile_rows.evaluate_all('elements => elements.map(row => row.dataset.programId)')
            self.assertEqual(touch_order[0], touch_before[1])
            await panel.locator('[data-action="program-save"]').click()
            await self.hass.async_block_till_done()
            await page.reload()
            await panel.locator('.main-tabs [data-action="settings"]').click()
            self.assertEqual(await mobile_rows.evaluate_all('elements => elements.map(row => row.dataset.programId)'), touch_order)
            self.assertEqual(self.entry.options['selected_program_id'], 'genusszeit')
            self.assertEqual(self.entry.options['button_program'], 'gipfelstuermer')
            self.assertLessEqual(await panel.evaluate("p => p.shadowRoot.querySelector('main').scrollWidth"), 390)
            self.assertEqual(errors, [])
        finally:
            await context.close()
        self.assertEqual(self.errors, [])

    async def test_manual_oven_cooling_end_is_only_in_automatic_admin_control(self):
        await device_tests.DevicePathTests.prepare_gang_after_run(self)
        self.now += timedelta(seconds=10)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        end_cooling = self.panel.locator('#current').get_by_role(
            "button", name="Ofenkühlung jetzt beenden", exact=True
        )
        await expect(end_cooling).to_be_visible()
        await self.panel.locator('[data-action="details"]').click()
        self.assertEqual(await self.panel.locator('#details [data-action^="end-phase:"]').count(), 0)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        self.assertFalse(self.heater.is_on)
        await end_cooling.click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        self.assertEqual(await self.panel.locator('#current [data-action^="end-phase:"]').count(), 0)
        await self.hass.async_block_till_done()
        self.assertTrue(self.heater.is_on)
        self.assertIsNone(self.runtime.session.after_run)
        self.assertIsNone(self.runtime.session.cooling)
        self.assertEqual(self.runtime.session.timeline.gang_count,1)
        self.assertEqual(self.errors,[])

    async def test_live_history_assignment_diagnostics_and_authenticated_download(self):
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        identity = self.runtime.session.session_id
        for second in range(15):
            self.now = self.base + timedelta(seconds=second)
            for pos in ("upper", "lower"):
                await self.set_source(pos + "_temperature", (72 if pos=="upper" else 62)+second/5)
                await self.set_source(pos + "_humidity", 15+second/10)
            await self.runtime.tick()
        # Browser verifies historical backassignment. The separate HA device-path
        # test proves the detector itself produces these signals from HA inputs.
        await self.emit(Kind.DOOR_OPEN, 20)
        await self.emit(Kind.DOOR_CLOSE, 25)
        await self.emit(Kind.PERSON_STRONG, 35)
        # The emitted timeline events are deliberate historical assignments;
        # attach their producing grid point after the backdated door onset.
        door_event = next(event for event in self.runtime.session.timeline.processed
                          if event.kind == Kind.DOOR_OPEN)
        trace_at = door_event.effective_at + timedelta(seconds=5)
        self.runtime.archive.append('detector_trace', trace_at, {
            'at': trace_at,
            'signals': ['door_open'],
            'channels': ['upper'],
            'metrics': {'upper': {'door_temperature_slope': -1.0},
                        'lower': {'door_temperature_slope': -0.8}},
            'conditions': {},
            'checks': {},
            'holds': {},
            'door_open': True,
            'ventilation_context': False,
        }, identity)
        self.runtime.archive.append('detection', trace_at, {
            'event': door_event, 'channels': ['upper'], 'trace_at': trace_at,
        }, identity)
        await self.runtime.archive.flush()
        await self.panel.locator('[data-action="history"]').click()
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Vorläufig", timeout=15000)
        gang_id = await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id")
        start = await self.panel.locator('[data-gang-id]').get_attribute("data-start")
        self.assertEqual(start, (self.base+timedelta(seconds=25)).isoformat())
        await self.emit(Kind.INFUSION, 40)
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Bestätigt", timeout=15000)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id"), gang_id)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-start"), start)
        # The curves render in canvas; the SVG retains accessible axes and
        # interactive markers. Height comparison is an explicit user choice.
        await expect(self.panel.locator('canvas.history-curves[role="img"]')).to_be_visible()
        self.assertFalse(await self.panel.evaluate("p=>p.historyDetail"))
        await self.panel.locator('[data-action="history-detail"]').click()
        await expect(self.panel.locator('[data-action="history-detail"]')).to_have_text("Messhöhen ausblenden")
        self.assertTrue(await self.panel.evaluate("p=>p.historyDetail"))
        self.assertEqual(await self.panel.evaluate("p=>[...p.positions].sort()"), ["lower", "upper"])
        chart = self.panel.locator("svg.session-chart")
        # Hover over a recorded sample, not the empty lead-in before the session.
        sample_time = (self.base + timedelta(seconds=7)).timestamp() * 1000
        sample_x = await self.panel.evaluate("(p,t)=>{const [a,b]=p.window;return (65+(t-a)/(b-a)*1070)/1200*p.shadowRoot.querySelector('svg.session-chart').getBoundingClientRect().width;}", sample_time)
        await chart.hover(position={"x": sample_x, "y": 200})
        await expect(self.panel.locator("#tooltip")).to_be_visible()
        await expect(self.panel.locator("#tooltip")).to_contain_text("Temperatur oben")
        # Valid but hostile palette: inspect rendered text consumers, not only
        # the palette variables. Curves must keep the chosen measurement color.
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        editor = self.panel.locator("#appearance-settings")
        for group in ("Grunddarstellung", "Bedienung", "Messungen und Ereignisse"):
            summary = editor.locator("summary").filter(has_text=group)
            if await summary.locator("..").get_attribute("open") is None:
                await summary.click()
        for role in ("card_background", "text", "ui_accent", "series_temperature", "series_humidity"):
            await editor.locator(f'[data-appearance-color="{role}"]').fill("#000000")
        await editor.locator('[data-appearance-color="page_background"]').fill("#FFFFFF")
        await editor.locator('[data-appearance-color="chart_background"]').fill("#000000")
        await editor.locator('[data-appearance-color="chart_text"]').fill("#FFFFFF")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator('header h1')).to_have_css('color', 'rgb(0, 0, 0)')
        info_button = self.panel.locator('#current .program-info button').first
        if not await info_button.is_visible():
            await self.panel.locator('[data-action="program-toggle"]').click()
        await expect(info_button).to_be_visible()
        await info_button.click()
        popup = self.panel.locator('#current .program-info-popup').first
        await expect(popup).to_be_visible()
        await expect(popup).to_have_css('background-color', 'rgb(0, 0, 0)')
        await expect(popup).to_have_css('color', 'rgb(255, 255, 255)')
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        for link in ("Sensoren und Geräte zuordnen", "Home-Assistant-Protokoll öffnen"):
            await expect(self.panel.get_by_role("link", name=link)).to_have_css("color", "rgb(255, 255, 255)")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await chart.hover(position={"x": sample_x, "y": 200})
        # Returning through the main History tab selects the single-height
        # overview, whose label intentionally omits the height suffix.
        label = self.panel.locator("#tooltip").get_by_text("Temperatur", exact=True)
        await expect(label).to_be_visible()
        await expect(label).to_have_css("color", "rgb(255, 255, 255)")
        await expect(self.panel.locator("#tooltip")).to_have_css("background-color", "rgb(0, 0, 0)")
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:temperature'].stroke"), "#000000")
        compare = self.panel.locator('[data-action="history-detail"]')
        await expect(compare).to_have_attribute("aria-pressed", "false")
        await expect(compare).to_have_css("opacity", "1")
        await expect(compare).to_have_css("color", "rgb(255, 255, 255)")
        await compare.click()
        lower = self.panel.locator('[data-action="position-lower"]')
        await lower.click()
        await expect(lower).to_be_enabled()
        await expect(lower).to_have_attribute("aria-pressed", "false")
        await expect(lower).to_have_css("opacity", "1")
        await expect(lower).to_have_css("color", "rgb(255, 255, 255)")
        await compare.click()
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        status_group = editor.locator("summary").filter(has_text="Zustände und Phasen")
        if await status_group.locator("..").get_attribute("open") is None:
            await status_group.click()
        await editor.locator('[data-appearance-color="text"]').fill("#757575")
        await editor.locator('[data-appearance-color="status_error"]').fill("#FFFFFF")
        await editor.locator('[data-appearance-color="status_warning"]').fill("#FFFFFF")
        await editor.locator('[data-appearance-color="focus"]').fill("#5A5A5A")
        await editor.locator('[data-appearance-color="chart_text"]').fill("#757575")
        await self.panel.evaluate("p=>p.message(new Error('Synthetischer Verbindungsfehler'), 'action')")
        error_notice = self.panel.locator("#message.notice.error")
        await expect(error_notice).to_contain_text("Synthetischer Verbindungsfehler")
        await expect(error_notice).to_have_css("background-color", "rgb(31, 31, 31)")
        await expect(error_notice).to_have_css("color", "rgb(255, 255, 255)")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        compare = self.panel.locator('[data-action="history-detail"]')
        await expect(compare).to_have_css("color", "rgb(117, 117, 117)")
        await compare.hover()
        await expect(compare).to_have_css("filter", "none")
        await expect(compare).to_have_css("color", "rgb(117, 117, 117)")
        await expect(compare).to_have_css("text-decoration-line", "none")
        await expect(compare).to_have_css("outline-style", "solid")
        await expect(compare).to_have_css("outline-width", "2px")
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await editor.locator('[data-action="appearance-discard"]').click()
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        zoom = await self.panel.evaluate("p=>p.zoom")
        await chart.dispatch_event("wheel", {"deltaY": -600, "clientX": 250, "clientY": 200})
        self.assertEqual(await self.panel.evaluate("p=>p.zoom"), zoom)
        for _ in range(4):
            await chart.dispatch_event("wheel", {"deltaY": -1000, "ctrlKey": True, "clientX": 250, "clientY": 200})
        self.assertGreater(await self.panel.evaluate("p=>p.zoom"), zoom)
        self.assertLessEqual(await self.panel.evaluate("p=>p.zoom"), 256)
        await self.panel.locator('[data-action="zoom-in"]').click()
        self.assertLessEqual(await self.panel.evaluate("p=>p.zoom"), 256)
        await self.panel.locator('[data-action="reset-zoom"]').click()
        await self.panel.locator('[data-action="details"]').click()
        await self.set_source("upper_temperature", "unavailable")
        await expect(self.panel.locator('#details')).to_contain_text("Messwert fehlt", timeout=15000)
        await self.panel.locator('[data-action="diagnostics"]').click()
        await expect(self.panel.locator('[data-series="detector_door_temperature_slope_upper"]')).to_be_attached()
        self.assertFalse(await self.panel.locator("#plots").is_visible())
        await self.panel.locator('.detail-tabs [data-action="detail-history"]').click()
        await self.panel.locator('#event-list details').first.locator('summary').click()
        event_link = self.panel.locator('#event-list [data-action^="event-row:"]').first
        await expect(event_link).to_be_visible()
        event_id = (await event_link.get_attribute('data-action')).split(':', 1)[1]
        await event_link.click()
        selected_row = self.panel.locator(f'.event-row[data-event-id="{event_id}"]')
        contrast = await selected_row.evaluate("""row => {
          const style = getComputedStyle(row);
          const light = color => {
            const channels = color.match(/[\\d.]+/g).slice(0, 3).map(Number);
            return channels.reduce((sum, value, index) => {
              const x = value / 255;
              return sum + [0.2126, 0.7152, 0.0722][index] *
                (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
            }, 0);
          };
          const a = light(style.outlineColor), b = light(style.backgroundColor);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        }""")
        self.assertGreaterEqual(contrast, 3)
        await expect(self.panel.locator('.detail-tabs [data-action="diagnostics"]')).to_have_attribute('aria-current', 'page')
        await expect(self.panel.locator('#detection-plots')).to_be_visible()
        marker = self.panel.locator(f'.diagnostic-marker[data-event-id="{event_id}"][data-selected="true"]').first
        await expect(marker).to_be_focused()
        await marker.press('Enter')
        await expect(self.panel.locator('.detail-tabs [data-action="detail-history"]')).to_have_attribute('aria-current', 'page')
        await expect(self.panel.locator('#event-list')).to_be_visible()
        selected_link = self.panel.locator(f'#event-list [data-action="event-row:{event_id}"]')
        await expect(selected_link).to_be_focused()
        await expect(selected_link).to_have_css("outline-width", "2px")
        button_contrast = await selected_link.evaluate("""button => {
          const style = getComputedStyle(button);
          const light = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number)
            .reduce((sum, value, index) => {
              const x = value / 255;
              return sum + [0.2126, 0.7152, 0.0722][index] *
                (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
            }, 0);
          const a = light(style.outlineColor), b = light(style.backgroundColor);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        }""")
        self.assertGreaterEqual(button_contrast, 3)
        await self.panel.locator('[data-action="settings"]').click()
        temperature_color = editor.locator('[data-appearance-color="series_temperature"]')
        if not await temperature_color.is_visible():
            await editor.locator("summary").filter(has_text="Messungen und Ereignisse").click()
        await temperature_color.fill("#42A5FF")
        await editor.locator('[data-appearance-color="series_humidity"]').fill("#FF6B4A")
        await self.panel.locator('.main-tabs [data-action="details"]').click()
        await self.panel.locator('.detail-tabs [data-action="detail-history"]').click()
        event_row = self.panel.locator(f'#event-list [data-action="event-row:{event_id}"]')
        if not await event_row.is_visible():
            await event_row.locator('xpath=ancestor::details').locator('summary').click()
        await event_row.click()
        plot = self.panel.locator('#detection-plots .plot-panel').filter(
            has=self.page.locator('[data-series="detector_door_temperature_slope_upper"]'))
        await expect(plot).to_be_visible()
        for position, color, label in (("upper", "rgb(66, 165, 255)", "Durchgezogen: oben"),
                                       ("lower", "rgb(255, 107, 74)", "Gestrichelt: unten")):
            await expect(plot.locator(f'path[data-series="detector_door_temperature_slope_{position}"]')).to_have_css("stroke", color)
            await expect(plot.locator(".diagnostic-legend span").filter(has_text=label).locator("i")).to_have_css("background-color", color)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await editor.locator('[data-action="appearance-discard"]').click()
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_be_disabled()
        await expect(self.panel.get_by_role("button", name="Standardwerte wiederherstellen", exact=True)).to_be_disabled()
        async with self.page.expect_download() as result:
            await self.panel.locator('[data-action="export"]').click()
        download = await result.value
        self.assertIsNone(await download.failure())
        with zipfile.ZipFile(await download.path()) as archive:
            session = json.loads(archive.read("sessions.jsonl").splitlines()[0])
            self.assertEqual(session["timeline"]["session_id"], identity)
            self.assertEqual(session["timeline"]["active"]["gang_id"], gang_id)
            self.assertGreater(len(archive.read("measurements.csv").splitlines()), 50)
        await self.runtime.set_operation(False)
        self.now += timedelta(minutes=3)
        await self.runtime.tick()
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await self.panel.locator('[data-action="history"]').click()
        await expect(self.panel.locator(f'#session option[value="{identity}"]')).to_be_attached(timeout=15000)
        await expect(self.panel.locator("svg.session-chart")).to_be_visible()
        await self.panel.locator("#session").select_option(identity)
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Bestätigt", timeout=15000)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-start"), start)
        tokens = await self.page.evaluate("localStorage.getItem('hassTokens')")

        async def zone_snapshot(zone):
            context = await self.browser.new_context(
                viewport={"width": 1440, "height": 1080}, timezone_id=zone
            )
            try:
                await context.add_init_script(
                    "localStorage.setItem('hassTokens', " + json.dumps(tokens) + ");"
                )
                page = await context.new_page()
                page.on("pageerror", lambda error: self.errors.append(str(error)))
                await page.goto(self.url + "/ha-sauna")
                panel = page.locator("ha-sauna-panel")
                await expect(
                    panel.locator('#current [data-action="operation"]')
                ).to_be_visible(timeout=60000)
                await panel.locator('.main-tabs [data-action="history"]').click()
                await expect(
                    panel.locator(f'#session option[value="{identity}"]')
                ).to_be_attached(timeout=15000)
                await panel.locator("#session").select_option(identity)
                await panel.evaluate("""async p => {
                  while (p.busy) await new Promise(resolve => setTimeout(resolve, 10));
                  if (p.historyLoad) await p.historyLoad.promise;
                }""")
                await expect(panel.locator("[data-gang-id]")).to_contain_text(
                    "Bestätigt", timeout=15000
                )
                self.assertEqual(
                    await panel.locator("[data-gang-id]").get_attribute("data-start"),
                    start,
                )
                return await panel.evaluate("""p => ({
                  gang: p.$('#gangs tbody tr td:nth-child(2)').textContent,
                  events: [...p.shadowRoot.querySelectorAll('#event-list tbody tr td:nth-child(2)')].map(e=>e.textContent),
                  annotations: [...p.shadowRoot.querySelectorAll('[data-history-annotations] title')].filter(e=>/Aufguss|Heizzeit/.test(e.textContent)).map(e=>e.textContent),
                  session: p.$('#session').selectedOptions[0].textContent,
                })""")
            finally:
                await context.close()

        utc = await zone_snapshot("UTC")
        berlin = await zone_snapshot("Europe/Berlin")
        self.assertTrue(utc["annotations"])
        for consumer in ("gang", "events", "annotations", "session"):
            self.assertNotEqual(utc[consumer], berlin[consumer], consumer)
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.panel.evaluate(
            "p=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
        )
        main_width = await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth")
        if main_width > 390:
            print("BROWSER_HISTORY_OVERFLOW", await self.panel.evaluate("""p => {
              const main = p.shadowRoot.querySelector('main');
              const box = node => {
                const r = node.getBoundingClientRect(), s = getComputedStyle(node);
                return {tag: node.tagName, id: node.id, classes: node.getAttribute('class'),
                  left: r.left, right: r.right, width: r.width,
                  scrollWidth: node.scrollWidth, clientWidth: node.clientWidth,
                  display: s.display, widthStyle: s.width, minWidth: s.minWidth,
                  maxWidth: s.maxWidth, overflowX: s.overflowX, whiteSpace: s.whiteSpace,
                  scrollContainer: node.closest('.scroll')?.className || null};
              };
              const right = main.getBoundingClientRect().right;
              return {main: box(main), host: box(p), view: p.view,
                sessionSelect: box(p.$('#session')),
                sessionRow: box(p.$('#session').parentElement),
                overflowingNodes: [...main.querySelectorAll('*')]
                  .filter(node => {
                    const r = node.getBoundingClientRect();
                    return !node.closest('.scroll') &&
                      r.width > 0 && r.right > right + 0.5;
                  }).map(box).sort((a,b) => b.right-a.right).slice(0,30)};
            }"""))
        self.assertLessEqual(main_width, 390)
        recorder = await self.page.evaluate("()=>document.querySelector('home-assistant').hass.callWS({type:'recorder/info'})")
        self.assertTrue(recorder["thread_running"])
        self.assertTrue(recorder["recording"])
        self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.testErrors"), [])
        self.assertEqual(self.ws_errors, [])

    async def test_overview_timers_stable_controls_german_settings_and_logging(self):
        await expect(self.panel.locator('.main-tabs [data-action="overview"]')).to_have_text("Steuerung")
        operation_button = self.panel.locator('#current [data-action="operation"]')
        await expect(operation_button).to_have_css("background-color", "rgb(33, 101, 81)")
        operation_box = await operation_button.bounding_box()
        await operation_button.hover()
        await expect(operation_button).to_have_css("outline-style", "solid")
        self.assertEqual(await operation_button.bounding_box(), operation_box)
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        self.assertNotIn("Bereitschaft", await self.panel.locator("#current").inner_text())
        gauges_text = await self.panel.locator("#current .gauges").inner_text()
        self.assertNotIn("Temperatur oben", gauges_text)
        self.assertNotIn("Relative Luftfeuchte oben", gauges_text)
        self.assertNotIn("Messung oben", gauges_text)
        await expect(self.panel.locator('[data-action="program-mode:program"]')).to_be_visible()
        await expect(self.panel.locator('[data-action="program-mode:individual"]')).to_be_visible()
        program_url=f"/api/ha_sauna/{self.entry.entry_id}/program"
        await self.panel.locator('[data-action="program-mode:constant"]').click()
        await expect(self.panel.locator('[data-action="program-mode:constant"]')).to_have_attribute("aria-pressed", "true")
        await self.panel.evaluate(
            "async panel => { while (panel.programRequest) await new Promise(resolve => setTimeout(resolve, 10)); }"
        )
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), 6)
        target_arc=self.panel.locator('[data-target-arc][role="slider"]')
        await expect(target_arc).to_be_visible(timeout=10000)
        await expect(target_arc).to_have_attribute("aria-label", "Solltemperatur einstellen")
        await target_arc.focus()
        await expect(target_arc).to_have_css("opacity", "0.35")
        async with self.page.expect_response(lambda response: response.url.endswith("/temperature") and response.request.method == "POST"):
            await target_arc.press("PageDown")
        await expect(target_arc).to_have_attribute("aria-valuenow", "70", timeout=10000)
        await self.panel.locator('[data-action="program-mode:individual"]').click()
        await self.panel.evaluate(
            "async panel => { while (panel.programRequest) await new Promise(resolve => setTimeout(resolve, 10)); }"
        )
        await expect(self.panel.locator("#progression-end")).to_be_visible()
        async with self.page.expect_response(lambda response: response.url.endswith("/temperature") and response.request.method == "POST"):
            await self.panel.locator('#progression-end').fill("86")
            await self.panel.locator('#progression-end').press("Tab")
        async with self.page.expect_response(lambda response: response.url.endswith("/temperature") and response.request.method == "POST") as result:
            await self.panel.locator('#progression-gangs').fill("3")
            await self.panel.locator('#progression-gangs').press("Enter")
        await self.panel.locator('[data-action="program-info:free"]').click()
        await expect(self.panel.locator('#current .program-info-popup')).to_contain_text("70 → 78 → 86 °C")
        response=await result.value
        self.assertTrue(response.ok)
        saved=await response.json()
        self.assertEqual(saved["parameters"]["target_temperature_c"],70)
        self.assertEqual(saved["parameters"]["final_temperature_c"],86)
        self.assertEqual(saved["parameters"]["temperature_gangs"],3)
        await expect(self.panel.locator('#progression-end')).to_have_value("86", timeout=15000)
        self.runtime=self.entry.runtime_data
        from datetime import UTC, datetime
        self.now=datetime.now(UTC)
        self.runtime._clock=lambda:self.now
        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"],86)
        self.assertEqual(self.entry.options["parameters"]["temperature_gangs"],3)
        await self.panel.locator('#current [data-action="operation"]').click()
        identity=self.runtime.session.session_id
        next_target=self.runtime.controller.target_temperature
        await self.panel.get_by_role("button", name="Programm ändern").click()
        await self.panel.locator('#progression-end').fill("90")
        temperature_url=f"/api/ha_sauna/{self.entry.entry_id}/temperature"
        async with self.page.expect_response(lambda response: response.url.endswith(temperature_url) and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="program-apply"]').click()
        response=await result.value
        self.assertTrue(response.ok)
        self.assertEqual((await response.json())["parameters"]["final_temperature_c"],90)
        await expect(self.panel.locator('#progression-end')).to_have_value("90")
        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"],90)
        self.assertEqual(self.runtime.controller.target_temperature,next_target)
        self.assertEqual(self.entry.options["program_mode"],"progressive")
        target_arc=self.panel.locator('[data-target-arc][role="slider"]')
        await target_arc.focus()
        await target_arc.press("PageUp")
        await self.panel.evaluate("p=>p.temperatureChange")
        await expect(target_arc).to_have_attribute("aria-valuenow", "80", timeout=10000)
        self.assertEqual(self.entry.options["program_mode"],"constant")
        await self.panel.get_by_role("button", name="Programm ändern").click()
        await self.panel.locator('[data-action="program-mode:individual"]').click()
        await self.panel.locator('#progression-gangs').fill("2")
        async with self.page.expect_response(lambda response: response.url.endswith(program_url) and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="program-apply"]').click()
        response=await result.value
        self.assertTrue(response.ok)
        self.assertEqual((await response.json())["parameters"]["temperature_gangs"],2)
        await expect(self.panel.locator('#progression-gangs')).to_have_value("2")
        self.assertIs(self.entry.runtime_data,self.runtime)
        self.assertEqual(self.runtime.session.session_id,identity)
        self.assertEqual(self.runtime.controller.target_temperature,80)
        self.assertEqual(await self.panel.locator('#current [data-mechanical-timer]').count(),0)
        self.now+=timedelta(seconds=20)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#current [data-phase-timer]')).to_have_count(0)
        await expect(self.panel.locator('#current')).to_contain_text("Noch nicht abschätzbar")
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        await self.set_source("upper_temperature", 90)
        overrides = self.panel.locator("#current .manual-overrides")
        if await overrides.get_attribute("open") is None:
            await overrides.locator("summary").click()
        await expect(self.panel.locator('#current #manual-light-value-overview')).to_be_visible(timeout=10000)
        await self.panel.locator('#current #manual-light-value-overview').fill("60")
        await self.panel.locator('#current [data-action="manual-light-overview"]').click()
        await expect(self.panel.locator('#current [data-light-status]')).to_have_text("Manuell · 60 %")
        await self.panel.locator('[data-action="details"]').click()
        await expect(self.panel.locator('#details .compact-times')).to_contain_text("Mechanischer Ofentimer")
        await expect(self.panel.locator('#details')).to_contain_text("60 %")
        paused=self.panel.locator('#details .compact-times')
        await expect(paused).to_contain_text("Angehalten · Schütz aus")
        self.now+=timedelta(seconds=5)
        await self.runtime.tick()
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14380)
        await self.set_source("upper_temperature", 70)
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#details .compact-times')).to_contain_text("Geschätzte Restzeit bei eingeschaltetem Schütz")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await self.panel.locator('#current [data-action="operation"]').click()
        await self.panel.locator('[data-action="details"]').click()
        timer=self.panel.locator('#details [data-mechanical-timer]')
        await expect(timer).to_contain_text("Angehalten · Saunabetrieb aus")
        frozen=await timer.inner_text()
        self.now+=timedelta(seconds=50)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(timer).to_have_text(frozen)
        self.assertIsNotNone(self.runtime.session)
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Tür geschlossen")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden(timeout=5000)
        await self.panel.get_by_role("button", name="Programm ändern").click()
        await self.panel.locator('[data-action="program-mode:constant"]').click()
        await expect(self.panel.locator('#current .program-pending')).to_contain_text("Konstant")
        await expect(self.panel.locator('[data-action="program-mode:constant"]')).to_have_attribute("aria-pressed", "false")
        await self.panel.locator('#current [data-action="program-apply"]').click()
        await expect(self.panel.locator('[data-action="program-mode:constant"]')).to_have_attribute("aria-pressed", "true")
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), 6)
        await expect(self.panel.locator('#current [data-action^="preset:"]').first).to_be_enabled()
        await self.panel.locator('[data-action="details"]').click()
        await expect(self.panel.locator('[data-readiness]')).to_be_visible()
        await self.panel.locator('[data-action="settings"]').click()
        settings = self.panel.locator("#settings")
        await expect(settings.locator("#program-library [data-program-id]").first).to_be_visible(timeout=10000)
        await expect(settings.locator("#button-program")).to_be_visible()
        await settings.locator('details.settings-group').filter(has_text="Betrieb und Kühlung").locator("summary").click()
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_be_disabled()
        await expect(self.panel.locator('input[name="target_temperature_c"]')).to_be_enabled()
        await expect(self.panel.locator('input[name="temperature_increase_c"]')).to_have_count(0)
        await settings.locator('details.settings-group').filter(has_text="Temperatursteigerung").locator("summary").click()
        await expect(self.panel.locator('input[name="final_temperature_c"]')).to_be_enabled()
        await expect(self.panel.locator('input[name="temperature_gangs"]')).to_be_enabled()
        await settings.locator('details.settings-group').filter(has_text="Überwachung").locator("summary").click()
        self.assertTrue(await self.panel.locator('#help-sensor_timeout_seconds').inner_text())
        await self.panel.locator('#log-level').select_option("DEBUG")
        async with self.page.expect_response(lambda response: response.url.endswith("/logging") and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="logging"]').click()
        self.assertTrue((await result.value).ok)
        await expect(self.panel.locator('#log-level')).to_have_value("DEBUG")
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, self.runtime)
        self.assertEqual(self.entry.options["log_level"], "DEBUG")
        self.now+=timedelta(minutes=3)
        await self.runtime.tick()
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14380)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await self.panel.evaluate("p=>p.refresh()")
        self.assertEqual(await self.panel.locator('#current [data-mechanical-timer]').count(),0)
        await expect(self.panel.locator('#current')).not_to_contain_text("Lichtnachlauf noch")
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        text=await self.panel.locator('#current').inner_text()
        for code in ("measurement_unavailable", "configuration_required", "pending", "sensor_timeout_seconds"):
            self.assertNotIn(code,text)
        self.now+=timedelta(minutes=10)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#current [data-phase-timer]')).to_have_count(0,timeout=15000)
        await self.page.set_viewport_size({"width":390,"height":844})
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"),390)
        await self.panel.locator('[data-action="details"]').click()
        await self.panel.locator('[data-action="settings"]').click()
        settings = self.panel.locator("#settings")
        monitoring = settings.locator('details.settings-group').filter(has_text="Überwachung")
        if await monitoring.get_attribute("open") is None:
            await monitoring.locator("summary").click()
        await expect(self.panel.locator('input[name="sensor_timeout_seconds"]')).to_be_enabled()
        await settings.get_by_text("Licht", exact=True).click()
        await expect(self.panel.locator('input[name="session_light_minutes"]')).to_have_count(0)
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Türerkennung ruht")
        await expect(self.panel.locator("#details")).to_contain_text("Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet.")
        await expect(self.panel.locator('input[name="session_light_brightness_percent"]')).to_have_value("50")
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"),390)
        bindings = dict(self.entry.options["bindings"])
        await self.panel.locator('input[name="sensor_timeout_seconds"]').fill("45")
        await self.panel.get_by_role("button", name="Standardwerte wiederherstellen", exact=True).click()
        await expect(self.panel.locator('#log-level')).to_have_value("INFO", timeout=15000)
        await expect(self.panel.locator('input[name="sensor_timeout_seconds"]')).to_have_value("180")
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_have_value("60")
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["bindings"], bindings)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator('#current').get_by_role("button", name="Hell 50 %", exact=True)).to_be_visible()
        self.assertEqual(self.errors,[])
        self.assertEqual(self.ws_errors,[])

    async def test_rejected_start_explains_stale_measurement_and_recovers(self):
        panel_file = Path(__file__).resolve().parents[2] / "custom_components/ha_sauna/panel.js"
        digest = sha256(panel_file.read_bytes()).hexdigest()[:16]
        loaded = await self.page.evaluate("performance.getEntriesByType('resource').map(r=>r.name)")
        self.assertIn(self.url + "/ha_sauna/panel.js?v=" + digest, loaded)

        # A source stops reporting after the last UI refresh. The real API
        # must reject the stale display's still-enabled button with HTTP 409.
        await self.panel.evaluate("p=>clearTimeout(p.timer)")
        self.now=self.base+timedelta(seconds=31)
        async with self.page.expect_response(lambda r: r.url.endswith("/control") and r.request.method == "POST") as response:
            await self.panel.locator('#current [data-action="operation"]').click()
        self.assertEqual((await response.value).status, 409)
        message = self.panel.locator('#message')
        for label in ("Start nicht möglich", "Temperaturwert"):
            await expect(message).to_contain_text(label)
        self.assertNotIn("Response error", await message.inner_text())
        self.assertIsNone(self.entry.runtime_data.session)
        self.assertNotIn(True, self.heater.calls)

        await self.panel.evaluate("p=>p.refresh()")
        await expect(message).to_contain_text("Start nicht möglich")
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_disabled()
        for position in ("upper","lower"):
            await self.set_source(f"{position}_temperature",50)
            await self.set_source(f"{position}_humidity",30)
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_enabled(timeout=15000)
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        await expect(self.panel.locator('#current')).to_contain_text("Noch nicht abschätzbar")
        self.assertTrue(self.entry.runtime_data.session.operation_enabled)
        self.assertIn(True, self.heater.calls)
        self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.testErrors"), [])
        self.assertEqual(self.ws_errors, [])
