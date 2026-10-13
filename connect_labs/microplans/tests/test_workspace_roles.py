"""Workspace programs: roles derived from Connect, per-team visibility, no NM deletes.

A workspace (``labs.models.Workspace``) is a labs-only organisation linked to real
Connect programs. Its Program Managers are Dimagi staff and the organisation's
admins; its Network Managers are people whose Connect organisation delivers an
opportunity in a linked program, one team per delivering organisation. Every name
and id below is made up.
"""

from __future__ import annotations

import json
import time

import pytest
from django.core.management import call_command
from django.urls import reverse

from connect_labs.labs import workspaces
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.marketplace.models import OrgMembership
from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, WorkspaceDeleteForbidden
from connect_labs.microplans.management.commands.setup_autoconnect_workspace import ensure_workspace

pytestmark = pytest.mark.django_db

# Made-up real Connect programs, one per partner (as Connect sets them up).
RIVERBEND_PROGRAM = 9101
HILLTOP_PROGRAM = 9102
UNLINKED_PROGRAM = 9199

_HULLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[8.0, 9.0], [8.1, 9.0], [8.1, 9.1], [8.0, 9.1], [8.0, 9.0]]],
            },
            "properties": {"cluster": "C0", "building_count": 10, "expected_visit_count": 10},
        }
    ],
}
_EMPTY = {"type": "FeatureCollection", "features": []}


def _tree(*memberships, opportunities=()):
    """A Connect organisation tree: the orgs the person is in + the opps they see."""
    return {
        "organizations": [{"id": i + 1, "slug": slug, "name": name} for i, (slug, name) in enumerate(memberships)],
        "programs": [],
        "opportunities": [
            {"id": 500 + i, "name": f"Opp {i}", "organization": org, "program": program}
            for i, (org, program) in enumerate(opportunities)
        ],
    }


@pytest.fixture
def trees(monkeypatch):
    """username -> Connect tree; stands in for the session / cached Connect fetch."""
    found: dict[str, dict] = {}
    monkeypatch.setattr(workspaces, "connect_org_data", lambda user: found.get(getattr(user, "username", None)))
    return found


@pytest.fixture
def ws(db):
    org, opp, _ = ensure_workspace("Test Workspace Org", "Test Stage Program")
    workspace = org.workspace
    workspace.connect_program_ids = [RIVERBEND_PROGRAM, HILLTOP_PROGRAM]
    workspace.save()
    return {"org": org, "workspace": workspace, "program": opp.opportunity_id}


def _user(django_user_model, username, email=None):
    return django_user_model.objects.create(username=username, email=email or f"{username}@example.org")


@pytest.fixture
def people(django_user_model, trees, ws):
    riverbend = ("riverbend-health", "Riverbend Health")
    hilltop = ("hilltop-partners", "Hilltop Partners")
    users = {
        "nm_river": _user(django_user_model, "nm_river"),
        "nm_hill": _user(django_user_model, "nm_hill"),
        "stranger": _user(django_user_model, "stranger"),
        "manager_org": _user(django_user_model, "manager_org"),
        "pm": _user(django_user_model, "pm_staff", "pm.staff@dimagi.com"),
        "org_admin": _user(django_user_model, "org_admin"),
    }
    trees["nm_river"] = _tree(riverbend, opportunities=[(riverbend[0], RIVERBEND_PROGRAM)])
    trees["nm_hill"] = _tree(hilltop, opportunities=[(hilltop[0], HILLTOP_PROGRAM)])
    # In an organisation delivering only in a program the workspace is not linked to.
    trees["stranger"] = _tree(("elsewhere-org", "Elsewhere Org"), opportunities=[("elsewhere-org", UNLINKED_PROGRAM)])
    # Manages the Connect program, so SEES the partners' opps -- but delivers none.
    trees["manager_org"] = _tree(
        ("program-owner", "Program Owner"),
        opportunities=[(riverbend[0], RIVERBEND_PROGRAM), (hilltop[0], HILLTOP_PROGRAM)],
    )
    OrgMembership.objects.create(org=ws["org"], user=users["org_admin"], role="admin")
    return users


