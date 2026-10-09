"""The namespaces config can hold, each declared by the app that reads it.

A namespace says what one layer may contain (a JSON schema), its code defaults,
and which layers may set it. Supply's tabs cannot be set by a person (decision
4), so the `supply` namespace leaves `user` out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import jsonschema


@dataclass(frozen=True)
class Namespace:
    key: str
    label: str
    description: str
    schema: dict
    defaults: dict = field(default_factory=dict)
    layers: frozenset = frozenset({"organization", "program", "opportunity"})
    # Extra rules a schema cannot say (e.g. "Overview cannot be hidden"). Raises
    # ValueError with a message for the person.
    check: Callable[[dict], None] | None = None


_REGISTRY: dict[str, Namespace] = {}


def register_namespace(namespace: Namespace) -> Namespace:
    _REGISTRY[namespace.key] = namespace
    return namespace


def get_namespace(key: str) -> Namespace:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"no settings namespace {key!r}; known: {', '.join(sorted(_REGISTRY)) or 'none'}") from None


def all_namespaces() -> list[Namespace]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def validate_layer(namespace: Namespace, data: dict) -> None:
    """Raise ValueError if `data` is not a valid layer of `namespace`."""
    try:
        jsonschema.validate(data, namespace.schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path)
        raise ValueError(f"{where + ': ' if where else ''}{exc.message}") from None
    if namespace.check:
        namespace.check(data)
