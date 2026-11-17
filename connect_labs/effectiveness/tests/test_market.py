import pytest
from django.urls import reverse

from connect_labs.effectiveness import engine, explore, market, result
from connect_labs.effectiveness.scenario import KNOB_DEFAULT, PlanLimits

PEOPLE = {
    "NGA": {"name": "Nigeria", "iso3": "NGA", "units": 30_000_000, "measures": {"nmr": 39.0}},
    "NER": {"name": "Niger", "iso3": "NER", "units": 4_000_000, "measures": {"nmr": 32.8}},
    "TUN": {"name": "Tunisia", "iso3": "TUN", "units": None},
}
#: The same places without anything to scale a model's risk to: the models' own flat figures.
FLAT = {k: {kk: vv for kk, vv in v.items() if kk != "measures"} for k, v in PEOPLE.items()}


def row(out, key):
    return next(r for r in out["rows"] if r["key"] == key)


def test_the_market_is_people_times_the_models_own_cost_per_person():
    out = market.size("kc", PEOPLE, {}, 6.0)
    assert out["totals"]["market_usd"] == pytest.approx(34_000_000 * engine.defaults("kc")["cost"])
    assert row(out, "NER")["market_usd"] == pytest.approx(4_000_000 * 60.0)


def test_chc_is_priced_for_a_year_of_campaigns():
    r = market.size("chc", {"NER": PEOPLE["NER"]}, {}, 6.0)["rows"][0]
    o = engine.run("chc", engine.country("chc", "NER")["inputs"])
    assert r["cost_per_person"] == pytest.approx(o["full_cost"])
    assert o["campaigns"] > 1


def test_a_changed_input_moves_the_price_and_the_multiple():
    base = market.size("chc", PEOPLE, {}, 6.0)["rows"]
    dear = market.size("chc", PEOPLE, {"cost": 4.0}, 6.0)["rows"]
    for a, b in zip(base, dear):
        if a["modelled"]:
            assert b["cost_per_person"] > a["cost_per_person"] and b["multiple"] < a["multiple"]


def test_a_country_with_no_figures_counts_in_the_market_but_never_clears_the_bar():
    out = market.size("chc", PEOPLE, {}, 6.0)
    tun = row(out, "TUN")
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


# ------------------------------------------------------------------ the dials
def test_reach_counts_only_that_share_of_the_people():
    full = market.size("kc", FLAT, {}, 6.0)
    half = market.size("kc", FLAT, {}, 6.0, {"reach": 50})
    assert half["totals"]["units"] == pytest.approx(full["totals"]["units"] / 2)
    assert half["totals"]["market_usd"] == pytest.approx(full["totals"]["market_usd"] / 2)
    r = row(half, "NER")
    assert r["units"] == 2_000_000 and r["units_all"] == 4_000_000
    assert r["multiple"] == row(full, "NER")["multiple"]


def test_extra_cost_raises_the_price_and_lowers_the_multiple_by_the_one_formula():
    base = row(market.size("kc", FLAT, {}, 6.0), "NER")
    dear = row(market.size("kc", FLAT, {}, 6.0, {"extra": 30}), "NER")
    assert dear["cost_per_person"] == pytest.approx(base["cost_per_person"] + 30)
    assert dear["multiple"] == pytest.approx(
        result.scale_multiple(base["multiple"], base["cost_per_person"], base["cost_per_person"] + 30)
    )


def test_extra_cost_may_be_negative_when_delivery_is_cheaper_than_the_model():
    base = row(market.size("kc", FLAT, {}, 6.0), "NER")
    cheap = row(market.size("kc", FLAT, {}, 6.0, {"extra": -20}), "NER")
    assert cheap["cost_per_person"] == pytest.approx(40.0)
    assert cheap["multiple"] == pytest.approx(base["multiple"] * 60 / 40)


def test_a_price_that_would_reach_zero_is_refused_in_words():
    with pytest.raises(explore.ExploreError) as e:
        market.size("kc", FLAT, {}, 6.0, {"extra": -60})
    assert "above zero" in e.value.message