def _fresh(user):
    """The same person as a new request would load them (no per-request memo)."""
    return type(user).objects.get(pk=user.pk)


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()


@pytest.fixture
def plans(ws):
    """One plan per team, one with no team (made before roles), and a Riverbend study."""
    prog = ws["program"]

    def make(team, name):
        da = ProgramPlanDataAccess(prog, access_token="labs-local", team=team)
        return da.create_plan(region=name, name=name, mode="coverage", pins=_EMPTY, hulls=_HULLS).id

    ids = {
        "river": make("riverbend-health", "River ward"),
        "hill": make("hilltop-partners", "Hill ward"),
        "untagged": make("", "Old ward"),
    }
    da = ProgramPlanDataAccess(prog, access_token="labs-local", team="riverbend-health")
    ids["river_group"] = da.create_group(name="River study", plan_ids=[ids["river"]]).id
    da = ProgramPlanDataAccess(prog, access_token="labs-local", team="hilltop-partners")
    ids["hill_group"] = da.create_group(name="Hill study", plan_ids=[ids["hill"]]).id
    return ids


# --- role derivation ---------------------------------------------------------


def test_member_of_a_delivering_org_is_a_network_manager_on_that_team(people, ws):
    role = workspaces.role_in(_fresh(people["nm_river"]), ws["workspace"])
    assert role.role == workspaces.NETWORK_MANAGER
    assert role.teams == ("riverbend-health",)
    assert role.team_names == {"riverbend-health": "Riverbend Health"}


def test_delivering_through_two_orgs_puts_someone_on_both_teams(people, ws, trees):
    trees["nm_river"] = _tree(
        ("riverbend-health", "Riverbend Health"),
        ("hilltop-partners", "Hilltop Partners"),
        opportunities=[("riverbend-health", RIVERBEND_PROGRAM), ("hilltop-partners", HILLTOP_PROGRAM)],
    )
    role = workspaces.role_in(_fresh(people["nm_river"]), ws["workspace"])
    assert role.teams == ("riverbend-health", "hilltop-partners")


def test_unrelated_and_managing_orgs_get_no_role(people, ws):
    assert workspaces.role_in(_fresh(people["stranger"]), ws["workspace"]) is None
    # Seeing a partner's opportunity is not delivering it.
    assert workspaces.role_in(_fresh(people["manager_org"]), ws["workspace"]) is None


def test_dimagi_staff_and_org_admins_are_program_managers(people, ws):
    assert workspaces.role_in(_fresh(people["pm"]), ws["workspace"]).is_program_manager
    assert workspaces.role_in(_fresh(people["org_admin"]), ws["workspace"]).is_program_manager


def test_an_org_member_who_is_not_admin_gets_nothing_from_membership(people, ws):
    OrgMembership.objects.create(org=ws["org"], user=people["stranger"], role="member")
    assert workspaces.role_in(_fresh(people["stranger"]), ws["workspace"]) is None


def test_with_no_linked_programs_only_program_managers_have_a_role(people, ws):
    ws["workspace"].connect_program_ids = []
    ws["workspace"].save()
    assert workspaces.role_in(_fresh(people["nm_river"]), ws["workspace"]) is None
    assert workspaces.role_in(_fresh(people["pm"]), ws["workspace"]).is_program_manager


# --- access to the workspace program ----------------------------------------


def test_network_managers_get_access_without_domain_toggle_or_invite(people, ws):
    from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason

    nm = _fresh(people["nm_river"])
    assert not nm.view_synthetic_opps
    assert labs_only_scope_denied_reason(nm, program_id=ws["program"]) is None
    assert labs_only_scope_denied_reason(_fresh(people["stranger"]), program_id=ws["program"])
    assert labs_only_scope_denied_reason(_fresh(people["manager_org"]), program_id=ws["program"])


def test_the_workspace_program_appears_in_a_network_managers_picker(people, ws, rf):
    from connect_labs.labs.context import get_org_data

    def picker(user):
        request = rf.get("/")
        request.user = _fresh(user)
        request.session = {"labs_oauth": {"organization_data": {}}}
        return {p["id"]: p.get("name") for p in get_org_data(request).get("programs", [])}

    # By name, for Network and Program Managers alike, with the synthetic-data toggle off.
    assert picker(people["nm_river"]).get(ws["program"]) == "Test Stage Program"
    assert picker(people["pm"]).get(ws["program"]) == "Test Stage Program"
    assert ws["program"] not in picker(people["stranger"])


