import pytest
from django.urls import reverse

from connect_labs.impact import engine
from connect_labs.scenarios import market

PEOPLE = {
    "NGA": {"name": "Nigeria", "units": 30_000_000},
    "NER": {"name": "Niger", "units": 4_000_000},
    "TUN": {"name": "Tunisia", "units": None},
}


def test_the_market_is_people_times_the_models_own_cost_per_person():
    out = market.size("kc", PEOPLE, {}, 6.0)
    assert out["totals"]["market_usd"] == pytest.approx(34_000_000 * engine.defaults("kc")["cost"])
    ner = next(r for r in out["rows"] if r["iso3"] == "NER")
    assert ner["market_usd"] == pytest.approx(4_000_000 * 60.0)


def test_chc_is_priced_for_a_year_of_campaigns():
    row = market.size("chc", {"NER": PEOPLE["NER"]}, {}, 6.0)["rows"][0]
    o = engine.run("chc", engine.country("chc", "NER")["inputs"])
    assert row["cost_per_person"] == pytest.approx(o["full_cost"])
    assert row["cost_per_person"] == pytest.approx(o["full_cost"] / o["campaigns"] * o["campaigns"])
    assert o["campaigns"] > 1


def test_a_changed_input_moves_the_price_and_the_multiple():
    base = market.size("chc", PEOPLE, {}, 6.0)["rows"]
    dear = market.size("chc", PEOPLE, {"cost": 4.0}, 6.0)["rows"]
    for a, b in zip(base, dear):
        if a["modelled"]:
            assert b["cost_per_person"] > a["cost_per_person"] and b["multiple"] < a["multiple"]


def test_a_country_with_no_figures_counts_in_the_market_but_never_clears_the_bar():
    out = market.size("chc", PEOPLE, {}, 6.0)
    tun = next(r for r in out["rows"] if r["iso3"] == "TUN")
    assert tun["modelled"] is False and tun["clears_bar"] is False and tun["market_usd"] is None
    assert out["totals"]["unmodelled_countries"] == 1 and out["totals"]["is_floor"] is True


def test_the_clearing_part_is_a_subset_of_the_market():
    out = market.size("chc", PEOPLE, {}, 6.0)["totals"]
    assert 0 < out["clearing_usd"] <= out["market_usd"]
    assert market.size("chc", PEOPLE, {}, 1000.0)["totals"]["clearing_usd"] == 0


def test_each_model_names_a_count_the_baseline_holds():
    from connect_labs.labs.indicators import measures

    for unit in market.UNITS.values():
        assert unit.measure in measures.MEASURES and not measures.get(unit.measure).is_rate


@pytest.mark.django_db
class TestPageAndApi:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="market", password="x"))
        return client

    def test_the_page_opens_with_its_tabs_and_the_pmc_link(self, client_in):
        body = client_in.get(reverse("cea:market")).content.decode()
        assert "Market size" in body and 'data-testid="cea-tabs"' in body and reverse("cea:pmc") in body

    def test_the_api_sizes_each_chosen_model(self, client_in):
        r = client_in.get(reverse("cea:market_data"), {"models": "chc,kc", "indicator": "u5mr", "threshold": 80})
        assert r.status_code == 200
        got = r.json()
        assert [m["model"] for m in got["models"]] == ["chc", "kc"] and got["selection"]["indicator"] == "u5mr"

    def test_a_bad_input_is_a_400_with_words(self, client_in):
        r = client_in.get(reverse("cea:market_data"), {"models": "chc", "i.chc.nonsense": 1})
        assert r.status_code == 400 and "nonsense" in r.json()["error"]

    def test_an_unknown_indicator_is_refused(self, client_in):
        assert client_in.get(reverse("cea:market_data"), {"indicator": "nope"}).status_code == 400

    def test_the_old_baseline_page_points_to_market_size(self, client_in):
        body = client_in.get(reverse("targeting:index")).content.decode()
        assert "Health indicators" in body and reverse("cea:market") in body
