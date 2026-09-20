"""Chromium inside the real HA frontend; no mock hass object or fake API."""
import base64
from datetime import timedelta
from hashlib import sha256
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

    async def test_manual_phase_buttons_only_in_details_and_follow_regular_sequence(self):
        await device_tests.DevicePathTests.prepare_gang_after_run(self)
        self.now += timedelta(seconds=10)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        self.assertEqual(await self.panel.locator('#current [data-action^="end-phase:"]').count(),0)
        await self.panel.locator('[data-action="details"]').click()
        end_after = self.panel.get_by_role("button", name="Nachlauf jetzt beenden", exact=True)
        await expect(end_after).to_be_visible()
        print("BROWSER_IMAGE_MANUAL_AFTER_RUN " + base64.b64encode(await self.page.screenshot(type="jpeg",quality=60)).decode())
        await end_after.click()
        end_cooling = self.panel.get_by_role("button", name="Zwangskühlung jetzt beenden", exact=True)
        await expect(end_cooling).to_be_visible()
        self.assertEqual(self.runtime.session.cooling.credited_seconds,10)
        self.assertFalse(self.heater.is_on)
        await end_cooling.click()
        await expect(self.panel.locator('#details [data-phase="aufheizen"]')).to_be_visible()
        self.assertEqual(await self.panel.locator('#details [data-action^="end-phase:"]').count(),0)
        await self.hass.async_block_till_done()
        self.assertTrue(self.heater.is_on)
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
        await self.panel.locator('[data-action="history"]').click()
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Vorläufig", timeout=15000)
        gang_id = await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id")
        start = await self.panel.locator('[data-gang-id]').get_attribute("data-start")
        self.assertEqual(start, (self.base+timedelta(seconds=25)).isoformat())
        await self.emit(Kind.INFUSION, 40)
        await expect(self.panel.locator('[data-gang-id]')).to_contain_text("Bestätigt", timeout=15000)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-gang-id"), gang_id)
        self.assertEqual(await self.panel.locator('[data-gang-id]').get_attribute("data-start"), start)
        # The normal history deliberately keeps one measurement height readable.
        # A comparison is an explicit user choice, not the default clutter.
        for quantity in ("temperature", "humidity"):
            path = self.panel.locator(f'[data-series="upper_{quantity}"]')
            self.assertTrue((await path.get_attribute("d")).startswith("M"))
            await expect(self.panel.locator(f'[data-series="lower_{quantity}"]')).to_have_count(0)
        await self.panel.locator('[data-action="history-detail"]').click()
        await expect(self.panel.locator('[data-action="history-detail"]')).to_have_text("Messhöhen ausblenden")
        for pos in ("upper", "lower"):
            for quantity in ("temperature", "humidity"):
                path = self.panel.locator(f'[data-series="{pos}_{quantity}"]')
                self.assertTrue((await path.get_attribute("d")).startswith("M"))
        chart = self.panel.locator("svg.session-chart")
        await chart.hover(position={"x": 250, "y": 200})
        await expect(self.panel.locator("#tooltip")).to_be_visible()
        await expect(self.panel.locator("#tooltip")).to_contain_text("Temperatur oben")
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
        await expect(self.panel.locator("svg.session-chart")).to_be_visible()
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

    async def test_overview_timers_stable_controls_german_settings_and_logging(self):
        await expect(self.panel.locator('[data-action="normal"]')).to_have_text("Übersicht")
        await expect(self.panel.locator("#current [data-door-status]")).to_have_text("Türerkennung ruht")
        self.assertNotIn("Bereitschaft", await self.panel.locator("#current").inner_text())
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), 6)
        await expect(self.panel.get_by_role("button", name="4-Gang-Programm", exact=True)).to_be_visible()
        await expect(self.panel.get_by_role("button", name="3-Gang-Programm", exact=True)).to_be_visible()
        await self.panel.locator('#current details summary').click()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator("#progression-end")).to_be_visible()
        await self.panel.evaluate("""p=>{const input=p.shadowRoot.querySelector('#target');input.value='75';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}""")
        await expect(self.panel.locator('#target')).to_have_value("75")
        await self.panel.locator('#progression-end').fill("86")
        await self.panel.locator('#progression-gangs').fill("3")
        await self.panel.locator('[data-action="program-free"]').click()
        await expect(self.panel.locator('#progression-end')).to_have_value("86", timeout=15000)
        self.runtime=self.entry.runtime_data
        from datetime import UTC, datetime
        self.now=datetime.now(UTC)
        self.runtime._clock=lambda:self.now
        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"],86)
        self.assertEqual(self.entry.options["parameters"]["temperature_gangs"],3)
        await self.panel.locator('#current [data-action="operation"]').click()
        identity=self.runtime.session.session_id
        await self.panel.evaluate("""p=>{const input=p.shadowRoot.querySelector('#target');input.value='80';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}""")
        await expect(self.panel.locator('#target')).to_have_value("80")
        await self.panel.locator('#progression-gangs').fill("2")
        await self.panel.locator('[data-action="progression"]').click()
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
        await expect(self.panel.locator("#current [data-door-status]")).to_have_text("Tür geschlossen")
        print("BROWSER_IMAGE_OVERVIEW_ACTIVE " + base64.b64encode(await self.page.screenshot(type="jpeg",quality=65)).decode())
        await self.set_source("upper_temperature", 90)
        await self.panel.locator('[data-action="details"]').click()
        await expect(self.panel.locator('#details [data-phase-timer="heating"]')).to_be_visible()
        await self.panel.locator('#manual-light-value').fill("60")
        await self.panel.locator('[data-action="manual-light"]').click()
        await expect(self.panel.locator('[data-light-status]')).to_have_text("Manuell · 60 %")
        paused=self.panel.locator('#details [data-mechanical-timer="paused"]')
        await expect(paused).to_contain_text("Angehalten · Schütz aus")
        self.now+=timedelta(seconds=5)
        await self.runtime.tick()
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14380)
        await self.set_source("upper_temperature", 70)
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#details [data-mechanical-timer="running"]')).to_be_visible()
        await self.panel.locator('[data-action="normal"]').click()
        await self.panel.locator('#current [data-action="operation"]').click()
        await self.panel.locator('[data-action="details"]').click()
        timer=self.panel.locator('#details [data-mechanical-timer="paused"] strong')
        await expect(timer).to_be_visible()
        frozen=await timer.inner_text()
        self.now+=timedelta(seconds=50)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(timer).to_have_text(frozen)
        self.assertIsNotNone(self.runtime.session)
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Tür geschlossen")
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), 6)
        await expect(self.panel.locator('#current [data-action^="preset:"]').first).to_be_disabled()
        await expect(self.panel.locator('[data-readiness]')).to_be_visible()
        await self.panel.locator('[data-action="settings"]').click()
        settings = self.panel.locator("#settings")
        await settings.locator('details.settings-group').filter(has_text="Betrieb und Kühlung").locator("summary").click()
        await expect(self.panel.locator('input[name="heating_minutes"]')).to_be_disabled()
        await expect(self.panel.locator('input[name="target_temperature_c"]')).to_be_enabled()
        await expect(self.panel.locator('input[name="temperature_increase_c"]')).to_have_count(0)
        await settings.locator('details.settings-group').filter(has_text="Temperatursteigerung und Profile").locator("summary").click()
        await expect(self.panel.locator('input[name="final_temperature_c"]')).to_be_enabled()
        await expect(self.panel.locator('input[name="temperature_gangs"]')).to_be_enabled()
        await settings.locator('details.settings-group').filter(has_text="Überwachung").locator("summary").click()
        self.assertTrue(await self.panel.locator('#help-sensor_timeout_seconds').inner_text())
        await self.panel.locator('#log-level').select_option("DEBUG")
        await self.panel.locator('[data-action="logging"]').click()
        await expect(self.panel.locator('#log-level')).to_have_value("DEBUG")
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, self.runtime)
        self.assertEqual(self.entry.options["log_level"], "DEBUG")
        self.now+=timedelta(minutes=3)
        await self.runtime.tick()
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14380)
        await self.panel.locator('[data-action="normal"]').click()
        await self.panel.evaluate("p=>p.refresh()")
        self.assertEqual(await self.panel.locator('#current [data-mechanical-timer]').count(),0)
        await expect(self.panel.locator('#current [data-phase-timer="session_light"]')).to_be_visible()
        await expect(self.panel.locator("#current [data-door-status]")).to_have_text("Türerkennung ruht")
        await expect(self.panel.locator('#current')).to_contain_text("Lichtnachlauf noch")
        text=await self.panel.locator('#current').inner_text()
        for code in ("measurement_unavailable", "configuration_required", "pending", "sensor_timeout_seconds"):
            self.assertNotIn(code,text)
        print("BROWSER_IMAGE_OVERVIEW " + base64.b64encode(await self.page.screenshot(type="jpeg",quality=60)).decode())
        self.now+=timedelta(minutes=10)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#current [data-phase-timer]')).to_have_count(0,timeout=15000)
        await self.page.set_viewport_size({"width":390,"height":844})
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"),390)
        await self.panel.locator('[data-action="details"]').click()
        await self.panel.locator('[data-action="settings"]').click()
        settings = self.panel.locator("#settings")
        await settings.locator('details.settings-group').filter(has_text="Überwachung").locator("summary").click()
        await expect(self.panel.locator('input[name="sensor_timeout_seconds"]')).to_be_enabled()
        await settings.locator('details.settings-group').filter(has_text="Licht").locator("summary").click()
        await expect(self.panel.locator('input[name="session_light_minutes"]')).to_have_value("10")
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Türerkennung ruht")
        await expect(self.panel.locator("#details")).to_contain_text("Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet.")
        await expect(self.panel.locator('input[name="session_light_brightness_percent"]')).to_have_value("50")
        await expect(self.panel.get_by_role("button", name="Raumlicht · 50%", exact=True)).to_be_visible()
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"),390)
        self.assertEqual(self.errors,[])
        self.assertEqual(self.ws_errors,[])

    async def test_rejected_start_explains_stale_measurement_and_recovers(self):
        panel_file = Path(__file__).resolve().parents[2] / "custom_components/ha_sauna/panel.js"
        digest = sha256(panel_file.read_bytes()).hexdigest()[:16]
        loaded = await self.page.evaluate("performance.getEntriesByType('resource').map(r=>r.name)")
        self.assertIn(self.url + "/ha_sauna/panel.js?v=" + digest, loaded)

        # A source stops reporting after the last UI refresh. The real API
        # must reject the stale display's still-enabled button with HTTP 409.
        await self.panel.evaluate("p=>clearInterval(p.timer)")
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
        await expect(self.panel.locator('#current [data-phase-timer="heating"]')).to_be_visible()
        self.assertTrue(self.entry.runtime_data.session.operation_enabled)
        self.assertIn(True, self.heater.calls)
        self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.testErrors"), [])
        self.assertEqual(self.ws_errors, [])
