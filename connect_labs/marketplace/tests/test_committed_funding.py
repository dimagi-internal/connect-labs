"""Committed funding: what has been paid for but not yet delivered.

All data invented.
"""

from decimal import Decimal

import pytest

from connect_labs.marketplace import queries
from connect_labs.pulse.models import PulseOpportunity


def _opp(opp_id, service, *, budget=None, per_visit=None, visits=0, currency="USD", slug="org", usd_rate=None):
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
    )


@pytest.mark.django_db
class TestCommittedByProgramme:
    def test_outstanding_is_what_is_funded_and_not_yet_delivered(self):
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=250)
        got = queries.committed_by_programme()["kmc"]
        assert got["funded"] == 100_000
        assert got["delivered"] == 25_000
        assert got["outstanding"] == 75_000

    def test_several_opportunities_sum_into_one_programme(self):
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=250)
        _opp(2, "kmc", budget=40_000, per_visit=50, visits=100)
        got = queries.committed_by_programme()["kmc"]
        assert got["funded"] == 140_000
        assert got["delivered"] == 30_000
        assert got["opportunities"] == 2

    def test_an_unread_budget_is_excluded_not_counted_as_zero(self):
        """A programme that looks unfunded because nobody fetched its budget is
        worse than one that says it does not know."""
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=100)
        _opp(2, "kmc", budget=None, per_visit=100, visits=5_000)
        got = queries.committed_by_programme()["kmc"]
        assert got["opportunities"] == 1
        assert got["funded"] == 100_000

    def test_visits_with_no_per_visit_budget_are_not_priced_by_guesswork(self):
        _opp(1, "kmc", budget=100_000, per_visit=None, visits=900)
        got = queries.committed_by_programme()["kmc"]
        assert got["delivered"] == 0
        assert got["outstanding"] == 100_000

    def test_over_delivery_does_not_produce_negative_outstanding(self):
        """An opportunity can exceed its budget. "Minus $4,000 still to come"
        is not something anyone can act on."""
        _opp(1, "kmc", budget=10_000, per_visit=100, visits=140)
        assert queries.committed_by_programme()["kmc"]["outstanding"] == 0

    def test_currencies_are_converted_before_being_summed(self):
        """A naira budget and a dollar budget are now comparable. Added raw
        they were not: this programme's local total would read 50,100,000."""
        _opp(1, "kmc", budget=100_000, currency="USD")
        _opp(2, "kmc", budget=50_000_000, currency="NGN", usd_rate=Decimal("0.00064"))
        got = queries.committed_by_programme()["kmc"]
        assert got["funded"] == 100_000 + 32_000
        assert got["currencies"] == ["NGN", "USD"]

    def test_an_opportunitys_own_rate_beats_the_pooled_one(self):
        """Its own works are the closest evidence of what it was paid at."""
        _opp(1, "kmc", budget=1_000_000, currency="NGN", usd_rate=Decimal("0.00064"))
        _opp(2, "kmc", budget=1_000_000, currency="NGN", usd_rate=Decimal("0.00100"))
        _opp(3, "chc", budget=1_000_000, currency="NGN", usd_rate=Decimal("0.00064"))
        assert queries.committed_by_programme()["chc"]["funded"] == 640

    def test_a_currency_with_no_rate_anywhere_is_excluded_and_counted(self):
        """A dollar figure no payment supports is worse than a missing one, and
        the count lets the page say how complete the answer is."""
        _opp(1, "kmc", budget=5_000_000, currency="XAF")
        got = queries.committed_by_programme()["kmc"]
        assert got["funded"] == 0
        assert got["unconvertible"] == 1
        assert got["opportunities"] == 0

    def test_an_unsampled_opportunity_borrows_its_currencys_rate(self):
        """Most opportunities' own works have never been sampled. Pooling is
        what stops that meaning "no figure at all"."""
        _opp(1, "kmc", budget=1_000_000, currency="NGN", usd_rate=Decimal("0.00064"))
        _opp(2, "chc", budget=1_000_000, currency="NGN", usd_rate=None)
        assert queries.committed_by_programme()["chc"]["funded"] == 640

    def test_the_unclassified_bucket_is_not_a_programme_here_either(self):
        _opp(1, "other", budget=999_999, per_visit=10, visits=1)
        assert "other" not in queries.committed_by_programme()

    def test_an_untagged_opportunity_is_skipped(self):
        _opp(1, "", budget=999_999, per_visit=10, visits=1)
        assert queries.committed_by_programme() == {}


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
