"""Chromium inside the real HA frontend; no mock hass object or fake API."""
import base64
import asyncio
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import test_device_path as device_tests
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components.onboarding import OnboardingStorage
from homeassistant.components.onboarding.const import STEPS
from homeassistant.setup import async_setup_component
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers import recorder as recorder_helper
from homeassistant.components.http.config import async_get_and_load_store
from playwright.async_api import async_playwright, expect
from custom_components.ha_sauna.core.timeline import Event, Kind


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    set_source = device_tests.DevicePathTests.set_source

    async def asyncSetUp(self):
        await device_tests.DevicePathTests.asyncSetUp(self)
        # This fixture intentionally binds a new ephemeral localhost port.
        # Confirm its working HTTP configuration so HA's own migration dialog
        # does not cover the panel under test.
        http_store = await async_get_and_load_store(self.hass)
        if http_store.pending:
            await http_store.async_promote_pending()
        await OnboardingStorage(self.hass, 4, "onboarding", private=True).async_save({"done": STEPS})
        # HA's shell queries recorder/info even on a custom panel. Use the real
        # bootstrap initialization and a real isolated database; never suppress
        # an unknown-command rejection or replace the endpoint with a stub.
        recorder_helper.async_initialize_recorder(self.hass)
        self.assertTrue(await async_setup_component(self.hass, "recorder", {"recorder": {}}))
        await asyncio.wait_for(recorder_helper.get_instance(self.hass).async_recorder_ready.wait(), 30)
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
        self.errors = []
        self.console_errors = []
        self.network_errors = []
        self.addAsyncCleanup(self.cleanup_browser)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda e: self.console_errors.append(e.text) if e.type == "error" else None)
        self.page.on("requestfailed", lambda r: self.network_errors.append((r.url.split("?")[0], r.failure)))
        self.page.on("response", lambda r: self.network_errors.append((r.url.split("?")[0], r.status)) if r.status >= 400 else None)
        self.ws_errors = []
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

    async def asyncTearDown(self):
        await self.cleanup_browser()
        await device_tests.DevicePathTests.asyncTearDown(self)

    async def cleanup_browser(self):
        if getattr(self, "browser", None):
            print("BROWSER_ERRORS", self.errors)
            print("BROWSER_CONSOLE", self.console_errors[-20:])
            print("BROWSER_NETWORK", self.network_errors[-20:])
            print("BROWSER_WS_ERRORS", self.ws_errors)
            print("BROWSER_REJECTIONS", await self.page.evaluate("window.testErrors"))
            print("BROWSER_SCRIPTS", await self.page.locator("script[src]").evaluate_all("els=>els.map(e=>e.src)"))
            print("BROWSER_HA_STATE", await self.page.evaluate("()=>{const e=document.querySelector('home-assistant'),h=e?.hass;return h?{...Object.fromEntries(['connected','states','config','services','themes','panels','user'].map(k=>[k,h[k]!=null])),migration:e._databaseMigration}:{element:!!e,defined:!!customElements.get('home-assistant')}}"))
            await self.browser.close()
            await self.playwright.stop()
            self.browser = None

    async def emit(self, kind, second):
        self.now = self.base + timedelta(seconds=second)
        await self.runtime.receive(Event(f"browser:{kind}:{second}", self.runtime.session.session_id,
            kind, self.now, self.now))
        await self.hass.async_block_till_done()

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
        await self.panel.locator('[data-action="history"]').click()
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Vorläufig", timeout=15000)
        gang_id = await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id")
        start = await self.panel.locator('[data-gang-id]').get_attribute("data-start")
        self.assertEqual(start, (self.base+timedelta(seconds=25)).isoformat())
        await self.emit(Kind.INFUSION, 40)
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Bestätigt", timeout=15000)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id"), gang_id)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-start"), start)
        for pos in ("upper", "lower"):
            for quantity in ("temperature", "humidity"):
                path = self.panel.locator(f'[data-series="{pos}_{quantity}"]')
                self.assertTrue((await path.get_attribute("d")).startswith("M"))
        chart = self.panel.locator("svg.session-chart")
        await chart.hover(position={"x": 250, "y": 200})
        await expect(self.panel.locator("#tooltip")).to_be_visible()
        await expect(self.panel.locator("#tooltip")).to_contain_text("Temperatur oben")
        await self.panel.locator('[data-action="zoom-in"]').click()
        self.assertEqual(await self.panel.evaluate("p=>p.zoom"), 2)
        await self.panel.locator('[data-action="reset-zoom"]').click()
        screenshot = await self.page.screenshot(type="jpeg", quality=55)
        print("BROWSER_IMAGE_SESSION " + base64.b64encode(screenshot).decode())
        await self.panel.locator('[data-action="details"]').click()
        await self.set_source("upper_temperature", "unavailable")
        await expect(self.panel.locator('#details [role="alert"]')).to_contain_text("Temperatur oben", timeout=15000)
        await self.panel.locator('[data-action="diagnostics"]').click()
        await expect(self.panel.locator('[data-series="detector_door_temperature_slope_upper"]')).to_be_attached()
        self.assertFalse(await self.panel.locator("#plots").is_visible())
        await self.panel.locator('[data-action="settings"]').click()
        await expect(self.panel.locator('input[name="heating_minutes"]')).to_be_disabled()
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
        await self.panel.locator('[data-action="normal"]').click()
        await self.panel.locator('[data-action="history"]').click()
        await expect(self.panel.locator(f'#session option[value="{identity}"]')).to_be_attached(timeout=15000)
        await self.panel.locator("#session").select_option(identity)
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Bestätigt", timeout=15000)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-start"), start)
        await self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"), 390)
        recorder = await self.page.evaluate("()=>document.querySelector('home-assistant').hass.callWS({type:'recorder/info'})")
        self.assertTrue(recorder["thread_running"])
        self.assertTrue(recorder["recording"])
        self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.testErrors"), [])
        self.assertEqual(self.ws_errors, [])
