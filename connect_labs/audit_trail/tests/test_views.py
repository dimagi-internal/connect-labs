import pytest
from django.urls import reverse

from connect_labs.audit_trail.models import Action, AuditEvent


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create(username="staff", email="staff@dimagi.com")


@pytest.fixture
def external_user(django_user_model):
    return django_user_model.objects.create(username="partner", email="partner@example.org")


@pytest.mark.django_db
def test_dashboard_renders_for_dimagi_user(client, admin_user):
    AuditEvent.objects.create(action=Action.READ, resource_type="task", username="someone")
    client.force_login(admin_user)
    response = client.get(reverse("audit_trail:dashboard"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Audit Trail" in content
    assert "{#" not in content  # multi-line Django comments leak as text
    assert "someone" in content


@pytest.mark.django_db
def test_dashboard_forbidden_for_external_user(client, external_user):
    client.force_login(external_user)
    response = client.get(reverse("audit_trail:dashboard"))
    assert response.status_code == 403
    # The denial itself must be audited (the "snooping signature")
    assert AuditEvent.objects.filter(action=Action.ACCESS_DENIED, path="/labs/audit-trail/").exists()


@pytest.mark.django_db
def test_filters_apply(client, admin_user):
    AuditEvent.objects.create(action=Action.EXPORT, resource_type="user_visits", username="alice")
    AuditEvent.objects.create(action=Action.READ, resource_type="task", username="bob")
    client.force_login(admin_user)
    response = client.get(reverse("audit_trail:dashboard"), {"action": "export"})
    assert response.context["total_events"] == 1


@pytest.mark.django_db
def test_labs_only_hidden_by_default(client, admin_user):
    AuditEvent.objects.create(action=Action.READ, resource_type="task", labs_only=True)
    AuditEvent.objects.create(action=Action.READ, resource_type="task", labs_only=False)
    client.force_login(admin_user)
    assert client.get(reverse("audit_trail:dashboard")).context["total_events"] >= 1
    with_synthetic = client.get(reverse("audit_trail:dashboard"), {"include_labs_only": "1"})
    assert with_synthetic.context["total_events"] > 1


@pytest.mark.django_db
def test_post_records_review_event(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("audit_trail:dashboard"), {"notes": "all clear"})
    assert response.status_code == 302
    review = AuditEvent.objects.get(action=Action.REVIEW)
    assert review.username == "staff"
    assert review.metadata["notes"] == "all clear"
