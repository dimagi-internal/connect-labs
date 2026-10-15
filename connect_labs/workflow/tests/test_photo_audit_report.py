"""Smoke checks for the Photo Audit Report seed template.

No JS/Babel test runner exists in this repo, so these are Python-level
substring assertions against the render_code string plus registry checks.
They pin that the report reads the right endpoints with the right filters and
computes the agreed formula; runtime rendering is verified in the browser.
"""

from connect_labs.workflow.templates import get_template

KEY = "photo_audit_report"


def _tmpl():
    t = get_template(KEY)
    assert t is not None, f"{KEY} did not register"
    return t


def test_template_registers_and_is_well_formed():
    t = _tmpl()
    assert t["render_code"].startswith("function WorkflowUI")
    assert t["definition"]["name"] == "Photo Audit Report"
    assert "config" in t["definition"]


def test_is_action_shaped_not_saved_runs():
    t = _tmpl()
    assert t.get("supports_saved_runs") in (None, False)
    assert "snapshot_inputs" not in t


def test_scopes_from_context_and_reads_both_summary_endpoints():
    rc = _tmpl()["render_code"]
    assert "/audit/api/scope-context/" in rc  # follows the context picker
    assert "/audit/api/program/" in rc  # program (all opps) mode
    assert "/audit/api/opportunity/" in rc  # single-opportunity mode
    assert "sessions-summary/" in rc


def test_migrates_legacy_freetext_config():
    rc = _tmpl()["render_code"]
    # Old instances saved auditor/auditIds as strings.
    assert "cfg.auditor" in rc and "cfg.auditIds" in rc


def test_success_rate_formula_excludes_pending_and_counts_dupfake_as_fail():
    rc = _tmpl()["render_code"]
    assert "const denom = p + f + d;" in rc
    assert "p / denom" in rc
    assert "duplicate_fake" in rc


def test_the_auditor_column_reads_the_summary_rows_auditor_username():
    rc = _tmpl()["render_code"]
    assert "auditor_username" in rc
