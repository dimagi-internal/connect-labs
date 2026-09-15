"""One test per rule. A bug here is a data incident, not a rendering glitch.

Each test drives a cohort that violates exactly one rule and asserts the value
is withheld, so a failure names the rule that broke.
"""

from connect_labs.benchmarks.disclosure import PeerObservation, anonymise_point, anonymise_series

DEFAULTS = {"min_peers": 5, "min_denominator": 25}


def _obs(opp, value, denominator=100, suppressed=False):
    return PeerObservation(opportunity_id=opp, value=value, denominator=denominator, suppressed=suppressed)


def _six():
    return [_obs(500 + i, 50.0 + i) for i in range(6)]


def test_r1_withholds_an_indicator_with_too_few_contributing_peers():
    assert anonymise_point(_six()[:4], **DEFAULTS) == []
    assert len(anonymise_point(_six(), **DEFAULTS)) == 6


def test_r2_excludes_a_peer_under_the_minimum_denominator():
    obs = _six() + [_obs(999, 12.0, denominator=3)]
    out = anonymise_point(obs, **DEFAULTS)
    assert 999 not in [opp for _, _, opp in out]
    assert len(out) == 6, "the thin peer should be dropped, not shown as a gap"


def test_r2_can_drop_the_set_below_r1():
    """A cohort where only four peers clear the denominator publishes nothing."""
    obs = [_obs(500 + i, 50.0 + i, denominator=100 if i < 4 else 2) for i in range(6)]
    assert anonymise_point(obs, **DEFAULTS) == []


def test_r3_never_returns_a_denominator():
    for entry in anonymise_point(_six(), **DEFAULTS):
        assert len(entry) == 3, "an entry carrying n would identify the opp by size"


def test_r4_orders_peers_by_value_independently_per_indicator():
    """The linkage test: the same opp must not hold the same index everywhere."""
    ind_a = [_obs(1, 10.0), _obs(2, 20.0), _obs(3, 30.0), _obs(4, 40.0), _obs(5, 50.0)]
    ind_b = [_obs(1, 90.0), _obs(2, 20.0), _obs(3, 30.0), _obs(4, 40.0), _obs(5, 50.0)]
    idx_a = {opp: i for i, _, opp in anonymise_point(ind_a, **DEFAULTS)}
    idx_b = {opp: i for i, _, opp in anonymise_point(ind_b, **DEFAULTS)}
    assert idx_a[1] != idx_b[1], "peer index is stable across indicators -- profiles can be joined"
    assert [v for _, v, _ in anonymise_point(ind_a, **DEFAULTS)] == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_r5_publishes_only_periods_where_enough_peers_qualify():
    """A period only one opp reached would name that opp by its launch date."""
    by_period = {
        "2026-01": [_obs(1, 10.0)],
        "2026-02": [_obs(500 + i, 50.0 + i) for i in range(5)],
    }
    out = anonymise_series(by_period, **DEFAULTS)
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
    out = anonymise_series(by_period, **DEFAULTS)
    present = {opp for points in out.values() for _, _, opp in points}
    assert present == {1, 2, 4, 5, 7}, "the surviving set is not the complete-in-every-period one"
    assert 3 not in present, "a series with a hole was published"
    assert 6 not in present, "a series that starts late was published"
    assert set(out) == {"2026-01", "2026-02"}, "the window itself was lost"


def test_r7_a_registry_suppressed_peer_never_enters_the_set():
    obs = _six() + [_obs(999, 99.0, suppressed=True)]
    out = anonymise_point(obs, **DEFAULTS)
    assert 999 not in [opp for _, _, opp in out]


def test_r7_suppression_can_drop_the_set_below_r1():
    obs = [_obs(500 + i, 50.0 + i, suppressed=i >= 4) for i in range(6)]
    assert anonymise_point(obs, **DEFAULTS) == []
