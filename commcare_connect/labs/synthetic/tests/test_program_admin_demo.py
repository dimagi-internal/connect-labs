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
