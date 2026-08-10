"""Opportunity Tracker — query correctness and access gating.

Covers the corrections that mattered when porting the real Superset SQL: visit
counts must come from PulseRollup (not PulseWork's payment-unit counts), the
Funder bucket is a name-substring rule ported verbatim, an org with no known
name still renders via the slug fallback, pivot subtotals/totals reconcile
against the grid, and a day with FLW activity but no approved visit yet still
shows up (rather than being silently dropped from the chart).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.admin import opportunity_tracker as ot
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseOrganization, PulseRollup, PulseWork
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


def _make_event(opp_id, *, worker="w1", field_ts=None):
    return PulseEvent.objects.create(
        connect_visit_id=hash((opp_id, worker, field_ts)) & 0x7FFFFFFF,
        opportunity_id=opp_id,
        field_ts=field_ts or timezone.now(),
        sync_ts=field_ts or timezone.now(),
        status="pending",
        worker_hash=worker,
    )


def _status_filtered(opps, status):
    """Mirrors the view's own status narrowing, done once per (opps, status)."""
    if status in (None, "all"):
        return list(opps)
    return [o for o in opps if ot.status_for(o) == status]


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


@pytest.mark.parametrize("code,expected", [("NG", "Nigeria"), ("KE", "Kenya"), ("", ""), ("ZZ", "ZZ")])
def test_country_label_falls_back_to_the_code_when_unnamed(code, expected):
    """Never invents a label -- an unmapped code renders as itself, same
    fallback philosophy as service_label() for delivery types."""
    assert ot.country_label(code) == expected


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
    opp = _make_opp(765, name="Mother Baby Wellness (Nigeria)")
    # One work row can represent several visits (e.g. KMC's ~0.23 ratio) --
    # PulseWork.completed_count is irrelevant here on purpose.
    _make_work(765, worker="flw-a", status="approved")
    _make_rollup(765, status="approved", n=10)
    _make_rollup(765, status="pending", n=2)

    rows = ot.opportunity_detail_rows([opp])
    row = next(r for r in rows if r["opportunity_id"] == 765)

    assert row["visits_claimed"] == 12  # sum across all statuses
    assert row["visits_approved"] == 10
    assert row["visits_pending"] == 2
    assert row["flws"] == 1
    assert row["funder"] == "GiveWell"


def test_detail_rows_org_name_falls_back_to_slug_when_unnamed(db):
    """Most partners are visible only as a slug -- must render, not error."""
    opp1 = _make_opp(1, org_slug="unnamed-partner")
    PulseOrganization.objects.create(slug="named-partner", name="Named Partner LLC")
    opp2 = _make_opp(2, org_slug="named-partner")

    rows = {r["opportunity_id"]: r for r in ot.opportunity_detail_rows([opp1, opp2])}
    assert rows[1]["llo"] == "unnamed-partner"
    assert rows[2]["llo"] == "Named Partner LLC"


def test_detail_rows_approved_7d_window(db):
    opp = _make_opp(1)
    _make_rollup(1, status="approved", n=5, hours_ago=2)  # inside 7d
    _make_rollup(1, status="approved", n=100, hours_ago=24 * 10)  # outside 7d

    rows = ot.opportunity_detail_rows([opp])
    row = next(r for r in rows if r["opportunity_id"] == 1)
    assert row["approved_7d"] == 5
    assert row["visits_approved"] == 105


def test_detail_rows_amount_paid_only_counts_works_with_a_payment_date(db):
    opp = _make_opp(1)
    _make_work(1, worker="a", paid=True, created=timezone.now())
    _make_work(1, worker="b", paid=False, created=timezone.now())  # accrued, not disbursed

    rows = ot.opportunity_detail_rows([opp])
    row = next(r for r in rows if r["opportunity_id"] == 1)
    assert row["amount_paid"] == 5.0
    assert row["has_payment_data"] is True


def test_detail_rows_distinguishes_genuine_zero_paid_from_no_payment_data(db):
    """Regression: a real $0-paid-so-far total must not look identical to
    'no payment data exists', or an admin reads a healthy pipeline as broken."""
    opp_no_data = _make_opp(1)  # no PulseWork rows at all
    opp_zero_paid = _make_opp(2)
    _make_work(2, worker="a", paid=True, created=timezone.now())
    PulseWork.objects.filter(opportunity_id=2).update(usd_to_worker=0)  # a real $0

    rows = {r["opportunity_id"]: r for r in ot.opportunity_detail_rows([opp_no_data, opp_zero_paid])}
    assert rows[1]["has_payment_data"] is False
    assert rows[2]["has_payment_data"] is True
    assert rows[2]["amount_paid"] == 0.0