def test_effect_scales_the_multiple_and_the_deaths_and_nothing_else():
    base = row(market.size("kc", FLAT, {}, 6.0), "NER")
    half = row(market.size("kc", FLAT, {}, 6.0, {"effect": 50}), "NER")
    assert half["multiple"] == pytest.approx(base["multiple"] / 2)
    assert half["deaths_averted"] == pytest.approx(base["deaths_averted"] / 2)
    assert half["market_usd"] == base["market_usd"]


def test_default_dials_change_nothing():
    kw = dict(place_risk=False)
    assert market.size("chc", PEOPLE, {}, 6.0, KNOB_DEFAULT, **kw) == market.size("chc", PEOPLE, {}, 6.0, **kw)


# ---------------------------------------------------------- deaths and impact
def test_deaths_are_the_models_own_per_100000_at_the_people_reached():
    o = engine.run("kc", {})
    r = row(market.size("kc", FLAT, {}, 6.0), "NER")
    assert r["deaths_averted"] == pytest.approx(4_000_000 * o["deaths"] / 100_000)
    assert r["cost_per_death"] == pytest.approx(r["market_usd"] / r["deaths_averted"])
    # and the model's own cost per death averted, at its own inputs
    assert r["cost_per_death"] == pytest.approx(o["cpd"])


def test_the_whole_is_the_spend_weighted_multiple_of_its_places():
    out = market.size("kc", PEOPLE, {}, 6.0)
    a, b = row(out, "NGA"), row(out, "NER")
    want = (a["market_usd"] * a["multiple"] + b["market_usd"] * b["multiple"]) / (a["market_usd"] + b["market_usd"])
    assert out["totals"]["multiple"] == pytest.approx(want)
    assert out["totals"]["deaths_averted"] == pytest.approx(a["deaths_averted"] + b["deaths_averted"])


# ------------------------------------------------------- risk scaled to the place
def _anchor(name):
    sc = next(s for s in engine.spec("kc")["scenarios"] if name in (s.get("name") or s.get("label")))
    return sc["sets"]["post_ref"]


@pytest.mark.parametrize("iso, anchor", [("NGA", "Nigeria"), ("LBR", "Liberia"), ("UGA", "Uganda")])
def test_the_reference_reproduces_the_model_owners_three_anchor_scenarios(iso, anchor):
    """Why 28.4: the model's own scenarios are each about 4.6 times the country's national newborn mortality."""
    risk = market.PLACE_RISK["kc"]
    nmr = engine.country("mbw", iso)["inputs"]["nmr"]
    predicted = engine.defaults("kc")[risk.input] * nmr / risk.reference
    assert predicted == pytest.approx(_anchor(anchor), rel=0.03)


def test_kc_risk_follows_the_places_newborn_mortality_and_so_does_its_multiple():
    out = market.size("kc", PEOPLE, {}, 6.0)
    nga, ner = row(out, "NGA"), row(out, "NER")
    assert nga["risk"] == pytest.approx(0.13 * 39.0 / 28.4) and nga["risk_basis"] == "place"
    assert ner["risk"] == pytest.approx(0.13 * 32.8 / 28.4)
    assert nga["multiple"] > ner["multiple"] > 0 and out["flat"] is False
    assert nga["multiple"] / ner["multiple"] == pytest.approx(39.0 / 32.8, rel=0.1)
    assert out["place_risk"] == {
        "applies": True,
        "on": True,
        "input": "post_ref",
        "measure": "nmr",
        "reference": 28.4,
        "scaled": 2,  # Tunisia has no mortality figure
        "far_from_country": 0,
        "local_inputs": 0,
    }


