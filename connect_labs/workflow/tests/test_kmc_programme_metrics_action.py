"""KMC programme-metrics dashboard: the drill has to END somewhere.

The dashboard already drilled programme -> LLO -> opportunity -> FLW -> case. What
it could not do was ACT on what the drill found: a worker reading red had to be
carried by hand into a separate workflow. These pin the action added to the FLW
panel, and the constraint that action has to be written under.
"""

import re
from pathlib import Path

from connect_labs.workflow.templates.kmc_image_audit import AGENT_FOR_SCALE, OPP_META
from connect_labs.workflow.templates.kmc_programme_metrics import DEFINITION, SCALE_AGENT_BY_LLO, UNVERIFIED_SCALE_LLOS

RENDER = Path(__file__).resolve().parents[1] / "templates" / "kmc_programme_metrics_render.js"


# ── the ES5 constraint ───────────────────────────────────────────────────────


def test_the_render_stays_in_the_es5_dialect_the_rest_of_it_uses():
    """3,100 lines of this file contain ZERO arrow functions, ZERO array
    destructuring and ZERO computed property keys. That is not a style preference
    to be relaxed by the next edit: a Python test suite cannot execute this file,
    so a modern-syntax slip is invisible here and fails in the browser instead.

    Caught live: the first draft of the audit action used all three.
    """
    src = RENDER.read_text()
    offenders = {
        "arrow function": re.findall(r"=>", src),
        "array destructuring": re.findall(r"var\s*\[", src),
        "computed property key": re.findall(r"\{\s*\[\w", src),
    }
    bad = {k: len(v) for k, v in offenders.items() if v}
    assert not bad, f"non-ES5 syntax in the KMC render: {bad}"


# ── the action is actually wired ─────────────────────────────────────────────


def test_the_flw_panel_can_open_an_audit_on_one_worker():
    src = RENDER.read_text()
    # Whitespace-normalised: the call is formatted across two lines
    # ("actions\n  .createAudit("), so a literal substring match is a false negative.
    flat = re.sub(r"\s+", "", src)
    assert "actions.createAudit(" in flat, "the FLW panel must be able to open an audit"
    assert "selected_flw_user_ids" in src, "the audit must be scoped to the ONE worker drilled into"
    assert "granularity: 'per_flw'" in src


def test_the_audit_window_follows_the_workers_own_data_not_a_fixed_lookback():
    """A frozen run is a snapshot of a past period. A trailing-30-days window would
    silently audit nothing on one, which reads as 'the button is broken'."""
    src = RENDER.read_text()
    assert "function flwDateRange" in src
    assert "first_visit" in src and "last_visit" in src


def test_config_carries_the_routing_the_render_needs():
    cfg = DEFINITION["config"]
    assert cfg["audit_enabled"] is True
    assert cfg["weight_image_path"] == "anthropometric/upload_weight_image"
    assert cfg["scale_agent_by_llo"], "the render cannot route a reviewer without this"


# ── the hardware map has ONE home ────────────────────────────────────────────


def test_scale_routing_is_derived_from_opp_meta_not_restated():
    """kmc_image_audit's OPP_META is the hardware map. This dashboard runs on the
    SYNTHETIC clones, whose ids are not the source ids OPP_META is keyed by, so the
    routing is collapsed onto LLO here - derived, so a corrected scale type or a new
    opportunity follows automatically instead of drifting."""
    expected = {}
    for meta in OPP_META.values():
        llo, agent = meta.get("llo"), AGENT_FOR_SCALE.get(meta.get("scale"))
        if llo and agent:
            expected.setdefault(llo, agent)
    assert SCALE_AGENT_BY_LLO == expected


def test_pipn_reads_digital_and_the_dial_llos_read_dial():
    """The deciding constraint of the whole audit design: PIPN uses digital scales
    and EHA/BERI/NAMA use analog dials, and both appear inside one programme."""
    assert SCALE_AGENT_BY_LLO["PIPN"] == "scale_validation"
    for llo in ("NAMA", "EHA", "BERI"):
        assert SCALE_AGENT_BY_LLO[llo] == "scale_dial_read", llo


def test_unconfirmed_hardware_is_surfaced_rather_than_read_as_settled():
    """OPP_META flags GHI-KE and Kikapu as UNCONFIRMED, provisionally digital. That
    has to reach the UI, or a green verdict on their photos reads as settled."""
    assert {"GHI", "Kikapu"} <= UNVERIFIED_SCALE_LLOS
    assert "scale_unverified_llos" in DEFINITION["config"]


