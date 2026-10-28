"""Complete immutable observations of existing interaction owners.

Capture only reads owner state. It does not recognize gestures, correlate a
reply, expire registrations, or emit any action.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from .model_handoff import freeze_data
from .notification_exchange import ReplyDisposition, ReplyKey
from .notification_recipients import ActorRef, TargetIdentity

if TYPE_CHECKING:
    from .button import ButtonGestures
    from .notification_exchange import (
        DeviceReceipt,
        NotificationEnvelope,
        ReplyAssociation,
        ReplyCorrelator,
        ReplyEnvelope,
        TransportHandoff,
    )


@dataclass(frozen=True)
class ButtonGestureSnapshot:
    """All configuration and pending recognition state of ButtonGestures."""

    session_gesture: str
    hold_threshold: timedelta | None
    pressed_at: datetime | None
    gesture_active: bool
    started_while_off: bool
    binary_press: bool
    event_only_gesture: bool
    long_seen: bool
    end_hold_sent: bool
    start_hold_sent: bool
    short_suppressed: bool
    short_completed: bool
    native_started_while_off: bool | None

    @classmethod
    def capture(cls, owner: ButtonGestures) -> ButtonGestureSnapshot:
        """Copy existing state without invoking gesture handling or advancing it."""
        return cls(
            session_gesture=owner.session_gesture,
            hold_threshold=owner.hold_threshold,
            pressed_at=owner._pressed_at,
            gesture_active=owner._gesture_active,
            started_while_off=owner._started_while_off,
            binary_press=owner._binary_press,
            event_only_gesture=owner._event_only_gesture,
            long_seen=owner._long_seen,
            end_hold_sent=owner._end_hold_sent,
            start_hold_sent=owner._start_hold_sent,
            short_suppressed=owner._short_suppressed,
            short_completed=owner._short_completed,
            native_started_while_off=owner._native_started_while_off,
        )


@dataclass(frozen=True)
class NotificationEnvelopeSnapshot:
    """The complete registered envelope with an immutable opaque payload.

    Payload data is frozen without interpreting its keys or choosing an action
    schema. The existing transport may carry arbitrary live objects; those
    objects are outside the immutable observation contract and capture rejects
    them through the existing freeze_data boundary.
    """

    message_id: str
    correlation_id: str
    entry_id: str
    target: TargetIdentity
    payload: object
    session_id: str | None

    @classmethod
    def capture(cls, envelope: NotificationEnvelope) -> NotificationEnvelopeSnapshot:
        """Copy every envelope field without changing the transport envelope."""
        return cls(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            entry_id=envelope.entry_id,
            target=envelope.target,
            payload=freeze_data(envelope.payload),
            session_id=envelope.session_id,
        )


@dataclass(frozen=True)
class ReplyEnvelopeSnapshot:
    """The complete reply, including independent provenance and opaque payload."""

    reply_id: str | None
    message_id: str | None
    correlation_id: str | None
    entry_id: str | None
    source: TargetIdentity | None
    actor: ActorRef
    payload: object
    session_id: str | None
    context_id: str | None
    observed_at: datetime | None
    reply_key: ReplyKey | None

    @classmethod
    def capture(cls, envelope: ReplyEnvelope) -> ReplyEnvelopeSnapshot:
        """Copy the existing reply without interpreting its identity or payload."""
        return cls(
            reply_id=envelope.reply_id,
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            entry_id=envelope.entry_id,
            source=envelope.source,
            actor=envelope.actor,
            payload=freeze_data(envelope.payload),
            session_id=envelope.session_id,
            context_id=envelope.context_id,
            observed_at=envelope.observed_at,
            reply_key=envelope.reply_key,
        )


@dataclass(frozen=True)
class TransportHandoffSnapshot:
    """An already observed transport acceptance, preserving its complete message."""

    notification: NotificationEnvelopeSnapshot
    accepted: bool
    evidence: str

    @classmethod
    def capture(cls, observation: TransportHandoff) -> TransportHandoffSnapshot:
        return cls(
            notification=NotificationEnvelopeSnapshot.capture(observation.notification),
            accepted=observation.accepted,
            evidence=observation.evidence,
        )


@dataclass(frozen=True)
class DeviceReceiptSnapshot:
    """An already evidenced device receipt, separately from transport acceptance."""

    notification: NotificationEnvelopeSnapshot
    evidence: str

    @classmethod
    def capture(cls, observation: DeviceReceipt) -> DeviceReceiptSnapshot:
        return cls(
            notification=NotificationEnvelopeSnapshot.capture(observation.notification),
            evidence=observation.evidence,
        )


@dataclass(frozen=True)
class ReplyAssociationSnapshot:
    """A complete already computed association, without a second reply decision."""

    disposition: ReplyDisposition
    reply: ReplyEnvelopeSnapshot
    notification: NotificationEnvelopeSnapshot | None
    handoff_accepted: bool | None

    @property
    def ambiguous(self) -> bool:
        return self.disposition is ReplyDisposition.AMBIGUOUS

    @classmethod
    def capture(cls, association: ReplyAssociation) -> ReplyAssociationSnapshot:
        return cls(
            disposition=association.disposition,
            handoff_accepted=association.handoff_accepted,
            reply=ReplyEnvelopeSnapshot.capture(association.reply),
            notification=(
                NotificationEnvelopeSnapshot.capture(association.notification)
                if association.notification is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ReplyRegistrationSnapshot:
    """A complete notification registration and all previously accepted reply IDs."""

    notification: NotificationEnvelopeSnapshot
    reply_ids: frozenset[str]
    reply_keys: frozenset[ReplyKey]
    handoff_accepted: bool | None


def _snapshot_order(value: object) -> tuple:
    """Compare already frozen snapshot values, including unordered collections."""
    value_type = type(value)
    name = f"{value_type.__module__}.{value_type.__qualname__}"
    if is_dataclass(value):
        return (
            name,
            tuple(
                (definition.name, _snapshot_order(getattr(value, definition.name)))
                for definition in fields(value)
            ),
        )
    if isinstance(value, tuple):
        return name, tuple(_snapshot_order(item) for item in value)
    if isinstance(value, frozenset):
        return name, tuple(sorted(_snapshot_order(item) for item in value))
    if isinstance(value, Enum):
        return name, _snapshot_order(value.value)
    return name, repr(value)


@dataclass(frozen=True)
class ReplyCorrelatorSnapshot:
    """All existing registrations; capture performs no reply or expiry work."""

    registrations: tuple[ReplyRegistrationSnapshot, ...]

    @classmethod
    def capture(cls, owner: ReplyCorrelator) -> ReplyCorrelatorSnapshot:
        """Read a deterministic snapshot without changing deduplication state."""
        registrations = tuple(
            ReplyRegistrationSnapshot(
                notification=NotificationEnvelopeSnapshot.capture(
                    registration.notification
                ),
                reply_ids=frozenset(registration.reply_ids),
                reply_keys=registration.reply_keys,
                handoff_accepted=registration.handoff_accepted,
            )
            for registration in owner._registrations.values()
        )
        return cls(
            registrations=tuple(
                sorted(
                    registrations,
                    key=lambda registration: (
                        registration.notification.entry_id,
                        registration.notification.session_id or "",
                        registration.notification.correlation_id,
                        registration.notification.target.transport,
                        registration.notification.target.endpoint,
                        registration.notification.message_id,
                        _snapshot_order(registration),
                    ),
                )
            )
        )
