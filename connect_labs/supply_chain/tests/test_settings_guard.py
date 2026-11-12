"""A partner cannot switch off read enforcement: `acl_enforce_reads` is for the programme's own people.

THIS REPOSITORY IS PUBLIC. Every organisation and person here is invented.

A partner can open a programme's Supply settings like any member of it (visibility is membership), so the
per-key rule lives in the scope_config layer (`Namespace.check_writer`) and every route -- the settings page,
the `labs_config_set` / `labs_config_undo` MCP tools, `service.update` / `service.undo` -- goes through it.
"""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM, Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgMembership
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.mcp.tools import scope_config as tools
from connect_labs.scope_config import service
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.scope_config.scopes import Scope
from connect_labs.supply_chain.runner import declare

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

PROGRAM = 10951
SCOPE = Scope.of("program", PROGRAM)
TREE = {"organizations": [], "programs": [{"id": PROGRAM, "name": "Guarded", "organization": ""}], "opportunities": []}


def _user(username, email=None, **flags):
    return get_user_model().objects.create(username=username, email=email or f"{username}@example.org", **flags)


def _org(slug, name):
    return LabsOrg.objects.create(slug=f"labs-synthetic-{slug}", name=name, labs_only=True)


@pytest.fixture(autouse=True)
def everyone_can_see_the_programme(monkeypatch):
    """Visibility is what lets a partner into the settings page at all: nobody here is refused for it."""
    monkeypatch.setattr(service.access, "may_use", lambda caller, **kw: None)
    monkeypatch.setattr(service, "tree_for", lambda caller: TREE)
    with (
        patch("connect_labs.supply_chain.data_access.may_use", return_value=None),
        patch("connect_labs.supply_chain.identity._caller_organizations", return_value=[]),
    ):
        yield


@pytest.fixture
def runner_org():
    org = _org("runner", "Runner Health")
    declare(PROGRAM, org)
    return org


@pytest.fixture
def runner_member(runner_org):
    user = _user("dev-runner")
    OrgMembership.objects.create(org=runner_org, user=user, role="admin")
    return user


@pytest.fixture
def partner(runner_org):
    user = _user("dev-partner")
    OrgMembership.objects.create(org=_org("partner", "Partner Trust"), user=user, role="admin")
    return user


@pytest.fixture
def support():
    return _user("dev-support", "support@dimagi.com", is_staff=True)


def _caller(user):
    return Caller(user=user, access_token="token")


def _switch():
    row = ScopeConfig.objects.filter(scope_type="program", scope_key=str(PROGRAM), namespace="supply").first()
    return (row.data if row else {}).get("acl_enforce_reads")


def _turn_on(runner_member):
    service.update("supply", SCOPE, {"acl_enforce_reads": True}, _caller(runner_member), via="test")
    assert _switch() is True


# ---- service (what every route funnels through) ------------------------------------------------


def test_a_partner_cannot_switch_enforcement_off(runner_member, partner):
    _turn_on(runner_member)
    for patch_ in ({"acl_enforce_reads": False}, {"acl_enforce_reads": None}):
        with pytest.raises(service.Forbidden, match="Runner Health"):
            service.update("supply", SCOPE, patch_, _caller(partner), via="test")
        assert _switch() is True


def test_a_partner_cannot_switch_it_on_either(runner_org, partner):
    with pytest.raises(service.Forbidden, match="decides what partners see"):
        service.update("supply", SCOPE, {"acl_enforce_reads": True}, _caller(partner))
    assert _switch() is None


def test_a_partner_cannot_smuggle_it_in_beside_a_change_they_may_make(runner_member, partner):
    _turn_on(runner_member)
    with pytest.raises(service.Forbidden):
        service.update("supply", SCOPE, {"purchase_packs_per_child": 4, "acl_enforce_reads": False}, _caller(partner))
    data = ScopeConfig.objects.get(scope_type="program", scope_key=str(PROGRAM), namespace="supply").data
    assert data == {"acl_enforce_reads": True}


def test_a_partner_cannot_undo_their_way_to_the_same_place(runner_member, partner):
    """An undo is a write: putting back a layer without the switch is a change to the switch."""
    service.update("supply", SCOPE, {"purchase_packs_per_child": 1}, _caller(runner_member))
    _turn_on(runner_member)
    with pytest.raises(service.Forbidden):
        service.undo("supply", SCOPE, _caller(partner))
    assert _switch() is True


def test_a_partner_may_still_change_what_is_not_access_control(runner_org, partner):
    service.update("supply", SCOPE, {"purchase_packs_per_child": 2}, _caller(partner))
    assert ScopeConfig.objects.get(scope_type="program", namespace="supply").data == {"purchase_packs_per_child": 2}


