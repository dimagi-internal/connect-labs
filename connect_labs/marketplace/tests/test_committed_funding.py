"""Committed funding: what has been paid for but not yet delivered.

All data invented.
"""

import pytest

from connect_labs.marketplace import queries
from connect_labs.pulse.models import PulseOpportunity


def _opp(opp_id, service, *, budget=None, per_visit=None, visits=0, currency="USD", slug="org"):
    return PulseOpportunity.objects.create(
        opportunity_id=opp_id,
        name=f"Opp {opp_id}",
        org_slug=slug,
        service_slug=service,
        total_budget=budget,
        budget_per_visit=per_visit,
        lifetime_visit_count=visits,
        currency=currency,
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

    def test_mixed_currencies_are_flagged_rather_than_silently_summed(self):
        """Adding naira to dollars produces a number that means nothing. The
        sum is still returned — the caller is told not to trust it as money."""
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=0, currency="USD")
        _opp(2, "kmc", budget=50_000_000, per_visit=5_000, visits=0, currency="NGN")
        got = queries.committed_by_programme()["kmc"]
        assert got["mixed_currency"] is True
        assert got["currencies"] == ["NGN", "USD"]

    def test_a_single_currency_is_not_flagged(self):
        _opp(1, "kmc", budget=100_000, per_visit=100, visits=0, currency="USD")
        got = queries.committed_by_programme()["kmc"]
        assert got["mixed_currency"] is False
        assert got["currencies"] == ["USD"]

    def test_the_unclassified_bucket_is_not_a_programme_here_either(self):
        _opp(1, "other", budget=999_999, per_visit=10, visits=1)
        assert "other" not in queries.committed_by_programme()

    def test_an_untagged_opportunity_is_skipped(self):
        _opp(1, "", budget=999_999, per_visit=10, visits=1)
        assert queries.committed_by_programme() == {}
