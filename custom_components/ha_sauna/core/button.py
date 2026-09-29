"""HA-freie Auswertung normierter Tastergesten.

Der Adapter normalisiert seine Geräteereignisse zu ``press``, ``short``,
``double``, ``triple``, ``long`` und ``release``. Binärtaster verwenden ``on``,
``off`` und ``unavailable``; bei ihnen wird der kurze Druck beim ``off``
abgeschlossen. Die Klasse speichert bewusst
nur die gerade laufende Geste und führt keine Uhr selbst: Die Zeit wird bei
jedem Aufruf als ``datetime`` übergeben.
"""

from datetime import datetime, timedelta

START_STANDARD_PROGRAM = "start_standard_program"
HEATER_TOGGLE_OVERRIDE = "heater_toggle_override"
END_HOLD = "end_hold"
END_RELEASE = "end_release"


class ButtonGestures:
    """Translate normalized input into the actions of each completed gesture.

    A press records its operation context. Only a short classification, or the
    confirmed short release of a binary input, can start operation. Completed
    native gestures retain their context until the next press so trailing
    classifications cannot act on the operation they just started or ended.
    A release without a known press deliberately has no effect.

    ``short`` and ``long`` are also accepted without a preceding press. Some
    event-only devices publish only their completed classification; each such
    event starts an independent gesture with the current operation context.
    """

    def __init__(self, hold_threshold: timedelta):
        if hold_threshold < timedelta(0):
            raise ValueError("hold_threshold must not be negative")
        self.hold_threshold = hold_threshold
        self._pressed_at: datetime | None = None
        self._gesture_active = False
        self._started_while_off = False
        self._binary_press = False
        self._event_only_gesture = False
        self._long_seen = False
        self._end_hold_sent = False
        self._short_suppressed = False
        self._short_completed = False
        self._native_started_while_off: bool | None = None

    def handle(self, event: str, operation_enabled: bool, now: datetime) -> str | None:
        """Accept one normalized event and return its single semantic action."""
        if event == "unavailable":
            if self._binary_press:
                # A gap cannot prove continuous pressure. An already emitted
                # HOLD still needs the eventual confirmed release to start its
                # light timer; an unconfirmed gesture has no remaining action.
                self._pressed_at = None
                if not self._end_hold_sent:
                    self._clear(suppress_short=True)
            return None
        if event == "on":
            return self._press(operation_enabled, now, binary=True)
        if event == "off":
            return self._release(operation_enabled, now, binary=True)
        if event == "press":
            return self._press(operation_enabled, now, binary=False)
        if event == "short":
            return self._short(operation_enabled)
        if event == "long":
            return self._long(operation_enabled)
        if event == "release":
            return self._release(operation_enabled, now, binary=False)
        raise ValueError(f"unknown button event: {event!r}")

    def handle_actions(
        self, event: str, operation_enabled: bool, now: datetime
    ) -> tuple[str, ...]:
        """Accept one event and return every action completed by it.

        Shelly publishes a click summary after the individual ``btn_down`` and
        ``btn_up`` events. Their first press supplies the operation context;
        the summary confirms all short presses. Event-only devices supply
        every press through their summary itself.
        """
        if event not in {"double", "triple"}:
            action = self.handle(event, operation_enabled, now)
            return () if action is None else (action,)
        if self._short_suppressed or self._short_completed:
            return ()
        if self._long_seen:
            if not self._event_only_gesture:
                return ()
            self._clear()

        clicks = 2 if event == "double" else 3
        native = self._native_started_while_off is not None
        started_while_off = (
            self._native_started_while_off if native else not operation_enabled
        )
        if started_while_off:
            actions = (
                (() if operation_enabled else (START_STANDARD_PROGRAM,))
                + (HEATER_TOGGLE_OVERRIDE,) * (clicks - 1)
            )
        elif operation_enabled:
            actions = (HEATER_TOGGLE_OVERRIDE,) * clicks
        else:
            actions = ()
        if native:
            # A native summary consumes this gesture, just like ``short``.
            # Keep its context so a trailing long cannot become a sparse hold.
            self._short_completed = True
            self._native_started_while_off = None
        else:
            self._clear()
        return actions

    def advance(self, now: datetime, operation_enabled: bool) -> str | None:
        """Recognize a held binary press after its configured duration."""
        if (
            not self._gesture_active
            or not self._binary_press
            or self._pressed_at is None
            or self._long_seen
        ):
            return None
        if now - self._pressed_at < self.hold_threshold:
            return None
        return self._long(operation_enabled)

    def _press(
        self, operation_enabled: bool, now: datetime, *, binary: bool
    ) -> str | None:
        # A new press always starts a new gesture, even if a prior release vanished.
        if binary or self._binary_press or self._short_completed or self._long_seen:
            self._native_started_while_off = None
        if not binary and self._native_started_while_off is None:
            self._native_started_while_off = not operation_enabled
        self._pressed_at = now
        self._gesture_active = True
        self._started_while_off = not operation_enabled
        self._binary_press = binary
        self._event_only_gesture = False
        self._long_seen = False
        self._end_hold_sent = False
        self._short_suppressed = False
        self._short_completed = False
        return None

    def _short(self, operation_enabled: bool) -> str | None:
        if self._short_suppressed:
            return None
        if not self._gesture_active:
            # Event-only devices may emit just their final click classification.
            return (
                HEATER_TOGGLE_OVERRIDE if operation_enabled else START_STANDARD_PROGRAM
            )
        if self._long_seen:
            if self._event_only_gesture:
                # BASIC has no release: this is its next independent click.
                self._clear()
                return (
                    HEATER_TOGGLE_OVERRIDE
                    if operation_enabled
                    else START_STANDARD_PROGRAM
                )
            return None
        if self._short_completed:
            return None
        self._short_completed = True
        self._native_started_while_off = None
        if self._started_while_off:
            return None if operation_enabled else START_STANDARD_PROGRAM
        return HEATER_TOGGLE_OVERRIDE if operation_enabled else None

    def _long(self, operation_enabled: bool) -> str | None:
        if self._event_only_gesture:
            # Without a press edge, the next long classification is a new
            # gesture. Native and binary holds keep their original context.
            self._clear()
        if self._short_completed:
            return None
        if not self._gesture_active:
            self._gesture_active = True
            self._started_while_off = not operation_enabled
            self._binary_press = False
            self._event_only_gesture = True
            self._pressed_at = None
        self._long_seen = True
        self._native_started_while_off = None
        if self._started_while_off or not operation_enabled or self._end_hold_sent:
            return None
        self._end_hold_sent = True
        return END_HOLD

    def _release(
        self, operation_enabled: bool, now: datetime, *, binary: bool
    ) -> str | None:
        if not self._gesture_active:
            return None
        if self._end_hold_sent:
            self._clear(suppress_short=True)
            return END_RELEASE
        if self._long_seen:
            self._clear(suppress_short=True)
            return None
        if binary and self._binary_press:
            # A delayed timer callback must not turn a completed long hold into
            # a short action. The runtime ends the session on END_RELEASE even
            # if it could not show the acknowledgement before this release.
            if now - self._pressed_at >= self.hold_threshold:
                action = (
                    END_RELEASE
                    if not self._started_while_off and operation_enabled
                    else None
                )
                self._clear(suppress_short=True)
                return action
            action = self._short(operation_enabled)
            self._clear(suppress_short=True)
            return action
        # Keep the press context for Shelly's usual release-then-short order.
        return None

    def _clear(self, *, suppress_short: bool = False) -> None:
        self._pressed_at = None
        self._gesture_active = False
        self._started_while_off = False
        self._binary_press = False
        self._event_only_gesture = False
        self._long_seen = False
        self._end_hold_sent = False
        self._short_suppressed = suppress_short
        self._short_completed = False
        self._native_started_while_off = None
