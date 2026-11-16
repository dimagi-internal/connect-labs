import pytest
from django.urls import reverse

from connect_labs.effectiveness import engine, market, result

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


def test_reach_counts_only_that_share_of_the_people():
    full = market.size("kc", PEOPLE, {}, 6.0)
    half = market.size("kc", PEOPLE, {}, 6.0, {"reach": 50})
    assert half["totals"]["units"] == pytest.approx(full["totals"]["units"] / 2)
    assert half["totals"]["market_usd"] == pytest.approx(full["totals"]["market_usd"] / 2)
    row = next(r for r in half["rows"] if r["iso3"] == "NER")
    assert row["units"] == 2_000_000 and row["units_all"] == 4_000_000
    assert row["multiple"] == next(r for r in full["rows"] if r["iso3"] == "NER")["multiple"]


def test_extra_cost_raises_the_price_and_lowers_the_multiple_by_the_one_formula():
    base = next(r for r in market.size("kc", PEOPLE, {}, 6.0)["rows"] if r["iso3"] == "NER")
    dear = next(r for r in market.size("kc", PEOPLE, {}, 6.0, {"extra": 30})["rows"] if r["iso3"] == "NER")
    assert dear["cost_per_person"] == pytest.approx(base["cost_per_person"] + 30)
    assert dear["multiple"] == pytest.approx(
        result.scale_multiple(base["multiple"], base["cost_per_person"], base["cost_per_person"] + 30)
    )


def test_effect_scales_the_multiple_and_nothing_else():
    base = next(r for r in market.size("kc", PEOPLE, {}, 6.0)["rows"] if r["iso3"] == "NER")
    half = next(r for r in market.size("kc", PEOPLE, {}, 6.0, {"effect": 50})["rows"] if r["iso3"] == "NER")
    assert half["multiple"] == pytest.approx(base["multiple"] / 2)
    assert half["market_usd"] == base["market_usd"]


def test_default_dials_change_nothing():
    assert market.size("chc", PEOPLE, {}, 6.0, {"extra": 0, "effect": 100, "reach": 100}) == market.size(
        "chc", PEOPLE, {}, 6.0
    )


@pytest.mark.django_db
class TestPageAndApi:
    @pytest.fixture
    def client_in(self, client, django_user_model):
        client.force_login(django_user_model.objects.create_user(username="market", password="x"))
        return client

    def test_the_page_opens_with_its_tabs(self, client_in):
        body = client_in.get(reverse("cea:market")).content.decode()
        assert "Market size" in body and 'data-testid="cea-tabs"' in body

    def test_the_api_sizes_each_chosen_model(self, client_in):
        r = client_in.get(reverse("cea:market_data"), {"models": "chc,kc", "indicator": "u5mr", "threshold": 80})
        assert r.status_code == 200
        got = r.json()
        assert [m["model"] for m in got["models"]] == ["chc", "kc"] and got["selection"]["indicator"] == "u5mr"

    def test_a_bad_input_is_a_400_with_words(self, client_in):
        r = client_in.get(reverse("cea:market_data"), {"models": "chc", "i.chc.nonsense": 1})
        assert r.status_code == 400 and "nonsense" in r.json()["error"]

    def test_an_unknown_indicator_falls_back_to_the_default_as_the_other_endpoints_do(self, client_in):
        got = client_in.get(reverse("cea:market_data"), {"indicator": "nope"})
        assert got.status_code == 200 and got.json()["selection"]["indicator"] == "u5mr"

    def test_the_old_baseline_page_points_to_market_size(self, client_in):
        body = client_in.get(reverse("indicators:index")).content.decode()
        assert "Health indicators" in body and reverse("cea:market") in body

    def test_everywhere_means_no_threshold_on_the_widest_indicator(self, client_in):
        got = client_in.get(reverse("cea:market_data"), {"models": "chc", "indicator": "all"}).json()
        sel = got["selection"]
        assert sel["everywhere"] is True and sel["indicator"] == "diarrhoea_prevalence" and sel["africa_total"] > 50
        assert sel["threshold"] == 0  # no threshold: not even the slider minimum

    def test_the_dials_travel_in_the_link_and_come_back_in_the_answer(self, client_in):
        got = client_in.get(reverse("cea:market_data"), {"models": "kc", "kc_reach": "50"}).json()
        assert got["models"][0]["dials"]["reach"] == 50.0 and got["changes"] == {"dials": 1, "assumptions": 0}
        assert got["link"] == {"models": "kc", "kc_reach": "50"}

    def test_the_page_starts_from_the_link_as_the_server_read_it(self, client_in):
        import json
        import re

        body = client_in.get(reverse("cea:market"), {"models": "kc", "kc_reach": "50"}).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert data["state"]["models"] == ["kc"] and data["state"]["dials"]["kc"]["reach"] == 50.0
        assert [d["key"] for d in data["dials"]] == ["extra", "effect", "reach"]

    def test_a_link_that_cannot_run_still_opens_the_page_and_says_why(self, client_in):
        import json
        import re

        body = client_in.get(reverse("cea:market"), {"i.chc.nonsense": "1"}).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert "nonsense" in data["error"] and data["state"]["overrides"] == {}
