"""Committed funding: what has been paid for but not yet delivered.

All data invented.
"""

import datetime as dt
from decimal import Decimal

import pytest

from connect_labs.marketplace import programs, queries
from connect_labs.pulse.models import PulseOpportunity, PulseWork


def _opp(
    opp_id, service, *, budget=None, per_visit=None, visits=0, currency="USD", slug="org", usd_rate=None, **extra
):
    return PulseOpportunity.objects.create(
        opportunity_id=opp_id,
        name=f"Opp {opp_id}",
        org_slug=slug,
        service_slug=service,
        total_budget=budget,
        budget_per_visit=per_visit,
        lifetime_visit_count=visits,
        currency=currency,
        usd_rate=usd_rate,
        **extra,
    )


def _paid(opp_id, usd_worker, usd_org=0, n=[0]):
    import datetime as dt

    # Ingest stamps every work with its opportunity's delivery type and
    # program, and "deployed" groups by that stamp exactly as Pulse does — so
    # an unstamped work, which production never produces, would vanish.
    opp = PulseOpportunity.objects.filter(opportunity_id=opp_id).first()
    n[0] += 1
    PulseWork.objects.create(
        work_key=f"w{opp_id}-{n[0]}",
        opportunity_id=opp_id,
        service_slug=opp.service_slug if opp else "",
        program_id=opp.program_id if opp else None,
        org_slug="org",
        worker_hash="w",
        status="approved",
        created_ts=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        usd_to_worker=Decimal(str(usd_worker)),
        usd_to_org=Decimal(str(usd_org)),
    )


LIVE = {"is_active": True, "end_date": None}
ENDED = {"is_active": False, "end_date": dt.date(2025, 1, 1)}


@pytest.mark.django_db
class TestCommittedByProgram:
    def test_deployed_is_what_was_actually_paid(self):
        """Not visits multiplied by a per-visit budget: a payment unit can
        cover several visits, and that estimate put Mother Baby Wellness at
        $335k delivered against $66k actually paid."""
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=5_000, **LIVE)
        _paid(1, 300, 200)
        assert queries.committed_by_program()["kmc"]["deployed"] == 500

    def test_remaining_is_live_budget_net_of_what_it_has_already_paid(self):
        _opp(1, "kmc", budget=10_000, **LIVE)
        _paid(1, 3_000, 1_000)
        assert queries.committed_by_program()["kmc"]["remaining"] == 6_000

    def test_an_ended_opportunitys_unspent_budget_is_not_remaining(self):
        """The error that made "remaining" read as twice what had ever been
        spent. An opportunity that closed with budget unused has nothing
        outstanding — that money expired."""
        _opp(1, "kmc", budget=1_000_000, **ENDED)
        _paid(1, 4_722)
        got = queries.committed_by_program()["kmc"]
        assert got["remaining"] == 0
        assert got["deployed"] == 4_722

    def test_an_inactive_opportunity_is_not_live_even_without_an_end_date(self):
        _opp(1, "kmc", budget=50_000, is_active=False, end_date=None)
        assert queries.committed_by_program()["kmc"]["remaining"] == 0

    def test_an_active_opportunity_past_its_end_date_is_not_live(self):
        """`is_active` lags; an end date in the past is the stronger fact."""
        _opp(1, "kmc", budget=50_000, is_active=True, end_date=dt.date(2025, 1, 1))
        assert queries.committed_by_program()["kmc"]["remaining"] == 0

    def test_remaining_stays_a_fraction_of_deployed_across_a_realistic_mix(self):
        """The shape a funder expects, and the one the first version broke:
        most budget sits on opportunities that are long finished, so what is
        still available is small beside what has been paid."""
        for i in range(8):
            _opp(10 + i, "chc", budget=300_000, **ENDED)
            _paid(10 + i, 40_000, 40_000)
        _opp(99, "chc", budget=150_000, **LIVE)
        _paid(99, 20_000, 20_000)
        got = queries.committed_by_program()["chc"]
        assert got["deployed"] == 8 * 80_000 + 40_000
        assert got["remaining"] == 110_000
        assert got["remaining"] < got["deployed"]

    def test_over_spending_does_not_produce_negative_remaining(self):
        _opp(1, "kmc", budget=10_000, **LIVE)
        _paid(1, 14_000)
        assert queries.committed_by_program()["kmc"]["remaining"] == 0

    def test_a_live_opportunity_with_an_unread_budget_is_counted_not_guessed(self):
        _opp(1, "kmc", budget=None, **LIVE)
        got = queries.committed_by_program()["kmc"]
        assert got["remaining"] == 0
        assert got["unconvertible"] == 1

    def test_currencies_are_converted_before_being_summed(self):
        _opp(1, "kmc", budget=100_000, currency="USD", **LIVE)
        _opp(2, "kmc", budget=50_000_000, currency="NGN", usd_rate=Decimal("0.00064"), **LIVE)
        assert queries.committed_by_program()["kmc"]["remaining"] == 100_000 + 32_000

    def test_an_unsampled_opportunity_borrows_its_currencys_rate(self):
        _opp(1, "kmc", budget=1_000_000, currency="NGN", usd_rate=Decimal("0.00064"), **ENDED)
        _opp(2, "chc", budget=1_000_000, currency="NGN", usd_rate=None, **LIVE)
        assert queries.committed_by_program()["chc"]["remaining"] == 640

    def test_a_currency_with_no_rate_anywhere_is_excluded_and_counted(self):
        _opp(1, "kmc", budget=5_000_000, currency="XAF", **LIVE)
        got = queries.committed_by_program()["kmc"]
        assert got["remaining"] == 0
        assert got["unconvertible"] == 1

    def test_the_unclassified_bucket_is_not_a_program_here_either(self):
        _opp(1, "other", budget=999_999, **LIVE)
        assert "other" not in queries.committed_by_program()