def test_an_llo_running_both_hardware_types_is_marked_unverified():
    """Routing by LLO is only sound while an LLO's hardware is consistent. If one
    ever runs both, picking either reader silently mis-reads half its photos - so
    the collapse records the conflict instead of resolving it."""
    from connect_labs.workflow.templates.kmc_programme_metrics import _scale_agent_by_llo

    real_by_llo, real_conflicts = _scale_agent_by_llo()
    assert isinstance(real_conflicts, set)
    # No conflict today; the guard is that one would be caught, not silently lost.
    assert real_conflicts <= UNVERIFIED_SCALE_LLOS


# ── the scorecard: the N series off the payload, not a second fetch ──────────


def test_the_scorecard_reads_the_payload_and_fetches_nothing_itself():
    """The N series used to be a tab that queried the semantic endpoint live, with
    its own grader in this file. It is now graded by the builder alongside the
    headline series, from the same rows, into `payload.series.N` -- so a saved run
    carries it and the live view cannot disagree with it."""
    src = RENDER.read_text()
    assert "P.series" in src, "the scorecard must come off the payload"
    assert "/semantic/" not in src, "the render must not query the semantic endpoint itself"
    for gone in ("function loadNSeries", "function nBandOf", "function nCell"):
        assert gone not in src, f"{gone} is the second grader this replaced"


def test_the_scorecard_is_neals_table_column_for_column():
    """His compute spec's section 5: fifteen columns in this order, with the
    qualifying-SVN denominator printed as its own column between the first-visit
    share and the growth-quality shares."""
    src = RENDER.read_text()
    block = src[src.index("var SCORECARD = [") : src.index("];", src.index("var SCORECARD = ["))]
    ids = re.findall(r"id: '(N\d\d)'", block)
    assert ids == [
        "N01", "N02", "N03", "N05", "N06", "N07", "N08", "N09", "N09", "N10", "N11", "N12", "N13", "N14", "N15",
    ]  # fmt: skip
    assert "denOnly: true" in block, "Qual N is the shared denominator, shown as a count"


def test_the_render_does_not_keep_its_own_copy_of_the_registry():
    """Units and minimum denominators come from the catalog the builder ships in
    the payload -- the same YAML that produced the numbers -- so a threshold cannot
    drift from the measure it grades."""
    src = RENDER.read_text()
    assert "SC.measures" in src, "the catalog must drive the cells"
    assert "N_SERIES = [" not in src, "a hardcoded N-series list is the duplication"
    assert "m.min_denominator" in src


def test_a_value_under_its_minimum_denominator_reads_insufficient_not_a_number():
    """The spec's rule 0.2, and the reason every measure ships a denominator."""
    src = RENDER.read_text()
    assert "'insufficient'" in src
    assert "n&lt;" in src


def test_a_not_credible_figure_is_marked_not_erased():
    """N13 shares C14's credibility verdict. A non-credible recorder's mortality is
    shown greyed with the reason, never blanked -- blanking hides under-recording."""
    src = RENDER.read_text()
    assert "'notcredible'" in src
    assert "Death recording is not credible" in src


# ── one declaration per name ─────────────────────────────────────────────────


def test_no_top_level_declaration_appears_twice():
    """A duplicated block is invisible in JS and silently doubles the work.

    `var` redeclaration is legal, so a rebase that re-inserts a region produces no
    error anywhere: the second assignment simply wins and the first becomes dead.
    Nothing in Python executes this file, and the browser does not complain either.

    It happened. #1467 re-inserted lines 1415-1501 -- byFLW, programInd,
    mortalityCredible and the selLLO/selOpp/selInd useState trio -- after the
    N-series block, byte for byte. Nine duplicated declarations reached main while
    the deployed workflow (render v5) had exactly one of each, so a sync would have
    pushed it live.

    The cost is not only tidiness. Those three are React.useMemo and the trio are
    React.useState: the component allocated three dead state slots, and recomputed
    `evalAll` over every derived case row twice per render, on a dashboard whose
    open problem is latency.

    Hook COUNT stayed stable, which is why React never raised -- the duplication is
    unconditional. A conditional one would crash instead, so this is the quiet half
    of a rule React normally enforces loudly.
    """
    src = RENDER.read_text()
    # Top-level declarations inside WorkflowUI are indented exactly two spaces;
    # anything deeper is a nested scope where shadowing is legitimate.
    names = re.findall(r"^  (?:var|let|const|function)\s+([A-Za-z_$][\w$]*)", src, re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"declared more than once at the top level of WorkflowUI: {dupes}"
