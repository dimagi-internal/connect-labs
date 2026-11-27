"""Escalate: a review handed up to its entity's parent, with documents attached to the action."""

import json

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings

from connect_labs.audit_reviews import api_views, engine, service
from connect_labs.audit_reviews.tests.test_api import (
    WORKFLOW,
    _user,
    dao,  # noqa: F401  (fixture)
)
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, _pending, kc_state, source_for

pytestmark = pytest.mark.django_db

CASE = CASES["go_live_first_run"]
IN_MEMORY = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


@pytest.fixture
def opened():
    """A source with every proposed review open."""
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    ents = _pending(source, "open")
    service.record_decisions(source, [{"kind": "open", "entity": e} for e in ents], user=None, by="S")
    return source


def _reviews(source):
    return service.world(source)["reviews"]


def _child_with_open_parent(source):
    w = service.world(source)
    open_ents = {r["ent"] for r in w["reviews"] if r["status"] == "open"}
    for r in w["reviews"]:
        parent = (w["entityInfo"].get(r["ent"]) or {}).get("parent")
        if r["type"] != "program" and parent in open_ents:
            return r, parent
    raise AssertionError("the case needs a review whose parent also has an open review")


def _event(source, key, event_id, payload, type="action"):
    return service.record_event(
        source, review_key=key, event_id=event_id, type=type, payload=payload, user=None, by="Staff One"
    )


def _rv(source, number):
    return next(r for r in _reviews(source) if r["id"] == number)


# --- Escalate ----------------------------------------------------------------------------------


def test_escalate_goes_to_the_parent_the_server_names(opened):
    child, parent = _child_with_open_parent(opened)
    # Whatever the page says the target is, the server decides it from the hierarchy.
    _event(opened, child["key"], "e1", {"action": "escalate", "note": "LLO to follow up", "escalation": {"to": "x"}})
    ev = next(e for e in _rv(opened, child["id"])["events"] if e.get("id") == "e1")
    esc = ev["payload"]["escalation"]
    assert esc["to"] == parent
    assert esc["to_type"] == ("program" if parent == "program" else "llo")


def test_escalation_shows_on_the_parents_open_review(opened):
    child, parent = _child_with_open_parent(opened)
    _event(opened, child["key"], "e1", {"action": "escalate", "note": "See the letter"})
    target = next(r for r in _reviews(opened) if r["ent"] == parent and r["status"] == "open")
    incoming = [e for e in target["events"] if e["type"] == "escalated_in"]
    assert len(incoming) == 1
    assert incoming[0]["payload"]["from_review"] == child["id"]
    assert incoming[0]["payload"]["note"] == "See the letter"
    sent = next(e for e in _rv(opened, child["id"])["events"] if e.get("id") == "e1")
    assert sent["payload"]["escalation"]["review"] == target["id"]


def test_the_program_has_nothing_to_escalate_to(opened):
    program = next((r for r in _reviews(opened) if r["type"] == "program"), None)
    if program is None:
        pytest.skip("no program review in this case")
    with pytest.raises(service.Refused, match="nothing above the program"):
        _event(opened, program["key"], "e1", {"action": "escalate"})


def test_another_action_cannot_claim_an_escalation(opened):
    child, _parent = _child_with_open_parent(opened)
    _event(opened, child["key"], "a1", {"action": "investigate", "escalation": {"to": "program"}})
    ev = next(e for e in _rv(opened, child["id"])["events"] if e.get("id") == "a1")
    assert "escalation" not in ev["payload"]


# --- Settings ----------------------------------------------------------------------------------


def test_escalate_is_on_by_default_where_there_is_a_parent():
    cfg = engine.default_config({"metrics": {}}, None)
    assert "escalate" in cfg["levelActions"]["flw"]
    assert "escalate" in cfg["levelActions"]["llo"]
    assert "escalate" not in cfg["levelActions"]["program"]
    # The opportunity level still has no actions, so it still opens no reviews.
    assert cfg["levelActions"]["opportunity"] == []


