"""What a service really cost. All data invented."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.pulse import costs, ingest
from connect_labs.pulse.models import (
    PulseCostEntry,
    PulseInvoice,
    PulseInvoiceReview,
    PulseOpportunity,
    PulseWork,
)

D = Decimal
_seq = [0]


def _opp(oid, *, currency="NGN", rate="0.0007", active=False, synced=True, **extra):
    return PulseOpportunity.objects.create(
        opportunity_id=oid,
        name=f"Opp {oid}",
        org_slug=f"org-{oid}",
        service_slug="chc",
        currency=currency,
        usd_rate=D(rate) if rate else None,
        is_active=active,
        end_date=dt.date(2025, 1, 1),
        invoices_synced_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) if synced else None,
        **extra,
    )


def _work(oid, worker, org=0, units=1, status="approved", month=3):
    _seq[0] += 1
    PulseWork.objects.create(
        work_key=f"w{_seq[0]}",
        opportunity_id=oid,
        worker_hash="w",
        status=status,
        approved_count=units if status == "approved" else 0,
        created_ts=dt.datetime(2026, month, 1, tzinfo=dt.timezone.utc),
        usd_to_worker=D(str(worker)),
        usd_to_org=D(str(org)),
    )


def _inv(oid, number, amount, usd, *, service=True):
    return PulseInvoice.objects.create(
        opportunity_id=oid,
        invoice_number=number,
        amount=D(str(amount)) if amount is not None else None,
        amount_usd=D(str(usd)) if usd is not None else None,
        service_delivery=service,
    )


@pytest.mark.django_db
class TestResolveInvoice:
    def test_connects_usd_is_used_when_plausible(self):
        opp = _opp(1)
        got = costs.resolve_invoice(_inv(1, "A", 1_000_000, 700), opp)
        assert (got.usd, got.basis) == (D("700"), costs.BASIS_CONNECT)

    def test_a_missing_usd_amount_is_converted_at_connects_own_rate(self):
        opp = _opp(1)
        got = costs.resolve_invoice(_inv(1, "A", 1_000_000, None), opp)
        assert (got.usd, got.basis) == (D("700.00"), costs.BASIS_MISSING_USD)

    def test_a_local_amount_typed_as_usd_is_converted(self):
        """1,414,647 'USD' on a naira invoice is a naira figure."""
        opp = _opp(1)
        got = costs.resolve_invoice(_inv(1, "A", 1_414_647, 1_414_647), opp)
        assert got.basis == costs.BASIS_USD_IS_LOCAL
        assert got.usd == D("990.25")

    def test_a_usd_invoice_equal_to_its_amount_is_fine(self):
        opp = _opp(1, currency="USD", rate="1")
        got = costs.resolve_invoice(_inv(1, "A", 500, 500), opp)
        assert got.basis == costs.BASIS_CONNECT

    def test_a_persons_decision_wins(self):
        opp = _opp(1)
        inv = _inv(1, "A", 1_000_000, 700)
        review = PulseInvoiceReview.objects.create(
            opportunity_id=1, invoice_number="A", usd_override=D("650"), reason="x"
        )
        assert costs.resolve_invoice(inv, opp, review).usd == D("650")
        review.exclude = True
        assert costs.resolve_invoice(inv, opp, review).usd == 0


@pytest.mark.django_db
class TestOpportunityCosts:
    def test_service_delivery_invoices_are_not_added_twice(self):
        """They bill pay that already accrued on completed works."""
        _opp(1)
        _work(1, 100, 60)
        _inv(1, "SD", None, 160, service=True)
        c = costs.opportunity_costs()[1]
        assert c.per_service_usd == 160
        assert c.fixed_usd == 0
        assert c.total_usd == 160

    def test_custom_invoices_and_labs_entries_are_fixed_costs(self):
        _opp(1)
        _work(1, 100, 60, units=4)
        _inv(1, "C1", None, 1_200, service=False)
        PulseCostEntry.objects.create(opportunity_id=1, kind="org_fee", usd=D("400"), reason="paid by contract")
        c = costs.opportunity_costs()[1]
        assert c.fixed_usd == 1_600
        assert c.fixed_per_unit == 400
        assert c.total_usd == 1_760

    def test_fixed_costs_spread_by_share_of_approved_units(self):
        _opp(1)
        _work(1, 10, units=3, month=3)
        _work(1, 10, units=1, month=4)
        _inv(1, "C1", None, 800, service=False)
        by = costs.opportunity_costs()
        assert costs.spread_share(by, {1: 1}) == 200  # April's one unit of four
        assert costs.unallocated(by) == 0

    def test_fixed_costs_with_no_work_are_unallocated_not_dropped(self):
        _opp(1)
        _inv(1, "C1", None, 15_000, service=False)
        by = costs.opportunity_costs()
        assert costs.unallocated(by) == 15_000
        assert costs.spread_share(by, {1: 5}) == 0

    def test_test_opportunities_are_left_out(self):
        _opp(1, is_test=True)
        _inv(1, "C1", None, 999, service=False)
        assert 1 not in costs.opportunity_costs()


@pytest.mark.django_db
class TestIssues:
    def _kinds(self):
        return {(r["kind"], r["opportunity_id"]) for r in costs.cost_issues(today=dt.date(2026, 9, 1))}

    def test_currency_problems_are_adjusted_not_asked_about(self):
        _opp(1)
        _inv(1, "A", 1_000_000, None)
        rows = [r for r in costs.cost_issues() if r["kind"] == "missing_usd"]
        assert rows and rows[0]["who"] == costs.WHO_AUTO

    def test_delivery_with_no_org_pay_anywhere_is_raised(self):
        """The interview cohorts: worker pay, and nothing for the organisation."""
        _opp(1)
        _work(1, 500)
        assert ("no_org_pay", 1) in self._kinds()

    def test_an_entered_cost_answers_it(self):
        _opp(1)
        _work(1, 500)
        PulseCostEntry.objects.create(opportunity_id=1, kind="org_fee", usd=D("300"), reason="contract")
        assert ("no_org_pay", 1) not in self._kinds()

    def test_service_invoices_well_above_accrual_are_raised(self):
        _opp(1)
        _work(1, 5_000, 3_000)
        _inv(1, "SD", None, 20_000, service=True)
        assert ("service_invoices_exceed_accrual", 1) in self._kinds()

    def test_fixed_costs_with_no_work_are_raised(self):
        _opp(1)
        _inv(1, "C1", None, 15_000, service=False)
        assert ("fixed_cost_without_work", 1) in self._kinds()

    def test_finished_accrued_and_never_invoiced_is_raised(self):
        _opp(1)
        _work(1, 900, 900)
        assert ("accrued_not_invoiced", 1) in self._kinds()

    def test_unread_invoices_are_raised_and_nothing_else_guessed(self):
        _opp(1, synced=False)
        _work(1, 900)
        assert self._kinds() == {("invoices_unread", 1)}

    def test_people_come_before_automatic_fixes(self):
        _opp(1)
        _work(1, 500)
        _opp(2)
        _inv(2, "A", 1_000_000, None)
        rows = costs.cost_issues()
        assert rows[0]["who"] == costs.WHO_PERSON
        assert rows[-1]["who"] == costs.WHO_AUTO


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def fetch_all(self, endpoint, params=None):
        return self.rows


@pytest.mark.django_db
class TestRefreshInvoices:
    def test_mirrors_connect_exactly_and_leaves_reviews_alone(self):
        opp = _opp(1, synced=False)
        _inv(1, "GONE", 1, 1)
        PulseInvoiceReview.objects.create(opportunity_id=1, invoice_number="GONE", exclude=True, reason="dup")
        rows = [
            {
                "invoice_number": "A",
                "amount": "1000",
                "amount_usd": None,
                "date": "2026-02-01",
                "service_delivery": False,
            },
            {
                "invoice_number": "B",
                "amount": "50",
                "amount_usd": "50",
                "date": "2026-02-02",
                "service_delivery": True,
                "exchange_rate": 7,
            },
        ]
        assert ingest.refresh_invoices(FakeClient(rows), opp) == 2
        assert set(PulseInvoice.objects.values_list("invoice_number", flat=True)) == {"A", "B"}
        assert PulseInvoice.objects.get(invoice_number="B").exchange_rate_id == 7
        assert PulseInvoiceReview.objects.filter(invoice_number="GONE").exists()
        opp.refresh_from_db()
        assert opp.invoices_synced_at is not None


@pytest.mark.django_db
class TestCostsPage:
    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(username="staff", password="x")

    def test_lists_what_needs_a_person(self, client, user):
        _opp(1)
        _work(1, 500)
        client.force_login(user)
        body = client.get(reverse("pulse:costs")).content.decode()
        assert "was the organisation paid outside it?" in body

    def test_downloads_as_csv(self, client, user):
        _opp(1)
        _work(1, 500)
        client.force_login(user)
        response = client.get(reverse("pulse:costs") + "?format=csv")
        assert response["Content-Type"] == "text/csv"
        assert "no organisation pay" in response.content.decode().lower()

    def test_needs_a_login(self, client):
        assert client.get(reverse("pulse:costs")).status_code == 302


@pytest.mark.django_db
class TestTheTwoViews:
    """Every money figure: fixed costs alongside per-service pay, or spread in."""

    @pytest.fixture
    def portfolio(self, django_user_model):
        from django.core.cache import cache

        cache.clear()
        opp = _opp(1, currency="USD", rate="1", active=True)
        opp.service_slug, opp.org_slug = "chc", "lakeside"
        opp.save()
        for month in (3, 4):
            _seq[0] += 1
            PulseWork.objects.create(
                work_key=f"v{_seq[0]}",
                opportunity_id=1,
                org_slug="lakeside",
                service_slug="chc",
                country="NG",
                worker_hash="w",
                status="approved",
                approved_count=2,
                created_ts=dt.datetime(2026, month, 1, tzinfo=dt.timezone.utc),
                usd_to_worker=D("30"),
                usd_to_org=D("20"),
            )
        _inv(1, "START", 400, 400, service=False)  # $400 fixed over 4 units = $100/unit
        return django_user_model.objects.create_user(username="staff", password="x")

    def _summary(self, client, view=""):
        return client.get(reverse("pulse:api_summary") + (f"?costs={view}" if view else "")).json()["money"]

    def test_separate_keeps_per_service_pay_and_calls_out_fixed_costs(self, client, portfolio):
        m = self._summary(client)
        assert m["costs_view"] == "separate"
        assert m["total_paid"] == 100
        assert m["fixed_costs"] == 400
        chc = next(r for r in m["by_service"] if r["service"] == "chc")
        assert chc["usd_total"] == 100 and chc["fixed_usd"] == 400

    def test_spread_folds_fixed_costs_into_totals_and_rates(self, client, portfolio):
        m = self._summary(client, "spread")
        assert m["total_paid"] == 500
        assert m["per_service_paid"] == 100
        chc = next(r for r in m["by_service"] if r["service"] == "chc")
        assert chc["usd_total"] == 500
        assert chc["total_rate"] == pytest.approx(500 / 2)  # two approved works

    def test_a_window_carries_only_its_share(self, client, portfolio):
        m = client.get(reverse("pulse:api_summary") + "?costs=spread&from=2026-04-01&to=2026-04-30").json()["money"]
        assert m["per_service_paid"] == 50
        assert m["fixed_costs"] == 200  # April's 2 units of 4
        assert m["total_paid"] == 250

    def test_the_partner_window_and_dossier_follow_the_view(self, client, portfolio, settings):
        client.force_login(portfolio)
        partner = client.get(reverse("pulse:api_partner") + "?org=lakeside&costs=spread").json()
        assert partner["money"]["total_paid"] == 500
        assert partner["money"]["fixed_usd"] == 400
        assert partner["opportunities"][0]["usd_total"] == 500
        dossier = client.get(reverse("pulse:api_opp") + "?id=1&costs=spread").json()
        assert dossier["money"]["usd_total"] == 500
        assert dossier["money"]["fixed_usd"] == 400
        assert dossier["invoices"][0]["kind"] == "fixed cost"
        assert dossier["invoices"][0]["basis"] == costs.BASIS_CONNECT

    def test_a_donor_report_names_fixed_costs_in_either_view(self, client, portfolio):
        import re

        from connect_labs.pulse.models import PulseReport

        PulseReport.objects.create(slug="r1", title="Report", org_slug="lakeside")

        def text(url):
            return re.sub(r"\s+", " ", client.get(url).content.decode())

        body = text(reverse("pulse:report", args=["r1"]))
        assert "Cost per verified delivery, before startup and supplies" in body
        body = text(reverse("pulse:report", args=["r1"]) + "?costs=spread")
        assert "Cost per verified delivery, startup and supplies included" in body
