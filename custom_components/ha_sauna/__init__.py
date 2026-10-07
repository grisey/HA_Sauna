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
    previous = getattr(entry, "runtime_data", None)
    if previous is not None and not previous.closed:
        # Failed setup/close must not hide an adapter whose service is still
        # executing. The same bounded close applies to a later setup attempt.
        await previous.close()
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
    runtime = entry.runtime_data
    platforms_started = False
    try:
        from .backup import async_start_archive

        await async_start_archive(
            hass, runtime,
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
        platforms_started = True
        await hass.config_entries.async_forward_entry_setups(
            entry, ["number", "sensor", "switch", "climate", "button"]
        )
        from .device import HADevice

        runtime.device = HADevice(hass, runtime)
        await runtime.device.start()
        from .presence_adapter import HAPresenceAdapter

        presence = HAPresenceAdapter(
            hass, runtime.accept_presence,
            configuration.bindings.values.get("presence"), clock=runtime._clock,
        )
        runtime.presence_adapter = presence
        runtime.on_close(presence.close)
        await presence.start()
        runtime.on_close(
            async_track_time_interval(hass, runtime.tick, timedelta(seconds=1))
        )

        async def stopped(_event):
            await runtime.close()

        runtime.on_close(hass.bus.async_listen("homeassistant_stop", stopped))
        entry.async_on_unload(entry.add_update_listener(async_options_updated))
        return True
    except BaseException as setup_error:
        cleanup_errors = []
        if platforms_started:
            try:
                unloaded = await hass.config_entries.async_unload_platforms(
                    entry, ["number", "sensor", "switch", "climate", "button"]
                )
                if not unloaded:
                    cleanup_errors.append(RuntimeError("Sauna-Plattformen konnten nach Setupfehler nicht entladen werden"))
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            await runtime.close()
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
        for cleanup_error in cleanup_errors:
            setup_error.add_note(f"Zusätzlicher Bereinigungsfehler: {cleanup_error!r}")
        raise


async def async_options_updated(hass, entry):
    requested_options = entry.options
    runtime = getattr(entry, "runtime_data", None)
    if runtime and not runtime.closed:
        from .runtime import Configuration

        try:
            # An external writer persists options before notifying this
            # listener.  Validate its complete payload before comparing or
            # applying anything to the live runtime.
            updated = Configuration.from_options(entry.options)
        except (ValueError, TypeError):
            # Keep the persisted entry in step with the still-valid runtime;
            # otherwise the next reload would fail although this listener
            # rejected the external change.
            runtime.reconfiguring = False
            hass.config_entries.async_update_entry(
                entry, options=runtime.configuration.as_options()
            )
            return
        from dataclasses import replace

        before = runtime.configuration.as_options()
        after = updated.as_options()
        before.pop("log_level")
        after.pop("log_level")
        before.pop("appearance")
        after_appearance = after.pop("appearance")
        if before == after:
            async with runtime._lock:
                if getattr(entry, "runtime_data", None) is not runtime or runtime.closed:
                    return
                # This listener may have waited behind a newer API write. Use
                # the latest persisted entry, not its pre-lock snapshot.
                try:
                    current = Configuration.from_options(entry.options)
                except (ValueError, TypeError):
                    return  # The newer listener will reject its invalid write.
                current_options = current.as_options()
                current_options.pop("log_level")
                current_options.pop("appearance")
                live_options = runtime.configuration.as_options()
                live_options.pop("log_level")
                live_options.pop("appearance")
                if current_options != live_options:
                    return
                runtime.configuration = replace(
                    runtime.configuration, appearance=current.appearance
                )
                if runtime.configuration.log_level != current.log_level:
                    runtime.set_log_level(current.log_level)
                if runtime.device:
                    runtime.device.restore_light_ownership()
                    runtime.device.restore_heater_ownership()
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
            async with runtime.serialized():
                if getattr(entry, "runtime_data", None) is not runtime or runtime.closed:
                    return
                # A later options update may have overtaken this listener
                # while it waited for the live controller lock.
                try:
                    latest = Configuration.from_options(entry.options)
                except (ValueError, TypeError):
                    return  # The newer listener will reject its invalid write.
                if latest.as_options() != updated.as_options():
                    return
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
                runtime.configuration = replace(
                    runtime.configuration, appearance=after_appearance
                )
                runtime.set_log_level(updated.log_level)
                if before_selected_program != after_selected_program:
                    hass.config_entries.async_update_entry(
                        entry, options=runtime.configuration.as_options()
                    )
                if runtime.device:
                    runtime.device.restore_light_ownership()
                    runtime.device.restore_heater_ownership()
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
    try:
        if runtime and not runtime.closed and runtime.device:
            heater_finished = await runtime.device.prepare_heater_handoff(
                restore_on_failure=False
            )
            if not heater_finished:
                await _restore_failed_options(hass, entry, runtime, requested_options)
                return
            light_changed = (
                runtime.configuration.bindings.values["light"]
                != updated.bindings.values["light"]
            )
            if light_changed:
                # Finish every old output before a confirmed OFF hands this
                # binding away. Its phase never migrates to another light.
                finished = await runtime.device.finish_session_light(
                    runtime._clock(), light_timer
                )
            else:
                # Reject an unfinished handoff before HA unload begins: False
                # from its unload would permanently mark FAILED_UNLOAD. Keep
                # output withdrawn until the guarded options rollback finishes.
                finished = await runtime.device.prepare_light_handoff(
                    restore_on_failure=False
                )
            if (
                getattr(entry, "runtime_data", None) is not runtime
                or runtime.closed
                or entry.options != requested_options
            ):
                return
            if not finished:
                await _restore_failed_options(hass, entry, runtime, requested_options)
                return
            if light_changed:
                light_timer = None
        if entry.options != requested_options:
            return
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
    except BaseException as error:
        try:
            await _restore_failed_options(hass, entry, runtime, requested_options)
        except BaseException as cleanup_error:
            error.add_note(f"Rücknahme der Optionsänderung fehlgeschlagen: {cleanup_error!r}")
        raise
    if not reloaded:
        await _restore_failed_options(hass, entry, runtime, requested_options)
        return
    replacement = getattr(entry, "runtime_data", None)
    if (
        timer is not None
        and replacement is not None
        and replacement is not runtime
        and not replacement.closed
        and entry.options == requested_options
        and replacement.configuration.as_options() == updated.as_options()
    ):
        replacement.controller.mechanical_timer = timer
        replacement.controller.light_after_run = light_timer
        await replacement.tick()


async def _restore_failed_options(hass, entry, runtime, requested_options):
    """Restore only the still-live owner of this failed options attempt."""
    if runtime is None:
        return
    async with runtime._lock:
        if (
            getattr(entry, "runtime_data", None) is not runtime
            or runtime.closed
            or entry.options != requested_options
        ):
            return
        # A failed platform unload did not close the integration runtime.
        # Do not transfer its timers or resurrect a runtime already closed by
        # another unload. HA's own entry/platform error state remains visible.
        hass.config_entries.async_update_entry(
            entry, options=runtime.configuration.as_options()
        )
        runtime.reconfiguring = False
        if runtime.device:
            runtime.device.restore_light_ownership()
            runtime.device.restore_heater_ownership()
        runtime.log.error(
            "configuration_reload_failed",
            "Einstellungen konnten nicht neu geladen werden; "
            "die bisherige Laufzeitkonfiguration bleibt wirksam.",
        )


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]
) -> bool:
    """Beim Entladen Ofen ausschalten, Listener lösen und Archiv abschließen."""
    runtime = entry.runtime_data
    try:
        if runtime.device and not runtime.closed:
            # A rejected unload keeps the output owner alive, but must not
            # let a later tick resume heating after its final OFF completes.
            try:
                await runtime.set_operation(False)
            finally:
                # Archive/input processing failures must not skip the
                # independent final OFF attempt of the existing output owner.
                heater_finished = await runtime.device.prepare_heater_handoff()
            if not heater_finished:
                return False
            if not await runtime.device.prepare_light_handoff():
                runtime.device.restore_heater_ownership()
                return False
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, ["number", "sensor", "switch", "climate", "button"]
        )
    except BaseException:
        if (
            getattr(entry, "runtime_data", None) is runtime
            and not runtime.closed and runtime.device
        ):
            runtime.device.restore_light_ownership()
            runtime.device.restore_heater_ownership()
        raise
    if not unloaded:
        if (
            getattr(entry, "runtime_data", None) is runtime
            and not runtime.closed and runtime.device
        ):
            runtime.device.restore_light_ownership()
            runtime.device.restore_heater_ownership()
        return False
    await runtime.close()
    return True
