"""MCP-tool entry tests for the ``synthetic_env_*`` family.

These tools extend the synthetic_* family with composite ENV templates backed
by the env-template registry. The full realization contract is exercised by the
engine e2e (``commcare_connect/labs/synthetic/ensure/tests/test_par_env_e2e.py``);
these tests prove the THIN MCP wiring:

- ``synthetic_env_list`` surfaces the registry summaries (incl. the PAR env),
- ``synthetic_env_get`` returns one env's summary + resource list (template,
  not realization), and rejects bad names,
- ``synthetic_env_ensure`` resolves a name via the registry, runs the ensure
  engine in-app against the local-records backend, returns the realized id map,
  and rejects bad names.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

# Trigger @register side effect for the synthetic_* family.
import commcare_connect.mcp.tools.synthetic  # noqa: F401
from commcare_connect.labs.synthetic.registry import invalidate_cache
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool

PAR = "program-admin-report"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


def test_synthetic_env_list_includes_par():
    tool = get_tool("synthetic_env_list")
    assert tool is not None and not tool.is_write
    result = tool.handler(user=None)
    keys = [e["key"] for e in result["envs"]]
    assert PAR in keys


def test_synthetic_env_get_returns_summary():
    tool = get_tool("synthetic_env_get")
    assert tool is not None and not tool.is_write
    result = tool.handler(user=None, env=PAR)
    assert result["key"] == PAR
    assert result["env"] == PAR
    kinds = [r["kind"] for r in result["resources"]]
    assert kinds == ["opp_data", "opp_data", "weekly_runs", "run_audits", "tasks", "rollup"]
    assert 10000 in result["opportunity_ids"]
    assert 10001 in result["opportunity_ids"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "does-not-exist", "a/b", ""])
def test_synthetic_env_get_rejects_bad_names(bad):
    tool = get_tool("synthetic_env_get")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=None, env=bad)
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_synthetic_env_ensure_realizes_par_via_tool(user):
    invalidate_cache()
    tool = get_tool("synthetic_env_ensure")
    assert tool is not None and tool.is_write

    result = tool.handler(user=user, env=PAR)

    assert isinstance(result, dict)
    # Headline walkthrough vars the realized map must carry.
    for var in ("par_run_id", "par_def_id", "par_url", "good_run_id", "incomplete_run_id"):
        assert result.get(var), f"realized map missing/empty {var!r}"
    # Both PAR opps realized.
    assert result["opp_10000_ready"] is True
    assert result["opp_10001_ready"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["../etc/passwd", "does-not-exist", "a/b", ""])
def test_synthetic_env_ensure_rejects_bad_names(user, bad):
    tool = get_tool("synthetic_env_ensure")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, env=bad)
    assert exc.value.code == "NOT_FOUND"
