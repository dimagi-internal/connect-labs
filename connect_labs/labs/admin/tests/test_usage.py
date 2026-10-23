"""Labs usage analytics: rollups of the audit trail by organisation /
programme / opportunity / user, and the admin pages that show them."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from connect_labs.audit_trail.models import Action, AuditEvent, Source
from connect_labs.labs.admin import usage
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.pulse.models import PulseOpportunity, PulseOrganization, PulseProgram


@pytest.fixture
def partner(db):
    return get_user_model().objects.create_user(username="amina", email="amina@acme.org", password="pw")


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def directory(db):
    PulseOrganization.objects.create(slug="acme", name="Acme Health")
    PulseProgram.objects.create(program_id=25, name="Nutrition", org_slug="acme")
    PulseOpportunity.objects.create(opportunity_id=812, name="Kano rollout", org_slug="acme", program_id=25)


def event(user, action=Action.PAGE_VIEW, *, days_ago=0, source=Source.WEB, path="/labs/workflow/", **kwargs):
    return AuditEvent.objects.create(
        user=user,
        username=user.username if user else "",
        user_email=user.email if user else "",
        action=action,
        source=source,
        path=path,
        occurred_at=timezone.now() - timedelta(days=days_ago),
        **kwargs,
    )


def by_key(rows):
    return {r["key"]: r for r in rows}


@pytest.mark.django_db
def test_page_view_attributed_through_opp_to_programme_and_organisation(partner, directory):
    # An older row: no scope columns, only the URL the context middleware wrote.
    event(partner, query_string="opportunity_id=812")
    event(partner, Action.UPDATE, opportunity_id=812, resource_type="workflow_run")

    report = usage.build_report(usage.Scope())

    assert by_key(report["opps"])[812]["views"] == 1
    assert by_key(report["opps"])[812]["changes"] == 1
    assert by_key(report["programs"])[25]["label"] == "Nutrition"
    org = by_key(report["orgs"])["acme"]
    assert (org["label"], org["users"], org["top_area"]) == ("Acme Health", 1, "Workflows")
    assert report["totals"] == {
        "users": 1,
        "views": 1,
        "changes": 1,
        "exports": 0,
        "agent": 0,
        "orgs": 1,
        "programs": 1,
        "opps": 1,
    }


@pytest.mark.django_db
def test_only_people_count(partner, directory):
    event(partner, Action.UPDATE, source=Source.CELERY, opportunity_id=812)
    event(None, Action.UPDATE, opportunity_id=812)
    event(partner, Action.LOGIN)

    assert usage.build_report(usage.Scope())["totals"]["users"] == 0


@pytest.mark.django_db
def test_agent_calls_count_tool_calls_not_data_touches(partner, directory):
    mcp = {"source": Source.MCP, "opportunity_id": 812, "path": "mcp:workflow_get"}
    for _ in range(3):
        event(partner, Action.LIST, request_id="mcp:aa11", **mcp)
    event(partner, Action.LIST, request_id="mcp:cc33", **mcp)

    opp = by_key(usage.build_report(usage.Scope())["opps"])[812]
    assert opp["agent"] == 2
    assert opp["top_area"] == usage.AGENT_AREA


@pytest.fixture
def dimagi_people(db):
    User = get_user_model()
    return [
        User.objects.create_user(username=f"d{i}", email=f"d{i}@{domain}", password="pw")
        for i, domain in enumerate(("dimagi.com", "dimagi-ai.com", "dimagi-associate.com"))
    ]


@pytest.mark.django_db
def test_hide_staff_excludes_every_dimagi_domain(partner, dimagi_people, directory):
    for person in [partner, *dimagi_people]:
        event(person, query_string="opportunity_id=812")

    assert usage.build_report(usage.Scope())["totals"]["users"] == 4
    report = usage.build_report(usage.Scope(), hide_staff=True)
    assert [r["label"] for r in report["users"]] == ["amina"]


@pytest.mark.django_db
def test_window_excludes_older_activity(partner, directory):
    event(partner, query_string="opportunity_id=812", days_ago=45)

    assert usage.build_report(usage.Scope(), days=30)["totals"]["views"] == 0
    assert usage.build_report(usage.Scope(), days=90)["totals"]["views"] == 1


@pytest.mark.django_db
def test_org_scope_gathers_its_opps_programmes_and_slug(partner, staff, directory):
    event(partner, Action.CREATE, opportunity_id=812)
    event(partner, query_string="program_id=25")
    event(partner, query_string="organization_id=acme", path="/supply/")
    event(staff, query_string="opportunity_id=999")  # another organisation's opp

    report = usage.build_report(usage.Scope("org", "acme"))

    assert report["totals"]["users"] == 1
    assert report["totals"]["views"] == 2
    assert report["totals"]["changes"] == 1
    assert {r["area"] for r in report["areas"]} == {"Workflows", "Supply"}


@pytest.mark.django_db
def test_programme_scope_includes_activity_recorded_only_against_its_opps(partner, directory):
    event(partner, Action.UPDATE, opportunity_id=812)

    assert usage.build_report(usage.Scope("program", "25"))["totals"]["changes"] == 1


@pytest.mark.django_db
def test_unknown_opp_keeps_its_id_and_an_out_of_range_id_is_ignored(partner, db):
    event(partner, query_string="opportunity_id=4242")
    event(partner, query_string="opportunity_id=99999999999999")

    report = usage.build_report(usage.Scope())
    assert [r["label"] for r in report["opps"]] == ["Opportunity #4242"]
    assert by_key(report["orgs"])[usage.NO_ORG]["link_kind"] is None


@pytest.mark.django_db
def test_series_buckets_weekly_for_long_windows(partner, directory):
    event(partner, query_string="opportunity_id=812", days_ago=1)

    daily = usage.build_report(usage.Scope(), days=7)["series"]
    weekly = usage.build_report(usage.Scope(), days=90)["series"]
    assert len(daily["labels"]) == 8 and sum(daily["views"]) == 1
    assert len(weekly["labels"]) in (13, 14) and sum(weekly["users"]) == 1


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        ("labs_admin:usage", []),
        ("labs_admin:usage_scope", ["org", "acme"]),
        ("labs_admin:usage_scope", ["program", "25"]),
        ("labs_admin:usage_scope", ["opp", "812"]),
    ],
)
def test_pages_render(client, staff, partner, directory, url):
    event(partner, query_string="opportunity_id=812", path="/labs/workflow/42/run/")
    client.force_login(staff)

    response = client.get(reverse(url[0], args=url[1]) + "?days=90")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Acme Health" in content
    assert "{#" not in content
    assert "amina" in content


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_user_page_shows_their_click_path(client, staff, partner, directory):
    event(partner, query_string="opportunity_id=812", path="/labs/workflow/42/run/")
    client.force_login(staff)

    response = client.get(reverse("labs_admin:usage_scope", args=["user", partner.pk]))

    content = response.content.decode()
    assert response.status_code == 200
    assert "/labs/workflow/42/run/?opportunity_id=812" in content
    assert "Kano rollout" in content


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_recent_activity_names_the_mcp_tool(client, staff, partner, directory):
    event(partner, Action.UPDATE, source=Source.MCP, opportunity_id=812, path="mcp:workflow_update_render_code")
    client.force_login(staff)

    response = client.get(reverse("labs_admin:usage_scope", args=["opp", "812"]))

    assert "MCP workflow_update_render_code" in response.content.decode()


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_exclude_dimagi_staff_filters_and_carries_into_every_drill_down(
    client, staff, partner, dimagi_people, directory
):
    event(partner, query_string="opportunity_id=812")
    event(dimagi_people[2], query_string="opportunity_id=812")
    client.force_login(staff)

    response = client.get(reverse("labs_admin:usage") + "?days=90&exclude_dimagi_staff=1")

    content = response.content.decode()
    assert "amina" in content
    assert "d2@dimagi-associate.com" not in content
    assert 'name="exclude_dimagi_staff" value="1"' in content  # the window picker keeps it
    for kind, key in [("org", "acme"), ("program", 25), ("opp", 812), ("user", partner.pk)]:
        link = reverse("labs_admin:usage_scope", args=[kind, key]) + "?days=90&amp;exclude_dimagi_staff=1"
        assert link in content, f"{kind} link drops the filter"


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_staff_toggle_at_the_top_switches_the_filter_and_keeps_the_rest(client, staff):
    client.force_login(staff)
    url = reverse("labs_admin:usage_scope", args=["org", "acme"])

    on = client.get(url + "?days=90").content.decode()
    off = client.get(url + "?days=90&exclude_dimagi_staff=1").content.decode()

    assert f'href="{url}?days=90&amp;exclude_dimagi_staff=1" id="staff-excluded"' in on
    assert f'href="{url}?days=90" id="staff-included" aria-current="true"' in on
    assert f'href="{url}?days=90" id="staff-included"' in off
    assert 'id="staff-excluded" aria-current="true"' in off
    assert "Partner activity only" in off and "Partner activity only" not in on


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params, target",
    [
        ("opportunity_id=2166&program_id=25", ["opp", "2166"]),
        ("program_id=25", ["program", "25"]),
        ("organization_id=acme", ["org", "acme"]),
    ],
)
def test_overview_honours_an_explicit_context_param(rf, staff, params, target):
    # The view alone: the context middleware in front of it validates access to
    # the context first, which is not what this pins.
    from connect_labs.labs.admin.usage_views import UsageView

    request = rf.get(f"{reverse('labs_admin:usage')}?{params}&days=90&exclude_dimagi_staff=1")
    request.user = staff
    response = UsageView.as_view()(request)

    assert response.status_code == 302
    assert response["Location"] == reverse("labs_admin:usage_scope", args=target) + "?days=90&exclude_dimagi_staff=1"


@pytest.mark.django_db
def test_overview_offers_the_usage_of_the_context_you_are_in(rf, staff, directory):
    from connect_labs.labs.admin.usage_views import UsageView

    request = rf.get(reverse("labs_admin:usage"), {"days": "90"})
    request.user = staff
    request.labs_context = {"opportunity_id": 812, "program_id": 25}
    response = UsageView.as_view()(request)
    response.render()
    content = response.content.decode()

    assert "Opportunity: Kano rollout" in content
    assert f'href="{reverse("labs_admin:usage_scope", args=["opp", 812])}?days=90" id="usage-context-link"' in content
    # ...and the context is not dragged onto the links the page draws.
    assert "opportunity_id=812" not in content


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_viewing_usage_is_audited(client, staff):
    client.force_login(staff)
    client.get(reverse("labs_admin:usage_scope", args=["org", "acme"]))

    assert AuditEvent.objects.filter(
        action=Action.READ, resource_type="usage_analytics", resource_id="org:acme"
    ).exists()


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
@pytest.mark.parametrize("args", [["bogus", "1"], ["opp", "abc"]])
def test_bad_scope_is_404(client, staff, args):
    client.force_login(staff)
    assert client.get(reverse("labs_admin:usage_scope", args=args)).status_code == 404


@override_settings(**LABS_SETTINGS)
@pytest.mark.django_db
def test_forbidden_for_non_dimagi(client, partner):
    client.force_login(partner)
    assert client.get(reverse("labs_admin:usage")).status_code == 403
    assert client.get(reverse("labs_admin:usage_scope", args=["org", "acme"])).status_code == 403
