"""A workflow run backed by audit sessions should complete only when EVERY
linked session is completed (an audit run can span more than one session).
"""
from connect_labs.audit.data_access import all_sessions_completed


class _S:
    def __init__(self, status):
        self.status = status


def test_empty_is_not_complete():
    assert all_sessions_completed([]) is False


def test_single_completed_is_complete():
    assert all_sessions_completed([_S("completed")]) is True


def test_any_in_progress_blocks_completion():
    assert all_sessions_completed([_S("completed"), _S("in_progress")]) is False


def test_all_completed():
    assert all_sessions_completed([_S("completed"), _S("completed")]) is True
