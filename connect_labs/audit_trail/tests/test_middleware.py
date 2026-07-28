import pytest
from django.http import HttpResponse, HttpResponseForbidden

from connect_labs.audit_trail import service
from connect_labs.audit_trail.middleware import AuditTrailMiddleware
from connect_labs.audit_trail.models import Action, AuditEvent, Outcome, Source


@pytest.mark.django_db
def test_view_events_flushed_with_envelope_and_status(rf, user):
    def view(request):
        service.record(Action.LIST, resource_type="task", record_count=2)
        return HttpResponse("ok")

    request = rf.get("/labs/tasks/", HTTP_USER_AGENT="pytest-agent", HTTP_X_FORWARDED_FOR="10.1.2.3, 172.0.0.1")
    request.user = user
    response = AuditTrailMiddleware(view)(request)
    assert response.status_code == 200

    event = AuditEvent.objects.get(action=Action.LIST)
    assert event.action == Action.LIST
    assert event.user_id == user.pk
    assert event.username == user.username
    assert event.source == Source.WEB
    assert event.ip_address == "10.1.2.3"
    assert event.user_agent == "pytest-agent"
    assert event.path == "/labs/tasks/"
    assert event.status_code == 200
    assert event.request_id


@pytest.mark.django_db
def test_long_path_and_user_agent_reach_the_row_at_full_width(rf, user):
    """End-to-end through the real middleware: the producer hands over raw
    values and the row-boundary clamp is what bounds them."""
    limits = service._char_limits()
    request = rf.get("/labs/" + "a" * 500, HTTP_USER_AGENT="u" * 500)
    request.user = user
    AuditTrailMiddleware(lambda r: HttpResponse("ok"))(request)

    event = AuditEvent.objects.get(action=Action.PAGE_VIEW)
    assert len(event.path) == limits["path"]
    assert len(event.user_agent) == limits["user_agent"]


@pytest.mark.django_db
def test_403_records_access_denied(rf, user):
    def view(request):
        return HttpResponseForbidden("no")

    request = rf.get("/labs/secret/")
    request.user = user
    AuditTrailMiddleware(view)(request)

    event = AuditEvent.objects.get(action=Action.ACCESS_DENIED)
    assert event.outcome == Outcome.FAILURE
    assert event.status_code == 403
    assert event.path == "/labs/secret/"


@pytest.mark.django_db
def test_403_from_anonymous_is_not_recorded(rf):
    """Scanner traffic must not land in the access-denied record.

    §164.308(a)(1)(ii)(D) is about a known workforce member being refused; an
    anonymous 403 is a stranger. In prod every recorded denial over the first
    four days was unauthenticated — bare `/` hits and /admin/*.php probes —
    which is exactly the noise that makes the card unreadable.
    """
    from django.contrib.auth.models import AnonymousUser

    def view(request):
        return HttpResponseForbidden("no")

    request = rf.get("/admin/index.php")
    request.user = AnonymousUser()
    AuditTrailMiddleware(view)(request)

    assert not AuditEvent.objects.filter(action=Action.ACCESS_DENIED).exists()


@pytest.mark.django_db
def test_buffered_events_keep_their_own_timestamps(rf, user):
    """occurred_at is the time of the ACCESS, not of the flush.

    Under auto_now_add, bulk_create stamped every event in a request identically,
    so a request making hundreds of sequential API calls recorded them all as one
    instant and intra-request ordering was lost.
    """

    def view(request):
        service.record(Action.READ, resource_type="first")
        service.record(Action.READ, resource_type="second")
        return HttpResponse("ok")

    request = rf.get("/labs/thing/")
    request.user = user
    AuditTrailMiddleware(view)(request)

    first = AuditEvent.objects.get(resource_type="first")
    second = AuditEvent.objects.get(resource_type="second")
    assert first.occurred_at < second.occurred_at


@pytest.mark.django_db
def test_exception_still_flushes_buffer(rf, user):
    def view(request):
        service.record(Action.READ, resource_type="task", resource_id=7)
        raise RuntimeError("view blew up")

    request = rf.get("/labs/tasks/7/")
    request.user = user
    with pytest.raises(RuntimeError):
        AuditTrailMiddleware(view)(request)

    event = AuditEvent.objects.get()
    assert event.action == Action.READ
    assert event.status_code == 500


@pytest.mark.django_db
def test_context_reset_after_request(rf, user):
    from connect_labs.audit_trail.context import get_audit_context

    def view(request):
        return HttpResponse("ok")

    request = rf.get("/")
    request.user = user
    AuditTrailMiddleware(view)(request)
    assert get_audit_context() is None
