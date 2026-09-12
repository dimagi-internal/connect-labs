"""The dashboard's headline count must come from the scorecard, not the rows.

The subline used `meta.cases` -- the length of the snapshot's case list, which is
the PIPELINE's grain: its own cache partition, its own grouping. Every indicator
on the same page counts Layer 2's per-baby rows instead, keyed
(opportunity, baby_case_id) and skipping a null key. On the real KMC cohort
(2026-09-11) that read 8,850 babies above a scorecard whose C05 said 8,823 --
and 8,823 is the figure that matched Neal's workbook exactly. The header was the
wrong one, and a header contradicting its own table reads as a broken page.
"""

import re
from pathlib import Path

RENDER = Path(__file__).resolve().parents[1] / "templates" / "kmc_programme_metrics_render.js"


def _flat():
    return re.sub(r"\s+", "", RENDER.read_text())


def test_the_headline_count_comes_from_c05_not_the_case_rows():
    flat = _flat()
    assert "functiontotalCases(ind,fallback)" in flat, "the indicator-first helper must exist"
    assert "nCount(totalCases(programInd,meta.cases))" in flat, (
        "the programme subline must read the C05 indicator, falling back to meta.cases "
        "only for a saved run from before the scorecard carried it"
    )
    assert "nCount(meta.cases)" not in flat, "no scope may headline the raw snapshot row count again"


def test_one_organisation_headlines_the_same_measure_as_the_programme():
    """C01 is REGISTERED cases. Using it here made the word 'babies' mean a
    different number depending on which scope you had drilled into."""
    flat = _flat()
    assert "nCount(totalCases(scopeLLO&&scopeLLO.ind,null))" in flat
    assert "entryOf(scopeLLO&&scopeLLO.ind,'C01')" not in flat


def test_the_helper_prefers_a_real_zero_over_the_fallback():
    """`n: 0` is an answer -- an empty week -- and `||` would discard it."""
    src = RENDER.read_text()
    body = src[src.index("function totalCases(") :]
    body = body[: body.index("\n  }")]
    assert "!== null" in body and "!== undefined" in body, "must test presence, not truthiness"
