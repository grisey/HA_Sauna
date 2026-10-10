"""Shared light phase and plan inputs for real and simulated device adapters."""

from datetime import timedelta
from math import isfinite

from .light import phase_target


def light_phase(controller, now=None):
    """Derive light ownership and deadlines from the leading controller."""
    light_after_run = controller.light_after_run
    if controller.control_mode == "manual" and (
        light_after_run is None
        or (now is not None and now >= light_after_run.ends_at)
    ):
        # Im manuellen Betrieb verändern Gang- und Heizphasen die explizite
        # Lichtwahl nicht. Nur der Betriebsartwechsel gibt sie wieder frei.
        return (("manual",), "manual", None)
    if light_after_run is not None:
        key = (
            "session_light",
            light_after_run.session_id,
            light_after_run.started_at,
        )
        # Das Objekt bleibt als Sitzungsnachweis erhalten, die logische
        # Lichtphase endet aber exakt mit seiner Frist. Ein noch
        # ausstehender OFF-Service wird unabhängig in ``apply_light``
        # abgewickelt, damit ein neuer manueller Befehl nicht den alten
        # ``session_light``-Schlüssel erbt.
        if now is not None and now >= light_after_run.ends_at:
            return (("aus",), "aus", None)
        return (key, "session_light", light_after_run.ends_at)
    session = controller.session
    if session is None or not session.operation_enabled:
        return (("aus",), "aus", None)
    phase = controller.phase
    if phase == "nachlauf" and session.after_run is not None:
        return (
            (session.session_id, phase, session.after_run.phase_id),
            phase,
            session.after_run.ends_at,
        )
    return ((session.session_id, phase), phase, None)


def light_plan(output, controller, now, actual, normal):
    """Plan from controller phases; adapters own observation and command transport."""
    key, name, ends_at = light_phase(controller, now)
    after_run = controller.session.after_run if controller.session else None
    paused = (name == "nachlauf" and after_run is not None
              and not after_run.pending_start and after_run.ends_at is None)
    target = 0.0 if name in ("aus", "session_light") else phase_target(
        name, controller.temperature, controller.target_temperature,
        output.parameters, normal,
    )
    return output.update(
        now, key, name, target, actual, ends_at, phase_paused=paused,
        phase_brightness_percent=(controller.light_after_run.brightness_percent
                                  if name == "session_light" else None),
    )


def validate_light_selection(value):
    if value not in (None, True, False, "normal"):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (isinstance(value, float) and not isfinite(value))
                or not 0 <= value <= 100):
            raise ValueError("Lichtwert muss zwischen 0 und 100 Prozent liegen.")


def select_light(output, controller, value, now, normal):
    """Apply a selection with the same phase lifetime in every adapter."""
    validate_light_selection(value)
    phase_key = light_phase(controller, now)[0]
    ends_at = (now + timedelta(minutes=output.parameters.values["manual_override_minutes"])
               if controller.control_mode == "automatic" and value is not None else None)
    after_run = controller.light_after_run
    if value is not None and after_run is not None and now >= after_run.ends_at:
        output.finish_automatic()
    if value is None:
        output.return_to_automatic()
    else:
        brightness = (output.parameters.values["session_light_brightness_percent"]
                      if value is True else 0 if value is False
                      else normal if value == "normal" else value)
        output.set_manual(brightness, phase_key=phase_key, ends_at=ends_at)
