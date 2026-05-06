def test_program_admin_audit_registered():
    from commcare_connect.workflow.templates import list_templates

    keys = {t["key"] for t in list_templates()}
    assert "program_admin_audit" in keys


def test_program_admin_audit_definition_has_watched_workflow_slot():
    from commcare_connect.workflow.templates.program_admin_audit import DEFINITION

    assert "watched_workflow_id" in DEFINITION["config"]


def test_program_admin_audit_supports_saved_runs():
    from commcare_connect.workflow.templates.program_admin_audit import TEMPLATE

    assert TEMPLATE["supports_saved_runs"] is True


def test_program_admin_audit_is_multi_opp_capable():
    from commcare_connect.workflow.templates.program_admin_audit import TEMPLATE

    assert TEMPLATE["multi_opp"] is True
