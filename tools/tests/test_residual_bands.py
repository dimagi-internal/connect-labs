"""The refusals are the product; the verdict is the easy part.

Every test here pins a state in which a plausible-looking answer COULD be
computed from the numbers present, and must not be. That is the failure mode
this tool exists for: on 2026-09-01 the same question was answered off the
duration-gated stream and returned "self_ms is highest when the tier is idle" --
real data, plentiful, and the exact opposite of the truth, because the gated
population's floor moves with load.

The two structural guarantees (`sampled = 1`, and one `stats ... by` so a band's
fields come from the same requests) are properties of the QUERY, so they are
tested against the query text -- there is no way to observe them from a result.
"""

import importlib.util
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "residual_bands.py"
_spec = importlib.util.spec_from_file_location("residual_bands", _SRC)
residual_bands = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(residual_bands)


def _band(index, label, n=500, duration=900.0, cpu=140.0, self_ms=260.0, outbound=500.0, db=13.0):
    return {
        "band": label,
        "index": index,
        "n": n,
        "duration_ms": duration,
        "cpu_ms": cpu,
        "self_ms": self_ms,
        "outbound_ms": outbound,
        "db_ms": db,
    }


def _hal_1386_bands():
    """The #1386 measurement, window 09-02 06:00-14:00Z, 4,177 sampled requests.

    Kept as the fixture because it is the case the tool was built to re-run: on
    the >=3s band only 217 ms of the 2,689 ms residual was CPU.
    """
    return [
        _band(0, "<1500ms", n=3845, duration=876.0, cpu=141.7, self_ms=265.9, outbound=597.0, db=13.5),
        _band(1, "1500-3000ms", n=242, duration=1942.0, cpu=197.0, self_ms=1153.8, outbound=745.2, db=43.2),
        _band(2, ">=3000ms", n=90, duration=3750.0, cpu=217.0, self_ms=2689.0, outbound=963.1, db=98.1),
    ]


# --- the query is where the two traps are closed ---------------------------


def test_query_filters_on_sampled_not_on_reason():
    """`reason = "sample"` drops requests that are both slow AND sampled.

    A sampled slow request logs reason "slow,sample", so selecting on reason
    removes exactly the slow tail -- re-introducing the bias the unbiased sample
    exists to remove, while looking like it did the right thing.
    """
    q = residual_bands.build_query((1500, 3000), "", allow_biased=False)
    assert "filter sampled = 1" in q
    assert "reason" not in q


def test_query_computes_every_band_statistic_in_one_stats_pass():
    """A band's cpu_ms and self_ms must describe the SAME requests.

    The inference is within-request, so separate per-field queries (or separate
    percentile calls) would compare two different populations and could show a
    residual/CPU gap that no request ever had.
    """
    q = residual_bands.build_query((1500, 3000), "", allow_biased=False)
    assert q.count("| stats ") == 1
    stats_line = next(line for line in q.splitlines() if line.startswith("| stats "))
    for field in ("duration_ms", "cpu_ms", "self_ms", "outbound_ms", "db_ms"):
        assert f"avg({field})" in stats_line
    assert stats_line.rstrip().endswith("by band")


def test_path_prefix_is_a_true_prefix_not_a_substring():
    """`like` is a substring match in Logs Insights, so scoping with it would pull
    in any path that merely CONTAINS the prefix -- which defeats the point of
    scoping to one endpoint."""
    q = residual_bands.build_query((1500,), "/audit/image/", allow_biased=False)
    assert 'startsWith(path, "/audit/image/") = 1' in q
    assert "like" not in q


def test_edges_stay_within_the_case_branch_limit():
    """`case` caps at 10 branches; N edges produce N conditions plus a default, so
    a longer --edges builds a query AWS rejects only after it has been run."""
    assert residual_bands.MAX_EDGES <= 9


def test_allow_biased_drops_the_filter_so_the_stamp_is_load_bearing():
    """--allow-biased genuinely changes the population, which is why the verdict
    it produces has to be a refusal rather than a caveat."""
    q = residual_bands.build_query((1500, 3000), "", allow_biased=True)
    assert "filter sampled = 1" not in q


# --- the refusals ----------------------------------------------------------


def test_biased_run_never_returns_a_real_verdict():
    """Even with a textbook-looking result set, --allow-biased cannot conclude."""
    result = residual_bands.judge(_hal_1386_bands(), allow_biased=True)
    assert result["verdict"] == "uninterpretable"
    assert "artefact" in " ".join(result["findings"])


def test_no_sampled_rows_is_inconclusive_not_healthy():
    """The sample being switched off must not read as a clean bill of health.

    This is the perf_triage.py rule -- an empty query is a broken query, never a
    quiet system.
    """
    result = residual_bands.judge([])
    assert result["verdict"] == "inconclusive"
    assert "TELEMETRY_SAMPLE_RATE" in result["next_step"]


def test_thin_total_sample_is_inconclusive():
    bands = [_band(0, "<1500ms", n=40), _band(1, "1500-3000ms", n=10), _band(2, ">=3000ms", n=5)]
    result = residual_bands.judge(bands)
    assert result["verdict"] == "inconclusive"


