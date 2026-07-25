"""Query-string capture + page_view events — the per-user session
reconstruction layer."""
import pytest
from django.http import HttpResponse, JsonResponse

from connect_labs.audit_trail import service
from connect_labs.audit_trail.middleware import AuditTrailMiddleware
from connect_labs.audit_trail.models import Action, AuditEvent


def test_redact_query_string_keeps_identifiers_redacts_free_text():
    out = service.redact_query_string("username=alice&status=open&q=typed+patient+name&entity_id=42")
    assert "username=alice" in out
    assert "status=open" in out
    assert "entity_id=42" in out
    assert "patient" not in out
    assert "q=%5Bredacted%5D" in out


def test_redact_query_string_empty_and_caps():
    assert service.redact_query_string("") == ""
    assert len(service.redact_query_string("a=" + "x" * 2000)) <= 500


@pytest.mark.django_db
def test_page_view_recorded_with_query_string(rf, user):
    def view(request):
        return HttpResponse("<html>ok</html>", content_type="text/html; charset=utf-8")

    request = rf.get("/tasks/?username=alice&q=secret+text")
    request.user = user
    AuditTrailMiddleware(view)(request)

    event = AuditEvent.objects.get(action=Action.PAGE_VIEW)
    assert event.path == "/tasks/"
    assert "username=alice" in event.query_string
    assert "secret" not in event.query_string
    assert event.user_id == user.pk


@pytest.mark.django_db
def test_no_page_view_for_json_htmx_anonymous_or_post(rf, user, django_user_model):
    from django.contrib.auth.models import AnonymousUser

    def html_view(request):
        return HttpResponse("<html>ok</html>", content_type="text/html")

    def json_view(request):
        return JsonResponse({"ok": True})

    # JSON response
    request = rf.get("/api/data/")
    request.user = user
    AuditTrailMiddleware(json_view)(request)
    # htmx partial
    request = rf.get("/tasks/", HTTP_HX_REQUEST="true")
    request.user = user
    AuditTrailMiddleware(html_view)(request)
    # anonymous
    request = rf.get("/labs/login/")
    request.user = AnonymousUser()
    AuditTrailMiddleware(html_view)(request)
    # POST
    request = rf.post("/tasks/")
    request.user = user
    AuditTrailMiddleware(html_view)(request)

    assert AuditEvent.objects.filter(action=Action.PAGE_VIEW).count() == 0


@pytest.mark.django_db
def test_data_events_carry_query_string(rf, user):
    def view(request):
        service.record(Action.LIST, resource_type="task", record_count=1)
        return HttpResponse("<html>ok</html>", content_type="text/html")

    request = rf.get("/tasks/?status=open")
    request.user = user
    AuditTrailMiddleware(view)(request)

    list_event = AuditEvent.objects.get(action=Action.LIST)
    assert list_event.query_string == "status=open"


@pytest.mark.django_db
def test_dashboard_hides_page_views_by_default(client, django_user_model):
    admin = django_user_model.objects.create(username="staff", email="staff@dimagi.com")
    AuditEvent.objects.create(action=Action.PAGE_VIEW, resource_type="page", username="alice")
    AuditEvent.objects.create(action=Action.READ, resource_type="task", username="alice")
    client.force_login(admin)
    from django.urls import reverse

    url = reverse("audit_trail:dashboard")
    # Filter to alice so the test client's own page_view/login events don't
    # muddy the counts.
    default_total = client.get(url, {"username": "alice"}).context["total_events"]
    with_views = client.get(url, {"username": "alice", "include_page_views": "1"}).context["total_events"]
    assert default_total == 1  # the READ only
    assert with_views == 2  # READ + page_view
    # Explicitly filtering FOR page_view also surfaces them
    only_views = client.get(url, {"username": "alice", "action": "page_view"}).context["total_events"]
    assert only_views == 1