@pytest.mark.django_db
class TestFxRates:
    def test_a_dollar_is_a_dollar(self):
        """Asserted rather than derived: a broken derivation then shows up as
        figures wrong by a factor, instead of quietly rescaling the one
        currency whose answer everybody already knows."""
        assert queries.fx_rates()["USD"] == 1

    def test_the_rate_is_the_median_not_the_mean(self):
        """The first real sample of this data contained an opportunity whose
        accrual produced a rate of zero. A mean would have carried it into
        every naira figure on the page."""
        for i, rate in enumerate((Decimal("0.00064"), Decimal("0.00065"), Decimal("0.00000001"))):
            _opp(10 + i, "kmc", budget=1, currency="NGN", usd_rate=rate)
        assert queries.fx_rates()["NGN"] == Decimal("0.00064")

    def test_a_zero_rate_never_becomes_a_currencys_rate(self):
        _opp(1, "kmc", budget=1, currency="NGN", usd_rate=Decimal("0"))
        assert "NGN" not in queries.fx_rates()

    def test_a_currency_nobody_was_paid_in_has_no_rate(self):
        _opp(1, "kmc", budget=1, currency="XAF")
        assert "XAF" not in queries.fx_rates()


@pytest.mark.django_db
class TestOnlyRealWorkCounts:
    def test_a_test_opportunity_is_in_neither_figure(self):
        """Pulse drops scaffolding from every figure it shows, so both halves
        leave it out: its $25 is not delivery, and its $312,500 budget is a
        placeholder that would dwarf every real program's."""
        _opp(1, "chc", budget=312_500, is_test=True, **LIVE)
        _paid(1, 25)
        got = queries.committed_by_program().get("chc", {"deployed": 0, "remaining": 0})
        assert got["deployed"] == 0
        assert got["remaining"] == 0

    def test_a_real_opportunity_beside_it_still_counts(self):
        _opp(1, "chc", budget=312_500, is_test=True, **LIVE)
        _opp(2, "chc", budget=10_000, **LIVE)
        _paid(1, 25)
        _paid(2, 4_000)
        got = queries.committed_by_program()["chc"]
        assert got["deployed"] == 4_000
        assert got["remaining"] == 6_000

    def test_ace_demo_runs_under_a_real_delivery_type_count_for_nothing(self):
        """ACE's automated runs land in `ai-demo-space` carrying real delivery
        types. They were the whole of Nutrition's figures and 31 of Malaria's
        live opportunities until pulse flagged them."""
        _opp(1, "nutrition", budget=5_000, is_test=True, slug="ai-demo-space", visits=6, **LIVE)
        _paid(1, 8)
        assert queries.committed_by_program().get("nutrition", {}).get("deployed", 0) == 0
        assert queries.committed_by_program().get("nutrition", {}).get("remaining", 0) == 0
        assert queries.services_by_program().get("nutrition", 0) == 0

    def test_ace_is_not_a_program(self):
        """Dimagi's own tooling: real rows, nothing a partner delivered and
        nothing a funder is buying. 17 live ACE opportunities held $68k of
        budget against $143 ever paid."""
        _opp(1, "ace", budget=68_000, **LIVE)
        _paid(1, 143)
        assert "ace" not in queries.committed_by_program()
        assert not programs.is_program("ace")
