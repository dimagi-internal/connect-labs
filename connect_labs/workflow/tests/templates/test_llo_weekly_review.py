def test_llo_weekly_review_template_registered():
    from connect_labs.workflow.templates import list_templates

    keys = {t["key"] for t in list_templates()}
    assert "llo_weekly_review" in keys


def test_llo_weekly_review_supports_saved_runs():
    from connect_labs.workflow.templates.llo_weekly_review import TEMPLATE

    assert TEMPLATE["supports_saved_runs"] is True
    assert TEMPLATE["snapshot_inputs"] == {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    }


def test_llo_weekly_review_definition_has_kpi_config_slot():
    from connect_labs.workflow.templates.llo_weekly_review import DEFINITION

    assert "kpi_config" in DEFINITION["config"]
    assert "coaching_task_template" in DEFINITION["config"]


def test_llo_weekly_review_pipeline_schema_aggregates_per_flw():
    from connect_labs.workflow.templates.llo_weekly_review import PIPELINE_SCHEMA

    assert PIPELINE_SCHEMA["grouping_key"] == "username"
    assert PIPELINE_SCHEMA["terminal_stage"] == "aggregated"


def _render_code():
    from connect_labs.workflow.templates.llo_weekly_review import RENDER_CODE

    return RENDER_CODE


class TestTheScaffoldActuallyRenders:
    """#1184: the dashboard rendered bare — no summary cards, no styling, no
    threshold highlighting — and the "ACE polish skill will layer visuals on
    top" it deferred to was deprecated by Plan C, so nothing ever did.
    """

    def test_show_summary_cards_is_read_not_just_declared(self):
        """`config.showSummaryCards` was set in DEFINITION and appeared nowhere
        in the render, so the flag did nothing and the page opened with no
        orientation at all."""
        from connect_labs.workflow.templates.llo_weekly_review import DEFINITION

        assert DEFINITION["config"]["showSummaryCards"] is True
        assert "showSummaryCards" in _render_code()

    def test_show_filters_is_read_too(self):
        from connect_labs.workflow.templates.llo_weekly_review import DEFINITION

        assert DEFINITION["config"]["showFilters"] is True
        assert "showFilters" in _render_code()

    def test_thresholds_drive_a_visual_treatment_not_only_the_filter(self):
        """`threshold_underperform` was read exclusively inside the filter
        predicate, so a KPI below target looked identical to one above it."""
        rc = _render_code()
        assert rc.count("threshold_underperform") >= 2
        assert "bg-yellow-50" in rc, "an under-threshold cell needs a visible treatment"

    def test_the_table_carries_styling(self):
        """The render shipped with zero class names, which is why Status and
        Action ran together as `pending—`."""
        rc = _render_code()
        assert "className" in rc
        assert "divide-y" in rc

    def test_status_renders_its_label_not_its_raw_id(self):
        rc = _render_code()
        assert "statusLabel" in rc
        assert "definition.statuses" in rc

    def test_undefined_action_handlers_disable_the_button_rather_than_throwing(self):
        """`actions.spawnCoachingTask` is not part of the default ActionHandlers
        interface — it is wired per opportunity. Calling it unguarded is a
        TypeError on click."""
        rc = _render_code()
        assert "actions.spawnCoachingTask &&" in rc
        assert "actions.openTaskDrawer &&" in rc
        assert "disabled" in rc

    def test_there_is_an_empty_state(self):
        rc = _render_code()
        assert "No workers on this opportunity yet." in rc
        assert "No underperforming workers this week." in rc

    def test_render_code_is_valid_jsx(self):
        """A syntax error here doesn't fail loudly — Babel transpiles the render
        in the browser, so the whole page just goes blank."""
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        if not shutil.which("npx"):
            import pytest

            pytest.skip("npx unavailable")

        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "render.jsx"
            src.write_text(_render_code())
            try:
                proc = subprocess.run(
                    ["npx", "--yes", "esbuild@0.21.5", "--loader:.jsx=jsx", str(src)],
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
            except (subprocess.TimeoutExpired, OSError):
                import pytest

                pytest.skip("esbuild unavailable (offline)")
        assert proc.returncode == 0, proc.stderr
