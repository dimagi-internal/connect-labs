"""One test per rule. A bug here is a data incident, not a rendering glitch.

Each test drives a cohort that violates exactly one rule and asserts the value
is withheld, so a failure names the rule that broke.
"""

import pytest

from connect_labs.benchmarks.disclosure import PeerObservation, anonymise_point, anonymise_series

DEFAULTS = {"min_peers": 5, "min_denominator": 25}


def _obs(opp, value, denominator=100, suppressed=False):
    return PeerObservation(opportunity_id=opp, value=value, denominator=denominator, suppressed=suppressed)


def _six():
    return [_obs(500 + i, 50.0 + i) for i in range(6)]


def test_r1_withholds_an_indicator_with_too_few_contributing_peers():
    assert anonymise_point(_six()[:4], tie_salt="ind-r1", **DEFAULTS) == []
    assert len(anonymise_point(_six(), tie_salt="ind-r1", **DEFAULTS)) == 6


def test_r2_excludes_a_peer_under_the_minimum_denominator():
    obs = _six() + [_obs(999, 12.0, denominator=3)]
    out = anonymise_point(obs, tie_salt="ind-r2", **DEFAULTS)
    assert 999 not in [opp for _, _, opp in out]
    assert len(out) == 6, "the thin peer should be dropped, not shown as a gap"


def test_r2_can_drop_the_set_below_r1():
    """A cohort where only four peers clear the denominator publishes nothing."""
    obs = [_obs(500 + i, 50.0 + i, denominator=100 if i < 4 else 2) for i in range(6)]
    assert anonymise_point(obs, tie_salt="ind-r2b", **DEFAULTS) == []


def test_r2_boundary_denominator_exactly_at_minimum_is_included():
    """A peer AT the threshold (not just above it) must still count."""
    obs = _six()[:5] + [_obs(999, 12.0, denominator=DEFAULTS["min_denominator"])]
    out = anonymise_point(obs, tie_salt="ind-r2c", **DEFAULTS)
    assert 999 in [opp for _, _, opp in out]


def test_r3_never_returns_a_denominator():
    out = anonymise_point(_six(), tie_salt="ind-r3", **DEFAULTS)
    assert out, "fixture too small to exercise the rule"
    for entry in out:
        assert len(entry) == 3, "an entry carrying n would identify the opp by size"


def test_r4_orders_peers_by_value_independently_per_indicator():
    """The linkage test: the same opp must not hold the same index everywhere."""
    ind_a = [_obs(1, 10.0), _obs(2, 20.0), _obs(3, 30.0), _obs(4, 40.0), _obs(5, 50.0)]
    ind_b = [_obs(1, 90.0), _obs(2, 20.0), _obs(3, 30.0), _obs(4, 40.0), _obs(5, 50.0)]
    idx_a = {opp: i for i, _, opp in anonymise_point(ind_a, tie_salt="ind-a", **DEFAULTS)}
    idx_b = {opp: i for i, _, opp in anonymise_point(ind_b, tie_salt="ind-b", **DEFAULTS)}
    assert idx_a[1] != idx_b[1], "peer index is stable across indicators -- profiles can be joined"
    assert [v for _, v, _ in anonymise_point(ind_a, tie_salt="ind-a", **DEFAULTS)] == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_r4_ties_order_differently_with_different_tie_salt():
    """Tied values (common at this cohort size) must not fall back to a stable,
    opportunity-id-derived order -- that would be exactly as joinable as R4
    exists to prevent."""
    obs = [_obs(i, 50.0) for i in range(1, 6)]  # every value tied
    order_a = [opp for _, _, opp in anonymise_point(obs, tie_salt="indicator-a", **DEFAULTS)]
    order_b = [opp for _, _, opp in anonymise_point(obs, tie_salt="indicator-b", **DEFAULTS)]
    assert order_a != order_b, "tie order does not vary with tie_salt -- ties are stable across indicators"


def test_r5_publishes_only_periods_where_enough_peers_qualify():
    """A period only one opp reached would name that opp by its launch date."""
    by_period = {
        "2026-01": [_obs(1, 10.0)],
        "2026-02": [_obs(500 + i, 50.0 + i) for i in range(5)],
    }
    out = anonymise_series(by_period, tie_salt="series-r5", **DEFAULTS)
    assert "2026-01" not in out
    assert "2026-02" in out


