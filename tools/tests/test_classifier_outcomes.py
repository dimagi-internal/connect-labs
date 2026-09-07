"""The refusals are the product; the verdict is the easy part.

Every test here pins a state in which a plausible-looking answer COULD be computed
from the numbers present, and must not be. That is the failure this tool exists
for: the 2026-09-07 re-run of #1231 found 12 timeouts in 45,536 calls -- real,
plentiful, and completely silent on a concurrency bug, because across seven days
exactly ONE window put two runs on the gateway at once. Reported as a pass it would
have closed #1231 on a control that never ran.

The subtle half is that `SATURATING_AGENTS` gates the READOUT and not the LOAD, and
the two pull in opposite directions: a MUAC-only overlap is load with no signal,
while a scale-run overlapping a MUAC run is both. The first version of the tool
filtered the concurrency sweep itself and so reported "NONE" for a week that
genuinely contained the second case -- the right verdict for the wrong reason.
Both directions are pinned below.
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "classifier_outcomes.py"
_spec = importlib.util.spec_from_file_location("classifier_outcomes", _SRC)
classifier_outcomes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(classifier_outcomes)

co = classifier_outcomes
T0 = datetime(2026, 9, 7, 9, 0, 0, tzinfo=timezone.utc)


def _run(run_id, start_s, end_s, agents, attempted=100, errors=0, truncated=False):
    summary = (
        None
        if truncated
        else {
            "elapsed_s": float(end_s - start_s),
            "attempted": attempted,
            "passed": attempted - errors,
            "failed": 0,
            "errors": errors,
        }
    )
    return {
        "run_id": run_id,
        "started": T0 + timedelta(seconds=start_s),
        "ended": T0 + timedelta(seconds=end_s),
        "first": T0 + timedelta(seconds=start_s),
        "last": T0 + timedelta(seconds=end_s),
        "agents": list(agents),
        "summary": summary,
        "truncated": truncated,
    }


def _serial_scale_runs():
    """The 2026-09-07 shape: scale runs back-to-back with ~20s gaps, never overlapping."""
    return [
        _run("69041a81", 0, 247, ["scale_dial_read"], attempted=49),
        _run("becc8067", 271, 703, ["scale_dial_read"], attempted=87),
        _run("d264aed9", 727, 802, ["scale_dial_read"], attempted=24),
        _run("3abd3b9b", 4819, 4932, ["scale_validation"], attempted=145),
    ]


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


def test_back_to_back_runs_are_not_concurrent():
    """A run ending as another begins is a handoff, not an overlap.

    Counting it as one would manufacture exactly the windows this tool refuses to
    invent -- and the real data is full of 20-40s handoffs that a sloppy interval
    test reads as overlap.
    """
    peak, windows = co.concurrency(_serial_scale_runs(), co.SATURATING_AGENTS)
    assert peak == 1
    assert windows == []


def test_touching_intervals_are_not_concurrent():
    runs = [_run("a", 0, 100, ["scale_dial_read"]), _run("b", 100, 200, ["scale_dial_read"])]
    peak, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    assert peak == 1
    assert windows == []


def test_peak_is_simultaneous_not_pairwise():
    """#1231's incident was FIVE at once. A pairwise read reports that as 2."""
    runs = [_run(str(i), 0, 1000, ["scale_validation"]) for i in range(5)]
    peak, _ = co.concurrency(runs, co.SATURATING_AGENTS)
    assert peak == 5


def test_muac_only_overlap_loads_the_gateway_but_is_not_measurable():
    """MUAC x MUAC is real load on the shared gateway and an unusable readout.

    It counts toward `peak` -- they share one gateway, so a `muac_*` run puts calls
    on it exactly like a `scale_*` one -- but it is not REPORTED as a window,
    because MUAC error rates stayed ~99.9% clean through the worst periods and so
    measure nothing.
    """
    runs = [
        _run("a", 0, 800, ["muac_overzoom", "muac_match"]),
        _run("b", 640, 770, ["muac_overzoom", "muac_match"]),
    ]
    peak_any, windows_any = co.concurrency(runs)
    assert peak_any == 2 and len(windows_any) == 1

    peak_req, windows_req = co.concurrency(runs, co.SATURATING_AGENTS)
    assert peak_req == 2, "load is load: the sweep must not be filtered down"
    assert windows_req == [], "but a MUAC-only overlap is not a measurable window"


def test_a_scale_run_overlapping_a_muac_run_IS_a_measurable_window():
    """The case the first version of this tool got wrong.

    On 2026-09-01 21:10 a `scale_dial_read` run (c43336f7) overlapped a MUAC run
    (5be9d869). Filtering the sweep to `scale_*` reported "peak 1, NONE" for a
    period that genuinely put two runs on the gateway at once -- up to 80 concurrent
    calls against a plateau of ~20, which is exactly the condition under test.
    """
    runs = [
        _run("5be9d869", 0, 800, ["muac_overzoom", "muac_match"]),
        _run("c43336f7", 640, 770, ["scale_dial_read"], attempted=24, errors=0),
    ]
    peak, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    assert peak == 2
    assert len(windows) == 1
    assert set(windows[0]["runs"]) == {"5be9d869", "c43336f7"}


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


