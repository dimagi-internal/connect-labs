"""Simple Microplan: list, create, open, save, rename and delete, and who may change a plan."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from connect_labs.microplans.models import SimpleMicroplan

pytestmark = pytest.mark.django_db

ROW = {
    "Area Slug": "KA123AB",
    "Ward": "Kaduna",
    "Centroid": "7.43000000 10.52000000",
    "Boundary": "POLYGON((7.4 10.5, 7.5 10.5, 7.5 10.6, 7.4 10.5))",
    "Building Count": 3,
    "Expected Visit Count": 3,
    "LGA": "",
    "State": "",
    "Target Population": 0,
    "Work Area Group Name": "KA-G-001",
}


def _user(django_user_model, username, email):
    return django_user_model.objects.create(username=username, email=email, name=username.title())


@pytest.fixture
def owner(django_user_model):
    return _user(django_user_model, "owner", "owner@example.org")


@pytest.fixture
def other(django_user_model):
    return _user(django_user_model, "other", "other@example.org")


@pytest.fixture
def plan(owner):
    return SimpleMicroplan.objects.create(name="Kaduna test", created_by=owner, created_by_name="Owner")


def _post_api(client, plan, body):
    return client.post(
        reverse("microplans:simple_api", kwargs={"plan_id": plan.pk}),
        data=json.dumps(body),
        content_type="application/json",
    )


def test_list_requires_login(client):
    resp = client.get(reverse("microplans:simple_list"))
    assert resp.status_code == 302


def test_create_records_name_and_creator_then_opens_editor(client, owner):
    client.force_login(owner)
    resp = client.post(reverse("microplans:simple_list"), {"name": "  Ward 4 grid  "})
    plan = SimpleMicroplan.objects.get()
    assert resp.status_code == 302
    assert resp["Location"] == reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk})
    assert plan.name == "Ward 4 grid"
    assert plan.created_by == owner
    assert plan.created_by_name == "Owner"


def test_create_without_name_is_refused(client, owner):
    client.force_login(owner)
    resp = client.post(reverse("microplans:simple_list"), {"name": "   "})
    assert resp.status_code == 400
    assert not SimpleMicroplan.objects.exists()


def test_list_shows_every_plan_with_its_creator(client, plan, other):
    client.force_login(other)
    resp = client.get(reverse("microplans:simple_list"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Kaduna test" in body
    assert "Owner" in body
    # someone else's plan: no delete button
    assert reverse("microplans:simple_delete", kwargs={"plan_id": plan.pk}) not in body


def test_editor_carries_saved_state(client, plan, owner):
    plan.data = {"rows": [ROW], "csv_name": "grid_kaduna.csv"}
    plan.save()
    client.force_login(owner)
    resp = client.get(reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk}))
    assert resp.status_code == 200
    state = resp.context["plan_state"]
    assert state["editable"] is True
    assert state["data"]["rows"] == [ROW]
    assert state["created_by"] == "Owner"


def test_owner_saves_rows_and_the_count_follows(client, plan, owner):
    client.force_login(owner)
    resp = _post_api(
        client,
        plan,
        {"data": {"rows": [ROW, dict(ROW, **{"Area Slug": "KA456CD"})], "boundary": [], "junk": "dropped"}},
    )
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.n_work_areas == 2
    assert plan.data["rows"][0] == ROW
    assert "junk" not in plan.data


def test_owner_renames(client, plan, owner):
    client.force_login(owner)
    resp = _post_api(client, plan, {"name": "Kaduna — 50 m"})
    assert resp.status_code == 200
    plan.refresh_from_db()
    assert plan.name == "Kaduna — 50 m"


def test_rename_to_blank_is_refused(client, plan, owner):
    client.force_login(owner)
    assert _post_api(client, plan, {"name": " "}).status_code == 400


def test_bad_body_is_400(client, plan, owner):
    client.force_login(owner)
    resp = client.post(
        reverse("microplans:simple_api", kwargs={"plan_id": plan.pk}), data="nope", content_type="application/json"
    )
    assert resp.status_code == 400
    assert _post_api(client, plan, {"data": {"rows": "not a list"}}).status_code == 400


def test_someone_else_opens_read_only_and_cannot_change_it(client, plan, other):
    client.force_login(other)
    resp = client.get(reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk}))
    assert resp.context["plan_state"]["editable"] is False
    assert _post_api(client, plan, {"name": "hijacked"}).status_code == 403
    assert client.post(reverse("microplans:simple_delete", kwargs={"plan_id": plan.pk})).status_code == 404
    plan.refresh_from_db()
    assert plan.name == "Kaduna test"


def test_dimagi_staff_may_change_anyones_plan(client, plan, django_user_model):
    staff = _user(django_user_model, "staffer", "staffer@dimagi.com")
    client.force_login(staff)
    assert _post_api(client, plan, {"name": "Tidied"}).status_code == 200


def test_owner_deletes(client, plan, owner):
    client.force_login(owner)
    resp = client.post(reverse("microplans:simple_delete", kwargs={"plan_id": plan.pk}))
    assert resp.status_code == 302
    assert not SimpleMicroplan.objects.exists()


def test_hub_offers_chc_wa_revisit_and_simple(client, owner):
    client.force_login(owner)
    resp = client.get(reverse("microplans:hub"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "/microplans/program/217/" in body  # nothing picked: falls back to 217
    assert "/wa-revisit/program/217/" in body
    assert reverse("microplans:simple_list") in body


# --- in-page help: the guide, the page and the docs updater stay in step -----


def _editor_template() -> str:
    from django.conf import settings

    return (settings.BASE_DIR / "connect_labs/templates/microplans/simple_editor.html").read_text()


def test_help_is_the_user_docs_guide_split_by_section():
    from connect_labs.microplans.simple_views import help_sections

    sections = help_sections()
    ids = [s["id"] for s in sections]
    assert ids[0] == "about"
    for expected in ("step-1-boundary", "step-2-work-areas", "step-3-grouping", "step-4-export"):
        assert expected in ids
    assert all(s["html"] and s["text"] for s in sections)
    assert not any("<script" in s["html"] for s in sections)


def test_every_help_button_on_the_page_opens_a_real_article():
    import re

    from connect_labs.microplans.simple_views import help_sections

    ids = {s["id"] for s in help_sections()}
    wanted = set(re.findall(r'data-help-open="([^"]+)"', _editor_template()))
    assert wanted, "the editor lost its help buttons"
    assert wanted <= ids, f"help buttons with no section in user_docs/simple-microplan.md: {wanted - ids}"


def test_every_step_has_a_help_button():
    import re

    template = _editor_template()
    steps = re.findall(r'<div class="step-head">(.*?)</div>', template, flags=re.S)
    assert steps
    assert all("data-help-open=" in s for s in steps), "a step card has no help button"


def test_editor_page_carries_the_help(client, plan, owner):
    client.force_login(owner)
    resp = client.get(reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk}))
    assert any(s["id"] == "step-3-grouping" for s in resp.context["help_sections"])
    body = resp.content.decode()
    assert 'id="mp-help"' in body
    # articles ship as sanitised <template>s; the JSON index carries no HTML
    assert 'id="mp-help-art-step-3-grouping"' in body
    assert all("html" not in s for s in resp.context["help_index"])


def test_docs_updater_keeps_the_guide_current():
    """Every merged PR touching Simple Microplan rewrites the guide (and so the popup)."""
    import sys

    from django.conf import settings

    sys.path.insert(0, str(settings.BASE_DIR / "automation"))
    try:
        import update_docs
    finally:
        sys.path.pop(0)
    changed = [
        "connect_labs/microplans/simple_views.py",
        "connect_labs/static/microplans/simple_microplan.js",
        "connect_labs/templates/microplans/simple_editor.html",
    ]
    for path in changed:
        assert update_docs.affected_features([path]) == {"Simple Microplan"}, path
    assert update_docs.FEATURE_TO_DOC_FILE["Simple Microplan"] == "user_docs/simple-microplan.md"
