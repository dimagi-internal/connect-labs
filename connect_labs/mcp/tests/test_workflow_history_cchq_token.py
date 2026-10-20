"""The history and cache MCP tools pass the caller's CommCare HQ token through.

Without it, warming the cache, previewing or rebuilding a workflow whose pipelines
read CommCare HQ fails headless (CCHQHeadlessError). The token is best effort, the
same as workflow_run_default: a caller with no usable HQ login still gets the call,
with None, so workflows that never read HQ are unaffected.
"""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.workflow_history  # noqa: F401  -- triggers @register
from connect_labs.labs.integrations.commcare import cchq_tokens
from connect_labs.mcp.tool_registry import get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.fixture
def wda(monkeypatch):
    from connect_labs.mcp.tools import workflow_history as wh

    fake = MagicMock()
    monkeypatch.setattr(wh, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake)
    return fake


def _token(monkeypatch, value):
    def fake(user):
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cchq_tokens, "get_valid_cchq_access_token", fake)


def _capture(monkeypatch, module, name, result):
    seen = {}
    monkeypatch.setattr(module, name, lambda *a, **kw: (seen.update(kw), result)[1])
    return seen


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["hq-token", cchq_tokens.CCHQTokenError("no stored HQ login")])
def test_rebuild_history_passes_the_token_or_none(user, wda, monkeypatch, token):
    from connect_labs.workflow import history_rebuild

    _token(monkeypatch, token)
    seen = _capture(monkeypatch, history_rebuild, "rebuild_history", {"runs": []})

    get_tool("workflow_rebuild_history").handler(user=user, definition_id=1, program_id=60)

    assert seen["cchq_access_token"] == (None if isinstance(token, Exception) else token)


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["hq-token", cchq_tokens.CCHQTokenError("no stored HQ login")])
def test_preview_as_of_passes_the_token_or_none(user, wda, monkeypatch, token):
    from connect_labs.workflow import history_rebuild

    _token(monkeypatch, token)
    seen = _capture(monkeypatch, history_rebuild, "preview_as_of", {"cells": []})

    get_tool("workflow_preview_as_of").handler(user=user, definition_id=1, program_id=60, as_of="2026-10-19")

    assert seen["cchq_access_token"] == (None if isinstance(token, Exception) else token)


@pytest.mark.django_db
@pytest.mark.parametrize("token", ["hq-token", cchq_tokens.CCHQTokenError("no stored HQ login")])
def test_ensure_visit_cache_passes_the_token_or_none(user, wda, monkeypatch, token):
    from connect_labs.workflow import visit_cache

    _token(monkeypatch, token)
    seen = _capture(monkeypatch, visit_cache, "ensure_visit_cache", {"opportunities": [], "done": True})

    get_tool("workflow_ensure_visit_cache").handler(user=user, definition_id=1, program_id=60)

    assert seen["cchq_access_token"] == (None if isinstance(token, Exception) else token)
