def test_llo_weekly_review_template_registered():
    from commcare_connect.workflow.templates import list_templates

    keys = {t["key"] for t in list_templates()}
    assert "llo_weekly_review" in keys


def test_llo_weekly_review_supports_saved_runs():
    from commcare_connect.workflow.templates.llo_weekly_review import TEMPLATE

    assert TEMPLATE["supports_saved_runs"] is True
    assert TEMPLATE["snapshot_inputs"] == {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    }


def test_llo_weekly_review_definition_has_kpi_config_slot():
    from commcare_connect.workflow.templates.llo_weekly_review import DEFINITION

    assert "kpi_config" in DEFINITION["config"]
    assert "coaching_task_template" in DEFINITION["config"]


def test_llo_weekly_review_pipeline_schema_aggregates_per_flw():
    from commcare_connect.workflow.templates.llo_weekly_review import PIPELINE_SCHEMA

    assert PIPELINE_SCHEMA["grouping_key"] == "username"
    assert PIPELINE_SCHEMA["terminal_stage"] == "aggregated"