def test_leaving_the_switch_as_it_is_is_not_a_change_to_it(runner_member, partner):
    _turn_on(runner_member)
    service.update("supply", SCOPE, {"acl_enforce_reads": True, "purchase_packs_per_child": 3}, _caller(partner))
    assert _switch() is True


def test_the_running_organisations_members_flip_it_both_ways(runner_member):
    _turn_on(runner_member)
    service.update("supply", SCOPE, {"acl_enforce_reads": False}, _caller(runner_member))
    assert _switch() is False


def test_platform_support_flips_it(runner_org, support):
    service.update("supply", SCOPE, {"acl_enforce_reads": True}, _caller(support))
    assert _switch() is True


def test_the_system_caller_is_unchanged(runner_org):
    service.update("supply", SCOPE, {"acl_enforce_reads": True}, SYSTEM)
    assert _switch() is True


def test_a_programme_nobody_runs_leaves_it_to_support(partner, support):
    undeclared = Scope.of("program", 10952)
    with pytest.raises(service.Forbidden, match="no running organisation"):
        service.update("supply", undeclared, {"acl_enforce_reads": True}, _caller(partner))
    service.update("supply", undeclared, {"acl_enforce_reads": True}, _caller(support))


def test_standing_that_cannot_be_established_is_refused(runner_org, runner_member):
    with patch("connect_labs.supply_chain.runner.standing", side_effect=RuntimeError("connect is down")):
        with pytest.raises(service.Forbidden, match="could not be established"):
            service.update("supply", SCOPE, {"acl_enforce_reads": True}, _caller(runner_member))


def test_the_switch_is_only_read_from_the_programme_so_it_is_only_written_there(support):
    org = Scope.of("organization", "some-org")
    with pytest.raises(service.Forbidden, match="program's own settings"):
        service.update("supply", org, {"acl_enforce_reads": True}, _caller(support))


# ---- MCP: labs_config_set / labs_config_undo -------------------------------------------------


@pytest.fixture
def over_mcp(monkeypatch):
    monkeypatch.setattr(tools, "require_connect_token", lambda user: "token")


def _mcp(name, user, **kwargs):
    return get_tool(name).handler(user, namespace="supply", scope_type="program", scope_key=str(PROGRAM), **kwargs)


def test_mcp_refuses_a_partner_and_names_who_may(over_mcp, runner_member, partner):
    _mcp("labs_config_set", runner_member, patch={"acl_enforce_reads": True}, expected_version=0)
    with pytest.raises(MCPToolError) as err:
        _mcp("labs_config_set", partner, patch={"acl_enforce_reads": False}, expected_version=1)
    assert err.value.code == "PERMISSION_DENIED" and "Runner Health" in str(err.value)
    assert _switch() is True


def test_mcp_undo_refuses_a_partner(over_mcp, runner_member, partner):
    _mcp("labs_config_set", runner_member, patch={"acl_enforce_reads": True}, expected_version=0)
    _mcp("labs_config_set", runner_member, patch={"acl_enforce_reads": None}, expected_version=1)
    with pytest.raises(MCPToolError) as err:
        _mcp("labs_config_undo", partner)  # would put the switch back on, but it is still not theirs to move
    assert err.value.code == "PERMISSION_DENIED"


def test_mcp_lets_the_running_organisation_through(over_mcp, runner_member):
    got = _mcp("labs_config_set", runner_member, patch={"acl_enforce_reads": True}, expected_version=0)
    assert got["data"]["acl_enforce_reads"] is True


# ---- the settings page -------------------------------------------------------------------------


def _save(client, user, data, version):
    client.force_login(user)
    return client.post(
        reverse("labs:settings", args=["program", PROGRAM]),
        {"namespace": "supply", "action": "save", "version": version, "data": json.dumps(data)},
        follow=True,
    )


def _messages(response):
    return [str(m) for m in response.context["messages"]] if response.context else []


def test_the_settings_page_refuses_a_partner_and_says_why(client, runner_member, partner):
    _turn_on(runner_member)
    response = _save(client, partner, {}, 1)
    assert any("Runner Health" in m for m in _messages(response))
    assert _switch() is True


def test_the_settings_page_undo_refuses_a_partner(client, runner_member, partner):
    _turn_on(runner_member)
    change = ScopeConfig.objects.get(namespace="supply").changes.first()
    client.force_login(partner)
    response = client.post(
        reverse("labs:settings", args=["program", PROGRAM]),
        {"namespace": "supply", "action": "undo", "version": 1, "change_id": change.pk},
        follow=True,
    )
    assert any("Runner Health" in m for m in _messages(response))
    assert _switch() is True


def test_the_settings_page_still_saves_for_the_running_organisation(client, runner_member):
    _save(client, runner_member, {"acl_enforce_reads": True}, 0)
    assert _switch() is True
