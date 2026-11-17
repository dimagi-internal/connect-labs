"""What the sizing reads from the baseline, declared once: a rebuild of the baseline can be checked against it."""

import ast
from pathlib import Path

from connect_labs.effectiveness import baseline, market
from connect_labs.labs.indicators import measures, policy

HERE = Path(__file__).resolve().parent.parent


def test_every_measure_the_layer_reads_is_registered_and_has_a_source_policy():
    for code in baseline.MEASURES_READ:
        assert code in measures.MEASURES, f"{code} is read but no longer registered"
        assert policy.POLICY.get(code), f"{code} is read but no source is eligible for it"


def test_the_counts_and_risks_the_market_uses_are_among_them():
    used = set(market.count_measures()) | set(market.risk_measures())
    assert used <= set(baseline.MEASURES_READ) | {"births", "pop_u5", "births_lbw"}, sorted(used)


def test_no_measure_is_named_in_baseline_py_that_is_not_listed():
    tree = ast.parse((HERE / "baseline.py").read_text())
    named = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    unlisted = sorted((named & set(measures.MEASURES)) - set(baseline.MEASURES_READ))
    assert (
        not unlisted
    ), f"baseline.py reads {unlisted}; add them to MEASURES_READ so a rebuild is checked against them"


def test_the_country_series_it_prefetches_are_all_read():
    assert set(baseline.COUNTRY_MEASURES) <= set(baseline.MEASURES_READ)
