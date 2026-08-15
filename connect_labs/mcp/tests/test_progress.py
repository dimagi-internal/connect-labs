"""MCP-layer progress plumbing (connect-labs#1220).

The registry's handlers are synchronous and run in a worker thread, while the
MCP session that must emit ``notifications/progress`` is owned by an event loop
elsewhere. These tests pin the two halves of that bridge: which tools opt in,
and that the bridge never lets a reporting failure reach the tool.
"""

import asyncio

import pytest

import connect_labs.mcp.tools.synthetic  # noqa: F401 — trigger @register side effects
from connect_labs.mcp.progress import make_thread_safe_reporter
from connect_labs.mcp.tool_registry import get_tool


@pytest.mark.parametrize(
    "tool_name",
    ["synthetic_clone_profile", "synthetic_clone_generate", "synthetic_profile_opps_bulk"],
)
def test_long_running_cohort_tools_opt_into_progress(tool_name):
    """These are the tools that iterate per-opportunity for minutes at a time.

    An 11-opp `clone_profile` and an 11-opp `clone_generate` were both killed by
    the client's 300s idle timeout after their work had entirely succeeded; a
    3-opp `clone_generate` died too, while a 2-opp one returned normally. Silence
    for the whole run is the defect, so opting in is part of the contract, not a
    detail of the handler.
    """
    tool = get_tool(tool_name)
    assert tool is not None, f"{tool_name} is not registered"
    assert tool.wants_progress is True, f"{tool_name} must opt into progress reporting"


def test_short_tools_do_not_opt_into_progress():
    """Opting in is per-tool: a single-opp call has nothing to report against,
    and handing every handler a `progress` kwarg it never uses would make the
    signature lie about what the tool does."""
    assert get_tool("synthetic_register").wants_progress is False


def test_reporter_schedules_onto_the_owning_loop():
    """The handler runs in a worker thread and cannot await; the notification has
    to be scheduled back onto the loop that owns the MCP session."""
    reported = []

    class _Ctx:
        async def report_progress(self, progress, total=None, message=None):
            reported.append((progress, total, message))

    async def _drive():
        loop = asyncio.get_running_loop()
        report = make_thread_safe_reporter(_Ctx(), loop)
        # Call from a worker thread, exactly as sync_to_async does.
        await asyncio.get_running_loop().run_in_executor(None, lambda: report(2, 11, "profiled 2/11"))
        # Give the scheduled coroutine a turn.
        await asyncio.sleep(0.05)

    asyncio.run(_drive())
    assert reported == [(2, 11, "profiled 2/11")]


def test_reporter_swallows_a_dead_session():
    """A client that has gone away must not destroy work that already succeeded —
    that is the exact failure #1220 is about, so the bridge cannot raise."""

    class _DeadCtx:
        async def report_progress(self, progress, total=None, message=None):
            raise RuntimeError("session closed")

    class _DeadLoop:
        def call_soon_threadsafe(self, *a, **k):
            raise RuntimeError("loop closed")

    report = make_thread_safe_reporter(_DeadCtx(), _DeadLoop())
    report(1, 2, "still going")  # must not raise


@pytest.mark.parametrize(
    "tool_name",
    ["synthetic_clone_profile", "synthetic_clone_generate", "synthetic_profile_opps_bulk"],
)
def test_progress_is_not_an_advertised_argument(tool_name):
    """`progress` is injected by the server, not supplied by the caller.

    It must stay out of the advertised schema — otherwise clients see a
    parameter they cannot pass, and the audit log's captured arguments would
    grow a field that is not part of the call.
    """
    tool = get_tool(tool_name)
    assert "progress" not in tool.input_schema.get("properties", {})
    assert "progress" not in tool.input_schema.get("required", [])
