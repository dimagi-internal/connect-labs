"""Microplans applies the labs-only scope access check to every program it serves.

A labs-only program's plans live in the labs DB with no Connect membership check
behind them, so ``SyntheticOpportunity.is_accessible_to`` (via
``labs_only_scope_denied_reason``) is the only thing between a signed-in user and
another tenant's plans. These tests pin that every program-scoped view and the
data access consult it, that a refusal is a 404 that leaves the data alone, that
the people the access model admits still get in, and that a real program's path
is untouched.
"""

from __future__ import annotations

import json
import time

import pytest
from django.urls import URLPattern, URLResolver, reverse

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.microplans import urls as microplans_urls
from connect_labs.microplans.core.data_access import ProgramAccessDenied, ProgramPlanDataAccess

PROG = 10_051  # labs-only: the program id is the backing opp id
REAL_PROG = 133

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


def _user(django_user_model, username, email):
    return django_user_model.objects.create(username=username, email=email)


def _login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()


@pytest.fixture
def owner(django_user_model):
    return _user(django_user_model, "owner", "owner@partner-a.example")


@pytest.fixture
def program(db, owner):
    """A labs-only program restricted to one partner domain, with one plan + group."""
    SyntheticOpportunity.objects.create(
        opportunity_id=PROG,
        label="Restricted study",
        program_name="Restricted study",
        gdrive_folder_id="f",
        labs_only=True,
        allowed_domains=["@partner-a.example"],
        created_by=owner,
    )
    da = ProgramPlanDataAccess(PROG, access_token="labs-local")
    plan = da.create_plan(region="Ward A", name="Ward A", mode="coverage", pins=_EMPTY, hulls=_HULLS)
    group = da.create_group(name="Bundle", plan_ids=[plan.id])
    return {"plan": plan.id, "group": group.id}


@pytest.fixture
def stranger(db, django_user_model):
    return _user(django_user_model, "stranger", "someone@partner-b.example")


def _requests(ids):
    """(method, url, body) for list / detail / create / edit / delete on PROG."""
    plan, group = ids["plan"], ids["group"]
    return [
        ("get", reverse("microplans:program_workspace", args=[PROG]), None),
        ("get", reverse("microplans:program_plans", args=[PROG]), None),
        ("get", reverse("microplans:program_plan", args=[PROG, plan]), None),
        ("get", reverse("microplans:program_review", args=[PROG, plan]), None),
        ("get", reverse("microplans:program_group_page", args=[PROG, group]), None),
        (
            "post",
            reverse("microplans:program_create_plan", args=[PROG]),
            {"region": "Ward B", "name": "Ward B", "mode": "coverage", "hulls": _HULLS},
        ),
        ("post", reverse("microplans:program_plan_edit", args=[PROG, plan]), {"action": "exclude", "wa_ids": ["x"]}),
        ("post", reverse("microplans:program_plan_transition", args=[PROG, plan]), {"to": "archived"}),
        ("post", reverse("microplans:program_group_update", args=[PROG, group]), {"name": "Renamed"}),
        ("post", reverse("microplans:program_plan_delete", args=[PROG, plan]), {}),
        ("delete", reverse("microplans:program_plan", args=[PROG, plan]), None),
        ("post", reverse("microplans:program_group_delete", args=[PROG, group]), {}),
    ]


def _call(client, method, url, body):
    if method == "get":
        return client.get(url)
    if method == "delete":
        return client.delete(url)
    return client.post(url, data=json.dumps(body), content_type="application/json")


def test_every_program_or_opp_scoped_view_runs_the_scope_check():
    """New views cannot forget the check: anything routed with program_id/opp_id
    must carry the mixin that refuses a labs-only scope the user may not use."""
    from connect_labs.microplans.views import _ProgramScopeMixin

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns)
            elif isinstance(p, URLPattern):
                yield p

    scoped = [p for p in walk(microplans_urls.urlpatterns) if {"program_id", "opp_id"} & set(p.pattern.converters)]
    assert scoped, "expected program/opp-scoped routes"
    missing = [p.name for p in scoped if not issubclass(p.callback.view_class, _ProgramScopeMixin)]
    assert missing == []


