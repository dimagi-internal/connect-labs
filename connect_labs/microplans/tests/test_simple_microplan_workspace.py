"""Simple Microplan in a workspace program: per-team visibility, PM-only delete,
and no leak into the global list.

A workspace program's Simple Microplans carry ``program_id`` + ``team`` and follow
the workspace's roles (``labs.workspaces``): a Program Manager sees and may do
everything; a Network Manager sees and changes only their teams' plans and may
not delete. A workspace program offers Simple Microplan only (the CHC pages and
WA Revisit send people there); every other program is unchanged. Dimagi testers
see a workspace as a Network Manager of a team. Every name, slug, email and id
below is made up.
"""

from __future__ import annotations

import json
import time

import pytest
from django.urls import reverse

from connect_labs.labs import workspaces
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.microplans.management.commands.setup_autoconnect_workspace import ensure_workspace
from connect_labs.microplans.models import SimpleMicroplan

pytestmark = pytest.mark.django_db

RIVERBEND_PROGRAM = 9101
HILLTOP_PROGRAM = 9102
RIVER = ("riverbend-health", "Riverbend Health")
HILL = ("hilltop-partners", "Hilltop Partners")


def _tree(*memberships, opportunities=()):
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
def prog(db):
    org, opp, _ = ensure_workspace("Test Workspace Org", "Test Stage Program")
    org.workspace.connect_program_ids = [RIVERBEND_PROGRAM, HILLTOP_PROGRAM]
    org.workspace.save()
    return opp.opportunity_id


def _user(django_user_model, username, email=None):
    return django_user_model.objects.create(
        username=username, email=email or f"{username}@example.org", name=username.replace("_", " ").title()
    )


@pytest.fixture
def people(django_user_model, trees, prog):
    users = {
        "nm_river": _user(django_user_model, "nm_river"),
        "nm_river_2": _user(django_user_model, "nm_river_2"),
        "nm_hill": _user(django_user_model, "nm_hill"),
        "stranger": _user(django_user_model, "stranger"),
        "pm": _user(django_user_model, "pm_staff", "pm.staff@dimagi.com"),
    }
    trees["nm_river"] = _tree(RIVER, opportunities=[(RIVER[0], RIVERBEND_PROGRAM)])
    trees["nm_river_2"] = _tree(RIVER, opportunities=[(RIVER[0], RIVERBEND_PROGRAM)])
    trees["nm_hill"] = _tree(HILL, opportunities=[(HILL[0], HILLTOP_PROGRAM)])
    trees["stranger"] = _tree(("elsewhere-org", "Elsewhere Org"), opportunities=[("elsewhere-org", 9199)])
    # The Program Manager's own Connect tree shows both partners delivering.
    trees["pm_staff"] = _tree(
        ("program-owner", "Program Owner"), opportunities=[(RIVER[0], RIVERBEND_PROGRAM), (HILL[0], HILLTOP_PROGRAM)]
    )
    return users


@pytest.fixture
def plans(prog, people):
    def make(name, team, by="nm_river"):
        return SimpleMicroplan.objects.create(
            name=name, program_id=prog, team=team, created_by=people[by], created_by_name=by
        )

    return {
        "river": make("River grid", RIVER[0]),
        "hill": make("Hill grid", HILL[0], by="nm_hill"),
        "pm_only": make("PM only grid", "", by="pm"),
        "global": SimpleMicroplan.objects.create(name="Global test grid", created_by=people["pm"]),
    }


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()


def _url(name, prog, plan=None):
    kwargs = {"program_id": prog}
    if plan is not None:
        kwargs["plan_id"] = plan.pk
    return reverse(f"microplans:program_simple_{name}", kwargs=kwargs)


