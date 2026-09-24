"""Every tool that changes labs state must be registered with is_write=True.

is_write is what subjects a call to the per-user write rate limit AND what keeps
its full arguments in the MCP audit log (server._write_audit stores `{}` for a
read). A mutating tool registered without it is invisible in exactly the place
someone looks when asking "who changed this?". semantic_registry_create and
semantic_registry_update shipped that way -- the two tools that rewrite live
indicator definitions -- while their sibling set_indicator_meta was flagged.

The per-module tests (test_fund_tools, test_solicitation_tools, ...) each pin
their own tools; this one catches a NEW tool in a module nobody wrote a flag
test for. It keys on the verb in the tool's name, so a tool that genuinely
does not write despite its name goes in NOT_A_WRITE with the reason.
"""

import pytest

from connect_labs.mcp import tools  # noqa: F401 -- registers every tool
from connect_labs.mcp.tool_registry import _REGISTRY

MUTATING_VERBS = {
    "create",
    "update",
    "delete",
    "set",
    "patch",
    "add",
    "remove",
    "upsert",
    "save",
    "rebuild",
    "prune",
    "sync",
    "clone",
    "publish",
    "award",
    "void",
    "correct",
    "merge",
    "reset",
    "disable",
    "reseed",
    "transition",
    "attach",
    "revoke",
}

# name -> why it is not a write despite a mutating verb in its name.
NOT_A_WRITE: dict[str, str] = {
    "microplans_bulk_create_status": "polls a bulk-create task; reads only",
    "supply_chain_award_list": "lists awards; reads only",
    "supply_chain_update_link_list": "lists supplier update links ('update link' is a noun); reads only",
    "synthetic_clone_profile": (
        "writes an aggregate-stats profile bundle to storage, not labs records; registered "
        "is_write=False on purpose, like every synthetic_profile_* sibling"
    ),
}


def _mutating_by_name():
    return sorted(name for name in _REGISTRY if set(name.split("_")) & MUTATING_VERBS)


@pytest.mark.parametrize("name", _mutating_by_name())
def test_mutating_tool_is_flagged_is_write(name):
    if name in NOT_A_WRITE:
        pytest.skip(NOT_A_WRITE[name])
    assert _REGISTRY[name].is_write, (
        f"{name} looks like it changes labs state but is registered without is_write=True, "
        "so it skips the write rate limit and its arguments are dropped from the audit log. "
        "Flag it, or add it to NOT_A_WRITE with the reason."
    )


def test_semantic_registry_writes_are_flagged():
    for name in ("semantic_registry_create", "semantic_registry_update", "semantic_registry_set_indicator_meta"):
        assert _REGISTRY[name].is_write, name
