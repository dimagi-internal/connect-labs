"""Delivered opportunities' real cost per person, offered as a price and never applied unasked."""

import pytest

from connect_labs.effectiveness import market, observed
from connect_labs.effectiveness.scenario import Scenario


def row(model="chc", country="NG", usd=800.0, reached=1000, status="modelled"):
    return {"model": model, "country": country, "total_usd": usd, "reached": reached, "status": status}


class TestFromRows:
    def test_the_price_is_total_spend_over_total_people_so_a_big_opportunity_counts_for_more(self):
        got = observed.from_rows([row(usd=900, reached=1000), row(usd=100, reached=1000)])["chc"]["NGA"]
        assert got.cost_per_person == pytest.approx(0.5) and got.opportunities == 2 and got.people == 2000

    def test_only_what_was_delivered_and_modelled_counts_and_nothing_is_counted_twice(self):
        rows = [row(reached=0), row(status="cost pooled"), row(country="ZZ"), row(model=None), row(usd=0)]
        assert observed.from_rows(rows) == {}

    def test_models_and_countries_are_kept_apart(self):
        got = observed.from_rows([row("chc", "NG"), row("kc", "NG", 100, 10), row("chc", "KE", 900, 1000)])
        assert set(got) == {"chc", "kc"} and set(got["chc"]) == {"NGA", "KEN"}


class TestBlend:
    def test_no_evidence_means_the_models_price(self):
        assert observed.blend(1.84, None) == 1.84

    def test_one_opportunity_moves_the_price_a_third_of_the_way_and_many_nearly_all_of_it(self):
        one = observed.Observed(0.8, 1000, 1, 800.0)
        many = observed.Observed(0.8, 100_000, 18, 80_000.0)
        assert observed.blend(1.84, one) == pytest.approx(1.84 + (1 / 3) * (0.8 - 1.84))
        assert observed.blend(1.84, many) == pytest.approx(1.84 + 0.9 * (0.8 - 1.84))


def places():
    return {
        k: {"name": k, "country": k, "iso3": k, "units": 100_000, "measures": {"nmr": 30.0}} for k in ("NGA", "KEN")
    }


class TestSizing:
    def seen(self):
        return {"NGA": observed.Observed(30.0, 400, 3, 12_000.0)}  # $30 a baby against the model's $60

    def row(self, got, iso):
        return next(r for r in got["rows"] if r["iso3"] == iso)

    def test_without_it_the_model_price_is_used_everywhere_and_nothing_changes(self):
        got = market.size("kc", places(), {}, 6.0)
        assert all(r["cost_per_person"] == 60 and r["price_observed"] is None for r in got["rows"])

    def test_a_country_with_evidence_is_priced_at_the_blend_and_its_multiple_rises_with_the_lower_price(self):
        base = self.row(market.size("kc", places(), {}, 6.0), "NGA")
        got = self.row(market.size("kc", places(), {}, 6.0, observed=self.seen()), "NGA")
        assert got["cost_per_person"] == pytest.approx(60 + 0.6 * (30 - 60))
        assert got["price_model"] == 60 and got["price_observed"] == 30 and got["price_observed_n"] == 3
        assert got["multiple"] > base["multiple"] and got["deaths_per_person"] == pytest.approx(
            base["deaths_per_person"]
        )

    def test_a_country_without_evidence_stays_at_the_models_price(self):
        got = self.row(market.size("kc", places(), {}, 6.0, observed=self.seen()), "KEN")
        assert got["cost_per_person"] == 60 and got["price_observed"] is None

    def test_the_extra_cost_dial_still_adds_on_top(self):
        got = self.row(market.size("kc", places(), {}, 6.0, {"extra": 5}, observed=self.seen()), "NGA")
        assert got["cost_per_person"] == pytest.approx(42 + 5)


def test_a_link_carries_the_basis_and_ignores_one_it_does_not_know():
    sc = Scenario.from_params({"cost_basis": "observed"})
    assert (
        sc.cost_basis == "observed" and sc.to_params()["cost_basis"] == "observed" and sc.changes()["assumptions"] == 1
    )
    assert Scenario.from_params({"cost_basis": "bogus"}).cost_basis == "model"
    assert "cost_basis" not in Scenario.from_params({}).to_params()
