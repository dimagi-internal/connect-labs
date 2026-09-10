"""Tests for the workflow_rebuild_history / workflow_history_eligibility MCP tools.

The operation itself is tested in workflow/tests/test_history_rebuild.py. What
is pinned here is the MCP surface: that arguments reach the operation intact,
that a refusal arrives as an error class a caller can branch on rather than a
success with zero runs, and that the eligibility probe writes nothing.
"""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.workflow_history  # noqa: F401  -- triggers @register
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.workflow.history_rebuild import GENERATED_BY, HistoryRebuildError


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


def _patch_wda(monkeypatch, fake_wda):
    from connect_labs.mcp.tools import workflow_history as wh

    monkeypatch.setattr(wh, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)


def _run(run_id, period_end, generated=False, completed=True):
    r = MagicMock()
    r.id = run_id
    r.period_end = period_end
    r.is_completed = completed
    r.state = {"generated_by": GENERATED_BY} if generated else {}
    return r


# ---------------------------------------------------------------------------
# workflow_rebuild_history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_rebuild_passes_every_argument_through_and_returns_the_report(user, monkeypatch):
    from connect_labs.workflow import history_rebuild

    fake_wda = MagicMock()
    _patch_wda(monkeypatch, fake_wda)

    seen = {}

    def fake_rebuild(dao, definition_id, **kw):
        seen["dao"] = dao
        seen["definition_id"] = definition_id
        seen.update(kw)
        return {"definition_id": definition_id, "created": 3, "runs": []}

    monkeypatch.setattr(history_rebuild, "rebuild_history", fake_rebuild)

    result = get_tool("workflow_rebuild_history").handler(
        user=user,
        definition_id=5626,
        program_id=176,
        cadence="weekly",
        start="2026-04-06",
        end="2026-09-06",
        replace=False,
    )

    assert result["created"] == 3
    assert seen["definition_id"] == 5626
    assert seen["cadence"] == "weekly"
    assert seen["start"].isoformat() == "2026-04-06"
    assert seen["end"].isoformat() == "2026-09-06"
    assert seen["program_id"] == 176
    assert seen["opportunity_id"] is None
    assert seen["replace"] is False
    assert seen["dry_run"] is False
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_rebuild_defaults_are_weekly_replacing_and_not_a_dry_run(user, monkeypatch):
    from connect_labs.workflow import history_rebuild

    _patch_wda(monkeypatch, MagicMock())
    seen = {}
    monkeypatch.setattr(history_rebuild, "rebuild_history", lambda dao, did, **kw: (seen.update(kw), {"runs": []})[1])

    get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, opportunity_id=10)

    assert seen["cadence"] == "weekly"
    assert seen["replace"] is True
    assert seen["dry_run"] is False
    assert seen["start"] is None, "an omitted start is derived from the data, not defaulted here"
    assert seen["end"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code,expected",
    [
        ("not_periodic", "INVALID_SCHEMA"),
        ("empty_range", "INVALID_SCHEMA"),
        ("too_many_periods", "INVALID_SCHEMA"),
        ("no_start", "INVALID_SCHEMA"),
        ("no_definition", "NOT_FOUND"),
        ("cache_miss", "UPSTREAM_ERROR"),
    ],
)
def test_a_refusal_arrives_as_an_error_class_not_an_empty_success(user, monkeypatch, code, expected):
    # A rebuild that refuses must not look like a rebuild that found nothing
    # to do -- the caller has to be able to tell "you cannot do this" from
    # "there was nothing to build".
    from connect_labs.workflow import history_rebuild

    _patch_wda(monkeypatch, MagicMock())

    def boom(dao, did, **kw):
        raise HistoryRebuildError(code, f"reason for {code}")

    monkeypatch.setattr(history_rebuild, "rebuild_history", boom)

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, opportunity_id=10)
    assert e.value.code == expected
    assert f"reason for {code}" in str(e.value)


@pytest.mark.django_db
def test_the_client_is_closed_even_when_the_rebuild_raises(user, monkeypatch):
    from connect_labs.workflow import history_rebuild

    fake_wda = MagicMock()
    _patch_wda(monkeypatch, fake_wda)

    def boom(dao, did, **kw):
        raise HistoryRebuildError("empty_range", "nope")

    monkeypatch.setattr(history_rebuild, "rebuild_history", boom)

    with pytest.raises(MCPToolError):
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, opportunity_id=10)
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_a_malformed_date_is_named_rather_than_silently_ignored(user, monkeypatch):
    _patch_wda(monkeypatch, MagicMock())

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, opportunity_id=10, start="last April")
    assert e.value.code == "INVALID_SCHEMA"
    assert "start" in str(e.value)


@pytest.mark.django_db
@pytest.mark.parametrize("scope", [{}, {"opportunity_id": 10, "program_id": 176}])
def test_exactly_one_owner_is_required(user, monkeypatch, scope):
    _patch_wda(monkeypatch, MagicMock())

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, **scope)
    assert e.value.code == "INVALID_SCHEMA"


