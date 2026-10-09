"""How layers combine: maps merge by key, lists replace whole, `null` removes.

A patch is an RFC 7386 JSON merge patch, and a layer is applied to the layers
below it the same way, so "what this programme set" and "what changing it does"
follow one rule. Ordered things are maps with `position`, never lists, so a
programme can change one item without restating the rest.
"""

from __future__ import annotations

import copy


def merge_patch(target, patch):
    """`target` with `patch` applied. Neither argument is modified."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = merge_patch(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _leaves(value, prefix=""):
    """Dotted paths to every non-map value (an empty map counts as a leaf)."""
    if isinstance(value, dict) and value:
        for key, inner in value.items():
            yield from _leaves(inner, f"{prefix}.{key}" if prefix else str(key))
    elif prefix:
        yield prefix


def resolve_layers(defaults: dict, layers: list[tuple[str, dict]]) -> tuple[dict, dict]:
    """(value, provenance): `layers` applied over `defaults`, lowest first.

    `layers` is [(label, data)], e.g. [("organization dimagi", {...}), ...].
    Provenance maps each leaf's dotted path to the label of the layer that set
    it, or "default".
    """
    value = copy.deepcopy(defaults or {})
    provenance = {path: "default" for path in _leaves(value)}
    for label, data in layers:
        if not data:
            continue
        value = merge_patch(value, data)
        for path in _leaves(data):
            # A value set where a map was replaces everything under it.
            for existing in [p for p in provenance if p.startswith(path + ".")]:
                del provenance[existing]
            provenance[path] = label
    # A `null` removes a key; a map set where a value was leaves that value's
    # path behind. Only paths still present are reported.
    live = set(_leaves(value))
    return value, {path: who for path, who in provenance.items() if path in live}
