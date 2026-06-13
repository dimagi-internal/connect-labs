"""MCP-tool entry test for ``ensure_synthetic_env``.

The full realization contract is exercised by the engine e2e
(``commcare_connect/labs/synthetic/ensure/tests/test_par_env_e2e.py``). This
test proves the THIN MCP shim wiring: the tool resolves an env NAME to the
checked-in manifest, runs the ensure engine in-app against the local-records
backend, and returns the realized id map — and that bad names are rejected.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

# Trigger @register side effect.
import commcare_connect.mcp.tools.ensure_env  # noqa: F401
from commcare_connect.labs.synthetic.registry import invalidate_cache
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_ensure_synthetic_env_realizes_par_via_tool(user):
    invalidate_cache()
    tool = get_tool("ensure_synthetic_env")
    assert tool is not None and tool.is_write

    result = tool.handler(user=user, env="program-admin-report")

    assert isinstance(result, dict)
    # Headline walkthrough vars the realized map must carry.
    for var in ("par_run_id", "par_def_id", "par_url", "good_run_id", "incomplete_run_id"):
        assert result.get(var), f"realized map missing/empty {var!r}"
    # Both PAR opps realized.
    assert result["opp_10000_ready"] is True
    assert result["opp_10001_ready"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["../etc/passwd", "does-not-exist", "a/b", ""])
def test_ensure_synthetic_env_rejects_bad_names(user, bad):
    tool = get_tool("ensure_synthetic_env")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, env=bad)
    assert exc.value.code == "NOT_FOUND"
