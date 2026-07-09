from connect_labs.workflow.templates import template_supports_default_run


def test_program_audit_creator_is_schedulable():
    assert template_supports_default_run("program_audit_creator") is True


def test_unknown_template_is_not_schedulable():
    assert template_supports_default_run("does_not_exist") is False


def test_none_is_not_schedulable():
    assert template_supports_default_run(None) is False
