"""labs_config_* over MCP: a round trip, a refused stale version, a refused scope."""

import pytest

from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.mcp.tools import scope_config as tools
from connect_labs.scope_config.tests.conftest import _HOLDS

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="member", password="x")
    monkeypatch.setattr(tools, "require_connect_token", lambda user: "token")

    real = tools._caller

    def caller(u):
        c = real(u)
        _HOLDS[id(c)] = ("program:7",)
        return c

    monkeypatch.setattr(tools, "_caller", caller)
    return user


def _call(name, user, **kwargs):
    return get_tool(name).handler(user, **kwargs)


def test_set_then_get_shows_this_programme_set_it(member):
    _call(
        "labs_config_set",
        member,
        namespace="t",
        scope_type="program",
        scope_key="7",
        patch={"a": 3},
        expected_version=0,
    )
    got = _call("labs_config_get", member, namespace="t", scope_type="program", scope_key="7")
    assert got["value"]["a"] == 3 and got["provenance"]["a"] == "programme Programme Seven" and got["version"] == 1


def test_a_stale_version_is_a_version_conflict(member):
    _call(
        "labs_config_set",
        member,
        namespace="t",
        scope_type="program",
        scope_key="7",
        patch={"a": 3},
        expected_version=0,
    )
    with pytest.raises(MCPToolError) as err:
        _call(
            "labs_config_set",
            member,
            namespace="t",
            scope_type="program",
            scope_key="7",
            patch={"a": 4},
            expected_version=0,
        )
    assert err.value.code == "VERSION_CONFLICT"


def test_a_scope_the_caller_does_not_hold_is_permission_denied(member):
    with pytest.raises(MCPToolError) as err:
        _call("labs_config_get", member, namespace="t", scope_type="program", scope_key="8")
    assert err.value.code == "PERMISSION_DENIED"


def test_an_unknown_namespace_is_not_found(member):
    with pytest.raises(MCPToolError) as err:
        _call("labs_config_get", member, namespace="nope", scope_type="program", scope_key="7")
    assert err.value.code == "NOT_FOUND"


def test_history_and_undo(member):
    _call(
        "labs_config_set",
        member,
        namespace="t",
        scope_type="program",
        scope_key="7",
        patch={"a": 3},
        expected_version=0,
    )
    assert (
        len(_call("labs_config_history", member, namespace="t", scope_type="program", scope_key="7")["changes"]) == 1
    )
    undone = _call("labs_config_undo", member, namespace="t", scope_type="program", scope_key="7")
    assert undone["data"] == {}


def test_the_config_tools_are_on_the_restricted_endpoint():
    from connect_labs.mcp.token_scopes import RESTRICTED_TOOLS

    assert {"labs_config_get", "labs_config_set", "labs_config_history", "labs_config_undo"} <= RESTRICTED_TOOLS
