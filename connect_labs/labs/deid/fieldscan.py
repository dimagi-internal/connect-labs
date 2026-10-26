"""The one walk over a submitted form: every leaf, with the path that reads it.

A path is a tuple of keys from the top of ``form_json`` (``("form", "child", "weight")``);
a repeat group contributes ``"[]"`` for "each item of this list". Its dotted form
(``form.child.weight``, ``form.visits.[].weight``) is what people and the dictionary
name a field by.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

REPEAT = "[]"

#: Keys CommCare adds to every form that are not answers. ``meta`` holds the device,
#: the worker's user id and username, timestamps and the phone's GPS reading: never
#: something to analyse or export, so it is not walked at all.
SKIP_KEYS = frozenset({"@xmlns", "@uiVersion", "@version", "#type", "@name", "meta"})

_APP_BUILD = re.compile(r"App Version:\s*(\d+)", re.IGNORECASE)


def walk(node, path: tuple = (), *, skip: frozenset = SKIP_KEYS) -> Iterator[tuple[tuple, object]]:
    """Yield ``(path, value)`` for every leaf under ``node``, skipping ``skip`` keys."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip:
                continue
            yield from walk(value, path + (key,), skip=skip)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, path + (REPEAT,), skip=skip)
    else:
        yield path, node


def dotted(path: tuple) -> str:
    return ".".join(path)


def split(path: str) -> tuple:
    return tuple(p for p in (path or "").split(".") if p)


def normalise(path: str) -> str:
    """The key two spellings of one question share: case and stray spaces ignored.

    An app that renamed ``Lethargy`` to ``lethargy`` between versions asks the same
    question; matching on the exact spelling is how a pipeline came to read only one of
    them. A real rename (``ds_lethargy`` to ``danger_lethargy``) is not caught here --
    that is a person's call, made by merging two dictionary entries.
    """
    return ".".join(p.strip().lower() for p in split(path))


def values_at(form_json, path: tuple) -> list:
    """Every value at ``path`` (several, under a repeat group); [] when absent."""
    nodes = [form_json]
    for key in path:
        nxt = []
        for node in nodes:
            if key == REPEAT:
                if isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict) and key in node:
                nxt.append(node[key])
        nodes = nxt
        if not nodes:
            return []
    return [n for n in nodes if not isinstance(n, dict | list)]


def app_build(form_json) -> int | None:
    """The app build a form was filled in on, from ``form.meta.appVersion``.

    CommCare writes e.g. ``CommCare Android, version "2.53.1"... App Version: 123``.
    Only the number is read; None when the form does not say."""
    meta = ((form_json or {}).get("form") or {}).get("meta") or {}
    text = meta.get("appVersion") if isinstance(meta, dict) else None
    if isinstance(text, dict):  # some exports nest it as {"#text": ...}
        text = text.get("#text")
    match = _APP_BUILD.search(str(text or ""))
    return int(match.group(1)) if match else None
