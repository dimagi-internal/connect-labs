"""The Implementation view: what-if, filters, ranking and roll-up are worked out on the server.

The page used to do this in JavaScript. ``test_it_agrees_with_the_formulas_the_page_used`` is written from that
JavaScript's own arithmetic and holds the server to it across a grid of settings, so moving the arithmetic did not
change a number. The rest pin what the server now owns.
"""

from __future__ import annotations

import itertools

import pytest

from connect_labs.pulse import cea

BAR = 6.0


def _row(i, **kw):
    """A modelled row, hand-worked: 1,000 people for $4,000 is $4 each; the model gives 20x at a $2 price."""
    base = {
        "id": i,
        "name": f"Opp {i}",
        "org_slug": "partner-a",
        "program_id": 7,
        "service_name": "Child Health Campaign",
        "country": "NG",
        "country_name": "Nigeria",
        "active": True,
        "status": cea.STATUS_MODELLED,
        "status_text": "Modelled",
        "model": "chc",
        "total_usd": 4000.0,
        "own_usd": 4000.0,
        "reached": 1000,
        "model_x": 20.0,
        "model_cost_per_person": 2.0,
        "model_deaths_per_100k": 50.0,
        "cost_per_person": 4.0,
        "x": 10.0,
        "deaths_averted": 0.5,
        "cost_per_death": 8000.0,
        "clears_bar": True,
        "rank": i,
    }
    base.update(kw)
    return base


def _unmodelled(i):
    return _row(
        i,
        status="no_model",
        status_text="No model",
        model=None,
        x=None,
        deaths_averted=None,
        cost_per_death=None,
        clears_bar=None,
        model_x=None,
        model_cost_per_person=None,
        model_deaths_per_100k=None,
        rank=None,
        reached=0,
    )


def _what_if(**params):
    return cea.what_if_from(params, BAR, ["chc", "mbw", "kc"])


class TestWhatIf:
    def test_the_defaults_change_no_number(self):
        (r,) = cea.apply_what_if([_row(1)], _what_if())
        assert r["x"] == pytest.approx(10.0) and r["deaths_averted"] == pytest.approx(0.5)
        assert r["cost_per_death"] == pytest.approx(8000.0) and r["reached"] == 1000 and r["clears_bar"] is True

    def test_half_the_effect_halves_the_multiple_and_the_deaths(self):
        (r,) = cea.apply_what_if([_row(1)], _what_if(chc_effect="50"))
        assert r["x"] == pytest.approx(5.0) and r["deaths_averted"] == pytest.approx(0.25)
        assert r["clears_bar"] is False  # 5x is under the 6x bar

    def test_extra_cost_lowers_the_multiple(self):
        (r,) = cea.apply_what_if([_row(1)], _what_if(chc_extra="4"))
        assert r["cost_per_person"] == pytest.approx(8.0) and r["x"] == pytest.approx(5.0)

    def test_the_models_own_price_values_the_people_at_the_models_multiple(self):
        (r,) = cea.apply_what_if([_row(1)], _what_if(basis="model"))
        assert r["x"] == pytest.approx(20.0) and r["cost_used"] == pytest.approx(2.0 * 1000)

    def test_counting_fewer_people_for_the_same_spend_raises_their_cost(self):
        (r,) = cea.apply_what_if([_row(1)], _what_if(chc_reach="50"))
        assert r["reached"] == pytest.approx(500) and r["cost_per_person"] == pytest.approx(8.0)

    def test_the_bar_moves_who_clears_it(self):
        rows = [_row(1), _row(2, x=4.0, model_x=8.0)]
        assert [r["clears_bar"] for r in cea.apply_what_if(rows, _what_if(bar="12"))] == [False, False]
        assert [r["clears_bar"] for r in cea.apply_what_if(rows, _what_if(bar="3"))] == [True, True]

    def test_an_unmodelled_row_is_left_alone_and_the_input_rows_are_not_mutated(self):
        base = _row(1)
        out = cea.apply_what_if([base, _unmodelled(2)], _what_if(chc_effect="10"))
        assert base["x"] == 10.0 and out[1]["x"] is None and out[1]["status"] == "no_model"

    def test_it_agrees_with_the_formulas_the_page_used(self):
        """The old ``compute()`` in cea.html, as the page wrote it, against the server across a grid of settings."""

        def page(base, basis, extra, effect, reach):
            eff, people = effect / 100, base["reached"] * reach / 100
            cost = base["model_cost_per_person"] * people if basis == "model" else base["total_usd"] + extra * people
            cpp = cost / people
            x = base["model_x"] * eff * base["model_cost_per_person"] / cpp
            deaths = people * base["model_deaths_per_100k"] / 100000 * eff
            return x, deaths, cost / deaths, cost, cpp, people

        base = _row(1, reached=1234, total_usd=2900.0)
        for basis, extra, effect, reach in itertools.product(
            ("actual", "model"), (0, 0.7, 3), (25, 100, 140), (40, 100)
        ):
            wi = _what_if(basis=basis, chc_extra=str(extra), chc_effect=str(effect), chc_reach=str(reach))
            (r,) = cea.apply_what_if([base], wi)
            x, deaths, cpd, cost, cpp, people = page(base, basis, extra, effect, reach)
            assert (
                r["x"],
                r["deaths_averted"],
                r["cost_per_death"],
                r["cost_used"],
                r["cost_per_person"],
                r["reached"],
            ) == (
                pytest.approx(x),
                pytest.approx(deaths),
                pytest.approx(cpd),
                pytest.approx(cost),
                pytest.approx(cpp),
                pytest.approx(people),
            )


