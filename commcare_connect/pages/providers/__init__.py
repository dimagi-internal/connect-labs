"""Provider registry with auto-discovery of sibling provider modules."""

from __future__ import annotations

import importlib
import pkgutil

from commcare_connect.pages.providers import base  # noqa: F401 (re-exported for tests)

_REGISTRY: dict[str, base.CardProvider] = {}


def register(provider_cls):
    """Class decorator: instantiate and register a provider by its `key`."""
    instance = provider_cls()
    if not instance.key:
        raise ValueError(f"{provider_cls.__name__} must define a non-empty `key`")
    _REGISTRY[instance.key] = instance
    return provider_cls


def get_provider(key: str):
    _ensure_discovered()
    return _REGISTRY.get(key)


def list_providers() -> list:
    _ensure_discovered()
    return list(_REGISTRY.values())


_discovered = False


def _ensure_discovered() -> None:
    global _discovered
    if _discovered:
        return
    _discovered = True
    discover()


def discover() -> None:
    """Import every sibling module so their @register decorators fire."""
    package = importlib.import_module(__name__)
    for _finder, name, _ispkg in pkgutil.iter_modules(package.__path__):
        if name in {"base"} or name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{name}")
