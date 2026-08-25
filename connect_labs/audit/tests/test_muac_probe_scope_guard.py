"""The MUAC review-UI probe must not ask a question it already knows the answer to.

``_is_muac_picture_audit_session`` decides whether a session came from workflow
6840 by fetching its run under ``program_id=176``. For a viewer who is not in
program 176 that fetch is a **404 by construction** -- upstream
``_get_program_or_404`` filters programs by org membership -- so it can never
succeed, on any load, ever.

It fails closed to ``False``, which is the right verdict, so the review screen
was always correct. What was not correct is the cost: one remote round-trip per
bulk page load, and one ``outcome=failure`` row in the HIPAA audit trail for
each, at 83% of loads that attempted it (341 of 411 over the 2026-08-11 window;
still ~61 per 36h on 2026-08-25). That made it the loudest failure signature in
the audit app and it was chased as a correctness bug twice -- #1161, then #1242
-- before the cause was identified. (#1242)
"""

from __future__ import annotations

from unittest.mock import patch

from connect_labs.audit.views import (
    MUAC_PICTURE_AUDIT_PROGRAM_ID,
    MUAC_PICTURE_AUDIT_WORKFLOW_DEFINITION_ID,
    _can_reach_muac_program,
    _is_muac_picture_audit_session,
)


class _Request:
    def __init__(self, programs=()):
        self.session = {"labs_oauth": {"organization_data": {"programs": list(programs)}}}
        self.user = None


class _Session:
    def __init__(self, run_id):
        self.workflow_run_id = run_id


class _Run:
    def __init__(self, definition_id):
        self.definition_id = definition_id


def test_viewer_outside_program_176_makes_no_remote_call():
    """The 83% case: answer locally, don't log a failure to learn what we know."""
    request = _Request(programs=[{"id": 217}])

    with patch("connect_labs.audit.views.WorkflowDataAccess") as wda:
        assert _is_muac_picture_audit_session(_Session(14811), request) is False

    wda.assert_not_called()


def test_viewer_inside_program_176_still_probes_and_matches():
    request = _Request(programs=[{"id": MUAC_PICTURE_AUDIT_PROGRAM_ID}])

    with patch("connect_labs.audit.views.WorkflowDataAccess") as wda:
        wda.return_value.get_run.return_value = _Run(MUAC_PICTURE_AUDIT_WORKFLOW_DEFINITION_ID)
        assert _is_muac_picture_audit_session(_Session(9001), request) is True

    wda.return_value.get_run.assert_called_once_with(9001)


def test_viewer_inside_program_176_with_a_different_workflow_is_false():
    request = _Request(programs=[{"id": MUAC_PICTURE_AUDIT_PROGRAM_ID}])

    with patch("connect_labs.audit.views.WorkflowDataAccess") as wda:
        wda.return_value.get_run.return_value = _Run(1234)
        assert _is_muac_picture_audit_session(_Session(9001), request) is False


def test_a_session_with_no_run_short_circuits_first():
    request = _Request(programs=[{"id": MUAC_PICTURE_AUDIT_PROGRAM_ID}])

    with patch("connect_labs.audit.views.WorkflowDataAccess") as wda:
        assert _is_muac_picture_audit_session(_Session(None), request) is False

    wda.assert_not_called()


def test_probe_still_fails_closed_on_a_genuine_error():
    """A transient API problem must never block the normal review screen."""
    request = _Request(programs=[{"id": MUAC_PICTURE_AUDIT_PROGRAM_ID}])

    with patch("connect_labs.audit.views.WorkflowDataAccess") as wda:
        wda.return_value.get_run.side_effect = RuntimeError("upstream 502")
        assert _is_muac_picture_audit_session(_Session(9001), request) is False

    wda.return_value.close.assert_called_once()


def test_membership_check_reads_the_viewers_own_programs():
    assert _can_reach_muac_program(_Request(programs=[{"id": MUAC_PICTURE_AUDIT_PROGRAM_ID}])) is True
    assert _can_reach_muac_program(_Request(programs=[{"id": 217}])) is False
    assert _can_reach_muac_program(_Request(programs=[])) is False
