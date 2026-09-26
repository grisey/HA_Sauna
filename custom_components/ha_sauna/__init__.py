"""HA-Sauna-Integration mit sicherem Aus-Start und explizitem Saunabetrieb."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .runtime import SaunaRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]
) -> bool:
    """Eine konfigurierte Instanz laden, ohne einen Saunabetrieb zu starten."""
    from datetime import timedelta

    from homeassistant.exceptions import ConfigEntryError
    from homeassistant.helpers.event import async_track_time_interval

    from .runtime import Configuration, SaunaRuntime

    try:
        configuration = Configuration.from_options(entry.options)
    except (ValueError, TypeError) as error:
        raise ConfigEntryError("Ungültige HA-Sauna-Konfiguration") from error
    entry.runtime_data = SaunaRuntime(configuration)
    # Der Laufzeitkern kann nach einem expliziten physischen Neustart seine
    # bereits aktualisierte Konfiguration speichern. Weil sie vor diesem
    # Callback gesetzt wird, erkennt der Optionenlistener keinen Fremdwechsel
    # und startet keine Sitzung neu.
    entry.runtime_data.save_configuration = lambda updated: (
        hass.config_entries.async_update_entry(entry, options=updated.as_options())
    )
    from .log import SaunaLog

    entry.runtime_data.log = SaunaLog(entry.entry_id, configuration.log_level)
    entry.runtime_data.log.info(
        "setup", "Sauna-Integration wird geladen; Saunabetrieb bleibt ausgeschaltet."
    )
    await entry.runtime_data.start_archive(
        hass.config.path("ha_sauna", f"{entry.entry_id}.sqlite"), entry.entry_id
    )
    from .api import register

    register(hass)
    from .frontend import register as register_panel

    await register_panel(hass)
    if entry.options != configuration.as_options():
        hass.config_entries.async_update_entry(
            entry, options=configuration.as_options()
        )
    await hass.config_entries.async_forward_entry_setups(
        entry, ["number", "sensor", "switch", "climate", "button"]
    )
    from .device import HADevice

    entry.runtime_data.device = HADevice(hass, entry.runtime_data)
    await entry.runtime_data.device.start()
    from .presence_adapter import HAPresenceAdapter

    presence = HAPresenceAdapter(
        hass, entry.runtime_data.accept_presence,
        configuration.bindings.values.get("presence"), clock=entry.runtime_data._clock,
    )
    entry.runtime_data.presence_adapter = presence
    entry.runtime_data.on_close(presence.close)
    await presence.start()
    entry.runtime_data.on_close(
        async_track_time_interval(hass, entry.runtime_data.tick, timedelta(seconds=1))
    )

    async def stopped(_event):
        await entry.runtime_data.close()

    entry.runtime_data.on_close(hass.bus.async_listen("homeassistant_stop", stopped))
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    if runtime and not runtime.closed:
        from .runtime import Configuration

        updated = Configuration.from_options(entry.options)
        before = runtime.configuration.as_options()
        after = updated.as_options()
        before.pop("log_level")
        after.pop("log_level")
        if before == after:
            runtime.set_log_level(updated.log_level)
            runtime.reconfiguring = False
            return
        from .core.parameters import LIVE_TEMPERATURE_KEYS
        from .settings import apply_temperature_parameters, program_parameters

        before_program_mode = before.pop("program_mode")
        after_program_mode = after.pop("program_mode")
        before_button_program = before.pop("button_program")
        after_button_program = after.pop("button_program")
        before_selected_program = before.pop("selected_program_id")
        after_selected_program = after.pop("selected_program_id")
        before_parameters = before.pop("parameters")
        after_parameters = after.pop("parameters")
        changed = {
            k
            for k in before_parameters.keys() | after_parameters.keys()
            if before_parameters.get(k) != after_parameters.get(k)
        }
        if (
            before == after
            and before_button_program == after_button_program
            and not changed - LIVE_TEMPERATURE_KEYS
        ):
            async with runtime._lock:
                selected_parameters = updated.parameters
                selected_mode = (
                    after_program_mode
                    if before_program_mode != after_program_mode
                    else None
                )
                selected_steps = updated.temperature_steps
                if (
                    before_selected_program != after_selected_program
                    and after_selected_program is not None
                ):
                    selected_parameters, selected_mode = program_parameters(
                        updated.parameters,
                        after_selected_program,
                        catalog=updated.temperature_programs,
                    )
                    selected_steps = next(
                        program.temperature_steps
                        for program in updated.temperature_programs
                        if program.id == after_selected_program
                    )
                await apply_temperature_parameters(
                    runtime,
                    selected_parameters,
                    explicit_target=False,
                    program_mode=selected_mode,
                    new_program=(
                        before_program_mode != after_program_mode
                        or before_selected_program != after_selected_program
                    ),
                    selected_program_id=after_selected_program,
                    temperature_steps=selected_steps,
                )
                runtime.set_log_level(updated.log_level)
                if before_selected_program != after_selected_program:
                    hass.config_entries.async_update_entry(
                        entry, options=runtime.configuration.as_options()
                    )
                runtime.reconfiguring = False
            return
        if runtime.session:
            # Auch externe Optionsschreiber dürfen keine laufende Sitzung durch
            # einen Reload und damit gelöschte Fristen umgehen.
            runtime.log.error(
                "configuration_locked",
                "Änderung der Grundeinstellungen während einer Saunasitzung abgelehnt.",
            )
            hass.config_entries.async_update_entry(
                entry, options=runtime.configuration.as_options()
            )
            return
        runtime.reconfiguring = True
    timer = (
        runtime.controller.mechanical_timer.pause(runtime._clock())
        if runtime and not runtime.closed
        else None
    )
    light_timer = (
        runtime.controller.light_after_run if runtime and not runtime.closed else None
    )
    if (
        light_timer
        and runtime.configuration.bindings.values["light"]
        != updated.bindings.values["light"]
    ):
        # Bei neuer Lichtzuordnung den bisherigen Lichtnachlauf am alten Gerät
        # beenden; dessen Frist darf nicht auf eine andere Leuchte übergehen.
        await runtime.device.finish_session_light(runtime._clock(), light_timer)
        light_timer = None
    await hass.config_entries.async_reload(entry.entry_id)
    if (
        timer is not None
        and getattr(entry, "runtime_data", None)
        and not entry.runtime_data.closed
    ):
        entry.runtime_data.controller.mechanical_timer = timer
        entry.runtime_data.controller.light_after_run = light_timer
        await entry.runtime_data.tick()


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]
) -> bool:
    """Beim Entladen Ofen ausschalten, Listener lösen und Archiv abschließen."""
    if not await hass.config_entries.async_unload_platforms(
        entry, ["number", "sensor", "switch", "climate", "button"]
    ):
        return False
    await entry.runtime_data.close()
    return True
