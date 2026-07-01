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
    from connect_labs.labs.synthetic.program_admin_demo import _refresh_render_code

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
    from connect_labs.labs.synthetic.program_admin_demo import _refresh_render_code

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
    from connect_labs.labs.synthetic.program_admin_demo import _refresh_render_code
    from connect_labs.workflow.templates import get_template

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
    from connect_labs.labs.synthetic.program_admin_demo import _refresh_render_code

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


def _pipeline_row(
    archetype: str,
    *,
    flagged: bool,
    kpi_issue: str | None,
    flw_id: str = "f",
    seed: int = 7,
):
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    return build_flw_pipeline_row(
        flw_id=flw_id,
        archetype=archetype,
        flagged_this_week=flagged,
        rng_seed=seed,
        kpi_issue=kpi_issue,
    )


def test_auto_flags_for_flagged_muac_row_trip_sam_and_mam_low():
    from connect_labs.labs.synthetic.program_admin_demo import _auto_flags_for_row

    row = _pipeline_row("improver_closed_satisfactory", flagged=True, kpi_issue="muac")
    keys = {f["flag_key"] for f in _auto_flags_for_row(row)}
    assert keys == {"sam_low", "mam_low"}


def test_auto_flags_for_gender_skew_row_trip_gender_only():
    from connect_labs.labs.synthetic.program_admin_demo import _auto_flags_for_row

    row = _pipeline_row("improver_closed_satisfactory", flagged=True, kpi_issue="gender")
    flags = _auto_flags_for_row(row)
    assert {f["flag_key"] for f in flags} == {"gender_skew"}
    female_pct = flags[0]["evidence"]["female_pct"]
    assert female_pct < 40 or female_pct > 60


def test_auto_flags_clean_row_trips_nothing():
    from connect_labs.labs.synthetic.program_admin_demo import _auto_flags_for_row

    for seed in range(10):
        row = _pipeline_row("solid", flagged=False, kpi_issue=None, seed=seed)
        assert _auto_flags_for_row(row) == [], f"solid row tripped a flag (seed={seed}): {row}"


def test_auto_flag_mirror_matches_chc_template_catalog():
    """Keys + labels in the Python mirror must appear verbatim in the chc
    render code's FLAG_CATALOG — the PAR rollup and the chc pills render
    these exact strings, so drift = different chips for the same finding."""
    from pathlib import Path

    from connect_labs.labs.synthetic.program_admin_demo import AUTO_FLAG_LABELS
    from connect_labs.workflow.templates import chc_nutrition_analysis

    src = Path(chc_nutrition_analysis.__file__).read_text()
    for key, label in AUTO_FLAG_LABELS.items():
        assert f"'{key}'" in src, f"flag key {key!r} missing from chc FLAG_CATALOG"
        assert label in src, f"flag label {label!r} missing from chc FLAG_CATALOG"