@pytest.mark.django_db
def test_a_user_the_program_does_not_admit_is_refused_everywhere_with_404(client, program, stranger):
    _login(client, stranger)
    for method, url, body in _requests(program):
        resp = _call(client, method, url, body)
        assert resp.status_code == 404, (method, url, resp.status_code)

    # Nothing was created, changed or deleted.
    da = ProgramPlanDataAccess(PROG, access_token="labs-local")
    plans = da.list_plans()
    assert [p.id for p in plans] == [program["plan"]]
    assert plans[0].status == "draft"
    assert [g.name for g in da.list_groups()] == ["Bundle"]


@pytest.mark.django_db
def test_a_refusal_does_not_set_the_labs_context_to_the_program(client, program, stranger):
    _login(client, stranger)
    client.get(reverse("microplans:program_workspace", args=[PROG]))
    assert (client.session.get("labs_context") or {}).get("program_id") != PROG


@pytest.mark.django_db
@pytest.mark.parametrize(
    "username,email",
    [
        ("owner", None),  # the creator
        ("staffer", "staffer@dimagi.com"),  # Dimagi
        ("colleague", "colleague@partner-a.example"),  # an allowed domain
    ],
)
def test_users_the_program_admits_get_its_plans(client, django_user_model, program, owner, username, email):
    user = owner if email is None else _user(django_user_model, username, email)
    _login(client, user)
    resp = client.get(reverse("microplans:program_plans", args=[PROG]))
    assert resp.status_code == 200
    assert [p["plan_id"] for p in resp.json()["plans"]] == [program["plan"]]
    assert client.get(reverse("microplans:program_plan", args=[PROG, program["plan"]])).status_code == 200


@pytest.mark.django_db
def test_data_access_refuses_a_caller_the_program_does_not_admit(program, stranger):
    from connect_labs.audit_trail.models import Action, AuditEvent

    with pytest.raises(ProgramAccessDenied):
        ProgramPlanDataAccess(PROG, user=stranger, access_token="labs-local")
    assert AuditEvent.objects.filter(action=Action.ACCESS_DENIED, program_id=PROG, user=stranger).exists()


@pytest.mark.django_db
def test_data_access_admits_the_creator_and_callerless_system_paths(program, owner):
    assert ProgramPlanDataAccess(PROG, user=owner, access_token="labs-local").list_plans()
    # Celery tasks / seeders name no caller: they act on ids a view already checked.
    assert ProgramPlanDataAccess(PROG, access_token="labs-local").list_plans()


@pytest.mark.django_db
def test_mcp_data_access_refuses_with_not_found(program, stranger):
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.microplans import _data_access

    with pytest.raises(MCPToolError) as exc:
        _data_access(stranger, PROG)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_a_real_program_is_not_looked_at(django_assert_num_queries, stranger):
    from connect_labs.microplans.access import scope_denied_reason

    with django_assert_num_queries(0):
        assert scope_denied_reason(stranger, program_id=REAL_PROG) is None
        assert scope_denied_reason(stranger, opportunity_id=123) is None


@pytest.mark.django_db
def test_a_real_program_view_still_reaches_connect(client, monkeypatch, stranger):
    called = []
    monkeypatch.setattr(ProgramPlanDataAccess, "list_plans", lambda self: called.append(self.program_id) or [])
    monkeypatch.setattr(ProgramPlanDataAccess, "list_groups", lambda self: [])
    _login(client, stranger)
    resp = client.get(reverse("microplans:program_plans", args=[REAL_PROG]))
    assert resp.status_code == 200
    assert called == [REAL_PROG]
