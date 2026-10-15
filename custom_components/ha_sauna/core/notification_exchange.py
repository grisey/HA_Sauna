"""Opaque exchange forms without sending, authorization or lifecycle policy.

Adapters establish reply provenance before constructing these forms. Correlation
checks association only; it does not authenticate a client or authorize control.
Payloads remain caller-owned and are neither interpreted nor serialized here.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .notification_recipients import ActorRef, TargetIdentity


def _required(*values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Exchange identities and evidence must be nonempty strings")


def _instance(value: object, expected: type) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"Expected {expected.__name__}")


@dataclass(frozen=True)
class NotificationEnvelope:
    message_id: str
    correlation_id: str
    entry_id: str
    target: TargetIdentity
    payload: object
    session_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.message_id, self.correlation_id, self.entry_id)
        _instance(self.target, TargetIdentity)
        if self.session_id is not None:
            _required(self.session_id)


@dataclass(frozen=True)
class ReplyEnvelope:
    reply_id: str
    message_id: str
    correlation_id: str
    entry_id: str
    source: TargetIdentity
    actor: ActorRef
    payload: object
    session_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.reply_id, self.message_id, self.correlation_id, self.entry_id)
        _instance(self.source, TargetIdentity)
        _instance(self.actor, ActorRef)
        if self.session_id is not None:
            _required(self.session_id)


@dataclass(frozen=True)
class TransportHandoff:
    """Adapter's transport acceptance observation; no device receipt claim."""

    notification: NotificationEnvelope
    accepted: bool
    evidence: str

    def __post_init__(self) -> None:
        _instance(self.notification, NotificationEnvelope)
        _instance(self.accepted, bool)
        _required(self.evidence)


@dataclass(frozen=True)
class DeviceReceipt:
    """Separate device receipt observation, only when actually evidenced."""

    notification: NotificationEnvelope
    evidence: str

    def __post_init__(self) -> None:
        _instance(self.notification, NotificationEnvelope)
        _required(self.evidence)


class NotificationTransport(Protocol):
    """Future adapter boundary; capability and incoming provenance stay external."""

    async def handoff(self, notification: NotificationEnvelope) -> TransportHandoff:
        """Report transport handoff without implying receipt or a reply."""
        ...


class ReplyDisposition(StrEnum):
    UNKNOWN = "unknown"
    MESSAGE_MISMATCH = "message_mismatch"
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


def _correlation_key(
    envelope: NotificationEnvelope | ReplyEnvelope,
) -> tuple[str, str | None, str, TargetIdentity]:
    target = (
        envelope.target
        if isinstance(envelope, NotificationEnvelope)
        else envelope.source
    )
    return envelope.entry_id, envelope.session_id, envelope.correlation_id, target


def _matches(notification: NotificationEnvelope, reply: ReplyEnvelope) -> bool:
    return (
        _correlation_key(notification) == _correlation_key(reply)
        and notification.message_id == reply.message_id
    )


@dataclass(frozen=True)
class ReplyAssociation:
    disposition: ReplyDisposition
    reply: ReplyEnvelope
    notification: NotificationEnvelope | None = None

    def __post_init__(self) -> None:
        _instance(self.disposition, ReplyDisposition)
        _instance(self.reply, ReplyEnvelope)
        if self.notification is not None:
            _instance(self.notification, NotificationEnvelope)
        associated = self.disposition in (
            ReplyDisposition.ACCEPTED,
            ReplyDisposition.DUPLICATE,
        )
        if associated:
            if self.notification is None or not _matches(self.notification, self.reply):
                raise ValueError("Associated reply requires a matching notification")
        elif self.notification is not None:
            raise ValueError("Unassociated reply cannot carry a notification")


@dataclass
class _Registration:
    notification: NotificationEnvelope
    reply_ids: set[str] = field(default_factory=set)


class ReplyCorrelator:
    """Explicitly owned in-memory associations with no expiry or retry policy.

    The caller registers one message per scoped correlation/target and decides
    when to forget it. Re-registration is rejected, so it cannot silently erase
    duplicate knowledge. A reply ID is scoped to its associated notification;
    changed payloads with the same ID still count as duplicate deliveries.
    """

    def __init__(self) -> None:
        self._registrations: dict[
            tuple[str, str | None, str, TargetIdentity], _Registration
        ] = {}

    def register(self, notification: NotificationEnvelope) -> None:
        _instance(notification, NotificationEnvelope)
        key = _correlation_key(notification)
        if key in self._registrations:
            raise ValueError(
                "Correlation and target are already registered in this scope"
            )
        self._registrations[key] = _Registration(notification)

    def receive(self, reply: ReplyEnvelope) -> ReplyAssociation:
        _instance(reply, ReplyEnvelope)
        registration = self._registrations.get(_correlation_key(reply))
        if registration is None:
            return ReplyAssociation(ReplyDisposition.UNKNOWN, reply)
        notification = registration.notification
        if not _matches(notification, reply):
            return ReplyAssociation(ReplyDisposition.MESSAGE_MISMATCH, reply)
        if reply.reply_id in registration.reply_ids:
            disposition = ReplyDisposition.DUPLICATE
        else:
            registration.reply_ids.add(reply.reply_id)
            disposition = ReplyDisposition.ACCEPTED
        return ReplyAssociation(disposition, reply, notification)

    def forget(self, notification: NotificationEnvelope) -> bool:
        """Explicitly release a registration and its duplicate knowledge."""
        _instance(notification, NotificationEnvelope)
        key = _correlation_key(notification)
        registration = self._registrations.get(key)
        if registration is None:
            return False
        if registration.notification.message_id != notification.message_id:
            raise ValueError("Cannot forget a different message")
        del self._registrations[key]
        return True
