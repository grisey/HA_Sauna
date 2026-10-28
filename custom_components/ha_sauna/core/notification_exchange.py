"""Opaque exchange forms without sending, authorization or lifecycle policy.

Adapters preserve evidenced provenance and leave unknown identities absent.
Correlation checks association only; it does not authenticate or authorize.
Payloads remain caller-owned and are neither interpreted nor serialized here.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
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
class ReplyKey:
    """Caller-supplied native matching key, without identity or provenance claims."""

    transport: str
    value: str

    def __post_init__(self) -> None:
        _required(self.transport)
        _instance(self.value, str)


@dataclass(frozen=True)
class ReplyEnvelope:
    reply_id: str | None
    message_id: str | None
    correlation_id: str | None
    entry_id: str | None
    source: TargetIdentity | None
    actor: ActorRef
    payload: object
    session_id: str | None = None
    context_id: str | None = None
    observed_at: datetime | None = None
    reply_key: ReplyKey | None = None

    def __post_init__(self) -> None:
        for value in (
            self.reply_id,
            self.message_id,
            self.correlation_id,
            self.entry_id,
            self.session_id,
            self.context_id,
        ):
            if value is not None:
                _required(value)
        if self.source is not None:
            _instance(self.source, TargetIdentity)
        _instance(self.actor, ActorRef)
        if self.observed_at is not None:
            _instance(self.observed_at, datetime)
        if self.reply_key is not None:
            _instance(self.reply_key, ReplyKey)


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
    AMBIGUOUS = "ambiguous"
    MESSAGE_MISMATCH = "message_mismatch"
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


def _correlation_key(
    envelope: NotificationEnvelope | ReplyEnvelope,
) -> tuple[str | None, str | None, str | None, TargetIdentity | None]:
    target = (
        envelope.target
        if isinstance(envelope, NotificationEnvelope)
        else envelope.source
    )
    return envelope.entry_id, envelope.session_id, envelope.correlation_id, target


def _matches(notification: NotificationEnvelope, reply: ReplyEnvelope) -> bool:
    if reply.reply_key is None:
        return (
            _correlation_key(notification) == _correlation_key(reply)
            and notification.message_id == reply.message_id
        )
    return all(
        supplied is None or supplied == actual
        for supplied, actual in (
            (reply.entry_id, notification.entry_id),
            (reply.session_id, notification.session_id),
            (reply.correlation_id, notification.correlation_id),
            (reply.message_id, notification.message_id),
            (reply.source, notification.target),
        )
    )


@dataclass(frozen=True)
class ReplyAssociation:
    disposition: ReplyDisposition
    reply: ReplyEnvelope
    notification: NotificationEnvelope | None = None
    handoff_accepted: bool | None = None

    @property
    def ambiguous(self) -> bool:
        return self.disposition is ReplyDisposition.AMBIGUOUS

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
        elif self.notification is not None or self.handoff_accepted is not None:
            raise ValueError("Unassociated reply cannot carry notification acceptance")
        if self.handoff_accepted is not None:
            _instance(self.handoff_accepted, bool)


@dataclass(frozen=True, eq=False)
class RegistrationHandle:
    """Opaque identity used only for exact registration updates and rollback."""


@dataclass
class _Registration:
    notification: NotificationEnvelope
    handle: RegistrationHandle
    reply_keys: frozenset[ReplyKey]
    handoff_accepted: bool | None = None
    reply_ids: set[str] = field(default_factory=set)


class ReplyCorrelator:
    """Single association owner; callers explicitly manage registration lifetime.

    Explicit replies retain exact scope matching. Native keys can match several
    registrations and never establish reply provenance. Only independent reply
    IDs carry duplicate knowledge; context IDs never do.
    """

    def __init__(self) -> None:
        self._registrations: dict[RegistrationHandle, _Registration] = {}

    def register(
        self,
        notification: NotificationEnvelope,
        *,
        reply_keys: Iterable[ReplyKey] = (),
    ) -> RegistrationHandle:
        _instance(notification, NotificationEnvelope)
        keys = frozenset(reply_keys)
        for reply_key in keys:
            _instance(reply_key, ReplyKey)
        key = _correlation_key(notification)
        if not keys and any(
            _correlation_key(item.notification) == key
            for item in self._registrations.values()
        ):
            raise ValueError(
                "Correlation and target are already registered in this scope"
            )
        handle = RegistrationHandle()
        self._registrations[handle] = _Registration(notification, handle, keys)
        return handle

    def receive(self, reply: ReplyEnvelope) -> ReplyAssociation:
        _instance(reply, ReplyEnvelope)
        if reply.reply_key is not None:
            candidates = [
                registration
                for registration in self._registrations.values()
                if reply.reply_key in registration.reply_keys
                and _matches(registration.notification, reply)
            ]
            if len(candidates) > 1:
                return ReplyAssociation(ReplyDisposition.AMBIGUOUS, reply)
            registration = candidates[0] if candidates else None
        else:
            candidates = [
                item
                for item in self._registrations.values()
                if _correlation_key(item.notification) == _correlation_key(reply)
            ]
            if candidates and not any(
                item.notification.message_id == reply.message_id for item in candidates
            ):
                return ReplyAssociation(ReplyDisposition.MESSAGE_MISMATCH, reply)
            if len(candidates) > 1:
                candidates = [
                    item
                    for item in candidates
                    if item.notification.message_id == reply.message_id
                ]
                if len(candidates) > 1:
                    return ReplyAssociation(ReplyDisposition.AMBIGUOUS, reply)
            registration = candidates[0] if candidates else None
        if registration is None:
            return ReplyAssociation(ReplyDisposition.UNKNOWN, reply)
        notification = registration.notification
        if not _matches(notification, reply):
            return ReplyAssociation(ReplyDisposition.MESSAGE_MISMATCH, reply)
        if reply.reply_id is not None and reply.reply_id in registration.reply_ids:
            disposition = ReplyDisposition.DUPLICATE
        else:
            if reply.reply_id is not None:
                registration.reply_ids.add(reply.reply_id)
            disposition = ReplyDisposition.ACCEPTED
        return ReplyAssociation(
            disposition, reply, notification, registration.handoff_accepted
        )

    def mark_accepted(self, handle: RegistrationHandle) -> bool:
        """Record acceptance only if this exact registration still exists."""
        _instance(handle, RegistrationHandle)
        registration = self._registrations.get(handle)
        if registration is None:
            return False
        registration.handoff_accepted = True
        return True

    def rollback(self, handle: RegistrationHandle) -> bool:
        """Remove only this registration, preserving any later replacement."""
        _instance(handle, RegistrationHandle)
        return self._registrations.pop(handle, None) is not None

    def forget(self, notification: NotificationEnvelope) -> bool:
        """Explicitly release a registration and its duplicate knowledge."""
        _instance(notification, NotificationEnvelope)
        key = _correlation_key(notification)
        candidates = [
            item
            for item in self._registrations.values()
            if _correlation_key(item.notification) == key
        ]
        if not candidates:
            return False
        matching = [
            item
            for item in candidates
            if item.notification.message_id == notification.message_id
        ]
        if not matching:
            raise ValueError("Cannot forget a different message")
        for registration in matching:
            del self._registrations[registration.handle]
        return True

    def forget_message(self, entry_id: str, message_id: str) -> int:
        return self._forget_scope(entry_id, message_id=message_id)

    def forget_session(self, entry_id: str, session_id: str | None) -> int:
        return self._forget_scope(entry_id, session_id=session_id)

    def forget_entry(self, entry_id: str) -> int:
        return self._forget_scope(entry_id)

    def _forget_scope(self, entry_id: str, **scope: str | None) -> int:
        keys = [
            key
            for key, registration in self._registrations.items()
            if registration.notification.entry_id == entry_id
            and all(
                getattr(registration.notification, name) == value
                for name, value in scope.items()
            )
        ]
        for key in keys:
            del self._registrations[key]
        return len(keys)
