from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.audit_trail.models import Action, AuditEvent
from connect_labs.audit_trail.timeline import build_session_timeline


def _mk(action, minutes_ago, request_id="", **kwargs):
    """Build an unsaved AuditEvent with a controlled timestamp."""
    event = AuditEvent(action=action, request_id=request_id, **kwargs)
    event.occurred_at = timezone.now() - timedelta(minutes=minutes_ago)
    return event


def test_sessions_split_on_idle_gap():
    events = [
        _mk(Action.PAGE_VIEW, 120, "r1", path="/labs/overview/"),
        _mk(Action.PAGE_VIEW, 118, "r2", path="/tasks/"),
        # 90-minute gap → new session
        _mk(Action.PAGE_VIEW, 20, "r3", path="/labs/workflow/"),
    ]
    sessions = build_session_timeline(events)
    assert len(sessions) == 2
    assert sessions[0]["pages"] == 2
    assert sessions[1]["pages"] == 1


def test_data_events_nest_under_their_page_request():
    events = [
        _mk(Action.PAGE_VIEW, 10, "r1", path="/labs/workflow/7479/run/"),
        _mk(Action.READ, 10, "r1", resource_type="workflow_definition", record_count=1),
        _mk(Action.EXPORT, 10, "r1", resource_type="user_visits", record_count=2500),
        _mk(Action.CREATE, 9, "r2", resource_type="workflow_run"),  # background (no page_view)
    ]
    sessions = build_session_timeline(events)
    assert len(sessions) == 1
    session = sessions[0]
    assert session["rows_exported"] == 2500
    assert session["writes"] == 1
    page_step = session["steps"][0]
    assert page_step["kind"] == "page"
    assert page_step["title"] == "/labs/workflow/7479/run/"
    assert len(page_step["events"]) == 2
    background_step = session["steps"][1]
    assert background_step["kind"] == "background"


def test_auth_steps_marked():
    events = [_mk(Action.LOGIN, 5, "r1", resource_type="auth")]
    sessions = build_session_timeline(events)
    assert sessions[0]["steps"][0]["kind"] == "auth"


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create(username="staff", email="staff@dimagi.com")


@pytest.mark.django_db
def test_timeline_view_renders_and_audits_itself(client, admin_user):
    AuditEvent.objects.create(action=Action.PAGE_VIEW, resource_type="page", username="alice", path="/tasks/")
    AuditEvent.objects.create(action=Action.EXPORT, resource_type="user_visits", username="alice", record_count=100)
    client.force_login(admin_user)
    response = client.get(reverse("audit_trail:session_timeline"), {"username": "alice"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "/tasks/" in content
    assert "user_visits" in content
    assert "{#" not in content
    # Investigating alice is itself on the record, attributed to staff
    meta_event = AuditEvent.objects.get(action=Action.READ, resource_type="session_timeline")
    assert meta_event.metadata["viewed_username"] == "alice"


@pytest.mark.django_db
def test_timeline_view_user_picker_without_username(client, admin_user):
    AuditEvent.objects.create(action=Action.READ, resource_type="task", username="alice")
    client.force_login(admin_user)
    response = client.get(reverse("audit_trail:session_timeline"))
    assert response.status_code == 200
    assert "alice" in response.content.decode()
    assert not AuditEvent.objects.filter(resource_type="session_timeline").exists()


@pytest.mark.django_db
def test_timeline_forbidden_for_external(client, django_user_model):
    external = django_user_model.objects.create(username="partner", email="p@example.org")
    client.force_login(external)
    assert client.get(reverse("audit_trail:session_timeline")).status_code == 403
