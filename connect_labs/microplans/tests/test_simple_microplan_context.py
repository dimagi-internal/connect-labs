"""Simple Microplan uses the labs context: a new plan records the picked
organisation / program / opportunity, the global list shows the plans of what is
picked now (most specific wins; nothing picked = your own), and who may open,
change or delete a plan follows the same Connect tree the context picker offers.

Every name, slug, email and id below is made up.
"""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.urls import reverse

from connect_labs.microplans.models import SimpleMicroplan

pytestmark = pytest.mark.django_db

ORG_A = ("riverbend-health", "Riverbend Health")
ORG_B = ("hilltop-partners", "Hilltop Partners")
OWNER_ORG = ("program-owner", "Program Owner")
PROGRAM = 9301
OTHER_PROGRAM = 9302


def _org(slug_name, i=1):
    return {"id": i, "slug": slug_name[0], "name": slug_name[1]}


def _opp(oid, org, program):
    return {"id": oid, "name": f"Opp {oid}", "organization": org[0], "program": program}


# Connect's tree (`/export/opp_org_program_list/`): a partner sees its own orgs and
# opportunities and no programs; the program owner also sees every opportunity in
# its programs. Two of anything, so the middleware never auto-selects.
TREES = {
    "nm_a": {
        "organizations": [_org(ORG_A)],
        "programs": [],
        "opportunities": [_opp(701, ORG_A, PROGRAM), _opp(702, ORG_A, PROGRAM)],
    },
    "nm_a2": {
        "organizations": [_org(ORG_A)],
        "programs": [],
        "opportunities": [_opp(701, ORG_A, PROGRAM), _opp(702, ORG_A, PROGRAM)],
    },
    "nm_b": {
        "organizations": [_org(ORG_B, 2)],
        "programs": [],
        "opportunities": [_opp(703, ORG_B, PROGRAM), _opp(704, ORG_B, OTHER_PROGRAM)],
    },
    "pm": {
        "organizations": [_org(OWNER_ORG, 3)],
        "programs": [{"id": PROGRAM, "name": "Riverside Program", "organization": OWNER_ORG[0]}],
        "opportunities": [_opp(701, ORG_A, PROGRAM), _opp(702, ORG_A, PROGRAM), _opp(703, ORG_B, PROGRAM)],
    },
    "staffer": {},
}


@pytest.fixture(autouse=True)
def context_middleware(settings):
    """The labs context middleware (on in labs_aws/local, not in the test settings)."""
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "connect_labs.labs.context.LabsContextMiddleware"]


@pytest.fixture
def people(django_user_model):
    def make(username, email=None):
        return django_user_model.objects.create(
            username=username, email=email or f"{username}@example.org", name=username.replace("_", " ").title()
        )

    return {
        "nm_a": make("nm_a"),
        "nm_a2": make("nm_a2"),
        "nm_b": make("nm_b"),
        "pm": make("pm"),
        "staffer": make("staffer", "staffer@dimagi.com"),
    }


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"organization_data": TREES[user.username]}
    session.save()


def _plan(name, by, **context):
    return SimpleMicroplan.objects.create(name=name, created_by=by, created_by_name=by.name, **context)


@pytest.fixture
def plans(people):
    return {
        "opp701": _plan(
            "Opp 701 grid",
            people["nm_a"],
            organization_slug=ORG_A[0],
            organization_name=ORG_A[1],
            opportunity_id=701,
            opportunity_name="Opp 701",
        ),
        "opp703": _plan("Opp 703 grid", people["nm_b"], organization_slug=ORG_B[0], opportunity_id=703),
        "program": _plan("Program grid", people["pm"], context_program_id=PROGRAM, program_name="Riverside Program"),
        "org_a": _plan("Org A grid", people["nm_a2"], organization_slug=ORG_A[0], organization_name=ORG_A[1]),
        # Org A picked with ORG B's opportunity: the opportunity is what counts.
        "a_with_703": _plan("Mixed grid", people["nm_b"], organization_slug=ORG_A[0], opportunity_id=703),
        "legacy": _plan("AK Test", people["nm_a"]),
    }


def _list(client, **params):
    resp = client.get(reverse("microplans:simple_list"), params)
    assert resp.status_code == 200, resp
    return {p.name for p in resp.context["plans"]}


def _editor(plan):
    return reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk})


def _api(client, plan, body):
    return client.post(
        reverse("microplans:simple_api", kwargs={"plan_id": plan.pk}),
        data=json.dumps(body),
        content_type="application/json",
    )