def _post_api(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


# --- the global list never shows workspace plans ----------------------------


def test_global_list_and_routes_exclude_workspace_plans(client, people, plans):
    _login(client, people["pm"])
    body = client.get(reverse("microplans:simple_list")).content.decode()
    assert "Global test grid" in body
    for key in ("river", "hill", "pm_only"):
        assert plans[key].name not in body
    # Nor can a workspace plan be opened, changed or deleted through the global routes.
    river = plans["river"]
    assert client.get(reverse("microplans:simple_editor", kwargs={"plan_id": river.pk})).status_code == 404
    api = reverse("microplans:simple_api", kwargs={"plan_id": river.pk})
    assert _post_api(client, api, {"name": "leaked"}).status_code == 404
    assert client.post(reverse("microplans:simple_delete", kwargs={"plan_id": river.pk})).status_code == 404
    river.refresh_from_db()
    assert river.name == "River grid"


def test_global_plans_are_not_in_the_workspace_list(client, people, plans, prog):
    _login(client, people["pm"])
    body = client.get(_url("list", prog)).content.decode()
    assert "Global test grid" not in body
    assert client.get(_url("editor", prog, plans["global"])).status_code == 404


# --- Network Managers: their teams' plans only --------------------------------


def test_network_manager_lists_only_their_teams_plans(client, people, plans, prog):
    _login(client, people["nm_river"])
    resp = client.get(_url("list", prog))
    assert resp.status_code == 200
    assert [p.pk for p in resp.context["plans"]] == [plans["river"].pk]
    body = resp.content.decode()
    assert "Hill grid" not in body and "PM only grid" not in body
    # No delete control, even on their own team's plan.
    assert _url("delete", prog, plans["river"]) not in body


@pytest.mark.parametrize("key", ["hill", "pm_only"])
def test_another_teams_plan_is_404_for_a_network_manager(client, people, plans, prog, key):
    _login(client, people["nm_river"])
    plan = plans[key]
    assert client.get(_url("editor", prog, plan)).status_code == 404
    assert _post_api(client, _url("api", prog, plan), {"name": "hijacked"}).status_code == 404
    assert _post_api(client, _url("api", prog, plan), {"data": {"rows": []}}).status_code == 404
    assert client.post(_url("delete", prog, plan)).status_code == 404
    plan.refresh_from_db()
    assert plan.name in ("Hill grid", "PM only grid")


def test_a_network_manager_edits_and_renames_their_teams_plan_with_teammates(client, people, plans, prog):
    river = plans["river"]
    _login(client, people["nm_river_2"])  # a teammate, not the creator
    resp = client.get(_url("editor", prog, river))
    assert resp.status_code == 200
    state = resp.context["plan_state"]
    assert state["editable"] is True
    assert state["api_url"] == _url("api", prog, river)
    assert state["list_url"] == _url("list", prog)
    assert (
        _post_api(client, state["api_url"], {"name": "River grid v2", "data": {"rows": [{"a": 1}]}}).status_code == 200
    )
    river.refresh_from_db()
    assert river.name == "River grid v2"
    assert river.n_work_areas == 1


def test_a_network_manager_cannot_delete_their_teams_plan(client, people, plans, prog):
    from connect_labs.audit_trail.models import Action, AuditEvent

    _login(client, people["nm_river"])
    river = plans["river"]
    assert client.post(_url("delete", prog, river)).status_code == 403
    assert SimpleMicroplan.objects.filter(pk=river.pk).exists()
    assert AuditEvent.objects.filter(
        action=Action.ACCESS_DENIED, resource_type="simple_microplan", resource_id=str(river.pk), program_id=prog
    ).exists()


# --- Program Managers: everything ----------------------------------------------


def test_program_manager_sees_edits_and_deletes_every_plan(client, people, plans, prog):
    _login(client, people["pm"])
    resp = client.get(_url("list", prog))
    assert {p.pk for p in resp.context["plans"]} == {plans[k].pk for k in ("river", "hill", "pm_only")}
    body = resp.content.decode()
    assert _url("delete", prog, plans["hill"]) in body
    assert RIVER[0] in body and HILL[0] in body  # the team column
    assert reverse("microplans:program_workspace_settings", args=[prog]) in body
    assert client.get(_url("editor", prog, plans["hill"])).context["plan_state"]["editable"] is True
    assert _post_api(client, _url("api", prog, plans["hill"]), {"name": "Tidied"}).status_code == 200
    resp = client.post(_url("delete", prog, plans["hill"]))
    assert resp.status_code == 302
    assert resp["Location"] == _url("list", prog)
    assert not SimpleMicroplan.objects.filter(pk=plans["hill"].pk).exists()


# --- people with no role, and programs that are not workspaces -----------------


def test_someone_with_no_role_is_refused_everywhere(client, people, plans, prog):
    _login(client, people["stranger"])
    river = plans["river"]
    resp = client.get(_url("list", prog))
    assert resp.status_code == 404
    assert b"Your Connect organisation isn" in resp.content
    assert client.post(_url("list", prog), {"name": "Sneaky"}).status_code == 404
    assert client.get(_url("editor", prog, river)).status_code == 404
    assert _post_api(client, _url("api", prog, river), {"name": "hijacked"}).status_code == 404
    assert client.post(_url("delete", prog, river)).status_code == 404
    assert SimpleMicroplan.objects.filter(program_id=prog).count() == 3
    river.refresh_from_db()
    assert river.name == "River grid"


def test_a_program_that_is_not_a_workspace_has_no_simple_microplans(client, django_user_model):
    staff = _user(django_user_model, "staff_plain", "staff.plain@dimagi.com")
    SyntheticOpportunity.objects.create(
        opportunity_id=10_778,
        label="Plain",
        program_name="Plain",
        gdrive_folder_id="f",
        labs_only=True,
        created_by=staff,
    )
    _login(client, staff)
    assert client.get(_url("list", 10_778)).status_code == 404
    assert client.get(_url("list", 133)).status_code == 404  # a real Connect program


# --- creating ------------------------------------------------------------------


def test_a_network_managers_new_plan_is_filed_under_their_team(client, people, prog):
    _login(client, people["nm_river"])
    # Asking for another team is ignored: a Network Manager files under their own.
    resp = client.post(_url("list", prog), {"name": "New river grid", "team": HILL[0]})
    plan = SimpleMicroplan.objects.get(name="New river grid")
    assert resp.status_code == 302
    assert resp["Location"] == _url("editor", prog, plan)
    assert (plan.program_id, plan.team, plan.created_by) == (prog, RIVER[0], people["nm_river"])


def test_a_network_manager_on_two_teams_picks_one(client, people, trees, prog):
    trees["nm_river"] = _tree(RIVER, HILL, opportunities=[(RIVER[0], RIVERBEND_PROGRAM), (HILL[0], HILLTOP_PROGRAM)])
    _login(client, people["nm_river"])
    body = client.get(_url("list", prog)).content.decode()
    assert 'name="team"' in body and "Hilltop Partners" in body
    client.post(_url("list", prog), {"name": "Hill pick", "team": HILL[0]})
    client.post(_url("list", prog), {"name": "Default pick"})
    assert SimpleMicroplan.objects.get(name="Hill pick").team == HILL[0]
    assert SimpleMicroplan.objects.get(name="Default pick").team == RIVER[0]


def test_a_program_manager_files_under_a_team_or_pm_only(client, people, prog):
    _login(client, people["pm"])
    client.post(_url("list", prog), {"name": "For hilltop", "team": HILL[0]})
    client.post(_url("list", prog), {"name": "Just PMs", "team": ""})
    assert SimpleMicroplan.objects.get(name="For hilltop").team == HILL[0]
    assert SimpleMicroplan.objects.get(name="Just PMs").team == ""
    assert set(
        SimpleMicroplan.objects.filter(name__in=["For hilltop", "Just PMs"]).values_list("program_id", flat=True)
    ) == {prog}


def test_the_global_create_still_makes_a_plan_with_no_program(client, people):
    _login(client, people["nm_river"])
    client.post(reverse("microplans:simple_list"), {"name": "Plain test", "team": RIVER[0]})
    plan = SimpleMicroplan.objects.get(name="Plain test")
    assert plan.program_id is None and plan.team == ""


# --- a workspace program offers Simple Microplan only ---------------------------


@pytest.mark.parametrize("name", ["program_workspace", "program_create_plan_page", "program_bulk_create_page"])
def test_chc_pages_send_everyone_in_a_workspace_to_simple_microplan(client, people, prog, name):
    for who in ("nm_river", "pm"):  # Program Managers too
        _login(client, people[who])
        resp = client.get(reverse(f"microplans:{name}", args=[prog]))
        assert resp.status_code == 302, who
        assert resp["Location"] == _url("list", prog)


def test_chc_pages_still_refuse_someone_with_no_role_first(client, people, prog):
    _login(client, people["stranger"])
    resp = client.get(reverse("microplans:program_workspace", args=[prog]))
    assert resp.status_code == 404
    assert b"Your Connect organisation isn" in resp.content


def test_wa_revisit_sends_a_workspace_program_to_simple_microplan(client, people, prog):
    _login(client, people["pm"])
    for name in ("program_home", "setup"):
        resp = client.get(reverse(f"mopup:{name}", args=[prog]))
        assert resp.status_code == 302 and resp["Location"] == _url("list", prog)
    resp = client.post(reverse("mopup:create_run", args=[prog]), data="{}", content_type="application/json")
    assert resp.status_code == 404


def test_every_wa_revisit_route_is_closed_to_workspace_programs():
    from django.urls import URLPattern

    from connect_labs.mopup import urls as mopup_urls

    routes = [p for p in mopup_urls.urlpatterns if isinstance(p, URLPattern)]
    assert routes
    assert [p.name for p in routes if not getattr(p.callback, "simple_microplan_only_in_workspace", False)] == []


@pytest.fixture
def labs_context_middleware(settings):
    """The context-picker middleware (local and deployed settings run it; test settings do not)."""
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "connect_labs.labs.context.LabsContextMiddleware"]


