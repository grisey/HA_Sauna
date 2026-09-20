"""HA-freie Auswertung normierter Tastergesten.

Der Adapter normalisiert seine Geräteereignisse zu ``press``, ``short``,
``double``, ``triple``, ``long`` und ``release``.  Binärtaster verwenden ``on`` und ``off``; bei ihnen
wird der kurze Druck beim ``off`` abgeschlossen.  Die Klasse speichert bewusst
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

    A press made while the operation is off starts it immediately.  That gesture
    is then consumed, so a later long or release cannot turn the newly started
    operation off again.  A release without a known press deliberately has no
    effect: it is commonly the trailing event of an already-ended session.

    ``short`` is also accepted without a preceding press.  Some event-only
    devices publish only their completed click, which is still a real gesture.
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
        self._native_short_presses = 0
        self._native_start_actions = 0

    def handle(self, event: str, operation_enabled: bool, now: datetime) -> str | None:
        """Accept one normalized event and return its single semantic action."""
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
        ``btn_up`` events. A start can therefore already have been acted on
        before that summary arrives. The summary completes only the remaining
        short presses. Event-only devices have no preceding input, so their
        summary supplies every press itself.
        """
        if event not in {"double", "triple"}:
            action = self.handle(event, operation_enabled, now)
            return () if action is None else (action,)
        if self._short_suppressed:
            return ()

        clicks = 2 if event == "double" else 3
        starts = self._native_start_actions
        if self._native_short_presses:
            # Native edges may be incomplete, but a received summary is the
            # authoritative number of short presses in this completed gesture.
            actions = (HEATER_TOGGLE_OVERRIDE,) * max(0, clicks - starts)
        elif operation_enabled:
            actions = (HEATER_TOGGLE_OVERRIDE,) * clicks
        else:
            actions = (
                START_STANDARD_PROGRAM,
                *((HEATER_TOGGLE_OVERRIDE,) * (clicks - 1)),
            )
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
        self._pressed_at = now
        self._gesture_active = True
        self._started_while_off = not operation_enabled
        self._binary_press = binary
        self._event_only_gesture = False
        self._long_seen = False
        self._end_hold_sent = False
        self._short_suppressed = False
        if not binary:
            self._native_short_presses += 1
        if self._started_while_off:
            if not binary:
                self._native_start_actions += 1
            return START_STANDARD_PROGRAM
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
        if self._started_while_off or not operation_enabled:
            self._clear()
            return None
        self._clear()
        return HEATER_TOGGLE_OVERRIDE

    def _long(self, operation_enabled: bool) -> str | None:
        sparse_event = not self._gesture_active
        if not self._gesture_active:
            self._gesture_active = True
            self._started_while_off = not operation_enabled
            self._binary_press = False
            self._event_only_gesture = True
            self._pressed_at = None
        self._long_seen = True
        self._native_short_presses = 0
        self._native_start_actions = 0
        if self._started_while_off or not operation_enabled or self._end_hold_sent:
            return (
                START_STANDARD_PROGRAM
                if sparse_event and self._started_while_off
                else None
            )
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
            # a heater toggle. The adapter ends the session on END_RELEASE even
            # if it could not show the acknowledgement before this release.
            if (
                not self._started_while_off
                and operation_enabled
                and now - self._pressed_at >= self.hold_threshold
            ):
                self._clear(suppress_short=True)
                return END_RELEASE
            action = self._short(operation_enabled)
            self._clear()
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
        self._native_short_presses = 0
        self._native_start_actions = 0
