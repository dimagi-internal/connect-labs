"""Opportunity Tracker — query correctness and access gating.

Covers the corrections that mattered when porting the real Superset SQL: visit
counts must come from PulseRollup (not PulseWork's payment-unit counts), the
Funder bucket is a name-substring rule ported verbatim, an org with no known
name still renders via the slug fallback, and pivot subtotals/totals reconcile
against the grid.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.admin import opportunity_tracker as ot
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.pulse.models import PulseOpportunity, PulseOrganization, PulseRollup, PulseWork
from connect_labs.users.models import User


@pytest.fixture
def dimagi_user(db):
    return User.objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def external_user(db):
    return User.objects.create_user(username="ext", email="partner@external.com", password="pw")


def _make_opp(opp_id, **overrides):
    defaults = dict(
        opportunity_id=opp_id,
        name=f"Opp {opp_id}",
        org_slug="acme",
        program_id=1,
        country="NG",
        service_slug="chc",
        is_active=True,
        end_date=timezone.now().date() + timedelta(days=30),
        lifetime_visit_count=0,
    )
    defaults.update(overrides)
    return PulseOpportunity.objects.create(**defaults)


def _make_rollup(opp_id, *, status="approved", n=1, hours_ago=1):
    return PulseRollup.objects.create(
        bucket_hour=timezone.now() - timedelta(hours=hours_ago),
        opportunity_id=opp_id,
        status=status,
        n=n,
    )


def _make_work(opp_id, *, worker="w1", status="approved", org_slug="acme", paid=False, created=None):
    return PulseWork.objects.create(
        work_key=f"{opp_id}-{worker}-{status}-{paid}-{created}",
        opportunity_id=opp_id,
        org_slug=org_slug,
        worker_hash=worker,
        status=status,
        created_ts=created or timezone.now(),
        usd_to_worker=5 if paid else None,
        payment_date=timezone.now() if paid else None,
    )


# ---------------------------------------------------------------------------
# Funder / status business rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("GiveWell CHC Program", "GiveWell"),
        ("Mother Baby Wellness (Nigeria)", "GiveWell"),
        ("Founders Pledge Malaria RDT", "Founders Pledge"),
        ("Sujukwa - Child Health Campaign", "Founders Pledge"),
        ("KMC - Kampala Round 3", "ECF"),
        ("Kangaroo Mother Care Rollout", "ECF"),
        ("Readers Distribution - NG", "Other Funder"),
    ],
)
def test_funder_for_matches_sql_case_examples(name, expected):
    assert ot.funder_for(name) == expected


def test_status_for_requires_both_active_flag_and_future_end_date(db):
    today = timezone.now().date()
    still_running = _make_opp(1, is_active=True, end_date=today + timedelta(days=1))
    flag_true_but_expired = _make_opp(2, is_active=True, end_date=today - timedelta(days=1))
    flag_false = _make_opp(3, is_active=False, end_date=today + timedelta(days=30))
    no_end_date = _make_opp(4, is_active=True, end_date=None)

    assert ot.status_for(still_running) == "Active"
    assert ot.status_for(flag_true_but_expired) == "Inactive"
    assert ot.status_for(flag_false) == "Inactive"
    assert ot.status_for(no_end_date) == "Inactive"


# ---------------------------------------------------------------------------
# Detail rows -- the PulseRollup-vs-PulseWork correction
# ---------------------------------------------------------------------------


def test_detail_rows_source_visit_counts_from_rollup_not_work(db):
    """A payment-unit undercount in PulseWork must not leak into visit counts."""
    _make_opp(765, name="Mother Baby Wellness (Nigeria)")
    # One work row can represent several visits (e.g. KMC's ~0.23 ratio) --
    # PulseWork.completed_count is irrelevant here on purpose.
    _make_work(765, worker="flw-a", status="approved")
    _make_rollup(765, status="approved", n=10)
    _make_rollup(765, status="pending", n=2)

    rows = ot.opportunity_detail_rows(status="all")
    row = next(r for r in rows if r["opportunity_id"] == 765)

    assert row["visits_claimed"] == 12  # sum across all statuses
    assert row["visits_approved"] == 10
    assert row["visits_pending"] == 2
    assert row["flws"] == 1
    assert row["funder"] == "GiveWell"


def test_detail_rows_org_name_falls_back_to_slug_when_unnamed(db):
    """Most partners are visible only as a slug -- must render, not error."""
    _make_opp(1, org_slug="unnamed-partner")
    PulseOrganization.objects.create(slug="named-partner", name="Named Partner LLC")
    _make_opp(2, org_slug="named-partner")

    rows = {r["opportunity_id"]: r for r in ot.opportunity_detail_rows(status="all")}
    assert rows[1]["llo"] == "unnamed-partner"
    assert rows[2]["llo"] == "Named Partner LLC"


def test_detail_rows_approved_7d_window(db):
    _make_opp(1)
    _make_rollup(1, status="approved", n=5, hours_ago=2)  # inside 7d
    _make_rollup(1, status="approved", n=100, hours_ago=24 * 10)  # outside 7d

    rows = ot.opportunity_detail_rows(status="all")
    row = next(r for r in rows if r["opportunity_id"] == 1)
    assert row["approved_7d"] == 5
    assert row["visits_approved"] == 105


def test_detail_rows_amount_paid_only_counts_works_with_a_payment_date(db):
    _make_opp(1)
    _make_work(1, worker="a", paid=True, created=timezone.now())
    _make_work(1, worker="b", paid=False, created=timezone.now())  # accrued, not disbursed

    rows = ot.opportunity_detail_rows(status="all")
    row = next(r for r in rows if r["opportunity_id"] == 1)
    assert row["amount_paid"] == 5.0


def test_detail_rows_status_filter(db):
    today = timezone.now().date()
    _make_opp(1, is_active=True, end_date=today + timedelta(days=10))
    _make_opp(2, is_active=False, end_date=today + timedelta(days=10))

    active_only = ot.opportunity_detail_rows(status="Active")
    assert [r["opportunity_id"] for r in active_only] == [1]

    inactive_only = ot.opportunity_detail_rows(status="Inactive")
    assert [r["opportunity_id"] for r in inactive_only] == [2]


# ---------------------------------------------------------------------------
# Cohort pivot -- subtotal/total reconciliation
# ---------------------------------------------------------------------------


def test_cohort_pivot_subtotals_and_grand_total_reconcile(db):
    _make_opp(1, country="NG", service_slug="chc", org_slug="org-a")
    _make_opp(2, country="NG", service_slug="kmc", org_slug="org-b")
    _make_opp(3, country="KE", service_slug="chc", org_slug="org-a")
    for opp_id, n in ((1, 10), (2, 20), (3, 5)):
        _make_rollup(opp_id, status="approved", n=n)

    pivot = ot.cohort_pivot()
    services = [s["slug"] for s in pivot["services"]]
    ng_row = next(r for r in pivot["rows"] if r["country"] == "NG")
    ke_row = next(r for r in pivot["rows"] if r["country"] == "KE")

    assert ng_row["subtotal"]["visits"] == 30
    assert ke_row["subtotal"]["visits"] == 5
    assert pivot["grand_total"]["visits"] == 35
    # Column totals must sum to the same grand total, from the other axis.
    assert sum(ct["visits"] for ct in pivot["col_totals"]) == 35
    # org-a delivers both NG/chc and KE/chc -- counted once in the grand total.
    assert pivot["grand_total"]["orgs"] == 2
    chc_col_total = pivot["col_totals"][services.index("chc")]
    assert chc_col_total["orgs"] == 1  # org-a, deduped across NG and KE


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


def test_running_visit_total_is_cumulative_across_days(db):
    _make_opp(1)
    two_days_ago = timezone.now() - timedelta(days=2)
    yesterday = timezone.now() - timedelta(days=1)
    PulseRollup.objects.create(bucket_hour=two_days_ago, opportunity_id=1, status="approved", n=3)
    PulseRollup.objects.create(bucket_hour=yesterday, opportunity_id=1, status="approved", n=4)

    points = ot.running_visit_total()
    assert [p["value"] for p in points] == [3, 7]


def test_daily_visits_and_users_marks_stale_days_as_absent_not_zero(db):
    _make_opp(1)
    stale_day = timezone.now() - timedelta(days=90)
    PulseRollup.objects.create(bucket_hour=stale_day, opportunity_id=1, status="approved", n=8)

    points = ot.daily_visits_and_users()
    stale_point = points[0]
    assert stale_point["approved_visits"] == 8
    assert stale_point["unique_users"] is None  # absent, not 0 -- no PulseEvent in range


# ---------------------------------------------------------------------------
# Access gating + page load
# ---------------------------------------------------------------------------


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_loads_for_dimagi_staff(client, dimagi_user, db):
    _make_opp(765, name="Mother Baby Wellness (Nigeria)")
    _make_rollup(765, status="approved", n=5)
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:opportunity_tracker"))
    assert resp.status_code == 200
    assert b"Opportunity Tracker" in resp.content
    assert b"Mother Baby Wellness" in resp.content


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_forbidden_for_external_user(client, external_user, db):
    client.force_login(external_user)
    resp = client.get(reverse("labs_admin:opportunity_tracker"))
    assert resp.status_code == 403


@override_settings(**LABS_SETTINGS)
def test_admin_index_lists_opportunity_tracker_tile(client, dimagi_user, db):
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:index"))
    assert resp.status_code == 200
    assert b"Opportunity Tracker" in resp.content
