"""A small world of scopes, and callers who hold some of them.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest

from connect_labs.labs.access import scopes as access
from connect_labs.scope_config import service
from connect_labs.scope_config.namespaces import _REGISTRY, Namespace, register_namespace

TREE = {
    "organizations": [
        {"id": 1, "slug": "owner-org", "name": "Owner Org"},
        {"id": 2, "slug": "llo-org", "name": "Delivering Org"},
    ],
    "programs": [{"id": 7, "name": "Programme Seven", "organization": "owner-org"}],
    "opportunities": [
        {"id": 70, "name": "Opp Seventy", "organization": "llo-org", "program": 7},
        {"id": 80, "name": "Opp Eighty", "organization": "llo-org"},
    ],
}


class _User:
    def __init__(self, username, is_staff=False):
        self.username = username
        self.is_staff = is_staff
        self.is_authenticated = True


def caller(username, holds=(), is_staff=False):
    """A caller holding exactly `holds` (strings like 'program:7'), or nothing knowable if holds is None."""
    c = access.Caller(user=_User(username, is_staff=is_staff))
    # Caller is frozen; what it holds lives beside it, read by the fake below.
    _HOLDS[id(c)] = holds
    return c


_HOLDS: dict = {}


def _fake_may_use(caller, *, organization_id=None, program_id=None, opportunity_id=None):
    holds = _HOLDS.get(id(caller), ())
    if holds is None:
        return access.UNKNOWABLE
    for kind, value in (("organization", organization_id), ("program", program_id), ("opportunity", opportunity_id)):
        if value is not None and f"{kind}:{value}" not in holds:
            return f"{kind} {value} is not accessible to your account"
    return None


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(service.access, "may_use", _fake_may_use)
    monkeypatch.setattr(service, "tree_for", lambda caller: TREE)
    saved = dict(_REGISTRY)
    register_namespace(
        Namespace(
            key="t",
            label="Test",
            description="",
            schema={"type": "object"},
            defaults={"a": 0, "m": {"x": "default"}},
            layers=frozenset({"organization", "program", "opportunity"}),
        )
    )
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)
    _HOLDS.clear()