def _pick_program(client, program_id):
    session = client.session
    session["labs_context"] = {"program_id": program_id}
    session.save()


def test_hub_offers_only_the_workspace_simple_microplan_when_its_program_is_picked(
    client, people, prog, labs_context_middleware
):
    _login(client, people["nm_river"])
    _pick_program(client, prog)
    resp = client.get(reverse("microplans:hub"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'id="workspace-simple-link"' in body and _url("list", prog) in body
    assert "CHC Microplan" not in body and "CHC WA Revisit" not in body
    assert f'href="{reverse("microplans:simple_list")}"' not in body


# --- every other program, and the global list, behave as before ----------------


def test_hub_for_another_program_is_unchanged(client, people, labs_context_middleware):
    _login(client, people["pm"])
    _pick_program(client, 217)
    body = client.get(reverse("microplans:hub")).content.decode()
    assert "/microplans/program/217/" in body and "/wa-revisit/program/217/" in body
    assert f'href="{reverse("microplans:simple_list")}"' in body
    assert 'id="workspace-simple-link"' not in body


def test_chc_and_wa_revisit_pages_are_unchanged_for_other_programs(client, people, monkeypatch):
    import connect_labs.mopup.views as mopup_views

    class FakeProgramDA:  # no Connect call for the portfolio's plan list
        workspace = viewer = None
        can_delete = True

        def __init__(self, program_id, *a, **k):
            self.program_id = int(program_id)

        def list_plans(self):
            return []

    class FakeRunsDA:
        def __init__(self, *a, **k):
            pass

        def list_runs(self):
            return []

    monkeypatch.setattr("connect_labs.microplans.core.data_access.ProgramPlanDataAccess", FakeProgramDA)
    monkeypatch.setattr(mopup_views, "MopupRunDataAccess", FakeRunsDA)
    monkeypatch.setattr(mopup_views, "_program_opportunities", lambda request, program_id: [])
    _login(client, people["pm"])
    resp = client.get(reverse("microplans:program_workspace", args=[217]))
    assert resp.status_code == 200 and b"Microplan portfolio" in resp.content
    assert client.get(reverse("microplans:program_create_plan_page", args=[217])).status_code == 200
    assert client.get(reverse("microplans:program_bulk_create_page", args=[217])).status_code == 200
    assert client.get(reverse("mopup:program_home", args=[217])).status_code == 200


def test_the_global_list_is_unchanged_for_everyone(client, people, plans):
    _login(client, people["nm_river"])  # a Network Manager elsewhere is just a user here
    resp = client.get(reverse("microplans:simple_list"))
    assert resp.status_code == 200
    assert [p.pk for p in resp.context["plans"]] == [plans["global"].pk]
    assert "workspace" not in resp.context
    # someone else's global plan: read-only, no delete, as before
    assert reverse("microplans:simple_delete", kwargs={"plan_id": plans["global"].pk}) not in resp.content.decode()
    assert (
        client.get(reverse("microplans:simple_editor", kwargs={"plan_id": plans["global"].pk})).context["plan_state"][
            "editable"
        ]
        is False
    )


# --- testers: Dimagi people trying the Network Manager view --------------------


@pytest.fixture
def tester(django_user_model):
    return _user(django_user_model, "dimagi_tester", "tester.one@dimagi.com")


def _settings_url(prog):
    return reverse("microplans:program_workspace_settings", args=[prog])


def _make_tester(prog, user, team):
    from connect_labs.labs.models import WorkspaceTester

    return WorkspaceTester.objects.create(workspace=workspaces.workspace_for_program(prog), user=user, team=team)


def test_a_tester_sees_exactly_what_a_network_manager_of_their_team_would(client, people, plans, prog, tester):
    from connect_labs.audit_trail.models import Action, AuditEvent

    _login(client, people["pm"])
    resp = client.post(_settings_url(prog), {"action": "add_tester", "who": "Tester.One@dimagi.com", "team": RIVER[0]})
    assert resp.status_code == 302
    assert AuditEvent.objects.filter(action=Action.CREATE, resource_type="workspace_tester", program_id=prog).exists()

    _login(client, tester)  # Dimagi staff: a Program Manager, but testing wins
    resp = client.get(_url("list", prog))
    assert [p.pk for p in resp.context["plans"]] == [plans["river"].pk]
    body = resp.content.decode()
    assert 'id="testing-banner"' in body and f"Testing as Network Manager of {RIVER[0]}" in body
    assert 'id="workspace-settings-link"' not in body
    assert client.get(_url("editor", prog, plans["hill"])).status_code == 404
    assert client.post(_url("delete", prog, plans["river"])).status_code == 403
    assert client.get(_settings_url(prog)).status_code == 404  # Program Managers' page
    client.post(_url("list", prog), {"name": "Tester grid", "team": ""})
    assert SimpleMicroplan.objects.get(name="Tester grid").team == RIVER[0]


def test_a_tester_on_a_made_up_team_starts_empty_and_their_plans_reach_program_managers(
    client, people, plans, prog, tester
):
    _make_tester(prog, tester, "test-llo-a")
    _login(client, tester)
    assert client.get(_url("list", prog)).context["plans"] == []
    client.post(_url("list", prog), {"name": "LLO A grid"})
    _login(client, people["pm"])
    names = {p.name: p.team for p in client.get(_url("list", prog)).context["plans"]}
    assert names["LLO A grid"] == "test-llo-a"


def test_removing_a_tester_restores_their_usual_role(client, people, plans, prog, tester):
    from connect_labs.audit_trail.models import Action, AuditEvent
    from connect_labs.labs.models import WorkspaceTester

    _make_tester(prog, tester, RIVER[0])
    _login(client, people["pm"])
    assert b"tester.one@dimagi.com" in client.get(_settings_url(prog)).content
    client.post(_settings_url(prog), {"action": "remove_tester", "user_id": tester.pk})
    assert not WorkspaceTester.objects.exists()
    assert AuditEvent.objects.filter(action=Action.DELETE, resource_type="workspace_tester", program_id=prog).exists()
    _login(client, tester)
    assert len(client.get(_url("list", prog)).context["plans"]) == 3
    assert client.get(_settings_url(prog)).status_code == 200


def test_a_tester_can_stop_testing_themselves(client, plans, prog, tester):
    from connect_labs.labs.models import WorkspaceTester

    _make_tester(prog, tester, RIVER[0])
    _login(client, tester)
    resp = client.post(reverse("microplans:program_workspace_stop_testing", args=[prog]))
    assert resp.status_code == 302 and resp["Location"] == _url("list", prog)
    assert not WorkspaceTester.objects.exists()
    assert client.get(_settings_url(prog)).status_code == 200


def test_only_dimagi_people_can_be_testers(client, people, prog, django_user_model):
    from connect_labs.labs.models import WorkspaceTester

    _login(client, people["pm"])
    for who, team in [
        (people["nm_hill"].email, RIVER[0]),  # not a Dimagi sign-in
        ("nobody.yet@dimagi.com", RIVER[0]),  # never signed in to labs
        ("pm.staff@dimagi.com", "  "),  # no team
    ]:
        client.post(_settings_url(prog), {"action": "add_tester", "who": who, "team": team})
    assert not WorkspaceTester.objects.exists()
    # A row for anyone else is ignored rather than trusted.
    _make_tester(prog, people["nm_hill"], RIVER[0])
    nm = type(people["nm_hill"]).objects.get(pk=people["nm_hill"].pk)
    role = workspaces.role_for_program(nm, prog)
    assert role.teams == (HILL[0],) and not role.testing


def test_network_managers_cannot_add_testers(client, people, prog, tester):
    from connect_labs.labs.models import WorkspaceTester

    _login(client, people["nm_river"])
    resp = client.post(_settings_url(prog), {"action": "add_tester", "who": tester.email, "team": RIVER[0]})
    assert resp.status_code == 404
    assert not WorkspaceTester.objects.exists()