def _delete(client, plan):
    return client.post(reverse("microplans:simple_delete", kwargs={"plan_id": plan.pk}))


# --- the context picker is on --------------------------------------------------


def test_the_context_picker_uses_all_three_filters_on_simple_microplan_only():
    source = (settings.BASE_DIR / "connect_labs/static/labs/context_picker.js").read_text(encoding="utf-8")
    assert "'/microplans/simple/': ['orgs', 'programs', 'opps']" in source
    # A workspace program's own pages (/microplans/program/<id>/simple/) do not match.
    assert "'/microplans/program" not in source


def test_a_bare_list_url_picks_up_the_sessions_context(client, people):
    _login(client, people["nm_a"])
    client.get(reverse("microplans:simple_list"), {"opportunity_id": 701})
    resp = client.get(reverse("microplans:simple_list"))
    assert resp.status_code == 302
    assert "opportunity_id=701" in resp["Location"]


# --- create records the picked context -----------------------------------------


def test_create_records_the_picked_org_program_and_opportunity(client, people):
    _login(client, people["pm"])
    url = (
        reverse("microplans:simple_list") + f"?organization_id={OWNER_ORG[0]}&program_id={PROGRAM}&opportunity_id=703"
    )
    resp = client.post(url, {"name": "Picked grid"})
    assert resp.status_code == 302
    plan = SimpleMicroplan.objects.get(name="Picked grid")
    assert (plan.organization_slug, plan.organization_name) == OWNER_ORG
    assert (plan.context_program_id, plan.program_name) == (PROGRAM, "Riverside Program")
    assert (plan.opportunity_id, plan.opportunity_name) == (703, "Opp 703")
    assert plan.program_id is None and plan.team == ""  # not a workspace plan
    assert plan.context_names == ["Program Owner", "Riverside Program", "Opp 703"]


def test_create_with_only_an_opportunity_picked(client, people):
    _login(client, people["nm_a"])
    client.post(reverse("microplans:simple_list") + "?opportunity_id=702", {"name": "Opp only"})
    plan = SimpleMicroplan.objects.get(name="Opp only")
    assert plan.opportunity_id == 702
    assert plan.organization_slug == "" and plan.context_program_id is None


def test_create_with_nothing_picked_records_no_context(client, people):
    _login(client, people["nm_a"])
    client.post(reverse("microplans:simple_list"), {"name": "Bare grid"})
    plan = SimpleMicroplan.objects.get(name="Bare grid")
    assert plan.organization_slug == "" and plan.context_program_id is None and plan.opportunity_id is None
    assert plan.context_names == []


def test_create_never_records_a_context_the_person_cannot_pick(client, people):
    # The middleware passes an unknown opportunity id through; the plan must not take it.
    _login(client, people["nm_a"])
    client.post(reverse("microplans:simple_list") + "?opportunity_id=703", {"name": "Sneaky grid"})
    plan = SimpleMicroplan.objects.get(name="Sneaky grid")
    assert plan.opportunity_id is None


def test_dimagi_staff_may_file_a_plan_under_any_context(client, people):
    _login(client, people["staffer"])
    client.post(reverse("microplans:simple_list") + "?opportunity_id=703", {"name": "Staff grid"})
    plan = SimpleMicroplan.objects.get(name="Staff grid")
    assert plan.opportunity_id == 703
    assert plan.context_names == ["Opportunity #703"]


# --- the list follows the picked context -----------------------------------------


def test_list_by_opportunity(client, people, plans):
    _login(client, people["pm"])
    assert _list(client, opportunity_id=701) == {"Opp 701 grid"}
    assert _list(client, opportunity_id=703) == {"Opp 703 grid", "Mixed grid"}


def test_list_by_program(client, people, plans):
    _login(client, people["pm"])
    assert _list(client, program_id=PROGRAM) == {"Program grid"}


def test_list_by_organization(client, people, plans):
    _login(client, people["nm_a"])
    # Org A's plans this person can open: not "Mixed grid", whose opportunity is org B's.
    assert _list(client, organization_id=ORG_A[0]) == {"Opp 701 grid", "Org A grid"}


def test_list_with_nothing_picked_is_your_own_plans(client, people, plans):
    _login(client, people["nm_a"])
    assert _list(client) == {"Opp 701 grid", "AK Test"}
    _login(client, people["staffer"])
    assert _list(client) == set()


