"""Ceilings on a plan (conflict, remoteness): they leave a place out when asked, and never touch a multiple."""

import pytest

from connect_labs.effectiveness import market
from connect_labs.effectiveness.scenario import PlanLimits, Scenario
from connect_labs.effectiveness.tests.helpers import boundary, value


def places(*specs):
    """``(key, units, nmr, conflict_per_100k[, remote_share])`` -> places (None is a place Labs has no figure for)."""
    out = {}
    for key, units, nmr, fight, *rest in specs:
        measures = {"nmr": nmr}
        if fight is not None:
            measures["conflict_rate"] = fight
        if rest and rest[0] is not None:
            measures["remote_share"] = rest[0]
        out[key] = {"name": key, "country": key, "iso3": key, "units": units, "measures": measures}
    return out


def plan_of(ps, limits=None, target=900_000):
    return market.size("kc", ps, {}, 6.0, {"reach": 100}, target=target, limits=limits)["plan"]


SAFE_AND_NOT = places(("CALM", 500_000, 25.0, 0.4), ("WAR", 500_000, 40.0, 30.0), ("UNKNOWN", 500_000, 30.0, None))


def test_a_place_above_the_ceiling_is_left_out_and_counted_apart_from_small_ones():
    plan = plan_of(SAFE_AND_NOT, PlanLimits(caps={"max_conflict": 10.0}))
    assert {p["key"] for p in plan["places"]} == {"CALM", "UNKNOWN"}
    assert plan["limits"]["caps"] == {"max_conflict": {"value": 10.0, "left_out": 1}}
    assert plan["limits"]["left_out"] == 1 and plan["limits"]["left_out_small"] == 0


def test_a_place_with_no_conflict_figure_is_kept_because_nothing_says_it_is_unsafe():
    assert "UNKNOWN" in {p["key"] for p in plan_of(SAFE_AND_NOT, PlanLimits(caps={"max_conflict": 0.1}))["places"]}


def test_without_a_ceiling_the_worst_place_is_in_the_plan_and_the_rate_travels_with_it():
    plan = plan_of(SAFE_AND_NOT)
    war = next(p for p in plan["places"] if p["key"] == "WAR")
    assert war["conflict_rate"] == 30.0 and plan["limits"]["left_out"] == 0


def test_the_ceiling_never_changes_a_multiple():
    a = {r["key"]: r["multiple"] for r in market.size("kc", SAFE_AND_NOT, {}, 6.0, target=900_000)["rows"]}
    b = {
        r["key"]: r["multiple"]
        for r in market.size("kc", SAFE_AND_NOT, {}, 6.0, target=900_000, limits=PlanLimits(caps={"max_conflict": 1}))[
            "rows"
        ]
    }
    assert a == b


def test_a_place_both_too_small_and_too_unsafe_is_counted_in_each_but_left_out_once():
    ps = places(("BIG", 800_000, 30.0, 1.0), ("BAD", 5_000, 60.0, 40.0))
    limits = plan_of(ps, PlanLimits(min_place=50_000, caps={"max_conflict": 10.0}), target=700_000)["limits"]
    assert limits["left_out"] == 1 and limits["left_out_small"] == 1
    assert limits["caps"]["max_conflict"]["left_out"] == 1


def test_a_link_carries_the_ceiling_both_ways():
    sc = Scenario.from_params({"max_conflict": "12.5", "min_place": "1000"})
    assert sc.limits() == PlanLimits(min_place=1000.0, max_countries=None, caps={"max_conflict": 12.5})
    assert sc.to_params()["max_conflict"] == "12.5"
    assert "max_conflict" not in Scenario.from_params({}).to_params()
    assert Scenario.from_params({"max_conflict": "0"}).caps == {}  # zero is not a ceiling, it is blank


@pytest.mark.django_db
def test_a_territorys_figure_comes_from_its_own_boundary_and_a_country_from_its_own_row():
    from connect_labs.effectiveness import baseline
    from connect_labs.labs.indicators.models import Source

    ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
    kano = boundary(1, "Kano", "NGA-KN", 0, 0)
    lagos = boundary(1, "Lagos", "NGA-LA", 1, 0)
    value(ng, "conflict_rate", 4.0, source=Source.UCDP)
    value(kano, "conflict_rate", 9.0, source=Source.UCDP)
    b = baseline.Baselines([ng, kano, lagos])
    assert b.place_figure("conflict_rate", "NGA", kano)[0] == 9.0
    assert b.place_figure("conflict_rate", "NGA", None)[0] == 4.0
    assert (
        b.place_figure("conflict_rate", "NGA", lagos) is None
    )  # no figure of its own: never the country's, never a zero


REMOTE = places(
    ("NEAR", 500_000, 30.0, 0.1, 10.0), ("FAR", 500_000, 40.0, 0.1, 70.0), ("NOFIG", 500_000, 30.0, 0.1, None)
)


def test_remoteness_is_a_ceiling_in_the_same_way_and_the_two_can_be_set_together():
    plan = plan_of(REMOTE, PlanLimits(caps={"max_remote": 25.0}))
    assert {p["key"] for p in plan["places"]} == {"NEAR", "NOFIG"}
    assert plan["limits"]["caps"] == {"max_remote": {"value": 25.0, "left_out": 1}}
    both = plan_of(
        places(("A", 500_000, 30.0, 40.0, 10.0), ("B", 500_000, 30.0, 0.1, 70.0), ("C", 500_000, 30.0, 0.1, 10.0)),
        PlanLimits(caps={"max_conflict": 10.0, "max_remote": 25.0}),
        target=400_000,
    )
    assert [p["key"] for p in both["places"]] == ["C"] and both["limits"]["left_out"] == 2


def test_every_row_carries_each_ceilings_figure_and_the_plan_part_does_too():
    got = market.size("kc", REMOTE, {}, 6.0, {"reach": 100}, target=400_000)
    assert {r["key"]: r["remote_share"] for r in got["rows"]} == {"NEAR": 10.0, "FAR": 70.0, "NOFIG": None}
    assert all("remote_share" in p and "conflict_rate" in p for p in got["plan"]["places"])


def test_each_ceiling_names_the_baseline_figure_it_reads_and_every_one_is_read_by_the_layer():
    from connect_labs.effectiveness import baseline
    from connect_labs.effectiveness.scenario import CAP_KEYS, CAPS, caps_for_page

    assert len(set(CAP_KEYS)) == len(CAPS) and all(c.indicator in baseline.MEASURES_READ for c in CAPS)
    assert [c["key"] for c in caps_for_page()] == list(CAP_KEYS)