def test_switching_it_off_gives_the_models_flat_figures_and_says_so():
    out = market.size("kc", PEOPLE, {}, 6.0, place_risk=False)
    assert {round(r["multiple"], 9) for r in out["rows"] if r["multiple"] is not None} == {
        round(row(out, "NGA")["multiple"], 9)
    }
    assert out["flat"] is True and out["place_risk"]["on"] is False
    assert all(r["risk_basis"] == "model" and r["risk_scale"] is None for r in out["rows"])


def test_a_place_with_no_mortality_figure_keeps_the_models_risk_and_is_marked():
    r = row(market.size("kc", PEOPLE, {}, 6.0), "TUN")
    assert r["risk_basis"] == "model" and r["risk"] == pytest.approx(0.13)


def test_your_own_baseline_risk_is_scaled_to_the_place_not_replaced_by_it():
    r = row(market.size("kc", PEOPLE, {"post_ref": 0.10}, 6.0), "NGA")
    assert r["risk"] == pytest.approx(0.10 * 39.0 / 28.4)


def test_a_model_without_place_risk_is_untouched_by_the_switch():
    on = market.size("chc", PEOPLE, {}, 6.0, place_risk=True)
    off = market.size("chc", PEOPLE, {}, 6.0, place_risk=False)
    assert [r["multiple"] for r in on["rows"]] == [r["multiple"] for r in off["rows"]]
    assert on["place_risk"]["applies"] is False
    # the switch still says whether place figures are in use, which for this model means a territory's own inputs
    assert on["place_risk"]["on"] is True and off["place_risk"]["on"] is False


# -------------------------------------------------------------------- the plan
def places(*specs):
    """``(key, units, newborn mortality)`` -> places for ``market.size``."""
    return {k: {"name": k, "iso3": k, "units": u, "measures": {"nmr": n}} for k, u, n in specs}


def test_the_plan_fills_the_highest_multiple_first_and_stops_at_the_target():
    ps = places(("LOW", 1_000_000, 20.0), ("HIGH", 600_000, 40.0), ("MID", 800_000, 30.0))
    out = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000)
    plan = out["plan"]
    assert [p["key"] for p in plan["places"]] == ["HIGH", "MID"]
    assert [round(p["reached"]) for p in plan["places"]] == [600_000, 400_000]
    assert plan["totals"]["people"] == pytest.approx(1_000_000) and plan["feasible"] and plan["shortfall"] == 0
    assert {r["key"]: round(r["planned"]) for r in out["rows"]} == {"LOW": 0, "HIGH": 600_000, "MID": 400_000}


def test_no_place_gives_more_than_the_most_that_can_be_reached_there():
    ps = places(("A", 1_000_000, 40.0), ("B", 1_000_000, 30.0), ("C", 1_000_000, 20.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 50}, target=1_200_000)["plan"]
    assert [(p["key"], round(p["reached"])) for p in plan["places"]] == [
        ("A", 500_000),
        ("B", 500_000),
        ("C", 200_000),
    ]
    assert all(p["share_of_cases"] <= 0.5 + 1e-9 for p in plan["places"])


def test_a_target_beyond_what_can_be_reached_says_how_far_short():
    ps = places(("A", 1_000_000, 40.0), ("B", 1_000_000, 30.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 50}, target=5_000_000)["plan"]
    assert plan["feasible"] is False and plan["capacity"] == pytest.approx(1_000_000)
    assert plan["shortfall"] == pytest.approx(4_000_000) and plan["totals"]["people"] == pytest.approx(1_000_000)


def test_best_first_beats_spreading_evenly_and_the_gain_is_stated():
    ps = places(("A", 1_000_000, 40.0), ("B", 1_000_000, 30.0), ("C", 1_000_000, 20.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000)["plan"]
    best, even = plan["totals"], plan["even"]
    assert best["people"] == pytest.approx(even["people"]) and best["places"] == 1 and even["places"] == 3
    assert best["deaths_averted"] > even["deaths_averted"] and best["cost_per_death"] < even["cost_per_death"]
    assert best["multiple"] > even["multiple"]
    assert plan["gain"]["deaths_averted_pct"] > 0 and plan["gain"]["places_fewer"] == 2


def test_when_every_place_is_alike_there_is_nothing_to_gain_and_it_says_so():
    ps = {k: {**v, "measures": {}} for k, v in places(("A", 1_000_000, 40.0), ("B", 1_000_000, 30.0)).items()}
    out = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000)
    assert out["flat"] is True and out["plan"]["gain"]["deaths_averted_pct"] == pytest.approx(0, abs=1e-6)