def test_levels_saved_before_escalate_get_it_and_empty_ones_stay_off():
    saved = {"levelActions": {"flw": ["investigate"], "llo": []}}
    cfg = engine.default_config({"metrics": {}}, saved)
    assert cfg["levelActions"]["flw"] == ["investigate", "escalate"]
    assert cfg["levelActions"]["llo"] == []


def test_a_level_saved_knowing_escalate_keeps_it_off():
    saved = {"levelActions": {"flw": ["investigate"]}, "actionKeys": list(engine.ACTION_KEYS)}
    assert engine.default_config({"metrics": {}}, saved)["levelActions"]["flw"] == ["investigate"]


def test_settings_refuse_escalate_for_the_program_and_record_the_actions_known(opened):
    with pytest.raises(service.Refused, match="nothing above the program"):
        service.validate_settings(opened, {"levelActions": {"program": ["escalate"]}})
    clean = service.validate_settings(opened, {"levelActions": {"flw": ["investigate"]}})
    assert clean["actionKeys"] == engine.ACTION_KEYS


# --- Attachments -------------------------------------------------------------------------------


@override_settings(STORAGES=IN_MEMORY)
def test_an_uploaded_document_rides_on_the_action(opened):
    child, _parent = _child_with_open_parent(opened)
    info = service.upload_attachment(opened, b"%PDF-1.4 letter", "C:\\docs\\escalation letter.pdf", user=None, by="S")
    assert info["name"] == "escalation letter.pdf"
    assert info["type"] == "application/pdf"
    _event(opened, child["key"], "e1", {"action": "escalate", "attachments": [{"id": info["id"], "name": "renamed"}]})
    ev = next(e for e in _rv(opened, child["id"])["events"] if e.get("id") == "e1")
    assert ev["payload"]["attachments"] == [info]


@override_settings(STORAGES=IN_MEMORY)
def test_only_this_workflows_uploads_can_be_attached(opened):
    child, _parent = _child_with_open_parent(opened)
    for bad in [[999999], ["1"], [True], "nope"]:
        with pytest.raises(service.Refused):
            _event(opened, child["key"], f"e-{bad}", {"action": "escalate", "attachments": bad})


@override_settings(STORAGES=IN_MEMORY)
def test_uploads_are_documents_of_a_bounded_size(opened):
    with pytest.raises(service.Refused, match="Attach a PDF"):
        service.upload_attachment(opened, b"<script>", "page.html", user=None, by="S")
    with pytest.raises(service.Refused, match="Attach a PDF"):
        service.upload_attachment(opened, b"x", "no-extension", user=None, by="S")
    with pytest.raises(service.Refused, match="empty"):
        service.upload_attachment(opened, b"", "a.pdf", user=None, by="S")
    with pytest.raises(service.Refused, match="over 4 MB"):
        service.upload_attachment(opened, b"x" * (service.MAX_ATTACHMENT_BYTES + 1), "a.pdf", user=None, by="S")


@override_settings(STORAGES=IN_MEMORY)
def test_upload_and_download_through_the_api(dao, opened, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "open_source", lambda definition, **scope: opened)
    request = RequestFactory().post(
        f"/labs/audit-reviews/api/{WORKFLOW}/files/?opportunity_id=10301&name=record.txt",
        data=b"escalated by phone",
        content_type="application/octet-stream",
    )
    request.user = _user()
    response = api_views.files_api(request, WORKFLOW)
    assert response.status_code == 200, response.content
    info = json.loads(response.content)["attachment"]

    request = RequestFactory().get(f"/labs/audit-reviews/api/{WORKFLOW}/files/{info['id']}/?opportunity_id=10301")
    request.user = _user()
    response = api_views.file_api(request, WORKFLOW, info["id"])
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"escalated by phone"
    assert response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"

    request = RequestFactory().get(f"/labs/audit-reviews/api/{WORKFLOW}/files/999999/?opportunity_id=10301")
    request.user = _user()
    assert api_views.file_api(request, WORKFLOW, 999999).status_code == 404