def test_r6_drops_a_sparse_series_entirely_rather_than_leaving_holes():
    """Opp 3 misses an in-window period and opp 6 starts late; both series go.

    Sized so five peers survive R6 and the result is NON-EMPTY -- with a thinner
    cohort the function returns {} and the assertions below would pass whatever
    the code did.
    """
    by_period = {
        "2026-01": [_obs(i, 10.0 * i) for i in (1, 2, 3, 4, 5, 7)],
        "2026-02": [_obs(i, 11.0 * i) for i in (1, 2, 4, 5, 6, 7)],
    }
    out = anonymise_series(by_period, tie_salt="series-r6", **DEFAULTS)
    present = {opp for points in out.values() for _, _, opp in points}
    assert present == {1, 2, 4, 5, 7}, "the surviving set is not the complete-in-every-period one"
    assert 3 not in present, "a series with a hole was published"
    assert 6 not in present, "a series that starts late was published"
    assert set(out) == {"2026-01", "2026-02"}, "the window itself was lost"


def test_r7_a_registry_suppressed_peer_never_enters_the_set():
    obs = _six() + [_obs(999, 99.0, suppressed=True)]
    out = anonymise_point(obs, tie_salt="ind-r7", **DEFAULTS)
    assert out, "fixture too small to exercise the rule"
    assert 999 not in [opp for _, _, opp in out]


def test_r7_suppression_can_drop_the_set_below_r1():
    obs = [_obs(500 + i, 50.0 + i, suppressed=i >= 4) for i in range(6)]
    assert anonymise_point(obs, tie_salt="ind-r7b", **DEFAULTS) == []


def test_c1_duplicate_opportunity_in_one_observation_set_raises():
    """R1 must count distinct peers; silently keeping one of two rows for the
    same partner would also misrepresent them. Both mean: fail loudly."""
    obs = _six() + [_obs(500, 999.0)]  # 500 appears twice
    with pytest.raises(ValueError):
        anonymise_point(obs, tie_salt="dup", **DEFAULTS)


def test_c1_duplicate_within_one_series_period_raises():
    by_period = {"2026-01": _six() + [_obs(500, 999.0)]}
    with pytest.raises(ValueError):
        anonymise_series(by_period, tie_salt="dup-series", **DEFAULTS)


def test_c3_peer_index_is_stable_across_periods_in_a_series():
    """Without this, the same peer_index names a different opp in each period
    and the points cannot be connected into a line -- the product this exists
    to serve.

    Values are DELIBERATELY inverted between periods (opp 1 lowest in Jan,
    highest in Feb, and so on) so that sorting each period independently by
    its own values -- the bug -- would rank them in opposite orders. With a
    single series-wide ordering, opp 1's index stays put across both periods
    even though its value swung the most.
    """
    by_period = {
        "2026-01": [_obs(i, 10.0 * i) for i in range(1, 6)],  # 1:10 .. 5:50
        "2026-02": [_obs(i, 60.0 - 10.0 * i) for i in range(1, 6)],  # 1:50 .. 5:10
    }
    out = anonymise_series(by_period, tie_salt="series-c3", **DEFAULTS)
    idx_jan = {opp: i for i, _, opp in out["2026-01"]}
    idx_feb = {opp: i for i, _, opp in out["2026-02"]}
    assert idx_jan == idx_feb, "peer_index must denote the same peer in every period"