def test_a_place_the_model_has_no_figures_for_is_never_planned_however_many_people_it_has():
    ps = {**PEOPLE, "TUN": {"name": "Tunisia", "iso3": "TUN", "units": 90_000_000}}
    out = market.size("chc", ps, {}, 6.0, {"reach": 100}, target=100_000_000)
    assert row(out, "TUN")["multiple"] is None
    assert "TUN" not in [p["key"] for p in out["plan"]["places"]]
    assert out["plan"]["shortfall"] > 0


def test_no_target_means_no_plan():
    assert "plan" not in market.size("kc", PEOPLE, {}, 6.0)


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
        got = client_in.get(reverse("cea:market_data"), {"models": "kc", "kc_reach": "30"}).json()
        assert got["models"][0]["dials"]["reach"] == 30.0 and got["changes"] == {"dials": 1, "assumptions": 0}
        assert got["link"] == {"models": "kc", "kc_reach": "30"}

    def test_the_page_starts_from_the_link_as_the_server_read_it(self, client_in):
        import json
        import re

        body = client_in.get(reverse("cea:market"), {"models": "kc", "kc_reach": "30"}).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert data["state"]["models"] == ["kc"] and data["state"]["dials"]["kc"]["reach"] == 30.0
        assert data["dial_defaults"]["kc"]["reach"] == 50.0 and data["dial_defaults"]["chc"]["reach"] == 100.0
        assert [d["key"] for d in data["dials"]] == ["extra", "effect", "reach"]

    def test_a_link_that_cannot_run_still_opens_the_page_and_says_why(self, client_in):
        import json
        import re

        body = client_in.get(reverse("cea:market"), {"i.chc.nonsense": "1"}).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert "nonsense" in data["error"] and data["state"]["overrides"] == {}