def test_a_refused_user_is_told_why_on_the_workspace_page(client, people, ws):
    _login(client, people["stranger"])
    resp = client.get(reverse("microplans:program_workspace", args=[ws["program"]]))
    assert resp.status_code == 404
    assert b"Your Connect organisation isn" in resp.content


# --- team visibility --------------------------------------------------------


def test_network_managers_list_only_their_teams_plans_and_groups(client, people, ws, plans):
    _login(client, people["nm_river"])
    body = client.get(reverse("microplans:program_plans", args=[ws["program"]])).json()
    assert [p["plan_id"] for p in body["plans"]] == [plans["river"]]
    assert [g["group_id"] for g in body["groups"]] == [plans["river_group"]]
    assert body["can_delete"] is False


def test_program_managers_see_everything_including_untagged_plans(client, people, ws, plans):
    _login(client, people["pm"])
    body = client.get(reverse("microplans:program_plans", args=[ws["program"]])).json()
    assert {p["plan_id"] for p in body["plans"]} == {plans["river"], plans["hill"], plans["untagged"]}
    assert {g["group_id"] for g in body["groups"]} == {plans["river_group"], plans["hill_group"]}
    assert body["can_delete"] is True


@pytest.mark.parametrize("key", ["hill", "untagged"])
def test_another_teams_or_an_untagged_plan_is_404_for_a_network_manager(client, people, ws, plans, key):
    _login(client, people["nm_river"])
    prog, plan = ws["program"], plans[key]
    assert client.get(reverse("microplans:program_plan", args=[prog, plan])).status_code == 404
    assert client.get(reverse("microplans:program_review", args=[prog, plan])).status_code == 404
    resp = client.post(
        reverse("microplans:program_plan_transition", args=[prog, plan]),
        data=json.dumps({"to": "archived"}),
        content_type="application/json",
    )
    assert resp.status_code == 404
    resp = client.post(
        reverse("microplans:program_plan_regroup", args=[prog, plan]), data="{}", content_type="application/json"
    )
    assert resp.status_code == 404
    assert client.post(reverse("microplans:program_plan_csv", args=[prog, plan])).status_code == 404
    assert client.get(reverse("microplans:program_group_page", args=[prog, plans["hill_group"]])).status_code == 404
    assert ProgramPlanDataAccess(prog, access_token="labs-local").get_plan(plan).status == "draft"


