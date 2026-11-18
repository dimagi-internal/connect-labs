"""The guidance the agent is given names exactly the page actions agent.js registers."""

import re
from pathlib import Path

from connect_labs.effectiveness import agent_guidance

AGENT_JS = Path(__file__).resolve().parents[2] / "static" / "effectiveness" / "agent.js"


def registered() -> set[str]:
    src = AGENT_JS.read_text(encoding="utf-8")
    body = src[src.index("var ACTIONS = {") : src.index("function ack()")]
    return set(re.findall(r"^    (\w+): \[", body, re.M))


def test_every_registered_action_is_in_the_guidance_and_nothing_else_is():
    named = set(re.findall(r"\bpage_(\w+)", agent_guidance.PAGE_ACTIONS))
    assert registered() == named


def test_a_canopy_caller_is_given_it_and_a_person_is_not(monkeypatch):
    from connect_labs.mcp.tools import cea, labsdata

    monkeypatch.setattr(labsdata, "_from_canopy", lambda: True)
    assert cea._for_canopy({"a": 1})["page_actions"] == agent_guidance.PAGE_ACTIONS
    monkeypatch.setattr(labsdata, "_from_canopy", lambda: False)
    assert cea._for_canopy({"a": 1}) == {"a": 1}
