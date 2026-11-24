"""Saved audit setups: what the wizard's "Save as setup" keeps, and the one audit it makes."""

import json
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from connect_labs.audit import setup_views, setups
from connect_labs.audit.setup_models import AuditSetup

pytestmark = pytest.mark.django_db

PHOTO = "anthropometric/upload_weight_image"


def _body(**over):
    body = {
        "name": "KC weight photos",
        "opportunities": [{"id": 1487, "name": "PIPN"}],
        "criteria": {
            "audit_type": "last_n_per_flw",
            "countPerFlw": 25,
            "sample_percentage": 100,
            "deliver_unit_types": ["Follow-up visit"],
            "visit_statuses": ["approved"],
            "exclude_prior_audited": True,
            "pass_threshold": 80,
            "tag": "kc-review",
            # Never kept: who, and run-specific results.
            "selected_flw_user_ids": ["someone"],
        },
        "image_audits": [{"image_path": PHOTO, "reviewers": [{"agent_id": "scale_validation", "config": {}}]}],
        "context_fields": [{"imagePath": PHOTO, "fieldPath": "form/weight", "label": "Weight"}],
        "visit_ids": [1, 2, 3],
    }
    body.update(over)
    return body


def test_a_setup_keeps_how_to_audit_and_never_who():
    s = setups.save(_body(), user=None)
    assert s.name == "KC weight photos" and s.opportunity_ids == [1487]
    assert s.criteria == {
        "audit_type": "last_n_per_flw",
        "count_per_flw": 25,
        "sample_percentage": 100,
        "deliver_unit_types": ["Follow-up visit"],
        "visit_statuses": ["approved"],
        "exclude_prior_audited": True,
        "pass_threshold": 80,
        "tag": "kc-review",
    }
    assert s.image_audits == [
        {"image_path": PHOTO, "reviewers": [{"agent_id": "scale_validation", "config": {}, "auto_apply_actions": []}]}
    ]
    assert s.context_fields == [{"image_path": PHOTO, "field_path": "form/weight", "label": "Weight"}]
    assert "25 most recent visits per worker" in setups.summary(s) and "scale validation" in setups.summary(s)


def test_a_date_range_becomes_a_number_of_recent_days():
    s = setups.save(
        _body(criteria={"audit_type": "date_range", "startDate": "2026-10-28", "endDate": "2026-11-24"}), user=None
    )
    assert s.criteria["audit_type"] == "recent_days" and s.criteria["days"] == 28


@pytest.mark.parametrize(
    "over, match",
    [
        ({"name": ""}, "name"),
        ({"opportunities": []}, "opportunities"),
        ({"image_audits": []}, "photo"),
        ({"criteria": {"audit_type": "last_n_across_all", "countAcrossAll": 100}}, "each worker"),
        ({"criteria": {"audit_type": "last_n_per_flw", "countPerFlw": 0}}, "between 1 and 500"),
        (
            {"criteria": {"audit_type": "last_n_per_flw", "countPerFlw": 5, "visit_statuses": ["lost"]}},
            "Visit statuses",
        ),
        ({"image_audits": [{"image_path": PHOTO, "reviewers": [{"agent_id": ""}]}]}, "AI reviewer"),
    ],
)
def test_a_setup_that_cannot_make_an_audit_is_refused(over, match):
    with pytest.raises(setups.SetupRefused, match=match):
        setups.clean(_body(**over))


def test_run_kwargs_make_one_workers_audit_per_opportunity():
    s = setups.save(_body(), user=None)
    kw = setups.run_kwargs(
        s,
        access_token="t",
        creator_username="ann",
        worker_username="flw7",
        opportunity_ids=[1487, 1490],
        title="KC review #1004",
        workflow_run_id=30019,
    )
    assert kw["username"] == "ann" and kw["workflow_run_id"] == 30019
    assert kw["opportunities"] == [{"id": 1487, "name": ""}, {"id": 1490, "name": ""}]
    c = kw["criteria"]
    assert c["granularity"] == "per_flw" and c["selected_flw_user_ids"] == ["flw7"]
    assert c["audit_type"] == "last_n_per_flw" and c["count_per_flw"] == 25 and c["title"] == "KC review #1004"
    assert kw["image_audits"] == s.image_audits and kw["context_fields"] == s.context_fields


def test_recent_days_are_counted_back_from_today():
    s = setups.save(
        _body(criteria={"audit_type": "date_range", "startDate": "2026-01-01", "endDate": "2026-01-28"}), None
    )
    kw = setups.run_kwargs(
        s,
        access_token="t",
        creator_username="a",
        worker_username="f",
        opportunity_ids=[1],
        title="t",
        today=date(2026, 11, 24),
    )
    assert (kw["criteria"]["start_date"], kw["criteria"]["end_date"]) == ("2026-10-28", "2026-11-24")


def _call(user, method="get", body=None):
    rf = RequestFactory()
    request = (
        rf.post("/audit/api/setups/", data=json.dumps(body), content_type="application/json")
        if method == "post"
        else rf.get("/audit/api/setups/")
    )
    request.user = user
    return setup_views.setups_api(request)


def test_saving_a_setup_is_for_dimagi_staff_and_anyone_signed_in_may_list_them():
    User = get_user_model()
    staff = User.objects.create(username="s", email="ann@dimagi.com")
    other = User.objects.create(username="o", email="x@example.org")
    assert _call(other, "post", _body()).status_code == 403
    made = _call(staff, "post", _body())
    assert made.status_code == 201
    listed = json.loads(_call(other).content)["setups"]
    assert [x["name"] for x in listed] == ["KC weight photos"]
    bad = _call(staff, "post", _body(name=""))
    assert bad.status_code == 400 and "name" in json.loads(bad.content)["error"]


def test_archived_setups_are_not_offered():
    s = setups.save(_body(), None)
    AuditSetup.objects.filter(pk=s.pk).update(archived=True)
    assert setups.get(s.pk) is None