# ---------------------------------------------------------------------------
# workflow_history_eligibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_eligibility_reports_the_verdict_and_what_a_rebuild_would_replace(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_history as wh

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = MagicMock()
    fake_wda.list_runs.return_value = [
        _run(1, "2026-09-06", generated=True),
        _run(2, "2026-08-30", generated=True),
        _run(3, "2026-08-23", generated=False),
        _run(4, "2026-09-13", generated=True, completed=False),  # in progress: not history
    ]
    _patch_wda(monkeypatch, fake_wda)
    monkeypatch.setattr(wh, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    from connect_labs.workflow import history_rebuild

    monkeypatch.setattr(history_rebuild, "eligibility", lambda d: (True, None))

    result = get_tool("workflow_history_eligibility").handler(user=user, definition_id=5626, program_id=176)

    assert result["eligible"] is True
    assert result["reason"] is None
    assert result["completed_runs"] == 3
    assert result["generated_runs"] == 2
    assert result["manual_runs"] == 1, "a hand-made run is counted separately: a rebuild won't touch it"


@pytest.mark.django_db
def test_eligibility_carries_the_reason_when_a_workflow_cannot_be_rebuilt(user, monkeypatch):
    from connect_labs.workflow import history_rebuild

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = MagicMock()
    fake_wda.list_runs.return_value = []
    _patch_wda(monkeypatch, fake_wda)
    monkeypatch.setattr(history_rebuild, "eligibility", lambda d: (False, "builder 'copy_rows' is not periodic"))

    result = get_tool("workflow_history_eligibility").handler(user=user, definition_id=1, opportunity_id=10)

    assert result["eligible"] is False
    assert "copy_rows" in result["reason"]


@pytest.mark.django_db
def test_eligibility_is_a_read(user, monkeypatch):
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = MagicMock()
    fake_wda.list_runs.return_value = []
    _patch_wda(monkeypatch, fake_wda)

    get_tool("workflow_history_eligibility").handler(user=user, definition_id=1, opportunity_id=10)

    fake_wda.create_run.assert_not_called()
    fake_wda.complete_run.assert_not_called()
    fake_wda.delete_run.assert_not_called()
    assert get_tool("workflow_history_eligibility").is_write is False


@pytest.mark.django_db
def test_eligibility_on_a_missing_definition_is_not_found(user, monkeypatch):
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = None
    _patch_wda(monkeypatch, fake_wda)

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_history_eligibility").handler(user=user, definition_id=999, opportunity_id=10)
    assert e.value.code == "NOT_FOUND"
    fake_wda.close.assert_called_once()


# ---------------------------------------------------------------------------
# An unreadable definition. `get_definition` is annotated `-> Record | None`,
# but a 404 from the records API RAISES rather than returning None -- so the
# `is None` branch above is dead on the likeliest failure of all: a mistyped id,
# or the right id read under the wrong scope. Unhandled, the caller gets a
# Python traceback ending in a raw upstream URL instead of a sentence saying
# what to fix. Observed against production while probing definition 5626.
# ---------------------------------------------------------------------------


def _raising_wda(exc):
    wda = MagicMock()
    wda.get_definition.side_effect = exc
    return wda


@pytest.mark.django_db
def test_eligibility_turns_an_unreadable_definition_into_not_found(user, monkeypatch):
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError

    fake_wda = _raising_wda(LabsAPIError("Failed to fetch record 5626: Client error '404 Not Found'"))
    _patch_wda(monkeypatch, fake_wda)

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_history_eligibility").handler(user=user, definition_id=5626, program_id=176)

    assert e.value.code == "NOT_FOUND"
    msg = str(e.value)
    assert "5626" in msg
    # The scope is the likelier culprit than the id, and the upstream read is an
    # exact scope match rather than a hierarchical one -- so name it.
    assert "program_id=176" in msg
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_rebuild_turns_an_unreadable_definition_into_not_found(user, monkeypatch):
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.workflow import history_rebuild

    fake_wda = MagicMock()
    _patch_wda(monkeypatch, fake_wda)

    def boom(dao, did, **kw):
        raise LabsAPIError("Failed to fetch record 5626: Client error '404 Not Found'")

    monkeypatch.setattr(history_rebuild, "rebuild_history", boom)

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=5626, opportunity_id=523)

    assert e.value.code == "NOT_FOUND"
    assert "opportunity_id=523" in str(e.value)
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_an_upstream_failure_that_is_not_a_404_is_not_reported_as_not_found(user, monkeypatch):
    # A 500 or a timeout is transient and worth retrying; a 404 is not. Calling
    # both NOT_FOUND would tell a caller to go fix an id that was never wrong.
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.workflow import history_rebuild

    _patch_wda(monkeypatch, MagicMock())

    def boom(dao, did, **kw):
        raise LabsAPIError("Failed to fetch record 5626: Server error '502 Bad Gateway'")

    monkeypatch.setattr(history_rebuild, "rebuild_history", boom)

    with pytest.raises(MCPToolError) as e:
        get_tool("workflow_rebuild_history").handler(user=user, definition_id=5626, opportunity_id=523)
    assert e.value.code == "UPSTREAM_ERROR"
