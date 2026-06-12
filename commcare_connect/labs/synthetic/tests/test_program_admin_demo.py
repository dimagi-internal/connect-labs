"""Unit tests for program_admin_demo seed helpers.

The seed itself (``program_admin_demo_seed``) is an integration-level
orchestration over WorkflowDataAccess / FlagsDataAccess / AuditDataAccess /
TaskDataAccess and is exercised end-to-end by the recorder workflow.
These tests cover the smaller pieces whose behavior would otherwise be
invisible until a re-seed silently leaves stale render code in place.
"""

from unittest.mock import MagicMock


def test_refresh_render_code_writes_when_def_has_no_render_code():
    """A definition with no render_code record yet (shouldn't happen
    via create_from_template, but is the natural empty state on
    upsert) gets the current template source saved."""
    from commcare_connect.labs.synthetic.program_admin_demo import _refresh_render_code

    wda = MagicMock()
    wda.get_render_code.return_value = None
    definition = MagicMock(id=42)

    changed = _refresh_render_code(wda, definition, "chc_nutrition_analysis")

    assert changed is True
    wda.save_render_code.assert_called_once()
    kwargs = wda.save_render_code.call_args.kwargs
    assert kwargs["definition_id"] == 42
    assert kwargs["component_code"]  # non-empty
    assert kwargs["version"] == 1  # 0 + 1 for fresh write


def test_refresh_render_code_writes_when_existing_is_stale():
    """The common case: a re-seed finds the def already has a
    render_code, but its component_code is from a prior deploy. The
    helper should rewrite and bump the version."""
    from commcare_connect.labs.synthetic.program_admin_demo import _refresh_render_code

    wda = MagicMock()
    stale = MagicMock(data={"component_code": "function WorkflowUI() { return null; }", "version": 5})
    wda.get_render_code.return_value = stale
    definition = MagicMock(id=42)

    changed = _refresh_render_code(wda, definition, "chc_nutrition_analysis")

    assert changed is True
    wda.save_render_code.assert_called_once()
    kwargs = wda.save_render_code.call_args.kwargs
    assert kwargs["definition_id"] == 42
    assert kwargs["component_code"]
    assert kwargs["component_code"] != "function WorkflowUI() { return null; }"
    assert kwargs["version"] == 6  # bumped


def test_refresh_render_code_skips_when_already_current():
    """No-op when the def's render_code is already byte-for-byte
    identical to the template's. Skipping avoids version-number churn
    on re-seeds against an already-fresh def."""
    from commcare_connect.labs.synthetic.program_admin_demo import _refresh_render_code
    from commcare_connect.workflow.templates import get_template

    template = get_template("chc_nutrition_analysis")
    assert template, "chc_nutrition_analysis template must be registered for this test"

    wda = MagicMock()
    current = MagicMock(data={"component_code": template["render_code"], "version": 7})
    wda.get_render_code.return_value = current
    definition = MagicMock(id=42)

    changed = _refresh_render_code(wda, definition, "chc_nutrition_analysis")

    assert changed is False
    wda.save_render_code.assert_not_called()


def test_refresh_render_code_unknown_template_returns_false():
    """Defensive: an unrecognized template key shouldn't raise — the
    helper just no-ops. Avoids killing a seed when a future caller
    typos the template_key."""
    from commcare_connect.labs.synthetic.program_admin_demo import _refresh_render_code

    wda = MagicMock()
    definition = MagicMock(id=42)

    changed = _refresh_render_code(wda, definition, "does_not_exist")

    assert changed is False
    wda.save_render_code.assert_not_called()


# ---------------------------------------------------------------------------
# Auto-flag mirror + flag seeding for COMPLETED runs (round-2 realism fixes).
# Completed seeded runs are never mounted while open, so the chc render's
# ensureAutoFlags never ran for them — the PAR drill panels showed FLAGS "—"
# beside coaching tasks asserting the rule. The generator now seeds the
# equivalent Flag records at generation time.
# ---------------------------------------------------------------------------