def test_thin_TOP_band_is_inconclusive_even_when_the_window_is_busy():
    """The slow tail is where the question lives.

    A window with thousands of fast requests and four slow ones has plenty of
    total data and still cannot answer anything -- and its top band's means
    would look like a confident measurement.
    """
    bands = [
        _band(0, "<1500ms", n=5000),
        _band(1, "1500-3000ms", n=200),
        _band(2, ">=3000ms", n=4, duration=3800.0, cpu=210.0, self_ms=2700.0),
    ]
    result = residual_bands.judge(bands)
    assert result["verdict"] == "inconclusive"
    assert "top band" in " ".join(result["findings"]).lower()


# --- the fork --------------------------------------------------------------


def test_the_1386_measurement_still_reads_as_not_our_cpu():
    """Regression pin on the numbers the diagnosis was argued from.

    217 ms of CPU inside a 2,689 ms residual is 8%. If a change to the
    thresholds ever makes this read as `residual_is_cpu`, the tool would send
    the next investigation to profile a view that does no work.
    """
    result = residual_bands.judge(_hal_1386_bands())
    assert result["verdict"] == "residual_is_not_cpu"
    assert "not running" in result["next_step"].lower()


def test_a_genuine_hot_loop_reads_as_our_python():
    """The detection case matters as much as the refusals: a tool that never
    says "yes, profile it" would just teach the reader to ignore the verdict."""
    bands = [
        _band(0, "<1500ms", n=3000, duration=800.0, cpu=600.0, self_ms=700.0, outbound=80.0, db=20.0),
        _band(1, "1500-3000ms", n=400, duration=2000.0, cpu=1700.0, self_ms=1850.0, outbound=100.0, db=50.0),
        _band(2, ">=3000ms", n=120, duration=4000.0, cpu=3500.0, self_ms=3800.0, outbound=120.0, db=80.0),
    ]
    result = residual_bands.judge(bands)
    assert result["verdict"] == "residual_is_cpu"
    assert "profile" in result["next_step"].lower()


def test_an_ambiguous_split_says_so_rather_than_picking_a_side():
    bands = [
        _band(0, "<1500ms", n=3000),
        _band(1, "1500-3000ms", n=400),
        _band(2, ">=3000ms", n=120, duration=4000.0, cpu=1500.0, self_ms=3000.0),
    ]
    result = residual_bands.judge(bands)
    assert result["verdict"] == "mixed"


def test_a_fully_explained_duration_is_not_a_cpu_question_at_all():
    """self_ms at zero means outbound and db already account for the time --
    dividing by it would be a crash, and 'profile the view' would be nonsense."""
    bands = [
        _band(0, "<1500ms", n=3000),
        _band(1, "1500-3000ms", n=400),
        _band(2, ">=3000ms", n=120, duration=4000.0, cpu=100.0, self_ms=0.0, outbound=3800.0, db=100.0),
    ]
    result = residual_bands.judge(bands)
    assert result["verdict"] == "no_residual"
    assert "outbound_ms" in result["next_step"]


# --- reshaping -------------------------------------------------------------


def test_rows_to_bands_labels_and_orders_bands_from_insights_shape():
    rows = [
        [
            {"field": "band", "value": "2"},
            {"field": "n", "value": "90"},
            {"field": "duration_ms", "value": "3750.4"},
            {"field": "cpu_ms", "value": "217.0"},
            {"field": "self_ms", "value": "2689.0"},
            {"field": "outbound_ms", "value": "963.1"},
            {"field": "db_ms", "value": "98.1"},
        ],
        [
            {"field": "band", "value": "0"},
            {"field": "n", "value": "3845"},
            {"field": "duration_ms", "value": "876.2"},
            {"field": "cpu_ms", "value": "141.7"},
            {"field": "self_ms", "value": "265.9"},
            {"field": "outbound_ms", "value": "597.0"},
            {"field": "db_ms", "value": "13.5"},
        ],
    ]
    bands = residual_bands._rows_to_bands(rows, (1500, 3000))
    assert [b["band"] for b in bands] == ["<1500ms", ">=3000ms"]
    assert bands[0]["n"] == 3845
    assert bands[-1]["cpu_ms"] == 217.0


def test_rows_to_bands_drops_rows_whose_band_is_not_a_real_index():
    """Insights can return a null grouping key; it must not become a band."""
    rows = [
        [{"field": "band", "value": ""}, {"field": "n", "value": "10"}],
        [{"field": "band", "value": "9"}, {"field": "n", "value": "10"}],
    ]
    assert residual_bands._rows_to_bands(rows, (1500, 3000)) == []


@pytest.mark.parametrize("hours", [1, 8, 168])
def test_window_is_utc_floored_and_self_consistent(hours):
    """The epoch bounds and the printed ISO strings must describe one window --
    a result labelled with the wrong window gets compared against the wrong
    baseline on the next run."""
    start_epoch, end_epoch, start_iso, end_iso = residual_bands._window(hours)
    assert end_epoch - start_epoch == hours * 3600
    assert start_iso.endswith("Z") and end_iso.endswith("Z")
    assert start_epoch % 60 == 0 and end_epoch % 60 == 0
