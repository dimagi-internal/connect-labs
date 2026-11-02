"""The page behind a public link, and who may make one.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from connect_labs.audit_trail.models import AuditEvent
from connect_labs.labs.integrations.connect.api_client import LabsAPIError
from connect_labs.utils import request_telemetry
from connect_labs.workflow import public_share_views, public_shares, views
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.models import PublicRunShare
from connect_labs.workflow.tests.test_public_shares import SNAPSHOT, _definition, _run, _share

pytestmark = pytest.mark.django_db


def _url(token):
    return reverse("labs:workflow:public_run", kwargs={"token": token})


def _staff(name="staff-person"):
    return get_user_model().objects.create(username=name, email=f"{name}@dimagi.com")


def test_anyone_can_open_a_live_link_with_no_login():
    share = _share()

    response = Client().get(_url(share.token))

    assert response.status_code == 200
    data = json.loads(
        response.content.decode().split('id="public-run-data" type="application/json">')[1].split("</script>")[0]
    )
    assert data["payload"]["instance"]["snapshot"]["workers"][0]["name"] == "Worker One"
    assert "frame-src" in response["Content-Security-Policy"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert "noindex" in response["X-Robots-Tag"]
    assert "no-store" in response["Cache-Control"]


def test_unknown_expired_and_ended_links_look_exactly_the_same():
    expired = _share(expires_at=timezone.now() - timedelta(seconds=1))
    ended = _share()
    public_shares.end(ended, PublicRunShare.EndedReason.ENDED)

    answers = [Client().get(_url(t)) for t in ("no-such-token", expired.token, ended.token)]

    assert [a.status_code for a in answers] == [404, 404, 404]
    assert answers[0].content == answers[1].content == answers[2].content
    assert b"no longer available" in answers[0].content


def test_a_signed_in_visitor_is_anonymous_and_is_not_named_in_the_audit_trail():
    share = _share()
    client = Client()
    client.force_login(_staff())

    response = client.get(_url(share.token))

    assert response.status_code == 200
    events = AuditEvent.objects.filter(path__contains="/shared/")
    assert events.filter(resource_type=public_shares.RESOURCE_TYPE, action="read").count() == 1
    assert [e.username for e in events] == [""]


def test_every_view_is_audited_and_counted_and_the_token_is_never_written():
    share = _share()

    Client().get(_url(share.token))
    Client().get(_url(share.token))

    share.refresh_from_db()
    assert share.view_count == 2
    events = AuditEvent.objects.filter(resource_type=public_shares.RESOURCE_TYPE, action="read")
    assert events.count() == 2
    for event in AuditEvent.objects.filter(resource_type=public_shares.RESOURCE_TYPE):
        assert share.token not in json.dumps(
            event.as_dict() if hasattr(event, "as_dict") else str(vars(event)), default=str
        )
        assert share.token not in event.path


def test_a_closing_script_tag_in_the_data_cannot_break_out_of_the_page():
    share = _share()
    PublicRunShare.objects.filter(pk=share.pk).update(
        payload={**share.payload, "pipelines": {"visits": {"rows": [{"note": "</script><script>alert(1)</script>"}]}}}
    )

    body = Client().get(_url(share.token)).content.decode()

    assert "</script><script>alert(1)" not in body


def test_slow_request_logs_do_not_carry_the_token():
    request = RequestFactory().get(_url("a-real-token"))
    assert "a-real-token" not in request_telemetry.loggable_path(request)
    other = RequestFactory().get("/labs/workflow/")
    assert request_telemetry.loggable_path(other) == "/labs/workflow/"


# --- making a link ---------------------------------------------------------------------


def _post(user, body, run=None, definition=None, render="function WorkflowUI() {}", missing=()):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = user
    request.session = {"labs_oauth": {"access_token": "t"}}
    with (
        patch.object(public_share_views, "WorkflowDataAccess") as wda,
        patch.object(public_share_views, "resolve_render_code", return_value=(render, {})),
        patch.object(public_share_views, "_opportunities_without_access", return_value=list(missing)),
    ):
        wda.return_value.get_run.return_value = run if run is not None else _run()
        wda.return_value.get_definition.return_value = definition or _definition()
        response = public_share_views.public_shares_api(request, 22)
    return response, json.loads(response.content)


GOOD = {"days": 30, "confirmed": True}


def test_dimagi_staff_holding_the_acl_can_make_a_link_and_the_token_is_not_audited():
    response, body = _post(_staff(), GOOD)

    assert response.status_code == 201, body
    share = PublicRunShare.objects.get()
    assert share.token in body["share"]["url"]
    assert share.payload["instance"]["snapshot"] == SNAPSHOT
    assert (share.expires_at - timezone.now()).days in (29, 30)
    created = AuditEvent.objects.filter(resource_type=public_shares.RESOURCE_TYPE, action="create").get()
    assert share.token not in str(created.metadata)


def test_someone_who_is_not_dimagi_staff_cannot_even_with_the_acl():
    outsider = get_user_model().objects.create(username="partner", email="partner@example.org")

    response, _ = _post(outsider, GOOD)

    assert response.status_code == 403
    assert not PublicRunShare.objects.exists()


def test_staff_without_the_acl_cannot():
    request = RequestFactory().post("/x/", data=json.dumps(GOOD), content_type="application/json")
    request.user = _staff()
    with patch.object(public_share_views, "WorkflowDataAccess") as wda:
        wda.return_value.get_run.side_effect = LabsAPIError("nope", status_code=404)
        response = public_share_views.public_shares_api(request, 22)

    assert response.status_code == 404
    assert not PublicRunShare.objects.exists()


def test_a_run_holding_an_opportunity_the_person_lacks_is_refused_and_says_which():
    response, body = _post(_staff(), GOOD, missing=[99])

    assert response.status_code == 403
    assert "99" in body["error"]
    assert not PublicRunShare.objects.exists()


@pytest.mark.parametrize(
    "run",
    [_run(status="in_progress", snapshot=None), _run(snapshot={})],
)
def test_only_a_saved_run_can_be_shared(run):
    response, _ = _post(_staff(), GOOD, run=run)

    assert response.status_code == 400
    assert not PublicRunShare.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"days": 2, "confirmed": True},
        {"until": "2001-01-01", "confirmed": True},
        {"until": "soon", "confirmed": True},
        {"days": 7},
        {"days": 7, "confirmed": "yes"},
    ],
)
def test_a_bad_duration_or_no_confirmation_is_refused(body):
    response, _ = _post(_staff(), body)

    assert response.status_code == 400
    assert not PublicRunShare.objects.exists()


def test_the_opportunities_a_copy_would_hold_include_tagged_rows_and_workers():
    run = _run(
        snapshot={
            "opportunity_ids": [7],
            "pipelines": {"visits": {"rows": [{"opportunity_id": 8}, {"opportunity_id": "9"}, {"note": "x"}]}},
            "workers": [{"username": "w", "opportunity_id": 10}],
        }
    )
    definition = _definition()
    definition.data = {**definition.data, "opportunity_ids": [7, 11]}

    assert public_share_views.snapshot_opportunity_ids(definition, run) == [7, 8, 9, 10, 11]


# --- ending a link ---------------------------------------------------------------------


def _end(user, share, can_read=False):
    request = RequestFactory().post("/x/")
    request.user = user
    request.session = {"labs_oauth": {"access_token": "t"}}
    with patch.object(public_share_views, "_can_read_run", return_value=can_read):
        return public_share_views.end_public_share_api(request, share.pk)


def test_the_person_who_made_a_link_can_end_it():
    maker = _staff()
    share = _share()
    PublicRunShare.objects.filter(pk=share.pk).update(created_by=maker)

    assert _end(maker, share).status_code == 200

    share.refresh_from_db()
    assert share.payload is None
    assert share.ended_by == maker


def test_staff_with_the_acl_can_end_it_and_staff_without_cannot():
    share = _share()

    assert _end(_staff("no-acl"), share).status_code == 404
    share.refresh_from_db()
    assert share.payload is not None

    assert _end(_staff("has-acl"), share, can_read=True).status_code == 200
    share.refresh_from_db()
    assert share.payload is None


def test_a_routine_replacement_of_a_run_does_not_end_its_links():
    """hand_down and history_rebuild call delete_run themselves; only a person's delete ends a link."""
    share = _share()
    wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
    wda.labs_api = MagicMock()

    with patch.object(WorkflowDataAccess, "get_run", return_value=MagicMock(id=22, data={})):
        wda.delete_run(22, delete_linked=False)

    assert wda.labs_api.delete_record.called or wda.labs_api.delete_records.called
    share.refresh_from_db()
    assert share.payload is not None


def test_a_person_deleting_a_run_ends_its_links():
    share = _share()
    request = RequestFactory().post("/x/")
    request.user = _staff()
    request.session = {"labs_oauth": {"access_token": "t"}}
    run = _run()
    run.data = {"state": {}}
    with patch("connect_labs.workflow.views.WorkflowDataAccess") as wda:
        wda.return_value.get_run.return_value = run
        wda.return_value.delete_run.return_value = {"run": 1, "audit_sessions": 0}
        response = views.delete_run_api(request, 22)

    assert response.status_code == 200
    share.refresh_from_db()
    assert share.payload is None
    assert share.ended_reason == "source_deleted"


def test_share_is_offered_only_to_staff_on_a_saved_run():
    outsider = get_user_model().objects.create(username="partner", email="partner@example.org")

    assert public_share_views.can_share_run(_staff(), _run()) is True
    assert public_share_views.can_share_run(outsider, _run()) is False
    assert public_share_views.can_share_run(_staff("two"), _run(status="in_progress", snapshot=None)) is False
