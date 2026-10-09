"""Chromium inside the real HA frontend; no mock hass object or fake API."""
import asyncio
from datetime import timedelta
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import test_device_path as device_tests
from harness import retain_session
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.onboarding import OnboardingStorage
from homeassistant.components.onboarding.const import STEPS
from homeassistant.setup import async_setup_component
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.components.http.config import async_get_and_load_store
from playwright.async_api import async_playwright, expect
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.timeline import Event, Kind


DEFAULT_PROGRAMS = section("programs")
DEFAULT_PARAMETERS = {item["key"]: item["default"] for item in section("parameters")}


def default_css_color(role):
    color = next(item["default"] for item in section("appearance")["colors"]
                 if item["id"] == role)
    return "rgb(" + ", ".join(str(int(color[index:index + 2], 16))
                              for index in (1, 3, 5)) + ")"


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

    async def open_settings_section(self, section, panel=None):
        panel = panel or self.panel
        toggle = panel.locator('[data-action="settings-menu"]')
        if await toggle.is_visible() and await toggle.get_attribute("aria-expanded") == "false":
            await toggle.click()
        await panel.locator(f'[data-action="settings-section:{section}"]').click()
        if await toggle.is_visible():
            await expect(toggle).to_have_attribute("aria-expanded", "false")
            await expect(panel.locator(".settings-navigation")).to_be_hidden()

    async def open_appearance(self, panel=None):
        panel = panel or self.panel
        await self.open_settings_section("appearance", panel)
        advanced = panel.locator(".appearance-advanced")
        if await advanced.get_attribute("open") is None:
            await advanced.locator(":scope > summary").click()

    async def set_instrument_style(self, name, style):
        from custom_components.ha_sauna.settings import async_set_appearance

        appearance = self.runtime.configuration.as_options()["appearance"]
        appearance["instruments"][name] = style
        await async_set_appearance(self.hass, self.entry, appearance)
        await self.panel.evaluate("p => p.refresh()")

    async def open_parameter_group(self, key):
        field = self.panel.locator(f'input[name="{key}"]')
        section = await field.evaluate("input => input.closest('[data-settings-section]').dataset.settingsSection")
        await self.open_settings_section(section)
        await field.evaluate("input => {for(let node=input.parentElement;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;}")

    async def test_shared_dropdown_keyboard_pointer_and_native_selection(self):
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_settings_section("programs")
        program = self.panel.locator("#button-program")
        start_info = self.panel.locator('[data-action="program-info:button-start"]')
        await start_info.scroll_into_view_if_needed()
        program_before_info = await program.bounding_box()
        await start_info.click()
        await expect(self.panel.locator('#info-button-start')).to_be_visible()
        self.assertEqual(await program.bounding_box(), program_before_info)
        await expect(self.panel).to_have_js_property("busy", False)
        await self.panel.evaluate("panel => panel.refresh(true)")
        await expect(self.panel.locator('#info-button-start')).to_be_visible()
        self.assertEqual(await program.bounding_box(), program_before_info)
        await start_info.press("Escape")
        await program.click()
        popup_id = await program.get_attribute("aria-controls")
        active_id = await program.get_attribute("aria-activedescendant")
        await expect(self.panel).to_have_js_property("busy", False)
        await self.panel.evaluate("panel => panel.refresh(true)")
        await expect(program).to_be_focused()
        await expect(program).to_have_attribute("aria-expanded", "true")
        await expect(program).to_have_attribute("aria-controls", popup_id)
        await expect(program).to_have_attribute("aria-activedescendant", active_id)
        await expect(self.panel.locator(f'#{popup_id}')).to_be_visible()
        await program.press("Escape")
        await expect(program).not_to_have_attribute("aria-controls", popup_id)
        await self.open_settings_section("maintenance")
        select = self.panel.locator("#log-level")
        menu = self.panel.locator('.sauna-select-menu[role="listbox"]')
        await select.select_option("INFO")
        await select.evaluate("select => {select.testEvents = []; for (const kind of ['input', 'change']) select.addEventListener(kind, event => select.testEvents.push(event.type)); select.querySelector('[value=DEBUG]').disabled = true;}")
        await select.click()
        await expect(menu).to_be_visible()
        await expect(select).to_be_focused()
        await expect(select).to_have_attribute("aria-expanded", "true")
        await expect(menu.get_by_role("option", name="DEBUG", exact=False)).to_have_attribute("aria-disabled", "true")
        radius = await select.evaluate("element => getComputedStyle(element).borderRadius")
        await expect(menu).to_have_css("border-radius", radius)
        await select.press("Home")
        await expect(menu.locator('[data-active="true"]')).to_contain_text("ERROR")
        await select.press("ArrowDown")
        await select.press("End")
        await expect(menu.locator('[data-active="true"]')).to_contain_text("INFO")
        await select.press("ArrowUp")
        await select.press("Escape")
        await expect(menu).to_have_count(0)
        await expect(select).to_be_focused()
        await expect(select).to_have_value("INFO")
        self.assertEqual(await select.evaluate("select => select.testEvents"), [])
        await select.press("e")
        await expect(menu.locator('[data-active="true"]')).to_contain_text("ERROR")
        await select.press("Enter")
        await expect(select).to_have_value("ERROR")
        self.assertEqual(await select.evaluate("select => select.testEvents"), ["input", "change"])
        await select.click()
        await menu.get_by_role("option", name="INFO", exact=False).click()
        await expect(select).to_have_value("INFO")
        await expect(select).to_be_focused()
        await select.click()
        await select.evaluate("select => {select.testEvents = [];}")
        await select.press("ArrowUp")
        await select.press("Tab")
        await expect(menu).to_have_count(0)
        await expect(select).not_to_be_focused()
        await expect(select).to_have_value("ERROR")
        self.assertEqual(await select.evaluate("select => select.testEvents"), ["input", "change"])
        await select.click()
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await expect(menu).to_have_count(0)
        await select.click()
        await select.select_option("ERROR")
        await expect(menu).to_have_count(0)
        await expect(select).to_have_value("ERROR")
        self.assertEqual(self.errors, [])

    async def test_shared_dropdown_touch_stays_inside_mobile_viewport(self):
        context = await self.browser.new_context(
            viewport={"width": 390, "height": 844}, has_touch=True,
            storage_state=await self.context.storage_state(),
        )
        try:
            page = await context.new_page()
            await page.goto(self.url + "/ha-sauna")
            panel = page.locator("ha-sauna-panel")
            await expect(panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
            await panel.locator('.main-tabs [data-action="settings"]').tap()
            await panel.locator('[data-action="settings-menu"]').tap()
            await panel.locator('[data-action="settings-section:maintenance"]').tap()
            select = panel.locator("#log-level")
            await select.tap()
            menu = panel.get_by_role("listbox", name="Protokollstufe", exact=True)
            await expect(menu).to_be_visible()
            box = await menu.bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertGreaterEqual(box["y"], 0)
            self.assertLessEqual(box["x"] + box["width"], 390)
            self.assertLessEqual(box["y"] + box["height"], 844)
            index = await select.evaluate("select => [...select.options].findIndex(option => option.value === 'ERROR')")
            await menu.locator(f'[data-select-index="{index}"]').tap()
            await expect(select).to_have_value("ERROR")
            await expect(select).to_be_focused()
            await expect(menu).to_have_count(0)
        finally:
            await context.close()

    async def test_fullscreen_keeps_tabs_and_opens_real_home_assistant_sidebar(self):
        user = await self.hass.auth.async_create_user(
            "Normal fullscreen user", group_ids=[GROUP_ID_USER]
        )
        refresh = await self.hass.auth.async_create_refresh_token(user, client_id=self.url + "/")
        normal_tokens = json.dumps({
            "hassUrl": self.url, "clientId": self.url + "/",
            "access_token": self.hass.auth.async_create_access_token(refresh),
            "refresh_token": refresh.token, "expires": (time.time() + 1800) * 1000,
            "expires_in": 1800,
        })
        admin_tokens = await self.page.evaluate("localStorage.getItem('hassTokens')")
        for admin, tokens in ((True, admin_tokens), (False, normal_tokens)):
            for width in (1440, 390):
                with self.subTest(admin=admin, width=width):
                    context = await self.browser.new_context(viewport={"width": width, "height": 1080})
                    try:
                        await context.add_init_script(
                            "localStorage.setItem('hassTokens', " + json.dumps(tokens) + ");"
                        )
                        page = await context.new_page()
                        page.on("pageerror", lambda error: self.errors.append(str(error)))
                        await page.goto(self.url + "/ha-sauna")
                        panel = page.locator("ha-sauna-panel")
                        await expect(panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
                        tabs = panel.locator(".main-tabs")
                        menu = panel.locator('[data-action="menu"]')
                        fullscreen = panel.locator('[data-action="fullscreen"]')
                        original_kiosk = await panel.evaluate("element => Boolean(element.hass.kioskMode)")
                        original_docked = await panel.evaluate("element => element.hass.dockedSidebar")
                        await expect(menu).to_be_hidden()
                        await fullscreen.click()
                        await page.wait_for_function("document.fullscreenElement === document.documentElement")
                        await page.wait_for_function("document.querySelector('home-assistant').hass.kioskMode === true")
                        await expect(menu).to_be_visible()
                        actions = ["history", "settings"] + (["details"] if admin else [])
                        for action in actions:
                            tab = tabs.locator(f'[data-action="{action}"]')
                            await tab.click()
                            await expect(tab).to_have_attribute("aria-current", "page")
                            await expect(tabs).to_be_visible()

                        sidebar = page.locator("ha-sidebar")
                        drawer = page.locator("home-assistant-main ha-drawer")
                        await expect(drawer).to_have_js_property("open", False)
                        await menu.click()
                        await expect(drawer).to_have_js_property("open", True)
                        # The actual HA navigation must appear, not just receive an event.
                        await expect(sidebar.locator('a[href="/ha-sauna"]')).to_be_in_viewport()
                        await expect(tabs).to_be_visible()
                        # Dismiss HA's modal drawer through its real outside-click surface.
                        await page.mouse.click(width - 8, 400)
                        await expect(drawer).to_have_js_property("open", False)
                        self.assertEqual(await panel.evaluate("element => element.hass.dockedSidebar"), original_docked)
                        self.assertTrue(await page.evaluate("document.fullscreenElement === document.documentElement"))
                        await tabs.locator('[data-action="overview"]').click()
                        await expect(panel.locator("#current")).to_be_visible()
                        await fullscreen.click()
                        await page.wait_for_function("document.fullscreenElement === null")
                        await page.wait_for_function(
                            "value => Boolean(document.querySelector('home-assistant').hass.kioskMode) === value",
                            arg=original_kiosk,
                        )
                        await expect(menu).to_be_hidden()
                        await expect(tabs).to_be_visible()
                        self.assertEqual(await panel.evaluate("element => element.hass.dockedSidebar"), original_docked)
                        if admin and width == 1440:
                            await page.evaluate("window.dispatchEvent(new CustomEvent('hass-kiosk-mode', {detail: {enable: true}}))")
                            await page.wait_for_function("document.querySelector('home-assistant').hass.kioskMode === true")
                            await fullscreen.click()
                            await page.wait_for_function("document.fullscreenElement === document.documentElement")
                            await fullscreen.click()
                            await page.wait_for_function("document.fullscreenElement === null")
                            self.assertTrue(await panel.evaluate("element => element.hass.kioskMode"))
                            self.assertEqual(await panel.evaluate("element => element.hass.dockedSidebar"), original_docked)
                    finally:
                        await context.close()
        self.assertEqual(self.errors, [])

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
        await expect(self.panel.get_by_role("button", name="Manuell steuern", exact=True)).to_be_visible()
        await expect(self.panel.get_by_role('slider', name='Lichthelligkeit einstellen')).to_be_disabled()

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
            await expect(panel.get_by_role("button", name="Manuell steuern", exact=True)).to_be_visible()
            await expect(panel.get_by_role('slider', name='Lichthelligkeit einstellen')).to_be_disabled()
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
        retain_session(self.runtime)
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
        for second in range(30, 6030):
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
                pending_routes = set()

                async def delay_archive_page(route):
                    task = asyncio.current_task()
                    pending_routes.add(task)
                    try:
                        if route.request.url.endswith("after=0"):
                            await route.continue_()
                            return
                        response = await route.fetch()
                        entered.set()
                        await release.wait()
                        await route.fulfill(response=response)
                    finally:
                        pending_routes.discard(task)

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
                    await overview.scroll_into_view_if_needed()
                    await expect(overview).to_be_in_viewport(ratio=1)
                    box = await overview.bounding_box()
                    self.assertIsNotNone(box)
                    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    hit = await overview.evaluate("""(node, point) => {
                      const target = node.getRootNode().elementFromPoint(point.x, point.y);
                      return {matches: target === node, element: target?.outerHTML};
                    }""", {"x": x, "y": y})
                    self.assertTrue(hit["matches"], hit["element"])
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
                    # Finish delayed fulfill calls before removing the route or
                    # starting the next role's subtest with new events.
                    if pending_routes:
                        await asyncio.gather(*tuple(pending_routes))
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
        await expect(current.locator(".dial-temperature .reading > tspan").nth(0)).to_have_text("68")
        await expect(current.locator(".dial-temperature .reading .reading-unit")).to_have_text("°C")
        await expect(current.locator('[data-instrument="humidity"] .reading > tspan').nth(0)).to_have_text("38")
        await expect(current.locator('[data-instrument="humidity"] .reading .reading-unit')).to_have_text("%")
        await expect(current.locator(".gauges")).not_to_contain_text("Ersatzmessung unten")
        await expect(current.locator('[role="alert"]')).to_contain_text("Temperatur oben")
        self.assertEqual(self.errors, [])

    async def test_empty_history_uses_card_palette_and_clears_overview(self):
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_appearance()
        editor = self.panel.locator('#appearance-settings')
        group = editor.locator('summary').filter(has_text='Grunddarstellung')
        if await group.locator('..').get_attribute('open') is None:
            await group.click()
        await editor.locator('[data-appearance-color="card_background"]').fill('#000000')
        await editor.locator('[data-appearance-color="muted_text"]').fill('#000000')
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        empty = self.panel.locator('#plots .card.empty')
        await expect(empty).to_be_visible()
        await expect(empty).to_have_css('background-color', 'rgb(5, 5, 5)')
        await expect(empty).to_have_css('color', 'rgb(255, 255, 255)')
        await expect(self.panel.locator('#history-overview')).to_be_empty()

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

    async def test_instrument_variants_preview_persist_and_restore_catalog_defaults(self):
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_appearance()
        editor = self.panel.locator("#appearance-settings")
        definitions = section("appearance")["instruments"]
        selected = {}
        for name, definition in definitions.items():
            field = editor.locator(f'[data-appearance-instrument="{name}"]')
            await expect(field).to_have_value(definition["default"])
            selected[name] = next(value for value in definition["options"]
                                  if value != definition["default"])
            await field.select_option(selected[name])
        self.assertEqual(self.runtime.configuration.appearance["instruments"],
                         {name: definition["default"] for name, definition in definitions.items()})
        await expect(editor.locator("#appearance-status")).to_contain_text("Vorschau")
        async with self.page.expect_response(lambda response: response.url.endswith("/appearance") and response.request.method == "POST") as saved:
            await editor.locator('[data-action="appearance-save"]').click()
        self.assertTrue((await saved.value).ok)
        self.assertEqual(self.entry.options["appearance"]["instruments"], selected)
        await self.page.reload()
        await expect(self.panel.locator('#current [data-action="operation"]')).to_be_visible(timeout=60000)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_appearance()
        editor = self.panel.locator("#appearance-settings")
        for name, value in selected.items():
            await expect(editor.locator(f'[data-appearance-instrument="{name}"]')).to_have_value(value)
        await editor.locator('[data-action="appearance-default"]').click()
        for name, definition in definitions.items():
            await expect(editor.locator(f'[data-appearance-instrument="{name}"]')).to_have_value(definition["default"])
        await editor.locator('[data-action="appearance-discard"]').click()
        for name, value in selected.items():
            await expect(editor.locator(f'[data-appearance-instrument="{name}"]')).to_have_value(value)
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
        await self.open_appearance()
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
        scale_defaults = section("appearance")["scales"]
        await expect(minimum).to_have_value(str(scale_defaults["temperature"]["default"]["minimum"]))
        await expect(editor.locator('[data-appearance-scale="temperature.maximum"]')).to_have_value(str(scale_defaults["temperature"]["default"]["maximum"]))
        await expect(editor.locator('[data-appearance-scale="humidity.maximum"]')).to_have_value(str(scale_defaults["humidity"]["default"]["maximum"]))
        # The remaining geometry case intentionally uses its own 50–110 °C scale.
        await editor.locator('[data-appearance-scale="temperature.maximum"]').fill("110")
        await temperature.fill("#19A6C8")
        await humidity.fill("#9A35C0")
        await warmup.fill("#123456")
        await expect(editor.locator("#appearance-status")).to_contain_text("Vorschau")
        self.assertEqual(await self.panel.evaluate("p=>p.appearanceColor('series_temperature')"), "#19A6C8")
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:temperature'].stroke"), "#19A6C8")
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:humidity'].stroke"), "#9A35C0")
        self.assertEqual(await self.panel.evaluate("p=>p.style.getPropertyValue('--sauna-main-background')"), "#081018")
        await expect(self.panel.locator('#current .dial-temperature path[stroke="#19A6C8"]')).to_have_count(1)
        await expect(self.panel.locator('#current [data-instrument="humidity"] path[stroke="#9A35C0"]')).to_have_count(1)
        self.assertEqual(await self.panel.locator('#tooltip [data-quantity="temperature"]').first.evaluate("node => getComputedStyle(node, '::before').backgroundColor"), 'rgb(25, 166, 200)')
        self.assertEqual(await self.panel.locator('#tooltip [data-quantity="humidity"]').first.evaluate("node => getComputedStyle(node, '::before').backgroundColor"), 'rgb(154, 53, 192)')
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
        await self.open_appearance()
        editor = self.panel.locator("#appearance-settings")
        await editor.locator("summary").filter(has_text="Messungen und Ereignisse").click()
        await expect(editor.locator('[data-appearance-color="series_temperature"]')).to_have_value("#19A6C8")
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value("50")
        await editor.locator('[data-action="appearance-default"]').click()
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value(str(scale_defaults["temperature"]["default"]["minimum"]))
        await editor.locator('[data-action="appearance-discard"]').click()
        await expect(editor.locator('[data-appearance-scale="temperature.minimum"]')).to_have_value("50")
        self.assertEqual(self.entry.runtime_data.session.session_id, session_id)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        # The rightmost point of the visible arc is 100 °C at 50–110 °C.
        # Send a real pointer event through the rendered SVG transform.
        dial = self.panel.locator('svg.dial-temperature')
        await dial.scroll_into_view_if_needed()
        print("BROWSER_ARC_LAYOUT", await dial.evaluate("""svg => {
          const chain = [];
          for (let node = svg; node; node = node.parentElement || node.getRootNode()?.host) {
            const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
            chain.push({tag: node.tagName, id: node.id, classes: node.getAttribute('class'),
              rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
              clientHeight: node.clientHeight, scrollHeight: node.scrollHeight,
              scrollTop: node.scrollTop, height: style.height, minHeight: style.minHeight,
              display: style.display, overflow: style.overflow, flex: style.flex});
          }
          return {viewport: {width: innerWidth, height: innerHeight}, chain};
        }"""), flush=True)
        await expect(dial).to_be_in_viewport(ratio=1)
        arc_point = await dial.evaluate("""svg => {
            const point = svg.createSVGPoint(); point.x = 255; point.y = 130;
            const screen = point.matrixTransform(svg.getScreenCTM());
            const target = svg.getRootNode().elementFromPoint(screen.x, screen.y);
            return {x: screen.x, y: screen.y,
              hitsTargetArc: !!target?.closest('[data-target-arc]'),
              hit: target?.outerHTML};
        }""")
        self.assertTrue(arc_point["hitsTargetArc"], arc_point["hit"])
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
        await expect(self.panel.locator("#current .scale-hint")).to_have_count(0)
        await expect(self.panel.locator('#current [data-instrument="temperature"] .target-reading')).to_have_text("100°C")
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
            self.assertEqual(
                light_background, dark_background,
                "the explicit saved panel palette is independent of the HA theme",
            )
            await light_page.screenshot(path=str(screenshots / "light_control_collapsed.png"), full_page=True)
            await light_panel.locator('[data-action="program-toggle"]').click()
            await light_page.screenshot(path=str(screenshots / "light_control_open.png"), full_page=True)
            await light_panel.locator('.main-tabs [data-action="settings"]').click()
            await self.open_appearance(light_panel)
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
        await expect(active).to_have_text("Benannt individual")
        await self.panel.locator('[data-action="program-toggle"]').click()
        await self.panel.locator('[data-action="program-select:other"]').click()
        await expect(self.panel.locator(".program-pending")).to_contain_text("Benannt other")
        self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, "individual")
        await self.panel.evaluate("p => p.refresh()")
        await expect(self.panel.locator(".program-pending")).to_contain_text("Benannt other")
        await self.panel.locator('[data-action="program-cancel-draft"]').click()
        await expect(active).to_have_text("Benannt individual")

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
        await expect(active).to_have_text("Individuell")
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
        await expect(active).to_have_text("Konstant")
        self.assertEqual(self.errors, [])

    async def test_running_program_requires_confirmation_and_reports_saved_choice(self):
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await expect(self.panel.locator("#current .control-main .oven-feedback, #current .control-main .light-feedback")).to_have_count(0)
        active_program = self.panel.locator('#current .program-current .program-active-label')
        await expect(active_program).to_have_text("Individuell")
        overrides = self.panel.locator("#current .manual-heater")
        await expect(overrides).to_be_visible()
        await expect(overrides.locator('[data-action="heater:true"]')).to_be_enabled()
        self.assertEqual(await overrides.locator('[data-action^="light:"]').count(), 0)
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        await self.panel.get_by_role("button", name="Ändern", exact=True).click()
        await expect(self.panel.locator("#program-choice-body")).to_be_visible()
        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)
        await self.panel.locator('[data-action="program-mode:program"]').click()
        choice = self.panel.locator(f'[data-action="program-select:{DEFAULT_PROGRAMS[0]["id"]}"]')
        await expect(choice).to_be_visible()
        # The open editor marks its draft choice, while the active summary and
        # backend stay unchanged until the separate confirmation succeeds.
        await expect(choice).to_have_attribute("aria-pressed", "true")
        await expect(choice).not_to_contain_text("Vorgemerkt")
        await expect(choice).not_to_have_css("box-shadow", "none")
        action = self.panel.locator('#program-choice-body [data-action="program-apply"]')
        await expect(action).to_have_text("Programm übernehmen")
        await expect(action).to_be_enabled()
        await expect(self.panel.locator('#current .program-pending')).to_contain_text(DEFAULT_PROGRAMS[0]["name"])
        await expect(active_program).to_have_text("Individuell")
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator("#program-choice-body")).to_be_visible()
        await expect(active_program).to_have_text("Individuell")
        await expect(overrides).to_be_visible()
        await self.panel.locator('[data-action="program-cancel-draft"]').click()
        await expect(action).to_have_count(0)
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden()
        await expect(active_program).to_have_text("Individuell")
        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)

        await self.panel.get_by_role("button", name="Ändern", exact=True).click()
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
            self.assertEqual((await saved.json())["selected_program_id"], DEFAULT_PROGRAMS[0]["id"])
            await expect(action).to_have_text("✓ Übernommen", timeout=15000)
            await expect(action).to_have_class("program-saved program-main")
            self.assertEqual(await operation_geometry(), operation_box)
        finally:
            release.set()
            if entered.is_set():
                await asyncio.wait_for(route_finished.wait(), 10)
            await self.page.unroute("**" + program_url, delay_program)
        self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, DEFAULT_PROGRAMS[0]["id"])
        await expect(active_program).to_have_text(DEFAULT_PROGRAMS[0]["name"])
        await expect(choice).to_have_attribute("aria-pressed", "true")
        await expect(choice).not_to_contain_text("Vorgemerkt")
        await expect(choice).to_have_css("background-color", default_css_color("ui_accent"))
        await self.panel.evaluate("p=>p.refresh()")
        await expect(active_program).to_have_text(DEFAULT_PROGRAMS[0]["name"])
        await expect(choice).to_have_attribute("aria-pressed", "true")
        await expect(action).to_have_count(0, timeout=5000)
        self.assertEqual(await operation_geometry(), operation_box)
        self.assertEqual(self.errors, [])

    async def test_running_editor_confirmation_keeps_its_open_geometry(self):
        for width in (1440, 390):
            with self.subTest(width=width):
                await self.panel.evaluate('''p => p.api(`/${p.entry}/program`, "POST",
                  {target_temperature_c: 70, final_temperature_c: 90, temperature_gangs: 3})''')
                if self.runtime.session is None:
                    await self.runtime.set_operation(True)
                await self.page.set_viewport_size({"width": width, "height": 2400})
                await self.page.reload()
                await expect(self.panel.locator('[data-action="program-toggle"]')).to_be_visible(timeout=60000)
                await self.panel.locator('[data-action="program-toggle"]').click()
                await self.panel.locator("#progression-end").fill("91")
                await self.panel.locator("#progression-end").press("Tab")
                action = self.panel.locator('[data-action="program-apply"]')
                await expect(action).to_have_text("Programm übernehmen")
                await self.page.evaluate("window.scrollTo(0, 0)")
                geometry = lambda: self.panel.evaluate('''p => ({
                  scrollY: window.scrollY,
                  boxes: ['.program-form', '.program-actions', '[data-action="program-apply"]',
                    '.manual-controls', '.gauges', '[data-action="operation"]'].map(selector => {
                      const r = p.$('#current ' + selector).getBoundingClientRect();
                      return {selector, x: r.x, y: r.y, width: r.width, height: r.height};
                    })
                })''')
                before = await geometry()
                self.assertEqual(before["scrollY"], 0)
                entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
                temperature_url = f"/api/ha_sauna/{self.entry.entry_id}/temperature"

                async def delay_confirmation(route):
                    response = await route.fetch()
                    entered.set()
                    await asyncio.sleep(0.5)
                    await release.wait()
                    try:
                        await route.fulfill(response=response)
                    finally:
                        finished.set()

                await self.page.route("**" + temperature_url, delay_confirmation)
                try:
                    await action.click()
                    await asyncio.wait_for(entered.wait(), 10)
                    await expect(action).to_have_text("Wird übernommen …")
                    await expect(self.panel.locator("#program-choice-body")).to_be_visible()
                    during = await geometry()
                    release.set()
                    await expect(action).to_have_text("✓ Übernommen")
                    await expect(self.panel.locator("#program-choice-body")).to_be_visible()
                    saved = await geometry()
                    self.assertEqual(during, before)
                    self.assertEqual(saved, before)
                    self.assertEqual(self.entry.options["parameters"]["final_temperature_c"], 91)
                    # The scheduled collapse after feedback is a separate action;
                    # only the still-open editor is compared here.
                finally:
                    release.set()
                    if entered.is_set():
                        await asyncio.wait_for(finished.wait(), 10)
                    await self.page.unroute("**" + temperature_url, delay_confirmation)
        self.assertEqual(self.errors, [])

    async def test_completed_field_then_kind_click_saves_both_choices_in_order(self):
        temperature_url = f"/api/ha_sauna/{self.entry.entry_id}/temperature"
        async def delay_field_save(route):
            response = await route.fetch()
            await asyncio.sleep(0.5)
            await route.fulfill(response=response)

        choices = (
            ("program-kind:steps", {"temperature_steps": [70, 81, 91]}),
            ("program-mode:constant", {"profile": "constant"}),
            ("program-mode:program", {"profile": DEFAULT_PROGRAMS[0]["id"]}),
        )
        for action, program_write in choices:
            with self.subTest(action=action):
                await self.panel.evaluate('''p => p.api(`/${p.entry}/program`, "POST",
                  {target_temperature_c: 70, final_temperature_c: 90, temperature_gangs: 3})''')
                await self.page.reload()
                await expect(self.panel.locator("#progression-end")).to_have_value("90", timeout=60000)
                self.assertIsNone(self.runtime.session)
                writes = []
                order = []

                def record_write(request):
                    if request.method == "POST" and request.url.endswith(("/temperature", "/program")):
                        kind = request.url.rsplit("/", 1)[-1]
                        writes.append((kind, request.post_data_json))
                        order.append(kind + " request")

                def record_response(response):
                    if response.request.method == "POST" and response.url.endswith("/temperature"):
                        order.append("temperature response")

                self.page.on("request", record_write)
                self.page.on("response", record_response)
                await self.page.route("**" + temperature_url, delay_field_save)
                try:
                    await self.panel.locator("#progression-end").fill("91")
                    # One physical click closes the changed field and chooses the kind or mode.
                    await self.panel.locator(f'[data-action="{action}"]').click()
                    await self.panel.evaluate(
                        "async p => { while (p.programRequest) await new Promise(r => setTimeout(r, 10)); }"
                    )
                    self.assertEqual(writes, [
                        ("temperature", {"final_temperature_c": 91}),
                        ("program", program_write),
                    ])
                    self.assertEqual(order, [
                        "temperature request", "temperature response", "program request",
                    ])
                    await self.panel.evaluate("p => p.refresh()")
                    if action == "program-kind:steps":
                        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"], 91)
                        self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (70, 81, 91))
                        await expect(self.panel.locator("[data-free-step]").nth(2)).to_have_value("91")
                    elif action == "program-mode:constant":
                        self.assertEqual(self.entry.runtime_data.configuration.program_mode, "constant")
                        self.assertIsNone(self.entry.runtime_data.configuration.selected_program_id)
                    else:
                        self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, DEFAULT_PROGRAMS[0]["id"])
                        self.assertEqual(self.entry.options["parameters"]["final_temperature_c"], DEFAULT_PROGRAMS[0]["end_c"])
                        await expect(self.panel.locator(f'[data-action="program-select:{DEFAULT_PROGRAMS[0]["id"]}"]')).to_have_attribute("aria-pressed", "true")
                    await expect(self.panel.locator(f'[data-action="{action}"]')).to_have_attribute("aria-pressed", "true")
                    self.assertEqual(len(writes), 2)
                    if action == "program-kind:steps":
                        mode = self.panel.locator('[data-action="program-mode:individual"]')
                        await expect(mode).to_contain_text("✓ Übernommen")
                        last_step = self.panel.locator("[data-free-step]").nth(2)
                        await last_step.fill("92")
                        self.assertTrue(await last_step.evaluate("el => el === el.getRootNode().activeElement"))
                        self.assertNotIn("✓ Übernommen", await mode.inner_text())
                        self.assertEqual(len(writes), 2)
                        program_url = f"/api/ha_sauna/{self.entry.entry_id}/program"
                        async with self.page.expect_response(
                            lambda response: response.url.endswith(program_url) and response.request.method == "POST"
                        ) as response_wait:
                            await last_step.press("Tab")
                        response = await response_wait.value
                        self.assertEqual(response.status, 200)
                        await self.panel.evaluate(
                            "async p => { while (p.programRequest) await new Promise(r => setTimeout(r, 10)); }"
                        )
                        self.assertEqual(writes, [
                            ("temperature", {"final_temperature_c": 91}),
                            ("program", {"temperature_steps": [70, 81, 91]}),
                            ("program", {"temperature_steps": [70, 81, 92]}),
                        ])
                        self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (70, 81, 92))
                finally:
                    await self.page.unroute("**" + temperature_url, delay_field_save)
                    self.page.remove_listener("request", record_write)
                    self.page.remove_listener("response", record_response)
        self.assertEqual(self.errors, [])

    async def test_unrelated_hass_updates_do_not_restart_an_inflight_idle_poll(self):
        state_url = f"/api/ha_sauna/{self.entry.entry_id}/state"
        await self.panel.evaluate(
            "async p => { while (p.busy) await new Promise(r => setTimeout(r, 10)); }"
        )
        self.assertIsNone(self.runtime.session)
        self.assertEqual(await self.panel.evaluate("p => p.statusPollInterval()"), 10000)
        previous_hass = await self.panel.evaluate_handle("p => p.hass")
        entered, release = asyncio.Event(), asyncio.Event()
        starts = []

        async def delay_state(route):
            starts.append(time.monotonic())
            first = len(starts) == 1
            response = await route.fetch()
            if first:
                entered.set()
            await asyncio.sleep(0.25)
            if first:
                await release.wait()
            await route.fulfill(response=response)

        await self.page.route("**" + state_url, delay_state)
        try:
            # The original start/timer/setter paths stay active. A real unrelated
            # HA entity reaches the panel through the actual frontend websocket.
            await self.panel.evaluate("p => p.start()")
            await asyncio.wait_for(entered.wait(), 10)
            self.hass.states.async_set("sensor.unrelated_panel_probe", "1")
            await self.hass.async_block_till_done()
            reached_setter = await self.panel.evaluate('''async p => {
              const deadline = performance.now() + 5000;
              while (p.hass.states['sensor.unrelated_panel_probe']?.state !== '1' && performance.now() < deadline)
                await new Promise(resolve => setTimeout(resolve, 10));
              return p.hass.states['sensor.unrelated_panel_probe']?.state === '1' && p.busy;
            }''')
            self.assertTrue(reached_setter, "the original hass setter receives the foreign update during the request")
            self.assertTrue(await self.panel.evaluate("(p, old) => p.hass !== old", previous_hass))
            release.set()
            for value in range(2, 11):
                self.hass.states.async_set("sensor.unrelated_panel_probe", str(value))
                await self.hass.async_block_till_done()
                await asyncio.sleep(0.1)
            await self.page.wait_for_timeout(400)
            self.assertEqual(await self.panel.evaluate("p => p.hass.states['sensor.unrelated_panel_probe'].state"), "10")
            self.assertEqual(len(starts), 1, [round(at - starts[0], 3) for at in starts])
            self.assertLess(time.monotonic() - starts[0], 10)
            self.assertEqual(await self.panel.evaluate("p => p.statusPollInterval()"), 10000)
        finally:
            release.set()
            await self.page.unroute("**" + state_url, delay_state)
            await previous_hass.dispose()
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
                    '.program-form', '.manual-controls', '.gauges', '[data-action="operation"]']
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
                    await expect(self.panel.locator('#current .program-info-popup')).to_contain_text("70 → 80 → 90 °C")
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

    async def test_idle_automatic_controls_require_explicit_manual_entry(self):
        await expect(self.panel.get_by_role("button", name="Manuell steuern", exact=True)).to_be_enabled()
        await expect(self.panel.locator('#current .manual-controls [data-action^="light:"]')).to_have_count(0)
        await expect(self.panel.locator('#current .manual-controls [data-action^="heater:"]')).to_have_count(0)
        await expect(self.panel.get_by_role('slider', name='Lichthelligkeit einstellen')).to_be_disabled()
        await expect(self.panel.locator('[data-action="light-editor"], [data-action="manual-light-overview"]')).to_have_count(0)
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")

    async def test_both_roles_control_manual_outputs_without_session_controls(self):
        from custom_components.ha_sauna.settings import async_set_appearance, async_set_control_mode

        appearance = self.runtime.configuration.as_options()["appearance"]
        appearance["instruments"]["light"] = "linear"
        await async_set_appearance(self.hass, self.entry, appearance)
        for admin in (True, False):
            with self.subTest(admin=admin):
                await async_set_control_mode(self.hass, self.entry, "automatic")
                user = await self.hass.auth.async_create_user(
                    f"Idle control {admin}",
                    group_ids=[GROUP_ID_ADMIN if admin else GROUP_ID_USER],
                )
                refresh = await self.hass.auth.async_create_refresh_token(user, client_id=self.url + "/")
                tokens = {"hassUrl": self.url, "clientId": self.url + "/",
                          "access_token": self.hass.auth.async_create_access_token(refresh),
                          "refresh_token": refresh.token, "expires": (time.time() + 1800) * 1000,
                          "expires_in": 1800}
                context = await self.browser.new_context(viewport={"width": 390, "height": 844})
                try:
                    await context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps(tokens)) + ");")
                    page = await context.new_page()
                    writes = []
                    page.on("request", lambda request: writes.append((request.url.rsplit("/", 1)[-1], request.post_data_json))
                            if request.method == "POST" and "/api/ha_sauna/" in request.url else None)
                    await page.goto(self.url + "/ha-sauna")
                    panel = page.locator("ha-sauna-panel")
                    field = panel.locator('.light-instrument [data-manual-light-value]')
                    await expect(field).to_be_visible(timeout=60000)
                    await expect(field).to_have_attribute("type", "range")
                    await expect(field).to_be_disabled()
                    self.assertIsNone(self.runtime.session)
                    async with page.expect_response(lambda response: response.url.endswith("/control-mode") and response.request.method == "POST") as entered:
                        await panel.get_by_role("button", name="Manuell steuern", exact=True).click()
                    self.assertTrue((await entered.value).ok)
                    await expect(field).to_be_enabled()
                    self.assertIsNone(self.runtime.session)
                    async with page.expect_response(lambda response: response.url.endswith("/light") and response.request.method == "POST") as applied:
                        await field.evaluate("input => { input.value = '60'; }")
                        await field.dispatch_event("change")
                    self.assertTrue((await applied.value).ok)
                    self.assertEqual(self.runtime.configuration.control_mode, "manual")
                    self.assertIsNone(self.runtime.session)
                    self.assertEqual(self.entry.options["control_mode"], "manual")
                    async with page.expect_response(lambda response: response.url.endswith("/heater") and response.request.method == "POST") as heated:
                        await panel.locator('.manual-heater [data-action="heater:true"]').click()
                    self.assertTrue((await heated.value).ok)
                    self.assertIsNone(self.runtime.session)
                    self.assertTrue(self.heater.is_on)
                    await expect(panel.locator('.manual-heater [data-action="heater:true"]')).to_have_attribute("aria-pressed", "true")
                    self.assertFalse(await panel.evaluate("p => p.state.operation_enabled"))
                    self.assertEqual([item for item in writes if item[0] in ("control", "heater")], [("heater", {"value": True})])
                    async with page.expect_response(lambda response: response.url.endswith("/heater") and response.request.method == "POST") as cooled:
                        await panel.locator('.manual-heater [data-action="heater:false"]').click()
                    self.assertTrue((await cooled.value).ok)
                    self.assertIsNone(self.runtime.session)
                    self.assertFalse(self.heater.is_on)
                    await expect(panel.locator('.manual-heater [data-action="heater:false"]')).to_have_attribute("aria-pressed", "true")
                    self.assertFalse(await panel.evaluate("p => p.state.operation_enabled"))
                    await expect(panel.locator('#current [data-action="operation"]')).to_have_count(0)
                    await expect(panel.locator('#current .control-main [data-action="control-mode:automatic"]')).to_be_enabled()
                    async with page.expect_response(lambda response: response.url.endswith("/control-mode") and response.request.method == "POST") as automatic:
                        await panel.locator('[data-action="control-mode:automatic"]').click()
                    self.assertTrue((await automatic.value).ok)
                    self.assertIsNone(self.runtime.session)
                    async with page.expect_response(lambda response: response.url.endswith("/control") and response.request.method == "POST") as started:
                        await panel.locator('#current [data-action="operation"]').click()
                    self.assertTrue((await started.value).ok)
                    identity = self.runtime.session.session_id
                    self.assertTrue(self.runtime.session.operation_enabled)
                    await expect(panel.locator('[data-action^="control-mode:"]')).to_have_count(0)
                    await self.runtime.set_operation(False)
                    await panel.evaluate("p => p.refresh()")
                    await expect(panel.locator('[data-action^="control-mode:"]')).to_have_count(0)
                    await expect(panel.locator('.manual-light [data-action="light:true"]')).to_be_disabled()
                    self.assertEqual(self.runtime.session.session_id, identity)
                    self.assertFalse(self.runtime.session.operation_enabled)
                    token = next(deadline.token for deadline in self.runtime.session.deadlines
                                 if deadline.purpose == "session_gap")
                    await self.runtime.finish_session_gap(token)
                finally:
                    await context.close()
        self.assertEqual(self.errors, [])

    async def test_round_light_keyboard_controls_brightness_without_a_session(self):
        from custom_components.ha_sauna.settings import async_set_appearance

        appearance = self.runtime.configuration.as_options()["appearance"]
        appearance["instruments"]["light"] = "round"
        await async_set_appearance(self.hass, self.entry, appearance)
        await self.panel.evaluate("p => p.refresh()")
        slider = self.panel.get_by_role("slider", name="Lichthelligkeit einstellen")
        await expect(slider).to_have_attribute("aria-disabled", "true")
        await self.panel.get_by_role("button", name="Manuell steuern", exact=True).click()
        await expect(slider).to_have_attribute("data-light-arc", "true")
        await expect(slider).to_have_attribute("aria-disabled", "false")
        await expect(self.panel.locator('.light-instrument input[type="range"]')).to_have_count(0)
        for key, expected in (("End", 100), ("Home", 0)):
            with self.subTest(key=key):
                async with self.page.expect_response(lambda response: response.url.endswith("/light") and response.request.method == "POST") as changed:
                    await slider.press(key)
                response = await changed.value
                self.assertTrue(response.ok)
                self.assertEqual(response.request.post_data_json, {"value": expected})
                await expect(slider).to_have_attribute("aria-valuenow", str(expected))
                await expect(self.panel.locator('#current .light-instrument [data-light-observation]')).to_have_text(re.compile(rf"^{expected}\s*%$"))
                self.assertIsNone(self.runtime.session)
                self.assertFalse(await self.panel.evaluate("p => p.state.operation_enabled"))
        self.assertEqual(self.errors, [])

    async def test_output_buttons_follow_observation_when_manual_commands_are_not_reported(self):
        await self.panel.get_by_role("button", name="Manuell steuern", exact=True).click()
        self.heater.accept_commands = False
        self.light.defer_state_writes = True
        try:
            for output in ("heater", "light"):
                async with self.page.expect_response(lambda response: response.url.endswith("/" + output) and response.request.method == "POST") as commanded:
                    await self.panel.locator(f'.manual-{output} [data-action="{output}:true"]').click()
                self.assertTrue((await commanded.value).ok)
                await expect(self.panel.locator(f'.manual-{output} .output-toggle [data-action="{output}:false"]')).to_have_attribute("aria-pressed", "true")
                await expect(self.panel.locator(f'.manual-{output} .output-toggle [data-action="{output}:true"]')).to_have_attribute("aria-pressed", "false")
                await expect(self.panel.locator(f'.manual-{output} [data-action="{output}:auto"]')).to_have_count(0)
            self.assertIs(self.runtime.controller.heater_override, True)
            self.assertGreater(self.runtime.device.light_output.manual_brightness, 0)
            self.assertIsNone(self.runtime.session)
            await expect(self.panel.locator('.manual-light [data-action="light:normal"]')).to_have_count(0)
        finally:
            self.heater.accept_commands = True
            self.light.defer_state_writes = False
        self.assertEqual(self.errors, [])

    async def test_return_to_auto_clears_overrides_while_outputs_and_session_stay_on(self):
        from custom_components.ha_sauna.settings import async_set_parameters

        previous_runtime = self.runtime
        await async_set_parameters(self.hass, self.entry, {"light_transition_seconds": 0}, partial=True)
        # Non-temperature options schedule an HA options-listener reload. Bind
        # the replacement only after HA has finished that asynchronous work.
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.assertIsNot(self.runtime, previous_runtime)
        self.assertFalse(self.runtime.closed or self.runtime.reconfiguring)
        self.assertEqual(self.runtime.configuration.parameters.values["light_transition_seconds"], 0)
        self.base = self.now = self.runtime._clock()
        self.runtime._clock = lambda: self.now
        await self.panel.evaluate("p => p.refresh()")
        async with self.page.expect_response(lambda response: response.url.endswith("/control") and response.request.method == "POST") as started:
            await self.panel.locator('#current [data-action="operation"]').click()
        self.assertTrue((await started.value).ok)
        await expect(self.panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
        self.assertIs(self.runtime, self.entry.runtime_data)
        identity = self.runtime.session.session_id
        phase = self.runtime.controller.phase
        for output in ("heater", "light"):
            with self.subTest(output=output):
                for value in (False, True):
                    action = str(value).lower()
                    async with self.page.expect_response(lambda response: response.url.endswith("/" + output) and response.request.method == "POST") as changed:
                        await self.panel.locator(f'.manual-{output} [data-action="{output}:{action}"]').click()
                    self.assertTrue((await changed.value).ok)
                    await expect(self.panel.locator(f'.manual-{output} [data-action="{output}:{action}"]')).to_have_attribute("aria-pressed", "true")
                await expect(self.panel.locator(f'.manual-{output} [data-action="{output}:auto"]')).to_have_attribute("aria-pressed", "false")
                async with self.page.expect_response(lambda response: response.url.endswith("/" + output) and response.request.method == "POST") as returned:
                    await self.panel.locator(f'.manual-{output} [data-action="{output}:auto"]').click()
                response = await returned.value
                self.assertTrue(response.ok)
                self.assertEqual(response.request.post_data_json, {"value": None})
                await expect(self.panel.locator(f'.manual-{output} [data-action="{output}:auto"]')).to_have_attribute("aria-pressed", "true")
                await expect(self.panel.locator(f'.manual-{output} [data-action="{output}:true"]')).to_have_attribute("aria-pressed", "true")
                self.assertEqual(self.runtime.session.session_id, identity)
                self.assertEqual(self.runtime.controller.phase, phase)
                self.assertTrue(self.runtime.session.operation_enabled)
        self.assertIsNone(self.runtime.controller.heater_override)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assertTrue(self.heater.is_on)
        self.assertTrue(self.light.is_on)
        self.assertEqual(self.errors, [])

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
            await expect(panel.locator('.manual-heater [data-action="heater:true"]')).to_be_enabled()
            await expect(panel.get_by_role('slider', name='Lichthelligkeit einstellen')).to_be_enabled()
            observation = await panel.evaluate("p => p.state.manual_controls.light.observation")
            self.assertTrue(observation["available"])
            requested = observation["brightness_percent"] == 0
            action = str(requested).lower()
            async with page.expect_response(lambda response: response.url.endswith("/light")
                                            and response.request.method == "POST") as saved:
                await panel.locator(f'[data-action="light:{action}"]').click()
            response = await saved.value
            self.assertTrue(response.ok)
            self.assertEqual(response.request.post_data_json, {"value": requested})
            await expect(panel.locator(f'[data-action="light:{action}"]')).to_have_attribute("aria-pressed", "true")
            await self.runtime.set_operation(False)
            await panel.evaluate("p => p.refresh()")
            for index in range(3):
                await expect(presets.nth(index)).to_be_disabled()
        finally:
            await context.close()
        self.assertEqual(self.errors, [])

    async def test_temperature_drag_cancels_on_detach_and_capture_loss(self):
        writes = []
        self.page.on("request", lambda request: writes.append(request.url)
                     if request.method == "POST" and request.url.endswith("/temperature") else None)
        await self.panel.locator(".target-temperature-handle").hover()
        await self.page.mouse.down()
        self.assertTrue(await self.panel.evaluate("p => !!p.temperatureInteraction"))
        await self.panel.evaluate("""p => {
          window.detachedPanel = p; window.panelParent = p.parentNode;
          p.remove();
        }""")
        await self.page.mouse.up()
        self.assertFalse(await self.page.evaluate("!!detachedPanel.temperatureInteraction"))
        await self.page.evaluate("panelParent.append(detachedPanel)")
        await self.set_source("upper_temperature", 65)
        await self.set_source("lower_temperature", 66)
        await self.panel.evaluate("""async p => {
          while (p.busy) await new Promise(resolve => setTimeout(resolve, 10));
          await p.refresh(true);
        }""")
        await expect(self.panel.locator(".dial-temperature .reading")).to_contain_text("65")
        self.assertFalse(await self.panel.evaluate("p => !!p.temperatureInteraction"))
        await self.panel.locator(".target-temperature-handle").hover()
        await self.page.mouse.down()
        await self.page.mouse.move(500, 500)
        self.assertTrue(await self.panel.evaluate("p => !!p.temperatureInteraction"))
        await self.panel.evaluate("""p => {
          const drag = p.temperatureInteraction;
          drag.svg.releasePointerCapture(drag.pointerId);
        }""")
        await self.page.mouse.move(510, 500)
        await self.page.mouse.up()
        self.assertFalse(await self.panel.evaluate("p => !!p.temperatureInteraction"))
        self.assertEqual(writes, [], "aborting either interaction sends no target command")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.console_errors, [])
        self.assertEqual(self.network_errors, [])
        self.assertEqual(self.ws_errors, [])

    async def test_design_settings_quantity_axes_and_mobile_navigation(self):
        screenshots = Path(os.environ.get("HA_SAUNA_BROWSER_ARTIFACT_DIR", tempfile.mkdtemp())) / "design"
        screenshots.mkdir(parents=True, exist_ok=True)
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_settings_section("appearance")
        editor = self.panel.locator("#appearance-settings")
        self.assertEqual(await editor.locator('[data-appearance-color="series_upper"], [data-appearance-color="series_lower"]').count(), 0)
        featured = [item["id"] for item in section("appearance")["colors"]
                    if item.get("featured") and not item.get("hidden")]
        self.assertEqual(await editor.locator('input[type="color"]:visible').evaluate_all(
            "inputs => inputs.map(input => input.dataset.appearancePicker)"), featured)
        self.assertEqual(await self.panel.locator('[data-settings-section]:visible').count(), 1)
        self.assertEqual(await self.panel.evaluate("""p => p.state.parameters.filter(d => {
          const fields=p.shadowRoot.querySelectorAll(`#parameters input[name="${d.key}"]`);
          if (d.settings_group === "programs") return fields.length !== 0;
          return fields.length!==1 || fields[0].form?.id!=="settings-parameters";
        }).map(d=>d.key)"""), [])
        await editor.locator('[data-appearance-color="series_temperature"]').fill("#E64AEB")
        await editor.locator('[data-appearance-color="series_humidity"]').fill("#23DDCC")
        await self.page.screenshot(path=str(screenshots / "settings_desktop.png"), full_page=True)
        await self.page.set_viewport_size({"width": 390, "height": 844})
        toggle = self.panel.locator('[data-action="settings-menu"]')
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(self.panel.locator(".settings-navigation")).to_be_hidden()
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        for button in await self.panel.locator(".settings-navigation button").all():
            await expect(button).to_be_in_viewport(ratio=1)
        await self.panel.locator('.settings-navigation button').first.focus()
        await self.page.keyboard.press("Escape")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(self.panel.locator(".settings-navigation")).to_be_hidden()
        await expect(toggle).to_be_focused()
        await self.open_settings_section("sensors")
        await self.open_parameter_group("door_open_drop_c")
        await expect(self.panel.locator('input[name="door_open_drop_c"]')).to_be_visible()
        mobile_info = self.panel.locator('[data-action="program-info:parameter:door_open_drop_c"]')
        mobile_field = self.panel.locator('input[name="door_open_drop_c"]')
        await mobile_info.scroll_into_view_if_needed()
        field_before_info = await mobile_field.bounding_box()
        await mobile_info.click()
        mobile_help = self.panel.locator('#help-door_open_drop_c')
        await expect(mobile_help).to_be_visible()
        help_box = await mobile_help.bounding_box()
        self.assertGreaterEqual(help_box["x"], 0)
        self.assertLessEqual(help_box["x"] + help_box["width"], 390)
        self.assertEqual(await mobile_field.bounding_box(), field_before_info)
        await mobile_info.press("Escape")
        await expect(mobile_help).to_be_hidden()
        await self.open_settings_section("appearance")
        await self.page.screenshot(path=str(screenshots / "settings_mobile.png"), full_page=True)
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"), 390)
        await self.page.set_viewport_size({"width": 1440, "height": 1080})
        await expect(self.panel.locator(".settings-navigation")).to_be_visible()
        await self.runtime.set_operation(True)
        for second in (1, 10, 20):
            self.now = self.base + timedelta(seconds=second)
            await self.set_source("upper_temperature", 80 + second / 10)
            await self.set_source("upper_humidity", 30 + second / 10)
            self.runtime.archive.append("detector_trace", self.now, {
                "at": self.now.isoformat(), "signals": [], "conditions": {}, "checks": {}, "holds": {},
                "metrics": {position: {"door_temperature_slope": -second / 10,
                                       "door_humidity_delta": -second / 20}
                            for position in ("upper", "lower")},
            }, self.runtime.session.session_id)
        await self.runtime.archive.flush()
        await self.panel.evaluate("p=>p.refresh()")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        chart = self.panel.locator("svg.session-chart")
        await expect(chart.locator(".axis-temperature").first).to_have_css("fill", "rgb(230, 74, 235)")
        await expect(chart.locator(".axis-humidity").first).to_have_css("fill", "rgb(35, 221, 204)")
        await expect(chart).to_contain_text("Temperatur (°C)")
        await expect(chart).to_contain_text("Luftfeuchte (%)")
        await self.page.screenshot(path=str(screenshots / "history_desktop.png"), full_page=True)
        await self.panel.locator('.main-tabs [data-action="details"]').click()
        await self.panel.locator('[data-action="diagnostics"]').click()
        for metric, color in (("door_temperature_slope", "rgb(230, 74, 235)"),
                              ("door_humidity_delta", "rgb(35, 221, 204)")):
            for position in ("upper", "lower"):
                await expect(self.panel.locator(f'[data-series="detector_{metric}_{position}"]')).to_have_css("stroke", color)
            await expect(self.panel.locator(f'[data-series="detector_{metric}_lower"]')).to_have_css("stroke-dasharray", "8px, 5px")
        await self.page.screenshot(path=str(screenshots / "diagnostics_desktop.png"), full_page=True)
        self.assertEqual(self.errors, [])

    async def test_design_light_instrument_applies_changes_and_keeps_observation_separate(self):
        await self.set_instrument_style("light", "linear")
        screenshots = Path(os.environ.get("HA_SAUNA_BROWSER_ARTIFACT_DIR", tempfile.mkdtemp())) / "design"
        screenshots.mkdir(parents=True, exist_ok=True)
        await self.panel.get_by_role("button", name="Manuell steuern", exact=True).click()
        await expect(self.panel.locator('.light-instrument #manual-light-value-overview')).to_be_visible()
        await self.panel.locator('[data-action="control-mode:automatic"]').click()
        await self.panel.locator('#current [data-action="operation"]').click()
        await expect(self.panel.locator('#current [data-light-status]')).to_have_count(0)
        field = self.panel.locator("#manual-light-value-overview")
        await field.evaluate("input => { input.value = '60'; }")
        await field.dispatch_event("change")
        status = self.panel.locator('#current [data-light-observation]')
        await expect(status).to_have_text("60 %")
        await self.panel.evaluate("p=>p.refresh()")
        await expect(status).to_have_text("60 %")
        await expect(field).to_have_value("60")
        self.assertEqual(await self.panel.evaluate("p=>p.state.manual_controls.light.manual"), 60)
        await self.page.screenshot(path=str(screenshots / "light_desktop.png"), full_page=True)
        self.light.defer_state_writes = True
        self.hass.states.async_set(self.runtime.device.bindings["light"], "unavailable")
        await self.hass.async_block_till_done()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(status).to_have_text("– %")
        await expect(self.panel.locator('.light-instrument')).to_contain_text("Rückmeldung fehlt")
        await expect(self.panel.locator('.light-instrument')).not_to_contain_text("Vorgabe")
        await expect(self.panel.locator('.light-instrument [data-light-target]')).to_have_count(0)
        await expect(self.panel.get_by_role("slider", name="Lichthelligkeit einstellen")).to_have_value("60")
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.page.screenshot(path=str(screenshots / "light_mobile.png"), full_page=True)
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"), 390)
        self.assertEqual(self.errors, [])

    async def test_settings_metadata_refresh_preserves_drafts_and_native_validity(self):
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        self.assertEqual(await self.panel.evaluate("""panel => panel.state.parameters
          .filter(definition => definition.settings_group === 'programs')
          .filter(definition => panel.shadowRoot.querySelector(`#parameters input[name="${definition.key}"]`))
          .map(definition => definition.key)"""), [])
        target = self.panel.locator('#parameters input[name="readiness_offset_c"]')
        await self.open_parameter_group("readiness_offset_c")
        await target.fill("55.5")
        before = await target.element_handle()
        await self.panel.evaluate("""p => {
          const state = structuredClone(p.state);
          for (const definition of state.parameters) {
            if (definition.key === "readiness_offset_c") {
              definition.minimum = 50;
              definition.maximum = 95;
              definition.step = 1;
            }
          }
          p.acceptState(state); p.drawSettings();
        }""")
        self.assertTrue(await target.evaluate("(input, old) => input === old", before))
        await expect(target).to_have_value("55.5")
        await expect(target).to_have_attribute("min", "50")
        await expect(target).to_have_attribute("max", "95")
        await expect(target).to_have_attribute("step", "1")
        self.assertTrue(await target.evaluate("input => input.validity.stepMismatch"))
        await target.fill("55")
        self.assertTrue(await target.evaluate("input => input.checkValidity()"))
        submitted = await target.evaluate("""input => {
          let submitted = false;
          input.form.addEventListener("submit", event => {
            event.preventDefault(); event.stopImmediatePropagation(); submitted = true;
          }, {capture: true, once: true});
          input.form.requestSubmit();
          return submitted;
        }""")
        self.assertTrue(submitted, "fresh input metadata allows native form submission")
        self.assertEqual(self.errors, [])

    async def test_return_from_hidden_discovers_a_whole_finished_session(self):
        await self.runtime.set_operation(True)
        retain_session(self.runtime)
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
        retain_session(self.runtime)
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

    async def test_program_editor_saves_actual_whole_degree_targets(self):
        program_id = self.entry.options["temperature_programs"][0]["id"]
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        row = self.panel.locator(f'#program-library [data-program-id="{program_id}"]')
        await row.locator(f'[data-action="program-edit:{program_id}"]').click()
        await row.locator('[data-program-field="start_c"]').fill("80.5")
        await row.locator('[data-program-field="end_c"]').fill("90.4")
        await row.locator('[data-program-field="distribution_gangs"]').fill("4")
        await row.locator('[data-action="program-finish"]').click()
        async with self.page.expect_response(lambda response: response.url.endswith("/programs")
                                            and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="program-save"]').click()
        response = await result.value
        self.assertTrue(response.ok)
        program = next(item for item in response.request.post_data_json["programs"]
                       if item["id"] == program_id)
        self.assertEqual((program["start_c"], program["end_c"], program["distribution_gangs"]), (81, 90, 4))
        await self.hass.async_block_till_done()
        self.assertEqual(next(item for item in self.entry.options["temperature_programs"]
                              if item["id"] == program_id), program)

        await row.locator(f'[data-action="program-edit:{program_id}"]').click()
        await row.locator(f'[data-action="catalog-kind:steps:{program_id}"]').click()
        steps = row.locator('[data-program-step]')
        self.assertEqual(await steps.evaluate_all("inputs => inputs.map(input => Number(input.value))"), [81, 84, 87, 90])
        for index, value in enumerate(("80.5", "84.4", "86.5", "90.4")):
            await steps.nth(index).fill(value)
        await row.locator('[data-action="program-finish"]').click()
        async with self.page.expect_response(lambda response: response.url.endswith("/programs")
                                            and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="program-save"]').click()
        response = await result.value
        self.assertTrue(response.ok)
        program = next(item for item in response.request.post_data_json["programs"]
                       if item["id"] == program_id)
        self.assertEqual(program["temperature_steps"], [81, 84, 87, 90])
        await self.hass.async_block_till_done()
        self.assertEqual(next(item for item in self.entry.options["temperature_programs"]
                              if item["id"] == program_id)["temperature_steps"], (81, 84, 87, 90))
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        async with self.page.expect_response(lambda response: response.url.endswith("/program")
                                            and response.request.method == "POST") as result:
            await self.panel.locator('[data-action="program-mode:program"]').click()
        self.assertTrue((await result.value).ok)
        await expect(self.panel.locator(f'[data-action="program-select:{program_id}"]')).to_contain_text("81 → 84 → 87 → 90 °C")
        self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (81, 84, 87, 90))
        self.assertEqual(self.entry.runtime_data.configuration.parameters.values["target_temperature_c"], 81)
        self.assertEqual(self.errors, [])

    async def test_catalog_editor_sorting_and_persisted_program_ids(self):
        entity = "event.catalog_sauna_button"
        self.hass.states.async_set(entity, "unknown", {
            "device_class": "button",
            "event_types": ["btn_down", "btn_up", "single_push", "double_push",
                            "triple_push", "long_push"],
        })
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "bindings": {**self.entry.options["bindings"], "control_input": entity},
            "control_input_mode": "button",
        })
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        await self.panel.evaluate("p => p.refresh(true)")
        state = await self.panel.evaluate("p => p.api(`/${p.entry}/state`)")
        self.assertEqual(state["configuration"]["control_input_mode"], "button")
        self.assertEqual(state["button_session_gestures"], ["long", "double", "triple"])
        await self.panel.locator('[data-action="program-mode:program"]').click()
        await expect(self.panel.locator(f'[data-action="program-select:{DEFAULT_PROGRAMS[0]["id"]}"]')).to_have_attribute(
            "aria-pressed", "true", timeout=15000
        )
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        gesture = self.panel.locator('#button-session-gesture')
        async with self.page.expect_response(lambda response: response.url.endswith("/button-gesture")
                                            and response.request.method == "POST") as gesture_saved:
            await gesture.select_option("double")
        self.assertTrue((await gesture_saved.value).ok)
        self.assertEqual(self.entry.options["button_session_gesture"], "double")
        await self.panel.evaluate("p => p.refresh()")
        await expect(gesture).to_have_value("double")
        async with self.page.expect_response(lambda response: response.url.endswith("/button-program")
                                            and response.request.method == "POST") as result:
            await self.panel.locator('#button-program').select_option(DEFAULT_PROGRAMS[-2]["id"])
        self.assertTrue((await result.value).ok)
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options['button_program'], DEFAULT_PROGRAMS[-2]["id"])
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
        # Raw pointer coordinates need the source and destination in the
        # scrollable settings viewport after the preceding discard action.
        await rows.first.evaluate("row => row.scrollIntoView({block: 'center'})")
        await expect(handle).to_be_in_viewport(ratio=1)
        await expect(target).to_be_in_viewport(ratio=1)
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
        self.assertEqual(self.entry.options['selected_program_id'], DEFAULT_PROGRAMS[0]["id"])
        self.assertEqual(self.entry.options['button_program'], DEFAULT_PROGRAMS[-2]["id"])

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
            self.assertEqual(self.entry.options['selected_program_id'], DEFAULT_PROGRAMS[0]["id"])
            self.assertEqual(self.entry.options['button_program'], DEFAULT_PROGRAMS[-2]["id"])
            self.assertLessEqual(await panel.evaluate("p => p.shadowRoot.querySelector('main').scrollWidth"), 390)
            self.assertEqual(errors, [])
        finally:
            await context.close()
        self.assertEqual(self.errors, [])

    async def test_cooling_count_stays_right_and_dashboard_uses_available_width(self):
        await device_tests.DevicePathTests.prepare_gang_after_run(self)
        await self.panel.evaluate("p => p.refresh()")
        await expect(self.panel.locator('#current [data-phase="nachlauf"]')).to_be_visible()
        for width in (390, 740, 800, 1000):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 900})
                read_geometry = """p => {
                  const line = p.$('#current .state-line').getBoundingClientRect();
                  const phase = p.$('#current .phase-time').getBoundingClientRect();
                  const badge = p.$('#current .state-line .badge').getBoundingClientRect();
                  const current = p.$('#current');
                  return {lineTop: line.top, lineRight: line.right,
                    badgeTop: badge.top, badgeRight: badge.right,
                    phaseRight: phase.right, badgeLeft: badge.left,
                    columns: getComputedStyle(p.$('.dashboard')).gridTemplateColumns.split(' ').length,
                    availableWidth: p.clientWidth, scrollWidth: current.scrollWidth,
                    clientWidth: current.clientWidth};
                }"""
                before_render = await self.panel.evaluate(read_geometry)
                await self.panel.evaluate("p => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                geometry = await self.panel.evaluate(read_geometry)
                if before_render != geometry:
                    print("BROWSER_COOLING_RESIZE", {"width": width, "before": before_render, "after": geometry})
                self.assertEqual(geometry["badgeTop"], geometry["lineTop"])
                self.assertEqual(geometry["badgeRight"], geometry["lineRight"])
                self.assertLess(geometry["phaseRight"], geometry["badgeLeft"])
                self.assertEqual(geometry["columns"], 1 if geometry["availableWidth"] <= 800 else 2)
                self.assertLessEqual(geometry["scrollWidth"], geometry["clientWidth"])

    async def test_oven_cooling_end_is_a_split_control_for_admin_and_normal_user(self):
        await device_tests.DevicePathTests.prepare_gang_after_run(self)
        self.now += timedelta(seconds=10)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        end_cooling = self.panel.locator('#current').get_by_role(
            "button", name="Kühlung beenden", exact=True
        )
        await expect(end_cooling).to_be_visible()
        blocked_reason = await self.panel.evaluate("p => p.state.manual_controls.heater.blocked_on_reason")
        self.assertTrue(blocked_reason)
        await expect(self.panel.locator('.manual-heater [data-action="heater:true"]')).to_be_disabled()
        await expect(self.panel.locator('.manual-heater [data-action="heater:true"]')).to_have_attribute("title", blocked_reason)
        await expect(self.panel.locator('#current .operation-control.split')).to_have_count(1)
        await expect(self.panel.locator('#current .operation-control.split')).to_contain_text("Ausschalten")
        await self.panel.locator('[data-action="details"]').click()
        self.assertEqual(await self.panel.locator('#details [data-action^="end-phase:"]').count(), 0)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        self.assertFalse(self.heater.is_on)
        user = await self.hass.auth.async_create_user("Normal cooling user", group_ids=[GROUP_ID_USER])
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
            page.on("pageerror", lambda error: self.errors.append(str(error)))
            await page.goto(self.url + "/ha-sauna")
            panel = page.locator("ha-sauna-panel")
            split = panel.locator('#current .operation-control.split')
            await expect(split).to_be_visible(timeout=60000)
            end_cooling_user = split.get_by_role("button", name="Kühlung beenden", exact=True)
            await expect(end_cooling_user).to_be_enabled()
            await expect(panel.locator('.manual-heater [data-action="heater:true"]')).to_be_disabled()
            await expect(panel.locator('.manual-heater [data-action="heater:true"]')).to_have_attribute("title", blocked_reason)
            async with page.expect_response(lambda response: response.url.endswith("/finish_phase")
                                            and response.request.method == "POST") as result:
                await end_cooling_user.click()
            self.assertTrue((await result.value).ok)
            await expect(panel.locator('#current [data-phase="aufheizen"]')).to_be_visible()
            self.assertEqual(await panel.locator('#current [data-action^="end-phase:"]').count(), 0)
        finally:
            await context.close()
        await self.panel.evaluate("p=>p.refresh()")
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
                await self.set_source(pos + "_humidity", 15)
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
        # Normal history exposes only the leading measurement height.
        await expect(self.panel.locator('canvas.history-curves[role="img"]')).to_be_visible()
        self.assertFalse(await self.panel.evaluate("p=>p.historyDetail"))
        await expect(self.panel.locator('[data-action="history-detail"]')).to_have_count(0)
        await expect(self.panel.locator('[data-history-positions]')).to_have_count(0)
        self.assertEqual(await self.panel.evaluate("p=>p.positions.size"), 1)
        chart = self.panel.locator("svg.session-chart")
        # Hover over a recorded sample, not the empty lead-in before the session.
        sample_time = (self.base + timedelta(seconds=7)).timestamp() * 1000
        sample_x = await self.panel.evaluate("(p,t)=>{const [a,b]=p.window;return (65+(t-a)/(b-a)*1070)/1200*p.shadowRoot.querySelector('svg.session-chart').getBoundingClientRect().width;}", sample_time)
        legend_before = await self.panel.locator("#history-legends").bounding_box()
        plot_before = await chart.bounding_box()
        await chart.hover(position={"x": sample_x, "y": 200})
        legend_after = await self.panel.locator("#history-legends").bounding_box()
        plot_after = await chart.bounding_box()
        self.assertAlmostEqual(legend_before["y"] - plot_before["y"], legend_after["y"] - plot_after["y"], delta=1)
        await expect(self.panel.locator("#tooltip")).to_be_visible()
        await expect(self.panel.locator("#tooltip")).to_contain_text("Temperatur:")
        curve_box = await chart.bounding_box()
        readout_box = await self.panel.locator("#tooltip").bounding_box()
        frame = self.panel.locator(".history-plot-frame")
        await expect(frame.locator("#plots")).to_have_count(1)
        await expect(frame.locator("#history-navigation")).to_have_count(1)
        await expect(frame.locator("#history-overview canvas")).to_have_count(0)
        alignment = await self.panel.evaluate("""p => {
          const chart = p.historyChart, plot = chart.surface.getBoundingClientRect();
          const selection = p.$('#history-overview [data-history-window]').getBoundingClientRect();
          const [start, end] = chart.domain, model = chart.model;
          const screenX = time => plot.left + (model.left + (time - start) / (end - start)
            * (model.right - model.left)) / model.width * plot.width;
          return {left: selection.left, right: selection.right,
            expectedLeft: screenX(p.window[0]), expectedRight: screenX(p.window[1])};
        }""")
        self.assertAlmostEqual(alignment["left"], alignment["expectedLeft"], delta=1)
        self.assertAlmostEqual(alignment["right"], alignment["expectedRight"], delta=1)
        await expect(frame.locator("#history-inspection")).to_have_count(0)
        navigation_box = await frame.locator("#history-navigation").bounding_box()
        inspection_box = await self.panel.locator("#history-inspection").bounding_box()
        self.assertGreaterEqual(navigation_box["y"], curve_box["y"] + curve_box["height"] - 1)
        self.assertLessEqual(navigation_box["y"] + navigation_box["height"], inspection_box["y"] + 1)
        areas = self.panel.locator(".history-background [data-history-annotations] rect")
        self.assertGreater(await areas.count(), 0)
        for area in await areas.all():
            await expect(area).to_have_css("stroke", "none")
        infusion = self.panel.locator(".history-background [data-history-annotations] line.infusion")
        await expect(infusion).to_have_count(1)
        await expect(infusion).not_to_have_css("stroke", "none")
        self.assertGreaterEqual(readout_box["y"], curve_box["y"] + curve_box["height"])
        self.assertGreaterEqual(readout_box["x"], curve_box["x"])
        self.assertLessEqual(readout_box["x"] + readout_box["width"], curve_box["x"] + curve_box["width"] + 1)
        # Real archived samples differ only in temperature text width. The
        # neighbouring humidity label and value must not move between hovers.
        readout = self.panel.locator("#tooltip .history-tooltip-values > div:not([hidden])")
        temperature = readout.get_by_text("Temperatur:", exact=True).locator("..").locator("span").nth(1)
        humidity_label = readout.get_by_text("Luftfeuchte:", exact=True)
        humidity = humidity_label.locator("..").locator("span").nth(1)
        humidity_positions = []
        for second, temperature_text in ((5, "73 °C"), (7, "73,4 °C")):
            at = (self.base + timedelta(seconds=second)).timestamp() * 1000
            x = await self.panel.evaluate("(p,t)=>{const [a,b]=p.window;return (65+(t-a)/(b-a)*1070)/1200*p.shadowRoot.querySelector('svg.session-chart').getBoundingClientRect().width;}", at)
            await chart.hover(position={"x": x, "y": 200})
            await expect(temperature).to_have_text(temperature_text)
            await expect(humidity).to_have_text("15 %")
            humidity_positions.append((await humidity_label.bounding_box(), await humidity.bounding_box()))
        for before, after in zip(*humidity_positions):
            for coordinate in ("x", "y"):
                self.assertAlmostEqual(before[coordinate], after[coordinate], delta=0.1)
        await self.panel.locator("#session").hover()
        await expect(self.panel.locator("#tooltip")).to_be_hidden()
        legend_hidden = await self.panel.locator("#history-legends").bounding_box()
        plot_hidden = await chart.bounding_box()
        self.assertAlmostEqual(legend_after["y"] - plot_after["y"],
                               legend_hidden["y"] - plot_hidden["y"], delta=1)
        await expect(self.panel).to_have_js_property("busy", False)
        await self.panel.evaluate("panel => panel.refresh(true)")
        await expect(self.panel.locator("#tooltip")).to_be_hidden()
        # Valid but hostile palette: inspect rendered text consumers, not only
        # the palette variables. Curves must keep the chosen measurement color.
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await self.open_appearance()
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
        await info_button.press("Escape")
        await expect(popup).to_be_hidden()
        await expect(info_button).to_be_focused()
        await info_button.press("Enter")
        await expect(popup).to_be_visible()
        await info_button.click()
        await expect(popup).to_be_hidden()
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        for section, link in (("sensors", "Sensoren und Geräte zuordnen"), ("maintenance", "Home-Assistant-Protokoll öffnen")):
            await self.open_settings_section(section)
            await expect(self.panel.get_by_role("link", name=link)).to_have_css("color", "rgb(255, 255, 255)")
        await self.open_settings_section("appearance")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await chart.hover(position={"x": sample_x, "y": 200})
        # Returning through the main History tab selects the single-height
        # overview, whose label intentionally omits the height suffix.
        # Persistent readout nodes retain the hidden second measurement height.
        visible_values = self.panel.locator("#tooltip .history-tooltip-values > div:not([hidden])")
        label = visible_values.get_by_text("Temperatur:", exact=True)
        await expect(label).to_have_count(1)
        await expect(label).to_be_visible()
        await expect(self.panel.locator("#tooltip .history-tooltip-time")).to_have_text(re.compile(r"^\d{2}:\d{2}$"))
        await expect(self.panel.locator("#tooltip .history-tooltip-status")).to_have_text("Saunastatus · Aufheizen")
        temperature_value = label.locator("..").locator("span").nth(1)
        humidity_value = visible_values.get_by_text("Luftfeuchte:", exact=True).locator("..").locator("span").nth(1)
        await expect(temperature_value).to_have_text(re.compile(r"^\d+(?:,\d)? °C$"))
        await expect(humidity_value).to_have_text(re.compile(r"^\d+(?:,\d)? %$"))
        await expect(label).to_have_css("color", "rgb(255, 255, 255)")
        await expect(self.panel.locator("#tooltip")).to_have_attribute("data-phase", "aufheizen")
        await expect(self.panel.locator("#tooltip")).to_have_css("background-color", "rgba(0, 0, 0, 0)")
        phase_colors = await self.panel.evaluate("""p => {
          const inspection = p.$('#history-inspection'), tooltip = p.$('#tooltip');
          const active = getComputedStyle(inspection);
          const colors = {background: active.backgroundColor,
            phase: active.getPropertyValue('--history-phase-color').trim(),
            expected: getComputedStyle(p).getPropertyValue('--sauna-color-phase-warmup').trim()};
          const phase = tooltip.dataset.phase;
          tooltip.dataset.phase = '';
          colors.neutral = getComputedStyle(inspection).backgroundColor;
          tooltip.dataset.phase = phase;
          return colors;
        }""")
        self.assertEqual(phase_colors["phase"], phase_colors["expected"])
        self.assertNotEqual(phase_colors["background"], phase_colors["neutral"])
        self.assertEqual(await self.panel.evaluate("p=>p.historyChart.curves.styles['upper:temperature'].stroke"), "#000000")
        await expect(self.panel.locator('[data-action="history-detail"]')).to_have_count(0)
        self.assertEqual(await self.panel.evaluate("p=>p.positions.size"), 1)
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
        await expect(error_notice).to_have_css("background-color", "rgb(35, 35, 35)")
        await expect(error_notice).to_have_css("color", "rgb(255, 255, 255)")
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        compare = self.panel.locator('[data-action="reset-zoom"]')
        # Zoom belongs to the chart surface. Check rendered contrast rather
        # than imposing the foreground color of the surrounding cards.
        zoom_contrast = """button => {
          const style = getComputedStyle(button);
          const surface = getComputedStyle(button.closest('.history-plot-frame')).backgroundColor;
          const light = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number)
            .reduce((sum, value, index) => {
              const x = value / 255;
              return sum + [0.2126, 0.7152, 0.0722][index] *
                (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
            }, 0);
          const ratio = color => {
            const a = light(color), b = light(surface);
            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          };
          return {text: ratio(style.color), outline: ratio(style.outlineColor)};
        }"""
        await expect(compare).to_have_css("background-color", "rgba(0, 0, 0, 0)")
        self.assertGreaterEqual((await compare.evaluate(zoom_contrast))["text"], 4.5)
        await compare.hover()
        await expect(compare).to_have_css("filter", "none")
        await expect(compare).to_have_css("background-color", "rgba(0, 0, 0, 0)")
        self.assertGreaterEqual((await compare.evaluate(zoom_contrast))["text"], 4.5)
        await expect(compare).to_have_css("outline-style", "solid")
        self.assertGreaterEqual((await compare.evaluate(zoom_contrast))["outline"], 3)
        await expect(compare).to_have_css("text-decoration-line", "none")
        await compare.focus()
        await compare.press("Tab")
        await self.page.keyboard.press("Shift+Tab")
        await expect(compare).to_be_focused()
        await expect(compare).to_have_css("outline-style", "solid")
        await expect(compare).to_have_css("outline-width", "2px")
        self.assertGreaterEqual((await compare.evaluate(zoom_contrast))["outline"], 3)
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
        # The main history owns the event list; detail history shows the
        # controller tracks instead of duplicating those events.
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await expect(self.panel.locator('#event-list')).to_be_visible()
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
        await expect(self.panel.locator('.main-tabs [data-action="history"]')).to_have_attribute('aria-current', 'page')
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
        await self.panel.locator('.main-tabs [data-action="history"]').click()
        await expect(self.panel.locator('#event-list')).to_be_visible()
        event_row = self.panel.locator(f'#event-list [data-action="event-row:{event_id}"]')
        if not await event_row.is_visible():
            await event_row.locator('xpath=ancestor::details').locator('summary').click()
        await event_row.click()
        plot = self.panel.locator('#detection-plots .plot-panel').filter(
            has=self.page.locator('[data-series="detector_door_temperature_slope_upper"]'))
        await expect(plot).to_be_visible()
        for position in ("upper", "lower"):
            await expect(plot.locator(f'path[data-series="detector_door_temperature_slope_{position}"]')).to_have_css("stroke", "rgb(66, 165, 255)")
        await expect(plot.locator("path.lower")).to_have_css("stroke-dasharray", "8px, 5px")
        await expect(plot.locator(".diagnostic-legend")).to_have_css("color", "rgb(66, 165, 255)")
        await self.panel.locator('.main-tabs [data-action="settings"]').click()
        await editor.locator('[data-action="appearance-discard"]').click()
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_be_disabled()
        await self.open_settings_section("maintenance")
        await expect(self.panel.get_by_role("button", name="Standardwerte wiederherstellen", exact=True)).to_be_disabled()
        async with self.page.expect_download() as result:
            await self.open_settings_section("maintenance")
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
                  gang: p.$('#gangs tbody tr td:nth-child(3)').textContent,
                  events: [...p.shadowRoot.querySelectorAll('#event-list .history-event-time')].map(e=>e.textContent),
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

    async def test_content_scroll_keeps_navigation_visible(self):
        for width in (1440, 538, 390):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 720})
                await self.panel.locator('.main-tabs [data-action="settings"]').click()
                await self.open_settings_section("operation")
                content = self.panel.locator(".settings-content")
                await content.evaluate("node => { node.scrollTop = 0; node.querySelectorAll('details').forEach(item => item.open = true); }")
                header = self.panel.locator("header")
                before = await header.bounding_box()
                await content.evaluate("node => node.scrollTop = node.scrollHeight")
                await self.panel.evaluate("p => new Promise(resolve => requestAnimationFrame(resolve))")
                self.assertGreater(await content.evaluate("node => node.scrollTop"), 0)
                self.assertEqual(await header.bounding_box(), before)
                navigation = self.panel.locator(".settings-navigation")
                if width > 700:
                    await expect(navigation).to_be_in_viewport(ratio=1)
                else:
                    toggle = self.panel.locator('[data-action="settings-menu"]')
                    await expect(toggle).to_be_in_viewport(ratio=1)
                    await expect(toggle).to_have_attribute("aria-expanded", "false")
                    await expect(navigation).to_be_hidden()
                    await toggle.click()
                    await expect(navigation).to_be_in_viewport(ratio=1)
                    await self.page.keyboard.press("Escape")
                    await expect(toggle).to_be_focused()
                await expect(self.panel.locator('.main-tabs [data-action="overview"]')).to_be_in_viewport(ratio=1)
                if width <= 600:
                    tabs = self.panel.locator('.main-tabs')
                    for tab in await tabs.locator('button:visible').all():
                        await expect(tab).to_be_in_viewport(ratio=1)
                    tab_box = await tabs.bounding_box()
                    header_box = await header.bounding_box()
                    self.assertAlmostEqual(tab_box["x"] + tab_box["width"] / 2,
                                           header_box["x"] + header_box["width"] / 2, delta=1)
                    if width == 538:
                        self.assertLess(tab_box["width"], header_box["width"])
                self.assertLessEqual(await content.evaluate("node => node.scrollWidth"), await content.evaluate("node => node.clientWidth"))
                self.assertEqual(await self.panel.evaluate("p => p.scrollTop"), 0)

    async def test_overview_timers_stable_controls_german_settings_and_logging(self):
        await expect(self.panel.locator('.main-tabs [data-action="overview"]')).to_have_text("Steuerung")
        operation_button = self.panel.locator('#current [data-action="operation"]')
        await expect(operation_button).to_have_css("background-color", default_css_color("ui_command"))
        await operation_button.scroll_into_view_if_needed()
        # Hover and keyboard focus may scroll the HA shell or the panel host.
        # Compare all four dimensions in panel content coordinates so those
        # viewport movements cannot masquerade as a control layout change.
        async def operation_geometry():
            return await operation_button.evaluate("""button => {
              const content = button.getRootNode().querySelector("#current");
              const control = button.getBoundingClientRect();
              const host = content.getBoundingClientRect();
              return {x: control.x - host.x + content.scrollLeft,
                y: control.y - host.y + content.scrollTop,
                width: control.width, height: control.height};
            }""")

        operation_box = await operation_geometry()
        await operation_button.hover()
        self.assertEqual(await operation_geometry(), operation_box)
        await operation_button.focus()
        await operation_button.press("Tab")
        await self.page.keyboard.press("Shift+Tab")
        await expect(operation_button).to_be_focused()
        await expect(operation_button).to_have_css("outline-style", "solid")
        self.assertEqual(await operation_geometry(), operation_box)
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
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), DEFAULT_PARAMETERS["preset_count"])
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
        await self.panel.get_by_role("button", name="Ändern", exact=True).click()
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
        await self.panel.get_by_role("button", name="Ändern", exact=True).click()
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
        await expect(self.panel.locator('#current')).not_to_contain_text("Noch nicht abschätzbar")
        self.assertEqual(await self.panel.locator("#current [data-door-status]").count(), 0)
        await self.set_source("upper_temperature", 90)
        await self.set_instrument_style("light", "linear")
        await expect(self.panel.locator('#current #manual-light-value-overview')).to_be_visible(timeout=10000)
        await self.panel.locator('#current #manual-light-value-overview').evaluate("input => { input.value = '60'; }")
        await self.panel.locator('#current #manual-light-value-overview').dispatch_event("change")
        await expect(self.panel.locator('#current [data-light-status]')).to_have_count(0)
        await expect(self.panel.locator('#current [data-light-observation]')).to_contain_text("60 %")
        await self.panel.locator('[data-action="details"]').click()
        await expect(self.panel.locator('#details [data-mechanical-timer]')).to_have_count(0)
        await expect(self.panel.locator('#details')).to_contain_text("60 %")
        self.now+=timedelta(seconds=5)
        await self.runtime.tick()
        await self.set_source("upper_temperature", 70)
        await self.panel.evaluate("p=>p.refresh()")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await self.panel.locator('#current [data-action="operation"]').click()
        await self.panel.locator('[data-action="details"]').click()
        self.now+=timedelta(seconds=50)
        await self.runtime.tick()
        await self.panel.evaluate("p=>p.refresh()")
        await expect(self.panel.locator('#details [data-mechanical-timer]')).to_have_count(0)
        self.assertIsNotNone(self.runtime.session)
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Tür geschlossen")
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.locator("#program-choice-body")).to_be_hidden(timeout=5000)
        await self.panel.get_by_role("button", name="Ändern", exact=True).click()
        await self.panel.locator('[data-action="program-mode:constant"]').click()
        await expect(self.panel.locator('#current .program-pending')).to_contain_text("Konstant")
        await expect(self.panel.locator('[data-action="program-mode:constant"]')).to_have_attribute("aria-pressed", "true")
        self.assertEqual(self.entry.options["program_mode"], "progressive")
        await self.panel.locator('#current [data-action="program-apply"]').click()
        await expect(self.panel.locator('[data-action="program-mode:constant"]')).to_have_attribute("aria-pressed", "true")
        self.assertEqual(await self.panel.locator('#current [data-action^="preset:"]').count(), DEFAULT_PARAMETERS["preset_count"])
        await expect(self.panel.locator('#current [data-action^="preset:"]').first).to_be_enabled()
        await self.panel.locator('[data-action="details"]').click()
        await expect(self.panel.locator('[data-readiness]')).to_be_visible()
        await self.panel.locator('[data-action="settings"]').click()
        settings = self.panel.locator("#settings")
        await expect(settings.locator("#program-library [data-program-id]").first).to_be_visible(timeout=10000)
        await expect(settings.locator("#button-program")).to_be_visible()
        await self.open_parameter_group("sauna_min_temperature_c")
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_be_disabled()
        await expect(self.panel.locator('input[name="target_temperature_c"]')).to_have_count(0)
        self.assertTrue(await self.panel.locator('#parameters input[name]').evaluate_all(
            "inputs => inputs.length > 0 && inputs.every(input => input.disabled)"))
        self.assertTrue(await self.panel.locator('button[form="settings-parameters"]').evaluate_all(
            "buttons => buttons.length > 0 && buttons.every(button => button.disabled)"))
        self.assertEqual(await self.panel.evaluate("""async panel => {
          const update = panel.updateParameters; let writes = 0;
          panel.updateParameters = async () => { writes++; };
          try { await panel.saveSettings(); return writes; }
          finally { panel.updateParameters = update; }
        }"""), 0)
        await expect(self.panel.locator('input[name="temperature_increase_c"]')).to_have_count(0)
        await self.open_parameter_group("sensor_timeout_seconds")
        help_text = self.panel.locator('#help-sensor_timeout_seconds')
        help_button = self.panel.locator('[data-action="program-info:parameter:sensor_timeout_seconds"]')
        await expect(help_text).to_be_hidden()
        await help_button.click()
        await expect(help_text).to_be_visible()
        self.assertTrue(await help_text.inner_text())
        await expect(help_button).to_have_attribute("aria-expanded", "true")
        await expect(self.panel).to_have_js_property("busy", False)
        await self.panel.evaluate("panel => panel.refresh(true)")
        await expect(help_text).to_be_visible()
        await help_button.press("Escape")
        await expect(help_text).to_be_hidden()
        await expect(help_button).to_be_focused()
        await help_button.press("Enter")
        await expect(help_text).to_be_visible()
        await self.panel.locator('header h1').click()
        await expect(help_text).to_be_hidden()
        await self.open_settings_section("maintenance")
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
        await self.open_parameter_group("sensor_timeout_seconds")
        await expect(self.panel.locator('input[name="sensor_timeout_seconds"]')).to_be_enabled()
        await self.open_settings_section("light")
        await expect(self.panel.locator('input[name="session_light_minutes"]')).to_have_count(0)
        await expect(self.panel.locator("#details [data-door-status]")).to_have_text("Türerkennung ruht")
        await expect(self.panel.locator("#details")).to_contain_text("Außerhalb einer Saunasitzung werden keine Türbewegungen ausgewertet.")
        await expect(self.panel.locator('input[name="session_light_brightness_percent"]')).to_have_value("50")
        self.assertLessEqual(await self.panel.evaluate("p=>p.shadowRoot.querySelector('main').scrollWidth"),390)
        bindings = dict(self.entry.options["bindings"])
        await self.open_parameter_group("sensor_timeout_seconds")
        await self.panel.locator('input[name="sensor_timeout_seconds"]').fill("45")
        await self.open_settings_section("maintenance")
        await self.panel.get_by_role("button", name="Standardwerte wiederherstellen", exact=True).click()
        await expect(self.panel.locator('#log-level')).to_have_value("INFO", timeout=15000)
        await expect(self.panel.locator('input[name="sensor_timeout_seconds"]')).to_have_value("180")
        await expect(self.panel.locator('input[name="sauna_min_temperature_c"]')).to_have_value("60")
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["bindings"], bindings)
        await self.panel.locator('.main-tabs [data-action="overview"]').click()
        await expect(self.panel.get_by_role("slider", name="Lichthelligkeit einstellen")).to_be_visible()
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
        await expect(self.panel.locator('#current')).not_to_contain_text("Noch nicht abschätzbar")
        self.assertTrue(self.entry.runtime_data.session.operation_enabled)
        self.assertIn(True, self.heater.calls)
        self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.testErrors"), [])
        self.assertEqual(self.ws_errors, [])