def test_seed_auto_flags_creates_records_for_flagged_flws_only():
    import datetime as dt

    from connect_labs.labs.synthetic.program_admin_demo import _seed_auto_flags_for_run
    from connect_labs.labs.synthetic.walkthrough_kit import monday_dt

    fda = MagicMock()
    rows = [
        _pipeline_row(
            "improver_closed_satisfactory",
            flagged=True,
            kpi_issue="gender",
            flw_id="isha_n",
        ),
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


# ---------------------------------------------------------------------------
# Display-name map — real human names instead of raw usernames everywhere a
# user-bearing record is created. The finding "task-header-identity-dup":
# without a name, the task hero header / PAR drill / visit cards render the
# raw username (amani_n). The generator now stamps a real name from a single
# source-of-truth map onto every record it writes.
# ---------------------------------------------------------------------------


def test_display_name_map_covers_every_demo_user():
    """Every FLW id AND every network manager id used by the shipped demo
    config must resolve to a curated real name — not a derived fallback and
    never the raw username."""
    import json
    from pathlib import Path

    from connect_labs.labs.synthetic.program_admin_demo import DISPLAY_NAMES

    cfg_path = (
        Path(__file__).resolve().parents[4] / "scripts" / "walkthroughs" / "program-admin-report" / "demo_config.json"
    )
    config = json.loads(cfg_path.read_text())
    user_ids: set[str] = set()
    for opp in config["opps"]:
        user_ids.add(opp["network_manager"])
        for flw in opp["flws"]:
            user_ids.add(flw["id"])

    missing = sorted(uid for uid in user_ids if uid not in DISPLAY_NAMES)
    assert not missing, f"demo users missing a curated display name: {missing}"
    # Curated names are real "First Last" — not the username.
    for uid in user_ids:
        name = DISPLAY_NAMES[uid]
        assert name != uid
        assert " " in name, f"{uid} -> {name!r} is not a full name"


def test_display_name_for_falls_back_to_real_name_for_unknown_id():
    """An id not in the curated map still gets a real-looking, stable full
    name — never the raw username — so a newly-added archetype can't leak."""
    from connect_labs.labs.synthetic.program_admin_demo import display_name_for

    name = display_name_for("zuberi_n")
    assert name != "zuberi_n"
    assert name.startswith("Zuberi ")
    assert " " in name
    # Stable across calls (deterministic surname).
    assert display_name_for("zuberi_n") == name


def test_pipeline_row_carries_real_display_name():
    """The chc pipeline row's ``name`` field (read for the worker label) is
    the real display name, not the username."""
    from connect_labs.labs.synthetic.program_admin_demo import _build_chc_pipeline_rows

    flws = [
        {
            "id": "jumoke_n",
            "archetype": "improver_warned",
            "flag_week": 0,
            "reason_key": "bad_muac_distribution",
        }
    ]
    rows = _build_chc_pipeline_rows(10000, flws, week_idx=0)
    assert len(rows) == 1
    assert rows[0]["username"] == "jumoke_n"
    assert rows[0]["name"] == "Jumoke Balogun"


def test_task_record_carries_real_flw_name():
    """A task built by the generator carries ``flw_name`` = the real name,
    which the Task model's ``flw_name`` property and the task hero header
    read instead of the username."""
    from connect_labs.labs.synthetic.archetypes import build_task_data

    data = build_task_data(
        archetype_name="closed_warned",
        flw_id="jumoke_n",
        monday_iso="2026-05-04",
        opportunity_id=10000,
        workflow_run_id=99,
        audit_session_id=None,
        title="Coach Jumoke Balogun — bad MUAC distribution",
        creator_name="Amani Nwosu",
        reason_key="bad_muac_distribution",
        flw_name="Jumoke Balogun",
    )
    # The username stays the stable Connect id; the display name is real.
    assert data["username"] == "jumoke_n"
    assert data["flw_name"] == "Jumoke Balogun"

    from connect_labs.tasks.models import TaskRecord

    rec = TaskRecord(
        {
            "id": 1,
            "experiment": "tasks",
            "type": "Task",
            "opportunity_id": 10000,
            "data": data,
        }
    )
    assert rec.flw_name == "Jumoke Balogun"  # what the task hero header renders


def test_audit_record_carries_real_flw_name():
    """A generator-built audit carries ``flw_name`` so record-level
    consumers show the real name (the bulk page resolves it from the opp
    export, but the record should still be honest)."""
    from connect_labs.labs.synthetic.archetypes import build_audit_data

    data = build_audit_data(
        archetype_name="completed_mixed_tape_usage",
        flw_id="jumoke_n",
        monday_iso="2026-05-04",
        opportunity_id=10000,
        opportunity_name="Northern Cluster",
        workflow_run_id=99,
        visit_id_base=9_000_001,
        flw_name="Jumoke Balogun",
    )
    assert data["username"] == "jumoke_n"
    assert data["flw_name"] == "Jumoke Balogun"
    # Every per-visit image record carries it too.
    first_visit = next(iter(data["visit_images"].values()))
    assert first_visit[0]["flw_name"] == "Jumoke Balogun"
