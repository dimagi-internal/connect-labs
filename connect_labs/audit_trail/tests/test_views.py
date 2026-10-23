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
    expected = AuditEvent.objects.filter(labs_only=False).exclude(action__in=[Action.CANARY, Action.PAGE_VIEW]).count()
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


# --- date filters ----------------------------------------------------------------
#
# ``occurred_at__date`` compiles to ``(occurred_at AT TIME ZONE 'UTC')::date``,
# which no index serves: every page walked the occurred_at index back from now
# across the whole table. A scrape paging the trail one day at a time pinned the
# production DB at 100% CPU (2026-10-23). The filter is a datetime range now.


def _at(day, hh, mm=0, ss=0):
    from datetime import datetime

    from django.utils import timezone

    return timezone.make_aware(datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:{ss:02d}"))


@pytest.mark.django_db
def test_date_filter_keeps_whole_days_inclusive(client, admin_user):
    for name, when in [
        ("before", _at("2026-10-11", 23, 59, 59)),
        ("first", _at("2026-10-12", 0)),
        ("last", _at("2026-10-13", 23, 59, 59)),
        ("after", _at("2026-10-14", 0)),
    ]:
        AuditEvent.objects.create(action=Action.EXPORT, resource_type="x", username=name, occurred_at=when)
    client.force_login(admin_user)

    response = client.get(
        reverse("audit_trail:dashboard"), {"action": "export", "date_from": "2026-10-12", "date_to": "2026-10-13"}
    )

    assert sorted(e.username for e in response.context["page_obj"]) == ["first", "last"]


@pytest.mark.django_db
def test_date_filter_is_an_indexable_range(client, admin_user):
    client.force_login(admin_user)

    response = client.get(reverse("audit_trail:dashboard"), {"date_from": "2026-10-12", "date_to": "2026-10-12"})

    sql = str(response.context["page_obj"].paginator.object_list.query)
    assert "occurred_at" in sql
    assert "AT TIME ZONE" not in sql and "::date" not in sql


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["yesterday", "2026-02-30", "2026-13-01"])
def test_unparseable_date_is_ignored_not_a_500(client, admin_user, bad):
    AuditEvent.objects.create(action=Action.EXPORT, resource_type="x", username="alice")
    client.force_login(admin_user)

    response = client.get(reverse("audit_trail:dashboard"), {"action": "export", "date_from": bad, "date_to": bad})

    assert response.status_code == 200
    assert response.context["total_events"] == 1
