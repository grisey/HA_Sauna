"""Phasenbezogene und nutzerorientierte Zeitanzeigen des Ablaufkerns."""

from datetime import timedelta
from math import isfinite


def phase_timer(controller, now):
    session = controller.session

    def remaining(kind, label, end):
        return {
            "kind": kind,
            "label": label,
            "seconds": max(0, (end - now).total_seconds()),
            "mode": "remaining",
        }

    def elapsed(kind, label, start):
        return {
            "kind": kind,
            "label": label,
            "seconds": max(0, (now - start).total_seconds()),
            "mode": "elapsed",
        }

    if session is None:
        light = controller.light_after_run
        return (
            remaining("session_light", "Lichtnachlauf noch", light.ends_at)
            if light and now < light.ends_at
            else None
        )

    if not session.operation_enabled:
        end = next(
            (d.due_at for d in session.deadlines if d.purpose == "session_gap"), None
        )
        return remaining("session_gap", "Sitzung endet in", end) if end else None
    if session.timeline.active:
        return elapsed("gang", "Saunagang seit", session.timeline.active.started_at)
    if session.after_run:
        phase = session.after_run
        if phase.paused_at is not None:
            return {
                "kind": "after_run",
                "label": "Nachlauf pausiert, noch",
                "seconds": phase.remaining_seconds,
                "mode": "paused",
            }
        return remaining("after_run", "Nachlauf noch", phase.ends_at)
    if (
        session.cooling
        and session.cooling.ends_at
        and session.cooling.paused_at is None
    ):
        return remaining("cooling", "Zwangskühlung noch", session.cooling.ends_at)
    if controller.control_mode == "manual":
        return elapsed(
            "manual",
            "Manueller Betrieb seit",
            controller.phase_since or session.started_at,
        )
    if controller.cooling_wait_until:
        return remaining(
            "person_wait",
            "Wartezeit auf Personenerkennung",
            controller.cooling_wait_until,
        )
    end = session.thermostat.cooldown_until
    if end and now < end:
        return remaining("thermostat_pause", "Heizpause noch", end)
    if session.heating.reported_heating and session.heating.intervals:
        end = session.heating.intervals[-1].started_at + timedelta(
            seconds=controller.parameters.seconds("minimum_heating_minutes")
        )
        if now < end:
            return remaining("minimum_heating", "Mindestheizzeit noch", end)
    if controller.phase == "bereit":
        return elapsed(
            "ready",
            "Bereit seit",
            controller.phase_since or session.ready_at or session.started_at,
        )
    return elapsed(
        "heating", "Aufheizen seit", controller.phase_since or session.started_at
    )