def test_a_network_managers_new_plan_and_study_belong_to_their_team(client, people, ws, plans):
    _login(client, people["nm_river"])
    prog = ws["program"]
    resp = client.post(
        reverse("microplans:program_create_plan", args=[prog]),
        data=json.dumps({"region": "New ward", "mode": "coverage", "hulls": _HULLS, "team": "hilltop-partners"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    resp = client.post(
        reverse("microplans:program_group_create", args=[prog]),
        data=json.dumps({"name": "New study", "kind": "study"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    da = ProgramPlanDataAccess(prog, access_token="labs-local")
    # Asking for another team is ignored: a Network Manager files under their own.
    assert {p.name: p.data.get("team") for p in da.list_plans()}["New ward"] == "riverbend-health"
    assert {g.name: g.data.get("team") for g in da.list_groups()}["New study"] == "riverbend-health"


def test_a_network_manager_cannot_bundle_another_teams_plan(client, people, ws, plans):
    _login(client, people["nm_river"])
    resp = client.post(
        reverse("microplans:program_group_create", args=[ws["program"]]),
        data=json.dumps({"name": "Grab", "plan_ids": [plans["river"], plans["hill"]]}),
        content_type="application/json",
    )
    assert resp.status_code == 404


def test_a_program_manager_chooses_the_team(client, people, ws):
    _login(client, people["pm"])
    resp = client.post(
        reverse("microplans:program_create_plan", args=[ws["program"]]) + "?team=hilltop-partners",
        data=json.dumps({"region": "PM ward", "mode": "coverage", "hulls": _HULLS}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    da = ProgramPlanDataAccess(ws["program"], access_token="labs-local")
    assert {p.name: p.data.get("team") for p in da.list_plans()}["PM ward"] == "hilltop-partners"


# --- no hard delete for Network Managers ------------------------------------


def test_network_managers_cannot_delete_but_can_archive(client, people, ws, plans):
    _login(client, people["nm_river"])
    prog, plan = ws["program"], plans["river"]
    assert client.post(reverse("microplans:program_plan_delete", args=[prog, plan])).status_code == 403
    assert client.delete(reverse("microplans:program_plan", args=[prog, plan])).status_code == 403
    assert (
        client.post(reverse("microplans:program_group_delete", args=[prog, plans["river_group"]])).status_code == 403
    )
    resp = client.post(
        reverse("microplans:program_plan_transition", args=[prog, plan]),
        data=json.dumps({"to": "archived"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    da = ProgramPlanDataAccess(prog, access_token="labs-local")
    assert da.get_plan(plan).status == "archived"
    assert da.get_group(plans["river_group"]) is not None


def test_a_refused_delete_is_on_the_audit_trail(people, ws, plans):
    from connect_labs.audit_trail.models import Action, AuditEvent

    da = ProgramPlanDataAccess(ws["program"], user=_fresh(people["nm_river"]), access_token="labs-local")
    with pytest.raises(WorkspaceDeleteForbidden):
        da.delete_plan(plans["river"])
    assert AuditEvent.objects.filter(
        action=Action.ACCESS_DENIED, program_id=ws["program"], resource_id=str(plans["river"])
    ).exists()


def test_program_managers_can_delete(client, people, ws, plans):
    _login(client, people["org_admin"])
    prog = ws["program"]
    assert client.post(reverse("microplans:program_plan_delete", args=[prog, plans["hill"]])).status_code == 200
    assert client.post(reverse("microplans:program_group_delete", args=[prog, plans["hill_group"]])).status_code == 200
    da = ProgramPlanDataAccess(prog, access_token="labs-local")
    assert da.get_plan(plans["hill"]) is None


def test_mcp_delete_is_refused_for_network_managers_and_allowed_for_program_managers(people, ws, plans):
    import connect_labs.mcp.tools.microplans  # noqa: F401 — trigger @register
    from connect_labs.mcp.tool_registry import MCPToolError, get_tool

    prog = ws["program"]
    with pytest.raises(MCPToolError) as exc:
        get_tool("microplans_delete_plan").handler(
            user=_fresh(people["nm_river"]), program_id=prog, plan_id=plans["river"]
        )
    assert exc.value.code == "PERMISSION_DENIED"
    with pytest.raises(MCPToolError) as exc:
        get_tool("microplans_delete_group").handler(
            user=_fresh(people["nm_river"]), program_id=prog, group_id=plans["river_group"]
        )
    assert exc.value.code == "PERMISSION_DENIED"
    out = get_tool("microplans_transition_plan").handler(
        user=_fresh(people["nm_river"]), program_id=prog, plan_id=plans["river"], to="archived"
    )
    assert out["plan_status"] == "archived"
    listed = get_tool("microplans_list_plans").handler(user=_fresh(people["nm_river"]), program_id=prog)
    assert [p["id"] for p in listed["plans"]] == [plans["river"]]
    out = get_tool("microplans_delete_plan").handler(user=_fresh(people["pm"]), program_id=prog, plan_id=plans["hill"])
    assert out["deleted"] is True


# --- nothing changes outside a workspace -----------------------------------


def test_a_labs_only_program_that_is_not_a_workspace_keeps_todays_rules(client, django_user_model, people):
    owner = _user(django_user_model, "owner_x")
    SyntheticOpportunity.objects.create(
        opportunity_id=10_777,
        label="Plain",
        program_name="Plain",
        gdrive_folder_id="f",
        labs_only=True,
        created_by=owner,
    )
    da = ProgramPlanDataAccess(10_777, user=owner, access_token="labs-local")
    assert da.workspace is None and da.can_delete
    plan = da.create_plan(region="X", name="X", mode="coverage", pins=_EMPTY, hulls=_HULLS)
    assert "team" not in plan.data
    _login(client, owner)
    assert client.post(reverse("microplans:program_plan_delete", args=[10_777, plan.id])).status_code == 200
    # Organisation admins there are not "program managers" of anything.
    assert workspaces.role_for_program(owner, 10_777) is None


def test_a_real_program_is_never_a_workspace(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert workspaces.workspace_for_program(133) is None


# --- setup command and Program Managers' page -------------------------------


def test_setup_command_is_idempotent_and_edits_the_linked_programs(django_user_model):
    from connect_labs.labs.models import Workspace

    admin = _user(django_user_model, "setup_admin")
    call_command("setup_autoconnect_workspace", "--connect-program-id", "9301", "--admin", "setup_admin")
    call_command("setup_autoconnect_workspace", "--connect-program-id", "9302", "--remove-connect-program-id", "9301")
    assert Workspace.objects.count() == 1
    workspace = Workspace.objects.get()
    assert workspace.org.name == "Auto-Connect"
    assert workspace.connect_program_ids == [9302]
    assert SyntheticOpportunity.objects.filter(labs_org=workspace.org, program_name="CLP Stage 4").count() == 1
    assert OrgMembership.objects.get(org=workspace.org, user=admin).role == "admin"


def _existing_program(opportunity_id, org_name="Existing Org", program_name="Existing Stage"):
    """A labs-only program made the way the MCP synthetic_create_labs_only tool makes one."""
    return SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id,
        label=program_name,
        org_name=org_name,
        program_name=program_name,
        gdrive_folder_id="",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )


def test_setup_command_adopts_an_existing_program_by_id_without_duplicating():
    from connect_labs.labs.models import Workspace

    opp = _existing_program(10_620)
    ProgramPlanDataAccess(10_620, access_token="labs-local").create_plan(
        region="Before", name="Before", mode="coverage", pins=_EMPTY, hulls=_HULLS
    )
    call_command("setup_autoconnect_workspace", "--program-id", "10620", "--connect-program-id", "9401")
    call_command("setup_autoconnect_workspace", "--program-id", "10620")
    opp.refresh_from_db()
    assert Workspace.objects.get().org == opp.labs_org
    assert Workspace.objects.get().connect_program_ids == [9401]
    assert SyntheticOpportunity.objects.count() == 1
    # A plan made before the program became a workspace has no team: Program Managers' only.
    assert "team" not in ProgramPlanDataAccess(10_620, access_token="labs-local").list_plans()[0].data


def test_setup_command_adopts_an_existing_program_by_name():
    from connect_labs.labs.models import Workspace

    opp = _existing_program(10_630, org_name="Auto-Connect", program_name="CLP Stage 4")
    call_command("setup_autoconnect_workspace")
    call_command("setup_autoconnect_workspace")
    opp.refresh_from_db()
    assert Workspace.objects.get().org == opp.labs_org
    assert SyntheticOpportunity.objects.count() == 1


def test_setup_command_refuses_an_org_holding_other_programs(django_user_model):
    from django.core.management.base import CommandError

    from connect_labs.labs.models import Workspace

    first = _existing_program(10_640)
    SyntheticOpportunity.objects.create(
        opportunity_id=10_641,
        label="Another",
        org_name="Existing Org",
        program_name="Another program",
        gdrive_folder_id="",
        labs_only=True,
        labs_org=first.labs_org,
    )
    with pytest.raises(CommandError):
        call_command("setup_autoconnect_workspace", "--program-id", "10640")
    assert not Workspace.objects.exists()


def test_settings_page_is_program_managers_only_and_sets_linked_programs(client, people, ws):
    url = reverse("microplans:program_workspace_settings", args=[ws["program"]])
    _login(client, people["nm_river"])
    assert client.get(url).status_code == 404
    _login(client, people["pm"])
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"9101, 9102" in resp.content  # the linked programs, ready to edit
    resp = client.post(url, {"connect_program_ids": "9102, 9103"})
    assert resp.status_code == 302
    ws["workspace"].refresh_from_db()
    assert ws["workspace"].connect_program_ids == [9102, 9103]