def test_the_list_never_shows_a_plan_the_person_cannot_open(client, people, plans):
    # Opportunity 703 is not in nm_a's tree: the URL is passed through, the plans are not.
    _login(client, people["nm_a"])
    assert _list(client, opportunity_id=703) == set()


def test_the_list_shows_each_plans_context(client, people, plans):
    _login(client, people["nm_a"])
    body = client.get(reverse("microplans:simple_list"), {"organization_id": ORG_A[0]}).content.decode()
    assert "Context" in body
    assert "Riverbend Health" in body and "Opp 701" in body
    assert 'id="mp-picked-context"' in body and "organization Riverbend Health" in body


def test_workspace_plans_stay_out_of_the_global_list(client, people, plans):
    # A workspace plan files under ``program_id``; picking that program shows only
    # plans whose CONTEXT is the program.
    SimpleMicroplan.objects.create(name="Workspace grid", program_id=PROGRAM, team="", created_by=people["pm"])
    _login(client, people["staffer"])
    assert _list(client, program_id=PROGRAM) == {"Program grid"}


# --- who may open, change and delete ---------------------------------------------


def test_dimagi_staff_open_change_and_delete_any_plan(client, people, plans):
    _login(client, people["staffer"])
    for plan in plans.values():
        resp = client.get(_editor(plan))
        assert resp.status_code == 200
        assert resp.context["plan_state"]["editable"] is True
    assert _api(client, plans["opp703"], {"name": "Tidied"}).status_code == 200
    assert _delete(client, plans["legacy"]).status_code == 302
    assert not SimpleMicroplan.objects.filter(pk=plans["legacy"].pk).exists()


def test_a_member_with_access_opens_and_changes_but_does_not_delete(client, people, plans):
    plan = plans["org_a"]  # made by nm_a2; nm_a is in the same organisation
    _login(client, people["nm_a"])
    resp = client.get(_editor(plan))
    assert resp.status_code == 200
    assert resp.context["plan_state"]["editable"] is True
    assert "Context: Riverbend Health" in resp.content.decode()
    assert _api(client, plan, {"name": "Org A grid v2", "data": {"rows": [{"a": 1}]}}).status_code == 200
    plan.refresh_from_db()
    assert plan.name == "Org A grid v2" and plan.n_work_areas == 1
    assert _delete(client, plan).status_code == 403
    assert SimpleMicroplan.objects.filter(pk=plan.pk).exists()


def test_the_program_owner_reaches_its_opportunities_plans(client, people, plans):
    _login(client, people["pm"])
    for key in ("opp701", "opp703", "program", "a_with_703"):
        assert client.get(_editor(plans[key])).status_code == 200, key
    assert client.get(_editor(plans["org_a"])).status_code == 404


@pytest.mark.parametrize(
    "who,key",
    [
        ("nm_b", "opp701"),  # another partner's opportunity
        ("nm_a", "opp703"),
        ("nm_a", "program"),  # a program the partner's tree does not hold
        ("nm_b", "org_a"),  # another organisation
        ("nm_a", "a_with_703"),  # in the plan's org, but its opportunity is what counts
        ("nm_b", "legacy"),  # no context: creator only
        ("pm", "legacy"),
    ],
)
def test_a_plan_out_of_reach_is_404_everywhere(client, people, plans, who, key):
    plan = plans[key]
    _login(client, people[who])
    assert client.get(_editor(plan)).status_code == 404
    assert _api(client, plan, {"name": "hijacked"}).status_code == 404
    assert _api(client, plan, {"data": {"rows": []}}).status_code == 404
    assert _delete(client, plan).status_code == 404
    plan.refresh_from_db()
    assert plan.name != "hijacked"


def test_a_legacy_plans_creator_keeps_it(client, people, plans):
    plan = plans["legacy"]
    _login(client, people["nm_a"])
    resp = client.get(_editor(plan))
    assert resp.status_code == 200
    assert "No context: only its creator and Dimagi staff can open it" in resp.content.decode()
    assert _api(client, plan, {"name": "AK Test 2"}).status_code == 200
    assert _delete(client, plan).status_code == 302
    assert not SimpleMicroplan.objects.filter(pk=plan.pk).exists()


def test_the_creator_keeps_their_plan_and_may_delete_it(client, people, plans):
    plan = plans["opp703"]
    _login(client, people["nm_b"])
    assert client.get(_editor(plan)).status_code == 200
    assert _delete(client, plan).status_code == 302
