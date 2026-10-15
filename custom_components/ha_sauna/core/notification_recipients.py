"""HA-independent recipient identities and explicit session set accumulation.

Resolvers supply evidence; these forms neither discover devices nor define which
operation counts as participation. Assignment is not control authorization.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


def _required(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identity and evidence fields must be nonempty strings")


def _typed(value, expected) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"Expected {expected.__name__}")


def _typed_set(values, expected):
    try:
        items = tuple(values)
    except TypeError as err:
        raise ValueError("Expected an iterable of typed values") from err
    for item in items:
        _typed(item, expected)
    return frozenset(items)


@dataclass(frozen=True)
class ActorRef:
    user_id: str | None
    source: str

    def __post_init__(self) -> None:
        _required(self.source)
        if self.user_id is not None:
            _required(self.user_id)


@dataclass(frozen=True)
class TargetIdentity:
    """Canonical transport endpoint, shared by entity and device aliases."""

    transport: str
    endpoint: str

    def __post_init__(self) -> None:
        _required(self.transport)
        _required(self.endpoint)


@dataclass(frozen=True)
class RecipientRef:
    kind: Literal["entity", "device"]
    reference_id: str
    transport: str

    def __post_init__(self) -> None:
        if self.kind not in ("entity", "device"):
            raise ValueError("Recipient reference must identify an entity or device")
        _required(self.reference_id)
        _required(self.transport)


@dataclass(frozen=True)
class CapabilityEvidence:
    """Adapter-supplied source and capability contract, not delivery evidence."""

    source: str
    contract: str

    def __post_init__(self) -> None:
        _required(self.source)
        _required(self.contract)


@dataclass(frozen=True)
class RecipientCapability:
    send: CapabilityEvidence | None = None
    reply: CapabilityEvidence | None = None

    def __post_init__(self) -> None:
        for evidence in (self.send, self.reply):
            if evidence is not None and not isinstance(evidence, CapabilityEvidence):
                raise ValueError("Capability requires explicit evidence")

    @property
    def interactive(self) -> bool:
        return self.send is not None and self.reply is not None


@dataclass(frozen=True)
class ResolvedRecipient:
    reference: RecipientRef
    target: TargetIdentity
    capability: RecipientCapability

    def __post_init__(self) -> None:
        _typed(self.reference, RecipientRef)
        _typed(self.target, TargetIdentity)
        _typed(self.capability, RecipientCapability)
        if self.reference.transport != self.target.transport:
            raise ValueError("Reference and canonical target transports differ")


@dataclass(frozen=True)
class RecipientResolution:
    """None represents an unresolved reference, with no replacement target."""

    reference: RecipientRef
    recipient: ResolvedRecipient | None = None

    def __post_init__(self) -> None:
        _typed(self.reference, RecipientRef)
        if self.recipient is not None:
            _typed(self.recipient, ResolvedRecipient)
        if self.recipient is not None and self.recipient.reference != self.reference:
            raise ValueError("Resolution belongs to a different reference")


@dataclass(frozen=True)
class RecipientAssignment:
    """Raw explicit assignment; validate_assignment is the pre-storage boundary."""

    user_id: str
    recipients: frozenset[RecipientRef]

    def __post_init__(self) -> None:
        _required(self.user_id)
        object.__setattr__(
            self, "recipients", _typed_set(self.recipients, RecipientRef)
        )


def _resolution_index(resolutions: Iterable[RecipientResolution]):
    resolved = {}
    for resolution in resolutions:
        _typed(resolution, RecipientResolution)
        if resolution.reference in resolved:
            if resolved[resolution.reference] != resolution:
                raise ValueError("Conflicting resolutions for one reference")
        resolved[resolution.reference] = resolution
    return resolved


def _eligible(resolution: RecipientResolution) -> bool:
    return (
        resolution.recipient is not None and resolution.recipient.capability.interactive
    )


def eligible_recipients(
    resolutions: Iterable[RecipientResolution],
) -> frozenset[RecipientRef]:
    """Candidates supported by the explicit resolver snapshot."""
    return frozenset(
        ref
        for ref, result in _resolution_index(resolutions).items()
        if _eligible(result)
    )


def validate_assignment(
    assignment: RecipientAssignment,
    resolutions: Iterable[RecipientResolution],
) -> RecipientAssignment:
    """Validate before storage, using the same suitability rule as selection.

    This checks the supplied snapshot, not future availability or authority.
    No partial assignment is returned when any selected reference is invalid.
    """
    _typed(assignment, RecipientAssignment)
    resolved = _resolution_index(resolutions)
    for ref in sorted(
        assignment.recipients,
        key=lambda item: (item.transport, item.kind, item.reference_id),
    ):
        result = resolved.get(ref)
        if result is None:
            raise ValueError(f"No resolution supplied for {ref.reference_id}")
        if result.recipient is None:
            raise ValueError(f"Recipient is unresolved: {ref.reference_id}")
        if not _eligible(result):
            raise ValueError(
                f"Send and reply capability evidence required: {ref.reference_id}"
            )
    return assignment


@dataclass(frozen=True)
class SessionRecipients:
    """Caller-owned snapshot; no persistence or assignment-change policy.

    The caller supplies the current session identity and explicitly decides when
    to accumulate and which assignment/resolution snapshots to pass. Existing
    targets are retained by this union operation; callers can construct another
    snapshot when a separately defined policy requires a different set.
    """

    entry_id: str
    session_id: str
    targets: frozenset[TargetIdentity] = frozenset()

    def __post_init__(self) -> None:
        _required(self.entry_id)
        _required(self.session_id)
        object.__setattr__(self, "targets", _typed_set(self.targets, TargetIdentity))

    def accumulate(
        self,
        actor: ActorRef,
        assignments: Iterable[RecipientAssignment],
        resolutions: Iterable[RecipientResolution],
    ) -> "SessionRecipients":
        _typed(actor, ActorRef)
        assignments = tuple(assignments)
        for assignment in assignments:
            _typed(assignment, RecipientAssignment)
        resolved = _resolution_index(resolutions)
        if actor.user_id is None:
            return self
        references = {
            ref
            for assignment in assignments
            if assignment.user_id == actor.user_id
            for ref in assignment.recipients
        }
        targets = {
            item.recipient.target
            for reference, item in resolved.items()
            if reference in references and _eligible(item)
        }
        return SessionRecipients(self.entry_id, self.session_id, self.targets | targets)