def test_a_target_that_is_met_is_met_exactly():
    ps = places(("A", 333_333.3333, 40.0), ("B", 333_333.3333, 30.0), ("C", 333_333.3334, 20.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000)["plan"]
    assert plan["totals"]["people"] == pytest.approx(1_000_000, abs=1e-9)
    assert plan["shortfall"] == 0 and plan["feasible"]


# ------------------------------------------------------ trusting a place's figures
def with_country(key, units, nmr, country_nmr, iso="NGA", **extra):
    return {
        key: {
            "name": key,
            "country": iso,
            "iso3": iso,
            "units": units,
            "measures": {"nmr": nmr, "nmr_country": country_nmr},
            **extra,
        }
    }


def test_trust_pulls_a_territory_toward_its_country_and_a_country_toward_the_model():
    terr = with_country("KANO", 100_000, 40.0, 30.0)
    full = row(market.size("kc", terr, {}, 6.0), "KANO")
    half = row(market.size("kc", terr, {}, 6.0, trust=0.5), "KANO")
    none = row(market.size("kc", terr, {}, 6.0, trust=0.0), "KANO")
    assert full["risk"] == pytest.approx(0.13 * 40.0 / 28.4)
    assert half["risk"] == pytest.approx(0.13 * 35.0 / 28.4)  # half way between 40 and the country's 30
    assert none["risk"] == pytest.approx(0.13 * 30.0 / 28.4)  # the country's own
    assert full["multiple"] > half["multiple"] > none["multiple"]
    # a country has nothing above it but the model: no trust is the model's flat 13%
    country = {"NGA": {"name": "Nigeria", "iso3": "NGA", "units": 1e6, "measures": {"nmr": 39.0}}}
    assert row(market.size("kc", country, {}, 6.0, trust=0.0), "NGA")["risk"] == pytest.approx(0.13)


def test_a_figure_far_from_its_countrys_is_flagged_and_one_near_it_is_not():
    far = with_country("A", 1, 70.0, 30.0)  # more than twice
    low = with_country("B", 1, 10.0, 30.0)  # less than half
    near = with_country("C", 1, 40.0, 30.0)
    assert row(market.size("kc", far, {}, 6.0), "A")["far_from_country"] is True
    assert row(market.size("kc", low, {}, 6.0), "B")["far_from_country"] is True
    assert row(market.size("kc", near, {}, 6.0), "C")["far_from_country"] is False
    assert market.size("kc", {**far, **near}, {}, 6.0)["place_risk"]["far_from_country"] == 1


def test_the_row_carries_the_places_figure_and_the_countrys_and_where_it_came_from():
    terr = with_country("KANO", 1, 40.0, 30.0, measures_how={"nmr": "Kano (state or province), igme_subnational 2021"})
    r = row(market.size("kc", terr, {}, 6.0), "KANO")
    assert (r["measure"], r["measure_country"]) == (40.0, 30.0) and "Kano" in r["measure_how"]


def test_a_territorys_own_inputs_replace_its_countrys_and_trust_pulls_them_back():
    country = engine.country("chc", "NGA")["inputs"]["ors"]
    terr = {"KANO": {"name": "Kano", "country": "Nigeria", "iso3": "NGA", "units": 1e6, "inputs": {"ors": 0.05}}}
    flat = row(market.size("chc", {"KANO": {**terr["KANO"], "inputs": {}}}, {}, 6.0), "KANO")
    local = row(market.size("chc", terr, {}, 6.0), "KANO")
    half = row(market.size("chc", terr, {}, 6.0, trust=0.5), "KANO")
    none = row(market.size("chc", terr, {}, 6.0, trust=0.0), "KANO")
    assert country > 0.05
    assert local["multiple"] != flat["multiple"] and local["local_inputs"] == ["ors"]
    assert none["multiple"] == pytest.approx(flat["multiple"])
    assert min(flat["multiple"], local["multiple"]) < half["multiple"] < max(flat["multiple"], local["multiple"])


def test_switching_place_figures_off_ignores_a_territorys_own_inputs_too():
    terr = {"KANO": {"name": "Kano", "country": "Nigeria", "iso3": "NGA", "units": 1e6, "inputs": {"ors": 0.05}}}
    off = row(market.size("chc", terr, {}, 6.0, place_risk=False), "KANO")
    plain = row(market.size("chc", {"KANO": {**terr["KANO"], "inputs": {}}}, {}, 6.0), "KANO")
    assert off["multiple"] == pytest.approx(plain["multiple"]) and off["local_inputs"] == []


# ----------------------------------------------------------- the plan's limits
def in_countries(*specs):
    """``(key, country, units, nmr)`` -> places."""
    return {k: {"name": k, "country": c, "iso3": c, "units": u, "measures": {"nmr": n}} for k, c, u, n in specs}


def test_a_place_too_small_to_be_worth_a_team_is_left_out_and_counted():
    ps = in_countries(("BIG", "AAA", 800_000, 30.0), ("TINY", "AAA", 5_000, 60.0), ("MID", "BBB", 600_000, 25.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=900_000, limits=PlanLimits(min_place=50_000))["plan"]
    assert [p["key"] for p in plan["places"]] == ["BIG", "MID"] and plan["limits"]["left_out"] == 1
    free = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=900_000)["plan"]
    assert free["places"][0]["key"] == "TINY" and free["limits"]["left_out"] == 0


def test_keeping_to_some_countries_keeps_the_ones_the_best_plan_leans_on_most():
    ps = in_countries(
        ("A1", "AAA", 400_000, 40.0),
        ("A2", "AAA", 400_000, 38.0),
        ("B1", "BBB", 300_000, 36.0),
        ("C1", "CCC", 300_000, 20.0),
    )
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000, limits=PlanLimits(max_countries=2))["plan"]
    assert {p["country"] for p in plan["places"]} == {"AAA", "BBB"} and plan["feasible"]
    assert plan["totals"]["countries"] == 2 and plan["limits"]["max_countries"] == 2