def test_detail_rows_status_filter(db):
    today = timezone.now().date()
    active = _make_opp(1, is_active=True, end_date=today + timedelta(days=10))
    inactive = _make_opp(2, is_active=False, end_date=today + timedelta(days=10))
    opps = [active, inactive]

    active_only = ot.opportunity_detail_rows(_status_filtered(opps, "Active"))
    assert [r["opportunity_id"] for r in active_only] == [1]

    inactive_only = ot.opportunity_detail_rows(_status_filtered(opps, "Inactive"))
    assert [r["opportunity_id"] for r in inactive_only] == [2]


# ---------------------------------------------------------------------------
# Cohort pivot -- subtotal/total reconciliation, and agreement with the
# detail table it sits beside on the same tab
# ---------------------------------------------------------------------------


def test_cohort_pivot_subtotals_and_grand_total_reconcile(db):
    opp1 = _make_opp(1, country="NG", service_slug="chc", org_slug="org-a")
    opp2 = _make_opp(2, country="NG", service_slug="kmc", org_slug="org-b")
    opp3 = _make_opp(3, country="KE", service_slug="chc", org_slug="org-a")
    for opp_id, n in ((1, 10), (2, 20), (3, 5)):
        _make_rollup(opp_id, status="approved", n=n)

    pivot = ot.cohort_pivot([opp1, opp2, opp3])
    services = [s["slug"] for s in pivot["services"]]
    ng_row = next(r for r in pivot["rows"] if r["country"] == "Nigeria")
    ke_row = next(r for r in pivot["rows"] if r["country"] == "Kenya")

    assert ng_row["subtotal"]["visits"] == 30
    assert ke_row["subtotal"]["visits"] == 5
    assert pivot["grand_total"]["visits"] == 35
    # Column totals must sum to the same grand total, from the other axis.
    assert sum(ct["visits"] for ct in pivot["col_totals"]) == 35
    # org-a delivers both NG/chc and KE/chc -- counted once in the grand total.
    assert pivot["grand_total"]["orgs"] == 2
    chc_col_total = pivot["col_totals"][services.index("chc")]
    assert chc_col_total["orgs"] == 1  # org-a, deduped across NG and KE


def test_cohort_pivot_counts_an_org_with_zero_approved_visits_yet(db):
    """Regression: an in-scope opportunity with no approved rollup row at all
    (e.g. newly onboarded, everything still pending) must still count its org
    in the Orgs column, not vanish because it never appeared in visits_rows."""
    opp = _make_opp(1, country="NG", service_slug="chc", org_slug="new-org")
    # No _make_rollup() call at all -- nothing approved for this opp yet.

    pivot = ot.cohort_pivot([opp])
    ng_row = next(r for r in pivot["rows"] if r["country"] == "Nigeria")
    assert ng_row["cells"][0]["orgs_count"] == 1
    assert ng_row["subtotal"]["orgs"] == 1
    assert pivot["grand_total"]["orgs"] == 1


def test_cohort_pivot_honors_the_same_status_filter_as_the_detail_table(db):
    """Regression: the pivot must reflect whatever's already filtered out of
    the opportunity list beside it, not re-query the whole unfiltered table."""
    today = timezone.now().date()
    active = _make_opp(1, is_active=True, end_date=today + timedelta(days=10), country="NG", service_slug="chc")
    inactive = _make_opp(2, is_active=False, end_date=today + timedelta(days=10), country="NG", service_slug="chc")
    _make_rollup(1, status="approved", n=10)
    _make_rollup(2, status="approved", n=999)

    active_only_pivot = ot.cohort_pivot(_status_filtered([active, inactive], "Active"))
    assert active_only_pivot["grand_total"]["visits"] == 10  # inactive opp's 999 excluded


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


def test_running_visit_total_is_cumulative_across_days(db):
    _make_opp(1)
    two_days_ago = timezone.now() - timedelta(days=2)
    yesterday = timezone.now() - timedelta(days=1)
    PulseRollup.objects.create(bucket_hour=two_days_ago, opportunity_id=1, status="approved", n=3)
    PulseRollup.objects.create(bucket_hour=yesterday, opportunity_id=1, status="approved", n=4)

    points = ot.running_visit_total([1])
    assert [p["value"] for p in points] == [3, 7]


def test_daily_visits_and_users_marks_stale_days_as_absent_not_zero(db):
    _make_opp(1)
    stale_day = timezone.now() - timedelta(days=90)
    PulseRollup.objects.create(bucket_hour=stale_day, opportunity_id=1, status="approved", n=8)

    points = ot.daily_visits_and_users([1])
    stale_point = points[0]
    assert stale_point["approved_visits"] == 8
    assert stale_point["unique_users"] is None  # absent, not 0 -- no PulseEvent in range


