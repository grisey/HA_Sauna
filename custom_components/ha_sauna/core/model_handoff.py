"""Explicit, immutable model handoffs without a runtime event collector.

The concrete dataclass is the model contract. Existing complete model values
remain intact; there is no second catalogue of their possible events. A handoff
describes the supplied observations, not delivery, authorization or persistence.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import cache
from math import isfinite
from types import UnionType
from typing import (
    Any,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .contracts import ConsumerEvent
from .notification_recipients import ActorRef
from .timeline import utc


def _scalar(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int, datetime, timedelta)):
        return True
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Model observations require finite numbers")
        return True
    if isinstance(value, Enum):
        require_immutable(value.value)
        return True
    return False


@cache
def _field_types(model_type: type) -> dict:
    return get_type_hints(model_type)


def _has_type(value: object, annotation: object) -> bool:
    """Interpret existing field annotations, without duplicating model schemas."""
    if annotation is Any or annotation is object or isinstance(annotation, TypeVar):
        return True
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        return any(_has_type(value, candidate) for candidate in arguments)
    if origin is Literal:
        return any(
            type(value) is type(candidate) and value == candidate
            for candidate in arguments
        )
    if origin is tuple:
        if not isinstance(value, tuple):
            return False
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return all(_has_type(item, arguments[0]) for item in value)
        return len(value) == len(arguments) and all(
            _has_type(item, expected) for item, expected in zip(value, arguments)
        )
    if origin is frozenset:
        return isinstance(value, frozenset) and all(
            _has_type(item, arguments[0]) for item in value
        )
    if origin is Mapping:
        return isinstance(value, Mapping) and all(
            _has_type(key, arguments[0]) and _has_type(item, arguments[1])
            for key, item in value.items()
        )
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(annotation, type):
        return isinstance(value, annotation)
    raise TypeError(f"Unsupported model field annotation: {annotation!r}")


def require_immutable(value: object, *, context: HandoffContext | None = None) -> None:
    """Reject live objects and mutable nested data, including inside dataclasses.

    This checks the handoff's structural boundary. The owning model remains
    responsible for its domain invariants; no business rules are reimplemented.
    """
    checked = set()

    def check(item, ancestors):
        if _scalar(item):
            return
        identity = id(item)
        if identity in ancestors:
            raise ValueError("Cyclic model observations are not supported")
        if identity in checked:
            return
        ancestors.add(identity)
        try:
            if isinstance(item, (tuple, frozenset)):
                for child in item:
                    check(child, ancestors)
            elif is_dataclass(item) and not isinstance(item, type):
                if not item.__dataclass_params__.frozen:
                    raise TypeError("Model dataclasses must be frozen")
                annotations = _field_types(type(item))
                for definition in fields(item):
                    child = getattr(item, definition.name)
                    if not _has_type(child, annotations[definition.name]):
                        raise TypeError(
                            f"Invalid model field: {type(item).__name__}.{definition.name}"
                        )
                    if context is not None and child is not None:
                        if (
                            definition.name == "entry_id"
                            and context.entry_id is not None
                            and child != context.entry_id
                        ):
                            raise ValueError("Handoff context and model entry differ")
                        if (
                            definition.name == "session_id"
                            and context.session_id is not None
                            and child != context.session_id
                        ):
                            raise ValueError("Handoff context and model session differ")
                    check(child, ancestors)
            else:
                raise TypeError(
                    f"Mutable or unsupported model value: {type(item).__name__}"
                )
        finally:
            ancestors.remove(identity)
        checked.add(identity)

    check(value, set())


@dataclass(frozen=True)
class FrozenMap(Mapping[str, object]):
    """An immutable copy of an existing extensible diagnostic/attribute map.

    Keys and all values are retained. Nested collections are copied by
    ``freeze_data``; this form does not select diagnostic fields or event kinds.
    """

    _items: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        if not isinstance(self._items, tuple):
            raise TypeError("FrozenMap requires immutable entries")
        keys = set()
        for item in self._items:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("FrozenMap entries must be key/value pairs")
            key, value = item
            if not isinstance(key, str):
                raise TypeError("Observation map keys must be strings")
            if key in keys:
                raise ValueError("Duplicate observation map key")
            keys.add(key)
            require_immutable(value)
        object.__setattr__(self, "_items", tuple(sorted(self._items)))

    @classmethod
    def _from_frozen(cls, items):
        """Build only from entries already checked and copied by freeze_data."""
        result = object.__new__(cls)
        object.__setattr__(result, "_items", tuple(sorted(items)))
        return result

    def __getitem__(self, key: str) -> object:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)


def freeze_data(value: object) -> object:
    """Copy existing observation data without retaining mutable source aliases."""

    def freeze(item, ancestors):
        if _scalar(item):
            return item
        if isinstance(item, FrozenMap) or is_dataclass(item):
            require_immutable(item)
            return item
        identity = id(item)
        if identity in ancestors:
            raise ValueError("Cyclic observation data is not supported")
        ancestors.add(identity)
        try:
            if isinstance(item, Mapping):
                keys, items = set(), []
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise TypeError("Observation map keys must be strings")
                    if key in keys:
                        raise ValueError("Duplicate observation map key")
                    keys.add(key)
                    items.append((key, freeze(child, ancestors)))
                return FrozenMap._from_frozen(items)
            if isinstance(item, (tuple, list)):
                return tuple(freeze(child, ancestors) for child in item)
            if isinstance(item, (set, frozenset)):
                return frozenset(freeze(child, ancestors) for child in item)
            raise TypeError(f"Unsupported observation data: {type(item).__name__}")
        finally:
            ancestors.remove(identity)

    return freeze(value, set())


@dataclass(frozen=True)
class HandoffContext:
    """Caller-supplied provenance and an optional existing session reference.

    ``observed_at`` dates this explicit handoff, not the effective time of every
    enclosed fact. Those source times remain in the original model values.
    IDs are not generated or interpreted as receipt acknowledgements here.
    A supplied entry or session reference scopes all enclosed facts accordingly.
    Cross-entry/session aggregates use None for the respective common scope and
    retain their own complete identities. None never invents a common owner.
    """

    entry_id: str | None
    handoff_id: str
    observed_at: datetime
    actor: ActorRef | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.handoff_id, str) or not self.handoff_id.strip():
            raise ValueError("A handoff identity is required")
        if self.entry_id is not None and (
            not isinstance(self.entry_id, str) or not self.entry_id.strip()
        ):
            raise ValueError("A supplied entry reference must be nonempty")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("A handoff observation time is required")
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        if self.actor is not None and not isinstance(self.actor, ActorRef):
            raise TypeError("A handoff actor must be an ActorRef")
        if self.session_id is not None and (
            not isinstance(self.session_id, str) or not self.session_id.strip()
        ):
            raise ValueError("A supplied session reference must be nonempty")


Model = TypeVar("Model")


@dataclass(frozen=True)
class ModelHandoff(Generic[Model]):
    """Whole values of one model before/after an explicitly supplied boundary.

    None represents absence. Equal values are allowed: an evaluated input need
    not change a state. Callers retain every intended handoff and its order;
    there is no change filter, subscription, queue, retry or event derivation.
    """

    context: HandoffContext
    before: Model | None
    after: Model | None

    def __post_init__(self) -> None:
        if not isinstance(self.context, HandoffContext):
            raise TypeError("A handoff context is required")
        states = tuple(
            state for state in (self.before, self.after) if state is not None
        )
        if not states:
            raise ValueError("A handoff must contain at least one complete model value")
        if any(type(state) is not type(states[0]) for state in states):
            raise TypeError("A handoff cannot mix different model contracts")
        for state in states:
            if (
                not is_dataclass(state)
                or isinstance(state, type)
                or isinstance(state, FrozenMap)
            ):
                raise TypeError("A handoff requires a concrete model dataclass")
            if isinstance(state, ConsumerEvent):
                raise TypeError(
                    "A ConsumerEvent requires its complete ConsumerEventOutput"
                )
        # Shared immutable submodels (including identical before/after values)
        # are checked once, together with their owner scope.
        require_immutable(states, context=self.context)

    @property
    def model_type(self) -> type[Model]:
        return type(self.after if self.after is not None else self.before)


class ModelConsumer(Protocol[Model]):
    """A future consumer receives the complete form, without owner lookups."""

    def consume(self, handoff: ModelHandoff[Model]) -> None: ...