def test_it_says_how_many_countries_it_takes_to_reach_the_target_at_all():
    ps = in_countries(("A1", "AAA", 400_000, 40.0), ("B1", "BBB", 300_000, 36.0), ("C1", "CCC", 300_000, 30.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=1_000_000, limits=PlanLimits(max_countries=1))["plan"]
    assert plan["countries_needed"] == 3 and plan["feasible"] is False
    assert plan["shortfall"] == pytest.approx(600_000)
    assert market.size("kc", ps, {}, 6.0, {"reach": 100}, target=600_000)["plan"]["countries_needed"] == 2


def test_the_plan_adds_up_by_country_and_the_countries_add_up_to_the_plan():
    ps = in_countries(("A1", "AAA", 400_000, 40.0), ("A2", "AAA", 400_000, 38.0), ("B1", "BBB", 300_000, 36.0))
    plan = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=900_000)["plan"]
    assert [c["country"] for c in plan["countries"]] == ["AAA", "BBB"]
    assert sum(c["reached"] for c in plan["countries"]) == pytest.approx(plan["totals"]["people"])
    assert sum(c["cost"] for c in plan["countries"]) == pytest.approx(plan["totals"]["cost"])
    assert sum(c["deaths_averted"] for c in plan["countries"]) == pytest.approx(plan["totals"]["deaths_averted"])
    assert sum(c["places"] for c in plan["countries"]) == plan["totals"]["places"]


# ----------------------------------------------------------- does the plan hold up
def test_a_plan_that_depends_on_a_high_figure_being_right_does_not_hold_up_when_it_is_only_half_believed():
    # A looks worse (40) than B (38) only by its own figure, which is well above its country's (30); B's is simply its
    # country's. Believe A halfway and it is 35: B is the better place, and the plan that chose A was leaning on one
    # figure.
    ps = {**with_country("A", 600_000, 40.0, 30.0, iso="AAA"), **with_country("B", 600_000, 38.0, 38.0, iso="BBB")}
    chosen = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=600_000)["plan"]
    less = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=600_000, trust=0.5)
    check = market.plan_check(chosen, less["rows"], less["plan"])
    assert [p["key"] for p in chosen["places"]] == ["A"] and [p["key"] for p in less["plan"]["places"]] == ["B"]
    assert check["trust"] == 50 and check["places_in_common"] == 0
    assert check["kept_share"] == pytest.approx(35 / 38, rel=0.02) and check["kept_share"] < 1


def test_a_plan_that_stays_the_best_even_pulled_toward_its_country_holds_up_exactly():
    ps = {
        **with_country("SPIKE", 600_000, 70.0, 28.0, iso="XXX"),
        **with_country("STEADY", 600_000, 30.0, 28.0, iso="YYY"),
    }
    chosen = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=600_000)["plan"]
    less = market.size("kc", ps, {}, 6.0, {"reach": 100}, target=600_000, trust=0.5)
    assert market.plan_check(chosen, less["rows"], less["plan"])["kept_share"] == pytest.approx(1.0)


def test_a_plan_of_places_that_are_alike_holds_up_exactly():
    alike = in_countries(("A", "AAA", 600_000, 30.0), ("B", "BBB", 600_000, 30.0))
    chosen = market.size("kc", alike, {}, 6.0, {"reach": 100}, target=600_000)["plan"]
    less = market.size("kc", alike, {}, 6.0, {"reach": 100}, target=600_000, trust=0.5)
    assert market.plan_check(chosen, less["rows"], less["plan"])["kept_share"] == pytest.approx(1.0)


def test_there_is_nothing_to_check_without_a_plan():
    assert market.plan_check(None, [], None) is None
