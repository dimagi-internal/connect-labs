"""The refusal is the product: an empty window must never read as health.

This tool exists because three of its inputs return a plausible WRONG answer
rather than an error, and the worst of them is silence. `[SingleFlight]` lines
are emitted only on the loser path -- the winner is silent by design -- so "no
SingleFlight lines" is produced equally by a guard working under no contention
and by a guard that never engaged. Read as reassurance it closes the
investigation, which is the most expensive possible outcome.

So the tests that matter here are not "does it compute the ratio". They pin the
states in which a confident answer COULD be assembled from the numbers present
and must not be.
"""

import importlib.util
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "kill_stampede_triage.py"


@pytest.fixture(scope="module")
def kst():
    spec = importlib.util.spec_from_file_location("kill_stampede_triage", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sf(**over):
    base = {"lent_existing": 0, "no_prior_rows": 0, "lock_unavailable": 0, "other": 0}
    base.update(over)
    return base


def _cursors(**over):
    base = {
        "total_fetches": 0,
        "fetches_without_cursor": 0,
        "distinct_cursors": 0,
        "cursors_repeated_across_streams": 0,
        "top": [],
    }
    base.update(over)
    return base


# --- the refusals -----------------------------------------------------------


def test_empty_window_is_no_evidence_not_health(kst):
    """Zero kills and zero misses is an unmeasured window, not a healthy one."""
    v = kst._verdict({}, _sf(), _cursors(), clipped=False)
    assert v["verdict"] == "no_evidence"
    assert "NOT a clean bill of health" in v["reason"]


def test_absent_singleflight_line_is_never_reported_as_the_guard_working(kst):
    """The winner path is silent, so absence cannot support a positive claim."""
    v = kst._verdict(
        {"b": {"kills": 7, "misses": 48}},
        _sf(),  # no lent_existing
        _cursors(distinct_cursors=16, cursors_repeated_across_streams=9),
        clipped=False,
    )
    note = " ".join(v["notes"])
    assert "NOT evidence the guard works" in note
    assert "demonstrably working" not in note


def test_guard_claimed_only_on_a_positive_loser_line(kst):
    v = kst._verdict(
        {"b": {"kills": 1, "misses": 5}},
        _sf(lent_existing=4),
        _cursors(distinct_cursors=3),
        clipped=False,
    )
    assert "demonstrably working" in " ".join(v["notes"])


def test_clipped_window_is_flagged_rather_than_totalled_silently(kst):
    """A narrow window under-reported a real incident by 39% on 2026-09-08."""
    v = kst._verdict({"b": {"kills": 2, "misses": 9}}, _sf(), _cursors(distinct_cursors=4), clipped=True)
    assert any("CLIPPED" in n for n in v["notes"])


# --- the classifications ----------------------------------------------------


def test_cross_stream_cursor_repeat_is_the_stampede_tell(kst):
    v = kst._verdict(
        {"b": {"kills": 7, "misses": 48}},
        _sf(),
        _cursors(distinct_cursors=16, cursors_repeated_across_streams=9),
        clipped=False,
    )
    assert v["verdict"] == "stampede_present"


def test_repagination_without_cross_stream_repeat_is_not_a_stampede(kst):
    v = kst._verdict(
        {"b": {"kills": 1, "misses": 20}},
        _sf(),
        _cursors(distinct_cursors=12, cursors_repeated_across_streams=0),
        clipped=False,
    )
    assert v["verdict"] == "repagination_without_stampede"


def test_kills_with_no_misses_are_not_blamed_on_rebuild_pressure(kst):
    v = kst._verdict({"b": {"kills": 3, "misses": 0}}, _sf(), _cursors(), clipped=False)
    assert v["verdict"] == "no_repagination_seen"
    assert any("ZERO cache misses" in n for n in v["notes"])


# --- parsing ----------------------------------------------------------------


def test_same_cursor_on_two_streams_is_concurrency_one_stream_is_a_retry(kst):
    out = kst._cursor_repetition(
        [
            {"page_size": "2500", "cursor": "111", "@logStream": "a"},
            {"page_size": "2500", "cursor": "111", "@logStream": "b"},  # two tasks -> concurrency
            {"page_size": "2500", "cursor": "222", "@logStream": "a"},
            {"page_size": "2500", "cursor": "222", "@logStream": "a"},  # one task -> retry
            {"page_size": "2500", "@logStream": "a"},  # first page, no cursor yet
        ]
    )
    assert "111" not in str(out["top"]) and "222" not in str(out["top"])  # visit ids stay out
    assert out["cursors_repeated_across_streams"] == 1
    assert out["distinct_cursors"] == 2
    assert out["fetches_without_cursor"] == 1
    assert out["total_fetches"] == 5


def test_series_folds_both_kinds_into_one_bucket(kst):
    s = kst._series(
        [
            {"t": "T1", "kind": "was sent SIGKILL", "n": "7"},
            {"t": "T1", "kind": "Raw cache MISS", "n": "48"},
        ]
    )
    assert s == {"T1": {"kills": 7, "misses": 48}}


def test_cursor_regex_matches_a_real_fetch_line(kst):
    """The query's own regex, against the log line shape recorded on #1361."""
    line = (
        "GET https://connect.dimagi.com/api/opportunity/2154/user_visits/"
        '?page_size=2500&last_id=1766518 "HTTP/1.1 200 OK"'
    )
    pattern = re.search(r"/last_id=\(\?<cursor>(.+?)\)/", kst.Q_CURSORS).group(1)
    assert re.search(f"last_id=(?P<cursor>{pattern})", line).group("cursor") == "1766518"


def test_naive_timestamp_is_refused_rather_than_read_as_local(kst):
    """A naive string would shift the whole window by the UTC offset, silently."""
    with pytest.raises(Exception):
        kst._parse_utc("2026-09-16T07:30:00")
    assert kst._parse_utc("2026-09-16T07:30:00Z").tzinfo is not None


def test_queries_pin_the_two_fields_the_analysis_depends_on(kst):
    """@logStream distinguishes concurrency from retry; the 30m bin matches the alarm."""
    assert "@logStream" in kst.Q_CURSORS
    assert f"bin(@timestamp, {kst.BUCKET_MINUTES}m)" in kst.Q_KILLS_AND_MISSES
    assert kst.BUCKET_MINUTES == 30  # labs-jj-web-worker-kill-rate's own Period


# --- the post-#2776 line shape ----------------------------------------------
# #2776 dropped query strings (the cursor is a visit id). The logger now appends
# `[query: page_size=2500 paginated=true has_cursor=true]`; these pin that the
# tool reads it, and that lines carrying neither shape are not read as health.

NEW_LINE = (
    "INFO 2026-10-30 10:01:02,123 httpx HTTP Request: GET "
    'https://connect.dimagi.com/export/opportunity/{id}/user_visits/?… "HTTP/1.1 200 OK" '
    "[query: page_size=2500 paginated=true has_cursor=false]"
)


def _query_regex(kst, name):
    return re.search(rf"/{name}=\(\?<{name}>(.+?)\)/", kst.Q_CURSORS).group(1)


def test_query_parses_the_new_facts_suffix(kst):
    assert "@message like /HTTP Request/" in kst.Q_CURSORS
    assert re.search(f"page_size=(?P<v>{_query_regex(kst, 'page_size')})", NEW_LINE).group("v") == "2500"
    assert re.search(f"has_cursor=(?P<v>{_query_regex(kst, 'has_cursor')})", NEW_LINE).group("v") == "false"
    assert "@message" not in kst.Q_CURSORS.splitlines()[0]  # raw bodies are never returned


def test_two_streams_repaginating_in_one_bin_is_concurrent_repagination(kst):
    rows = [
        {"@timestamp": "2026-10-30 10:00:05.000", "@logStream": "a", "page_size": "2500", "has_cursor": "false"},
        {"@timestamp": "2026-10-30 10:00:40.000", "@logStream": "b", "page_size": "2500", "has_cursor": "false"},
        {"@timestamp": "2026-10-30 10:01:10.000", "@logStream": "a", "page_size": "2500", "has_cursor": "true"},
        {"@timestamp": "2026-10-30 10:30:00.000", "@logStream": "a", "page_size": "2500", "has_cursor": "true"},
    ]
    out = kst._cursor_repetition(rows)
    assert (out["new_style_fetches"], out["walks_started"]) == (4, 2)
    assert (out["concurrent_bins"], out["concurrent_walk_start_bins"], out["max_streams_in_bin"]) == (1, 1, 2)
    v = kst._verdict({"b": {"kills": 4, "misses": 10}}, _sf(), out, clipped=False)
    assert v["verdict"] == "concurrent_repagination"
    assert "SAME opportunity" in v["reason"]


def test_one_stream_repaginating_is_not_concurrency(kst):
    rows = [
        {"@timestamp": "2026-10-30 10:00:05.000", "@logStream": "a", "page_size": "2500", "has_cursor": "false"},
        {"@timestamp": "2026-10-30 10:00:09.000", "@logStream": "a", "page_size": "2500", "has_cursor": "true"},
    ]
    v = kst._verdict({"b": {"kills": 1, "misses": 3}}, _sf(), kst._cursor_repetition(rows), clipped=False)
    assert v["verdict"] == "repagination_without_stampede"


def test_lines_with_neither_shape_are_blind_not_probably_not_1361(kst):
    """2026-10-29 .. facts-suffix deploy: no query, no facts. Silence is not evidence."""
    rows = [{"@timestamp": "2026-10-29 12:00:00.000", "@logStream": "a"}] * 3
    out = kst._cursor_repetition(rows)
    assert out["blind_fetches"] == 3
    v = kst._verdict({"b": {"kills": 5, "misses": 9}}, _sf(), out, clipped=False)
    assert v["verdict"] == "blind_window"
    assert "probably not" not in v["reason"]


def test_other_page_sizes_are_not_the_stampede(kst):
    rows = [
        {"@timestamp": "2026-10-30 10:00:05.000", "@logStream": s, "page_size": "500", "has_cursor": "true"}
        for s in ("a", "b")
    ]
    out = kst._cursor_repetition(rows)
    assert out["total_fetches"] == 0 and out["concurrent_bins"] == 0