def _pipeline_row(archetype: str, *, flagged: bool, kpi_issue: str | None, flw_id: str = "f", seed: int = 7):
    from commcare_connect.labs.synthetic.archetypes import build_flw_pipeline_row

    return build_flw_pipeline_row(
        flw_id=flw_id,
        archetype=archetype,
        flagged_this_week=flagged,
        rng_seed=seed,
        kpi_issue=kpi_issue,
    )


def test_auto_flags_for_flagged_muac_row_trip_sam_and_mam_low():
    from commcare_connect.labs.synthetic.program_admin_demo import _auto_flags_for_row

    row = _pipeline_row("improver_closed_satisfactory", flagged=True, kpi_issue="muac")
    keys = {f["flag_key"] for f in _auto_flags_for_row(row)}
    assert keys == {"sam_low", "mam_low"}


def test_auto_flags_for_gender_skew_row_trip_gender_only():
    from commcare_connect.labs.synthetic.program_admin_demo import _auto_flags_for_row

    row = _pipeline_row("improver_closed_satisfactory", flagged=True, kpi_issue="gender")
    flags = _auto_flags_for_row(row)
    assert {f["flag_key"] for f in flags} == {"gender_skew"}
    female_pct = flags[0]["evidence"]["female_pct"]
    assert female_pct < 40 or female_pct > 60


def test_auto_flags_clean_row_trips_nothing():
    from commcare_connect.labs.synthetic.program_admin_demo import _auto_flags_for_row

    for seed in range(10):
        row = _pipeline_row("solid", flagged=False, kpi_issue=None, seed=seed)
        assert _auto_flags_for_row(row) == [], f"solid row tripped a flag (seed={seed}): {row}"


def test_auto_flag_mirror_matches_chc_template_catalog():
    """Keys + labels in the Python mirror must appear verbatim in the chc
    render code's FLAG_CATALOG — the PAR rollup and the chc pills render
    these exact strings, so drift = different chips for the same finding."""
    from pathlib import Path

    from commcare_connect.labs.synthetic.program_admin_demo import AUTO_FLAG_LABELS
    from commcare_connect.workflow.templates import chc_nutrition_analysis

    src = Path(chc_nutrition_analysis.__file__).read_text()
    for key, label in AUTO_FLAG_LABELS.items():
        assert f"'{key}'" in src, f"flag key {key!r} missing from chc FLAG_CATALOG"
        assert label in src, f"flag label {label!r} missing from chc FLAG_CATALOG"


def test_seed_auto_flags_creates_records_for_flagged_flws_only():
    import datetime as dt

    from commcare_connect.labs.synthetic.program_admin_demo import _seed_auto_flags_for_run
    from commcare_connect.labs.synthetic.walkthrough_kit import monday_dt

    fda = MagicMock()
    rows = [
        _pipeline_row("improver_closed_satisfactory", flagged=True, kpi_issue="gender", flw_id="isha_n"),
        _pipeline_row("solid", flagged=False, kpi_issue=None, flw_id="amina_n", seed=8),
    ]
    created = _seed_auto_flags_for_run(
        fda=fda,
        rows=rows,
        workflow_run_id=99,
        opportunity_id=10000,
        monday_iso="2026-05-04",
        flagged_by="amani_nm",
    )

    assert created == 1
    assert fda.create_flag.call_count == 1
    kwargs = fda.create_flag.call_args.kwargs
    assert kwargs["flw_id"] == "isha_n"
    assert kwargs["flag_key"] == "gender_skew"
    assert kwargs["flag_label"] == "Gender split outside 40-60%"
    assert kwargs["source"] == "auto"
    assert kwargs["flagged_by"] == "amani_nm"  # the network-manager persona, not a system user
    assert kwargs["workflow_run_id"] == 99
    # Flagged while the review was still open — strictly before the run's
    # backdated completed_at (Monday 09:00 UTC).
    flagged_at = dt.datetime.fromisoformat(kwargs["flagged_at"])
    assert flagged_at < monday_dt("2026-05-04")
    assert flagged_at.date().isoformat() == "2026-05-04"
