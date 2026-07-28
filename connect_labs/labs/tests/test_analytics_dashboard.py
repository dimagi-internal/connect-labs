from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create(username="staff", email="staff@dimagi.com")


@pytest.mark.django_db
def test_analytics_dashboard_renders(client, admin_user, settings):
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    settings.UMAMI_ADMIN_PASSWORD = "pw"
    client.force_login(admin_user)
    with (
        patch(
            "connect_labs.utils.umami_api.website_stats",
            return_value={"pageviews": 42, "visitors": 7, "visits": 9, "bounces": 1},
        ),
        patch("connect_labs.utils.umami_api.active_visitors", return_value=3),
        patch(
            "connect_labs.utils.umami_api.pageviews_series",
            return_value={"pageviews": [{"x": "2026-07-24", "y": 42}], "sessions": [{"x": "2026-07-24", "y": 9}]},
        ),
        patch(
            "connect_labs.utils.umami_api.metrics",
            return_value=[{"x": "/labs/overview/", "y": 20}],
        ),
    ):
        response = client.get(reverse("labs_admin:analytics_dashboard"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Labs Analytics" in content
    assert "{#" not in content
    assert "/labs/overview/" in content


@pytest.mark.django_db
def test_analytics_dashboard_degrades_when_unconfigured(client, admin_user, settings):
    settings.UMAMI_HOST_URL = ""
    settings.UMAMI_WEBSITE_ID = ""
    settings.UMAMI_ADMIN_PASSWORD = ""
    client.force_login(admin_user)
    response = client.get(reverse("labs_admin:analytics_dashboard"))
    assert response.status_code == 200
    assert "not configured" in response.content.decode()


@pytest.mark.django_db
def test_analytics_dashboard_forbidden_for_external(client, django_user_model):
    external = django_user_model.objects.create(username="partner", email="p@example.org")
    client.force_login(external)
    assert client.get(reverse("labs_admin:analytics_dashboard")).status_code == 403


@pytest.mark.django_db
def test_dashboard_aggregates_full_urls_from_page_views(client, admin_user, settings):
    settings.UMAMI_HOST_URL = ""
    from connect_labs.audit_trail.models import Action, AuditEvent

    for _ in range(3):
        AuditEvent.objects.create(action=Action.PAGE_VIEW, path="/tasks/", query_string="status=open")
    AuditEvent.objects.create(action=Action.PAGE_VIEW, path="/tasks/", query_string="status=done")
    client.force_login(admin_user)
    response = client.get(reverse("labs_admin:analytics_dashboard"))
    urls = {(r["path"], r["query_string"]): r["n"] for r in response.context["top_full_urls"]}
    assert urls[("/tasks/", "status=open")] == 3
    assert urls[("/tasks/", "status=done")] == 1


@pytest.mark.django_db
def test_umami_sso_bridge(client, admin_user, settings):
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    settings.UMAMI_ADMIN_PASSWORD = "pw"
    from connect_labs.audit_trail.models import Action, AuditEvent

    client.force_login(admin_user)
    with patch("connect_labs.utils.umami_api.get_admin_token", return_value="jwt-token-abc"):
        response = client.get(reverse("labs_admin:umami_sso"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "jwt-token-abc" in content
    assert "umami.auth" in content
    assert AuditEvent.objects.filter(action=Action.READ, resource_type="umami_dashboard").exists()


@pytest.mark.django_db
def test_umami_sso_forbidden_for_external(client, django_user_model):
    external = django_user_model.objects.create(username="partner", email="p@example.org")
    client.force_login(external)
    assert client.get(reverse("labs_admin:umami_sso")).status_code == 403


@pytest.mark.django_db
def test_audit_bridge_sends_analytics_events(settings):
    """Successful HUMAN writes/exports become Umami feature events; reads do not.

    Two dimensions gate the mirror: the action must be a write, and the source
    must be a person (web/mcp). Source.SYSTEM — the default when record() runs
    with no audit context at all — is deliberately NOT mirrored: every such write
    in prod was an unattributed machine export. A human-driven write always
    carries a context (the web middleware or the MCP seam sets one), so a write
    reaching here as SYSTEM means no request drove it.
    """
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    from connect_labs.audit_trail import service
    from connect_labs.audit_trail.context import audit_context
    from connect_labs.audit_trail.models import Action, Source

    with patch("connect_labs.utils.server_analytics.send_event_task") as task:
        with audit_context(source=Source.WEB, request_id="req-1"):
            service.record(Action.CREATE, resource_type="workflow_run", labs_only=False)
            service.record(Action.READ, resource_type="workflow_run")
    names = [c.args[0] for c in task.delay.call_args_list]
    assert names == ["data_create"]
    assert task.delay.call_args_list[0].args[1] == {"resource_type": "workflow_run", "labs_only": False}


@pytest.mark.django_db
def test_audit_bridge_ignores_contextless_writes(settings):
    """A write with no audit context (Source.SYSTEM) is audited but not mirrored."""
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    from connect_labs.audit_trail import service
    from connect_labs.audit_trail.models import Action

    with patch("connect_labs.utils.server_analytics.send_event_task") as task:
        service.record(Action.EXPORT, resource_type="user_visits", record_count=250)
    assert task.delay.call_args_list == []