class TestParsing:
    def test_a_bad_value_falls_back_to_the_default(self):
        wi = _what_if(basis="nonsense", rank="whatever", bar="-3", chc_effect="lots", chc_extra="-1", mbw_reach="nan")
        assert wi["basis"] == "actual" and wi["rank"] == "x" and wi["bar"] == BAR
        assert wi["m"]["chc"] == {"extra": 0.0, "effect": 100.0, "reach": 100.0} and wi["m"]["mbw"]["reach"] == 100.0

    def test_good_values_are_kept(self):
        wi = _what_if(basis="model", rank="cost_per_death", bar="8", kc_effect="80")
        assert (wi["basis"], wi["rank"], wi["bar"], wi["m"]["kc"]["effect"]) == ("model", "cost_per_death", 8.0, 80.0)

    def test_filters(self):
        flt = cea.filters_from(
            {"models": "chc,zzz", "country": " Nigeria ", "active": "1", "min_spend": "500", "q": " Ruwoyd "}
        )
        assert flt["models"] == ["chc"] and flt["country"] == "Nigeria" and flt["active"] is True
        assert flt["min_spend"] == 500.0 and flt["q"] == "ruwoyd" and flt["modelled"] is False
        assert cea.filters_from({})["models"] == list(cea.models()["models"])
        assert cea.filters_from({"models": ""})["models"] == []


class TestSelectAndRank:
    ROWS = [
        _row(1, name="Alpha CHC", org_slug="ruwoyd", total_usd=5000.0),
        _row(
            2, name="Beta MBW", model="mbw", country="UG", country_name="Uganda", active=False, total_usd=100.0, x=3.0
        ),
        _row(
            3,
            name="Gamma",
            reached=8000,
            total_usd=32000.0,
            own_usd=32000.0,
            x=20.0,
            cost_per_death=1000.0,
            deaths_averted=4.0,
        ),
        _unmodelled(4),
    ]

    def _select(self, **params):
        flt = cea.filters_from(params)
        return [r["id"] for r in self.ROWS if cea.passes(r, flt)]

    def test_each_filter(self):
        assert self._select() == [1, 2, 3, 4]
        assert self._select(models="chc") == [1, 3, 4]  # an unmodelled row has no model to switch off
        assert self._select(country="Uganda") == [2]
        assert self._select(active="1") == [1, 3, 4]
        assert self._select(modelled="1") == [1, 2, 3]
        assert self._select(min_spend="1000") == [1, 3, 4]
        assert self._select(q="ruwoyd") == [1]

    def test_ranking_by_multiple_puts_the_highest_first_and_unmodelled_last_unranked(self):
        out = cea.rank_rows([dict(r) for r in self.ROWS], "x")
        assert [r["id"] for r in out] == [3, 1, 2, 4] and [r["rank"] for r in out] == [1, 2, 3, None]

    def test_ranking_by_cost_per_death_puts_the_cheapest_first(self):
        out = cea.rank_rows([dict(r) for r in self.ROWS], "cost_per_death")
        assert [r["id"] for r in out][:3] == [3, 1, 2]

    def test_view_returns_the_selection_ranked_and_rolled_up(self, monkeypatch):
        monkeypatch.setattr(cea, "programmes", lambda rows: [{"program_id": 7, "n": len(rows)}])
        data = {"rows": [dict(r) for r in self.ROWS], "bar_x": BAR}
        got = cea.view({"models": "chc", "rank": "deaths_averted"}, data)
        assert [r["id"] for r in got["rows"]] == [3, 1, 4] and got["totals"]["opportunities"] == 3
        assert got["totals"] == cea.rollup(got["rows"]) and got["programmes"] == [{"program_id": 7, "n": 3}]
        assert got["what_if"]["rank"] == "deaths_averted" and got["bar_x"] == BAR

    def test_the_multiple_of_a_rollup_is_weighted_by_the_cost_it_was_valued_at(self, monkeypatch):
        monkeypatch.setattr(cea, "programmes", lambda rows: [])
        rows = [
            _row(1),
            _row(
                2,
                total_usd=1000.0,
                own_usd=1000.0,
                reached=1000,
                x=40.0,
                deaths_averted=0.5,
                cost_per_death=2000.0,
                model_x=20.0,
            ),
        ]
        got = cea.view({"basis": "model"}, {"rows": rows, "bar_x": BAR})
        # Valued at the models' price both rows cost $2,000 and give the model's 20x.
        assert got["totals"]["portfolio_x"] == pytest.approx(20.0)
        assert got["totals"]["cost_per_death"] == pytest.approx(4000.0 / 1.0)
        # The spend shown is what was actually paid, whatever the what-if.
        assert got["totals"]["spend"] == pytest.approx(5000.0)


@pytest.mark.django_db
class TestPageAndApi:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        from django.core.cache import cache

        cache.clear()
        client.force_login(django_user_model.objects.create_user(username="impl-view", password="x"))
        return client

    def test_the_api_returns_the_view(self, client_in):
        from django.urls import reverse

        got = client_in.get(reverse("cea:implementation_view"), {"basis": "model", "rank": "total_usd"}).json()
        assert set(got) >= {"rows", "totals", "programmes", "what_if", "filters", "bar_x"}
        assert got["what_if"]["basis"] == "model" and got["what_if"]["rank"] == "total_usd"

    def test_the_page_embeds_the_view_the_url_asks_for(self, client_in):
        from django.urls import reverse

        body = client_in.get(reverse("cea:implementation"), {"bar": "9", "chc_effect": "60"}).content.decode()
        assert '"view_url"' in body and '"what_if"' in body and '"bar": 9.0' in body and '"effect": 60.0' in body

    def test_both_need_a_login(self, client):
        from django.urls import reverse

        assert client.get(reverse("cea:implementation_view")).status_code == 302