def start_availability(controller, now, temperature_rate=None):
    """Describe temperature readiness and known start blockers without changing state.

    ``temperature_rate`` is an optional, externally validated rise in °C/s.
    The controller deliberately does not derive it from short door-detection
    samples: without a reliable positive rate an arrival time would be made up.
    """
    session = controller.session
    result = {
        "until_ready_seconds": None,
        "ready_estimated": False,
        "minimum_wait_seconds": None,
        "start_window_seconds": None,
        "start_window_label": "mindestens",
        "message": "Saunabetrieb ist aus.",
        "blocker": None,
        "pending_cooling": False,
        "person_wait_seconds": None,
        "gang_elapsed_seconds": None,
    }
    if session is None or not session.operation_enabled:
        return result

    active = session.timeline.active
    if active is not None:
        result.update(
            blocker={"kind": "gang"},
            gang_elapsed_seconds=max(0, (now - active.started_at).total_seconds()),
            message="Ein Saunagang läuft bereits.",
        )
        return result

    target = controller.readiness_target
    temperature = controller.temperature
    valid_temperature = (
        not isinstance(temperature, bool)
        and isinstance(temperature, (int, float))
        and isfinite(temperature)
    )
    valid_target = (
        not isinstance(target, bool)
        and isinstance(target, (int, float))
        and isfinite(target)
    )
    # A displayed readiness must never bypass the conditions which also stop
    # the controller from heating.  These conditions have no honest deadline.
    if controller.protection:
        result.update(
            blocker={
                "kind": "protection",
                "reasons": tuple(sorted(controller.protection)),
            },
            message="Ein Saunagang ist wegen einer Schutzsperre derzeit nicht möglich.",
        )
        return result
    if controller.inhibits:
        result.update(
            blocker={"kind": "inhibit", "reasons": tuple(sorted(controller.inhibits))},
            message="Ein Saunagang ist derzeit gesperrt.",
        )
        return result
    if not valid_target:
        result.update(
            blocker={"kind": "temperature_configuration"},
            message="Die Regeltemperatur ist noch nicht verfügbar.",
        )
        return result
    if not valid_temperature:
        result.update(
            blocker={"kind": "temperature_unavailable"},
            message="Die aktuelle Temperatur ist noch nicht verfügbar.",
        )
        return result

    # A future start must include both the remaining wait and only the cooling
    # which remains after the complete running wait receives its credit.
    after_run = session.after_run
    cooling = session.cooling
    if after_run is not None:
        after_seconds = (
            after_run.remaining_seconds
            if after_run.paused_at is not None
            else max(0, (after_run.ends_at - now).total_seconds())
        )
        if after_run.paused_at is not None:
            result.update(
                blocker={"kind": "after_run_paused", "seconds": after_seconds},
                pending_cooling=bool(cooling),
                minimum_wait_seconds=(
                    0 if controller._paused_after_run_reentry_allowed(session) else None
                ),
                message=(
                    "Das manuelle Heizen läuft; ein manueller Wiedereinstieg ist jetzt möglich."
                    if controller._paused_after_run_reentry_allowed(session)
                    else "Der Nachlauf ist während des manuellen Heizens pausiert; ein neuer Saunagang ist noch nicht abschätzbar."
                ),
            )
            return result
        following_cooling_seconds = 0
        if cooling is not None:
            # The controller credits a running after-run at its end.  For a
            # future start we can nevertheless use the complete phase now:
            # its already elapsed part is credited together with the rest.
            prospective_credit = after_run.duration_seconds
            following_cooling_seconds = max(
                0, cooling.remaining_seconds - prospective_credit
            )
        result.update(
            minimum_wait_seconds=after_seconds + following_cooling_seconds,
            blocker={
                "kind": "after_run",
                "seconds": after_seconds,
                "following_cooling_seconds": following_cooling_seconds,
            },
            pending_cooling=bool(cooling and following_cooling_seconds > 0),
            message="Ein weiterer Saunagang ist frühestens nach der laufenden Wartezeit möglich; die Temperatur wird danach erneut geprüft.",
        )
        return result

    # A paused cooling cycle has no running deadline and does not reserve a
    # fictitious wall-clock wait.  It can resume later through the controller.
    if (
        cooling is not None
        and cooling.started_at is not None
        and cooling.paused_at is None
    ):
        cooling_seconds = cooling.remaining_seconds
        result.update(
            minimum_wait_seconds=cooling_seconds,
            blocker={"kind": "cooling", "seconds": cooling_seconds},
            pending_cooling=True,
            message="Ein weiterer Saunagang ist frühestens nach der laufenden Wartezeit möglich; die Temperatur wird danach erneut geprüft.",
        )
        return result

    if controller.control_mode == "manual":
        result.update(message="Ofen und Licht werden manuell bedient.")
        return result

    if cooling is not None and cooling.paused_at is not None:
        result.update(
            blocker={"kind": "cooling_paused"},
            pending_cooling=True,
            minimum_wait_seconds=0 if controller.heater_override is True else None,
            message=(
                "Ein manueller Wiedereinstieg ist jetzt möglich."
                if controller.heater_override is True
                else "Ein weiterer Saunagang ist noch nicht abschätzbar."
            ),
        )
        return result

    wait_until = controller.cooling_wait_until
    wait_seconds = max(0, (wait_until - now).total_seconds()) if wait_until else None
    result["person_wait_seconds"] = wait_seconds
    result["pending_cooling"] = bool(cooling is not None and cooling.started_at is None)
    if wait_seconds is not None:
        result["blocker"] = {"kind": "person_wait", "seconds": wait_seconds}
        result["message"] = (
            "Der nächste Saunagang hängt noch von der laufenden Erkennung ab."
        )

    ready = (
        session.ready_at is not None
        and temperature is not None
        and target is not None
        and temperature
        >= target - controller.parameters.values["readiness_hysteresis_c"]
    )
    if ready:
        result["until_ready_seconds"] = 0
        result["start_window_seconds"] = max(
            0, controller.heating_limit_seconds - session.heating.elapsed_seconds
        )
        if result["blocker"] is None:
            result["message"] = "Bereit für einen Saunagang."
        return result

    valid_rate = (
        not isinstance(temperature_rate, bool)
        and isinstance(temperature_rate, (int, float))
        and isfinite(temperature_rate)
        and temperature_rate > 0
    )
    if valid_rate and valid_temperature and valid_target:
        result.update(
            until_ready_seconds=max(0, (target - temperature) / temperature_rate),
            ready_estimated=True,
        )
        if result["blocker"] is None:
            result["message"] = (
                "Aufheizzeit wird aus dem belastbaren Temperaturtrend geschätzt."
            )
    elif result["blocker"] is None:
        result["message"] = (
            "Noch nicht abschätzbar: Es fehlt ein belastbarer positiver Temperaturtrend."
        )
    return result
