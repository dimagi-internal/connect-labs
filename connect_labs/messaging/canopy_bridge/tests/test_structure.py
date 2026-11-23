"""The shape the bridge keeps: one home for what an agent may see, one registry for its tools.

These pin structure, not behaviour, because the failure they prevent is silent: a brief, a
tool or a summary that filters the record its own way works on the day it is written and
drifts from the others the first time a rule changes (a new restricted kind, a new private
field). The visibility rules live in ``visibility.py``; everything that serves an agent reads
through it.
"""

from __future__ import annotations

import inspect
import pathlib
import re

from connect_labs.messaging import playbooks
from connect_labs.messaging.canopy_bridge import tools

BRIDGE = pathlib.Path(__file__).resolve().parent.parent
#: What decides visibility. Outside visibility.py, the code that serves an agent never says these.
RULES = re.compile(r"Visibility\.RESTRICTED|DeliveryStatus\.NOTE|private\s*=\s*(?:False|True)|_Scrubber")
#: Code that serves agents: the brief, the tools, the fallback summaries, the MCP endpoint.
AGENT_FACING = ["brief.py", "summaries.py", "mcp.py", *(f"tools/{p.name}" for p in (BRIDGE / "tools").glob("*.py"))]


def test_only_visibility_py_decides_what_an_agent_may_see():
    offenders = {name: RULES.findall((BRIDGE / name).read_text()) for name in AGENT_FACING if (BRIDGE / name).exists()}
    assert {k: v for k, v in offenders.items() if v} == {}, (
        "Filter through canopy_bridge.visibility (Scope, messages(), summaries(), Scrubber) instead of "
        "restating a visibility rule here."
    )


def test_every_tool_is_registered_once_by_its_function_and_is_one_a_playbook_may_grant():
    assert set(tools.TOOLS) == set(playbooks.WORKER_TOOLS)
    for name, spec in tools.TOOLS.items():
        assert spec.handler.__name__ == name
        assert spec.input_schema["type"] == "object" and spec.input_schema["additionalProperties"] is False
        # Every argument the schema allows is one the function takes, and the reverse.
        args = [p for p in inspect.signature(spec.handler).parameters if p != "ctx"]
        assert sorted(args) == sorted(spec.input_schema["properties"]), name
