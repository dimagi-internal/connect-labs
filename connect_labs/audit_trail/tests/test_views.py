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


# --- capped event count -------------------------------------------------------
#
# Paginator.count was a full COUNT(*) over every audit row ever written: 15.7s
# cold / 0.4s warm on production's 2.58M rows, and the ONE cost that resizing the
# database did not improve. These pin that small result sets still report an exact
# total, and that past the cap the page reports a floor instead of paying for a
# full scan.


@pytest.mark.django_db
def test_count_is_exact_below_the_cap(client, admin_user, monkeypatch):
    from connect_labs.audit_trail import views

    monkeypatch.setattr(views.CappedPaginator, "cap", 100)
    AuditEvent.objects.create(action=Action.READ, resource_type="task", username="alice")
    client.force_login(admin_user)  # note: logging in records a LOGIN event too

    response = client.get(reverse("audit_trail:dashboard"))

    # Below the cap the total must equal what an exact count would return, so
    # assert against the queryset rather than a hardcoded number.
    expected = (
        AuditEvent.objects.filter(labs_only=False)
        .exclude(action__in=[Action.CANARY, Action.PAGE_VIEW])
        .count()
    )
    assert response.context["total_events"] == expected
    assert response.context["total_events_capped"] is False


@pytest.mark.django_db
def test_count_reports_a_floor_at_the_cap(client, admin_user, monkeypatch):
    from connect_labs.audit_trail import views

    monkeypatch.setattr(views.CappedPaginator, "cap", 2)
    for i in range(5):
        AuditEvent.objects.create(action=Action.READ, resource_type="task", username=f"u{i}")
    client.force_login(admin_user)

    response = client.get(reverse("audit_trail:dashboard"))

    assert response.context["total_events"] == 2
    assert response.context["total_events_capped"] is True
    assert "2+ events" in response.content.decode()


@pytest.mark.django_db
def test_capped_count_stops_one_past_the_cap(monkeypatch):
    """Counting cap+1 is how "exactly cap" is told apart from "more than cap"."""
    from connect_labs.audit_trail import views

    monkeypatch.setattr(views.CappedPaginator, "cap", 3)
    for i in range(10):
        AuditEvent.objects.create(action=Action.READ, resource_type="task", username=f"u{i}")

    paginator = views.CappedPaginator(AuditEvent.objects.all(), 50)

    assert paginator.count == 4
    assert paginator.is_capped is True
    # The LIMIT is the whole point -- without it this is a full-table scan.
    assert "LIMIT 4" in str(AuditEvent.objects.all()[:4].query)