def test_serial_week_with_perfect_outcomes_is_inconclusive_not_a_pass():
    """THE test. Clean numbers + no concurrency must never read as "resolved"."""
    runs = _serial_scale_runs()
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    result = co.judge(runs, windows)
    assert result["verdict"] == "inconclusive"
    assert "concurrent" in result["reason"]
    assert "NOT evidence" in result["reason"]


def test_no_saturating_run_at_all_is_inconclusive():
    """A week of pure MUAC traffic never touches the path #1231 is about."""
    runs = [_run("a", 0, 500, ["muac_overzoom"]), _run("b", 600, 900, ["muac_match"])]
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    result = co.judge(runs, windows)
    assert result["verdict"] == "inconclusive"
    assert "never exercised" in result["reason"]


def test_one_concurrent_window_is_not_enough():
    """One overlap is an anecdote in either direction -- #1231's own 79% was one pair."""
    runs = [
        _run("a", 0, 1000, ["scale_validation"], attempted=100, errors=0),
        _run("b", 500, 1500, ["scale_validation"], attempted=100, errors=0),
    ]
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    assert len(windows) == 1
    assert co.judge(runs, windows)["verdict"] == "inconclusive"


def test_concurrent_windows_with_tiny_denominators_are_inconclusive():
    """`attempted=1, errors=1` is not a 100% failure rate."""
    runs = []
    for i in range(4):
        runs.append(_run(f"a{i}", i * 10, 5000, ["scale_validation"], attempted=3, errors=3))
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    assert len(windows) >= co.MIN_CONCURRENT_WINDOWS
    result = co.judge(runs, windows)
    assert result["verdict"] == "inconclusive"
    assert "denominator" in result["reason"]


def test_a_run_with_no_completion_summary_cannot_carry_a_rate():
    """A crashed or still-in-flight run has no errors count -- it must not imply zero."""
    runs = [_run(f"a{i}", i * 10, 5000, ["scale_validation"], truncated=True) for i in range(4)]
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    assert co.judge(runs, windows)["verdict"] == "inconclusive"


# --------------------------------------------------------------------------- #
# The verdicts, once the load was genuinely applied
# --------------------------------------------------------------------------- #


def _overlapping(errors_each, attempted=100, n=4):
    return [
        _run(f"r{i}", i * 10, 5000, ["scale_validation"], attempted=attempted, errors=errors_each) for i in range(n)
    ]


def test_concurrent_and_failing_reproduces():
    runs = _overlapping(errors_each=80)
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    result = co.judge(runs, windows)
    assert result["verdict"] == "reproduced"
    assert result["concurrent_error_rate"] == 0.8


def test_concurrent_and_clean_does_not_reproduce():
    runs = _overlapping(errors_each=5)
    _, windows = co.concurrency(runs, co.SATURATING_AGENTS)
    result = co.judge(runs, windows)
    assert result["verdict"] == "not_reproduced"
    assert "load WAS applied" in result["reason"]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parses_single_agent_run_start():
    msg = "[AIReview:9f22265f] Running agent 'scale_validation' on 1 sessions"
    assert co._parse_agents(msg) == {"scale_validation"}


def test_parses_per_image_type_agent_ids():
    msg = (
        "[AIReview:a3490291] Per-image-type review on 33 sessions: "
        "{'muac_group/x': [{'agent_id': 'muac_overzoom', 'auto_apply_actions': ['fail_overzoomed'], "
        "'comparison_field': None}, {'agent_id': 'muac_match', 'auto_apply_actions': "
        "['fail_unmatched'], 'comparison_field': 'muac_group/y'}]}"
    )
    assert co._parse_agents(msg) == {"muac_overzoom", "muac_match"}


def test_truncated_per_image_type_still_yields_the_agents_it_showed():
    """CloudWatch truncates long messages; a repr cut mid-dict must not lose the
    agent names plainly present in the intact prefix. This is why the parse is a
    regex over `agent_id` rather than literal_eval of the whole structure."""
    msg = (
        "[AIReview:a3490291] Per-image-type review on 33 sessions: "
        "{'muac_group/x': [{'agent_id': 'muac_overzoom', 'auto_apply_ac"
    )
    assert co._parse_agents(msg) == {"muac_overzoom"}


def test_outcome_query_groups_by_agent_so_no_blended_rate_is_possible():
    """Structural: the mix trap is closed by the QUERY, not by presentation.

    A `stats count(*) by outcome` with no `agent` key cannot be un-blended after
    the fact, so this is the one place the guarantee can be observed.
    """
    assert "by agent, outcome" in co.OUTCOME_QUERY
