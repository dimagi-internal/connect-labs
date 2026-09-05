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


# ── the N-series tab: the semantic layer finally on screen ───────────────────


def test_the_render_can_load_the_N_series_from_the_semantic_endpoint():
    """Everything else on this dashboard is computed in the browser from pipeline
    rows. These come from SQL, through the endpoint the semantic runtime exposes —
    which is the first time that layer reaches a screen at all."""
    src = RENDER.read_text()
    flat = re.sub(r"\s+", "", src)
    assert "/semantic/?series=N" in flat, "the render must ask for the N series specifically"
    assert "scopes=programme,opportunity,flw" in flat


def test_the_N_series_is_fetched_on_demand_not_with_the_page():
    """It is a real query against the visit cache. Firing it on mount would make
    every other tab pay for a tab the reader may never open."""
    src = RENDER.read_text()
    assert "function loadNSeries" in src
    # no effect hook drives it
    assert "React.useEffect" not in src, "loading must stay user-triggered"


def test_the_endpoints_error_message_is_shown_rather_than_a_generic_failure():
    """The message names the missing column or relation — that IS the diagnostic,
    and it is why the endpoint answers 400 with it instead of a 500."""
    src = RENDER.read_text()
    assert "nSeries.error" in src


def test_N05_is_absent_from_the_rendered_list():
    """Median gestational age needs a Layer 1 field the pipeline has never
    extracted. Listing it would render a permanent n/a that looks like missing data
    rather than a deferred metric."""
    src = RENDER.read_text()
    assert "'n05'" not in src
    for ind in ("n01", "n09", "n13", "n15"):
        assert f"'{ind}'" in src, ind


def test_every_rendered_value_shows_its_denominator():
    """The registry's no-bare-numbers rule, carried through to the screen."""
    src = RENDER.read_text()
    assert "_denominator" in src