def test_daily_visits_and_users_keeps_a_day_with_activity_but_no_approved_visit(db):
    """Regression: a day where FLWs worked but nothing's approved yet must
    still appear with approved_visits=0, not be dropped from the series."""
    _make_opp(1)
    today = timezone.now()
    _make_event(1, worker="flw-a", field_ts=today)  # pending, not approved -- no rollup row for today

    points = ot.daily_visits_and_users([1])
    today_point = next(p for p in points if p["t"] == today.date().isoformat())
    assert today_point["approved_visits"] == 0
    assert today_point["unique_users"] == 1


def test_monthly_visits_by_country_folds_long_tail_into_other(db):
    opp_ng = _make_opp(1, country="NG")
    opp_ke = _make_opp(2, country="KE")
    opp_ug = _make_opp(3, country="UG")
    _make_rollup(1, status="approved", n=100)
    _make_rollup(2, status="approved", n=50)
    _make_rollup(3, status="approved", n=5)

    result = ot.monthly_visits_by_country([opp_ng, opp_ke, opp_ug], top_n=2)
    assert result["countries"] == ["Nigeria", "Kenya", "Other"]
    month = result["series"][0]
    assert month["values"]["Nigeria"] == 100
    assert month["values"]["Kenya"] == 50
    assert month["values"]["Other"] == 5  # Uganda folded in, not given its own slot


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


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_empty_status_param_shows_all_not_nothing(client, dimagi_user, db):
    """Regression: ?status= (empty, e.g. from a bookmarked/stale link) must
    behave like no filter, not silently match zero opportunities."""
    _make_opp(1, name="Some Opp")
    client.force_login(dimagi_user)

    resp = client.get(reverse("labs_admin:opportunity_tracker"), {"status": ""})
    assert resp.status_code == 200
    assert b"Some Opp" in resp.content


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_country_filter_does_not_collapse_the_country_chart(client, dimagi_user, db):
    """Regression: Country is a filter on the Opportunities/Visit Stats tabs,
    but it's the Visits-by-Country chart's own axis -- selecting NG elsewhere
    must not reduce that chart to a single country's bars."""
    _make_opp(1, country="NG")
    _make_opp(2, country="KE")
    _make_rollup(1, status="approved", n=10)
    _make_rollup(2, status="approved", n=7)
    client.force_login(dimagi_user)

    resp = client.get(reverse("labs_admin:opportunity_tracker"), {"country": "NG", "tab": "country"})
    assert resp.status_code == 200
    # The chart's own data (embedded via json_script) must still carry both
    # countries -- if the country filter leaked in, only Nigeria would appear.
    assert b'"countries": ["Nigeria", "Kenya"' in resp.content


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_filter_by_delivery_type_narrows_both_panels(client, dimagi_user, db):
    """Regression: detail table and cohort pivot must agree on the same tab."""
    _make_opp(1, name="CHC Opp", service_slug="chc", country="NG")
    _make_opp(2, name="KMC Opp", service_slug="kmc", country="NG", is_active=False, end_date=None)
    _make_rollup(1, status="approved", n=10)
    _make_rollup(2, status="approved", n=999)
    client.force_login(dimagi_user)

    resp = client.get(reverse("labs_admin:opportunity_tracker"), {"delivery_type": "chc", "status": "all"})
    assert resp.status_code == 200
    assert b"CHC Opp" in resp.content
    assert b"KMC Opp" not in resp.content
    # 999 only exists on the excluded KMC opportunity -- if the pivot ignored
    # the delivery_type filter (the original bug) this would appear on screen.
    assert b"999" not in resp.content


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_unknown_tab_param_falls_back_to_opportunities(client, dimagi_user, db):
    """Regression: an unrecognized ?tab= (stale bookmark, typo, future link)
    must not leave every tab radio unchecked and the whole page blank."""
    _make_opp(1, name="Some Opp")
    client.force_login(dimagi_user)

    resp = client.get(reverse("labs_admin:opportunity_tracker"), {"tab": "visits"})
    assert resp.status_code == 200
    assert b'id="ot-tab-opps" class="ot-tab-input" checked' in resp.content
    assert b"Some Opp" in resp.content


@override_settings(**LABS_SETTINGS)
def test_opportunity_tracker_other_tab_forms_preserve_status_and_country(client, dimagi_user, db):
    """Regression: the Visit Stats / Visits-by-Country forms don't filter on
    Status or Country themselves, but must still round-trip those values via
    hidden fields so switching tabs and submitting doesn't reset the
    Opportunities tab's own selection."""
    _make_opp(1, country="NG")
    client.force_login(dimagi_user)

    resp = client.get(reverse("labs_admin:opportunity_tracker"), {"country": "NG", "status": "Active", "tab": "stats"})
    assert resp.status_code == 200
    assert b'<input type="hidden" name="status" value="Active">' in resp.content
    assert b'<input type="hidden" name="country" value="NG">' in resp.content
