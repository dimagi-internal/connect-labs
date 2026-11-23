"""The worker tools: what a canopy agent may ask labs about the ONE Connect Worker it is talking to.

Served by ``mcp.py`` to canopy's gateway in subject mode (S4): every call carries canopy's signed
word naming the worker (``Subject.sub``) and the conversation (``Subject.session``), and labs
resolves both to its own record before any tool runs. A tool never takes a worker, a thread or a
program as an argument: they come from the subject, so an agent cannot ask about anyone else.

Every tool reads through the call's ``visibility.Scope``, the same rules as the Worker Brief, and
only the tools the thread's playbook lists in ``tools`` are listed or run.

    base.py      the call's context (`Ctx`), the registry (`tool`, `TOOLS`), argument helpers
    reading.py   profile, timeline, threads, history search, tasks, learn, visits, payments, facts
    memory.py    recording and retracting facts
    thread.py    the outcome so far, handing off, closing
"""

from __future__ import annotations

from connect_labs.messaging import playbooks

from . import memory, reading, thread  # noqa: F401 -- each registers its tools on import
from .base import TOOLS, Ctx, ToolRefused, WorkerTool, tool

__all__ = ["TOOLS", "Ctx", "ToolRefused", "WorkerTool", "tool"]

assert set(TOOLS) == set(playbooks.WORKER_TOOLS), "every worker tool a playbook may grant is served here"
