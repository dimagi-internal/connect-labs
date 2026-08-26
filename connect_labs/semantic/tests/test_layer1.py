"""Layer 1 must come from the pipeline, and the paraphrase must stay impossible.

The first parity run against real data was driven by a hand-written extraction
that carried 3 of 10 danger-sign paths, 3 of 6 referral paths and 3 of 4
kmc-hours paths. C19, C20 and C23 -- precisely the indicators over those fields --
were the ones that disagreed with the existing dashboard. These tests pin the
generated form so nobody re-types it.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic.layer1 import MARKER_BOOLEANS, build_visit_sql

# A miniature stand-in for the engine: enough shape to exercise the rewrites,
# with a multi-path COALESCE so the "every path survives" test means something.
FAKE_EXTRACTION = {
    "visit_extraction_sql": (
        "SELECT\n"
        "visit_id,\n"
        "username,\n"
        "visit_date,\n"
        "COALESCE(a->>'p1', a->>'p2', a->>'p3') as danger_visits,\n"
        "COALESCE(a->>'r1', a->>'r2') as referral_visits,\n"
        "COALESCE(a->>'k1') as kmc_hours_mean,\n"
        "COALESCE(a->>'c1') as death_visits,\n"
        "COALESCE(a->>'s1') as self_referral_visits,\n"
        "COALESCE(a->>'e1') as ebf_visits,\n"
        "COALESCE(a->>'f1') as form_names\n"
        "FROM labs_raw_visit_cache AS labs_raw_visit_cache\n"
        "WHERE opportunity_id = 10042 AND pipeline_id = 5108\n"
        "ORDER BY visit_id"
    )
}


def _gen(schema, opportunity_id):
    return FAKE_EXTRACTION


def test_every_extraction_path_survives():
    """The whole point: nothing in the pipeline's expressions is dropped."""
    sql = build_visit_sql({}, [10042, 10016], generate_sql_preview=_gen)
    for path in ("'p1'", "'p2'", "'p3'", "'r1'", "'r2'", "'k1'"):
        assert path in sql, f"path {path} was lost in the rewrite"


def test_widens_to_every_requested_opportunity():
    sql = build_visit_sql({}, [10042, 10016, 10014], generate_sql_preview=_gen)
    assert "opportunity_id IN (10042,10016,10014)" in sql
    assert "opportunity_id = 10042 AND pipeline_id" not in sql


def test_dedupes_across_cache_partitions():
    """labs_raw_visit_cache is keyed by (opportunity, pipeline).

    The same visit is present once per pipeline that cached it; without the
    DISTINCT ON, opp 10042's rows were counted from two partitions and every
    denominator inflated.
    """
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    assert "DISTINCT ON (opportunity_id, visit_id)" in sql
    assert "ORDER BY opportunity_id, visit_id, pipeline_id" in sql


def test_selects_opportunity_id_which_the_extraction_omits():
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    assert "opportunity_id,\npipeline_id,\nvisit_id," in sql


def test_marker_booleans_use_the_pipelines_own_word_test():
    sql = build_visit_sql({}, [10042], generate_sql_preview=_gen)
    for name, (col, word) in MARKER_BOOLEANS.items():
        assert f"(x.{col} ~* '\\y{word}\\y') AS {name}" in sql


def test_no_opportunities_is_an_error():
    with pytest.raises(ValueError, match="at least one opportunity"):
        build_visit_sql({}, [], generate_sql_preview=_gen)
