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
def test_audit_bridge_sends_analytics_events(settings):
    """Successful writes/exports become Umami feature events; reads do not."""
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    from connect_labs.audit_trail import service
    from connect_labs.audit_trail.models import Action

    with patch("connect_labs.utils.server_analytics.send_event_task") as task:
        service.record(Action.CREATE, resource_type="workflow_run", labs_only=False)
        service.record(Action.READ, resource_type="workflow_run")
    names = [c.args[0] for c in task.delay.call_args_list]
    assert names == ["data_create"]
    assert task.delay.call_args_list[0].args[1] == {"resource_type": "workflow_run", "labs_only": False}