def test_c3_series_orders_by_true_mean_and_ignores_out_of_window_periods():
    """The stability test above proves the index doesn't MOVE, but its means all
    tie at 30.0 -- ANY deterministic tiebreak-only key is equally stable, so
    that test has no power over the ordering KEY itself (e.g. `vals[0]`
    instead of an actual mean would pass it too). This test uses five DISTINCT
    means, chosen so that neither in-window period's own values sort the same
    way as the true mean -- a broken "use one period's value" implementation
    is wrong regardless of which period it happened to pick:

      true means (2026-01, 2026-02 only): 1:10  2:20  3:30  4:40  5:50
      2026-01 alone sorts:                1:5   2:15  3:55  4:25  5:35  -> order [1,2,4,5,3]
      2026-02 alone sorts:                1:15  2:25  3:5   4:55  5:65  -> order [3,1,2,4,5]

    Also includes 2025-12, a period with only two peers (1 and 5) -- too few
    to clear R5, so it must be excluded from the window entirely. Its values
    (200.0 for peer 1, 1.0 for peer 5) are extreme specifically so that if the
    ordering computation wrongly folded it in anyway, peer 1's mean would jump
    to ~73 and peer 5's would drop to ~34, inverting their position relative
    to peers 2-4 -- the exact out-of-window contamination R5/R6 exist to keep
    out of a published series.
    """
    by_period = {
        "2025-12": [_obs(1, 200.0), _obs(5, 1.0)],  # too few peers -- excluded by R5
        "2026-01": [_obs(1, 5.0), _obs(2, 15.0), _obs(3, 55.0), _obs(4, 25.0), _obs(5, 35.0)],
        "2026-02": [_obs(1, 15.0), _obs(2, 25.0), _obs(3, 5.0), _obs(4, 55.0), _obs(5, 65.0)],
    }
    out = anonymise_series(by_period, tie_salt="series-c3-mean", **DEFAULTS)
    assert "2025-12" not in out, "the out-of-window period must not be published either"
    expected_order = [1, 2, 3, 4, 5]  # ascending by true mean, computed over 2026-01/02 only
    for period in ("2026-01", "2026-02"):
        order = [opp for _, _, opp in out[period]]
        assert order == expected_order, (
            f"{period}: not the true-mean order -- either a non-mean key was used, "
            "or the out-of-window period contaminated it"
        )


def test_f2_empty_tie_salt_raises_for_point():
    with pytest.raises(ValueError):
        anonymise_point(_six(), tie_salt="", **DEFAULTS)


def test_f2_whitespace_only_tie_salt_raises_for_point():
    with pytest.raises(ValueError):
        anonymise_point(_six(), tie_salt="   ", **DEFAULTS)


def test_f2_empty_tie_salt_raises_for_series():
    with pytest.raises(ValueError):
        anonymise_series({"2026-01": _six()}, tie_salt="", **DEFAULTS)


def test_f2_whitespace_only_tie_salt_raises_for_series():
    with pytest.raises(ValueError):
        anonymise_series({"2026-01": _six()}, tie_salt="   ", **DEFAULTS)


def test_f3_eligible_accepts_a_generator_not_just_a_list():
    """Pre-fix this was a single comprehension over the input, so a generator
    worked; the duplicate-detection pass added a second iteration that would
    silently drain a generator to [] without this fix."""
    gen = (o for o in _six())
    out = anonymise_point(gen, tie_salt="gen-test", **DEFAULTS)
    assert len(out) == 6


@pytest.mark.parametrize("below_the_floor", [0, 1, 2])
def test_i1_min_peers_below_three_raises_for_point(below_the_floor):
    """Three, not two. At two the reader is one of the two contributors, so the
    one bar left is a named peer's exact value."""
    with pytest.raises(ValueError):
        anonymise_point(_six(), min_peers=below_the_floor, min_denominator=25, tie_salt="floor")


@pytest.mark.parametrize("below_the_floor", [0, 1, 2])
def test_i1_min_peers_below_three_raises_for_series(below_the_floor):
    with pytest.raises(ValueError):
        anonymise_series({"2026-01": _six()}, min_peers=below_the_floor, min_denominator=25, tie_salt="floor")


def test_i1_min_peers_of_exactly_three_is_accepted():
    """The floor is a floor, not a ban: a cohort may legitimately sit on it."""
    assert len(anonymise_point(_six()[:3], min_peers=3, min_denominator=25, tie_salt="floor")) == 3


def test_r1_boundary_exactly_min_peers_publishes():
    """Exactly min_peers distinct opportunities must still publish, not be an
    off-by-one victim of a `>` vs `>=` slip."""
    obs = _six()[:5]  # exactly DEFAULTS["min_peers"]
    out = anonymise_point(obs, tie_salt="ind-r1c", **DEFAULTS)
    assert len(out) == 5


def test_empty_input_returns_empty():
    assert anonymise_point([], tie_salt="empty", **DEFAULTS) == []
    assert anonymise_series({}, tie_salt="empty", **DEFAULTS) == {}


def test_single_peer_is_withheld():
    assert anonymise_point([_obs(1, 50.0)], tie_salt="single", **DEFAULTS) == []


def test_all_peers_suppressed_is_withheld():
    obs = [_obs(500 + i, 50.0 + i, suppressed=True) for i in range(6)]
    assert anonymise_point(obs, tie_salt="allsup", **DEFAULTS) == []
