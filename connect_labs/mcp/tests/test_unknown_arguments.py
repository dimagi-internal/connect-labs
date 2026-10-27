"""An argument a tool does not take is refused by name, not raised as a TypeError traceback.

data_chat_eval (header-matches-raw): the run-page agent passed data_query's `workflow` to
data_workflow_context and got "TypeError: ... unexpected keyword argument 'workflow'" with
the server's traceback.
"""

import pytest

from connect_labs.mcp.testing import call_tool, mint_token
from connect_labs.users.models import User


@pytest.fixture
def raw_pat(db):
    user = User.objects.create(username="unknown-args")
    _, raw = mint_token(user, name="t")
    return raw


@pytest.mark.django_db
def test_an_argument_the_tool_does_not_take_is_refused_saying_what_it_takes(raw_pat):
    out = call_tool(raw_pat, "data_workflow_context", {"workflow": {"id": 9027, "program_id": 10121}})
    error = out["result"]["structuredContent"]["error"]
    assert error["code"] == "INVALID_SCHEMA"
    assert error["details"]["reason"] == "unknown_argument" and error["details"]["unknown"] == ["workflow"]
    assert "definition_id" in error["message"] and "Traceback" not in error["message"]


@pytest.mark.django_db
def test_a_tool_taking_any_keyword_is_not_second_guessed(raw_pat):
    from connect_labs.mcp import server
    from connect_labs.mcp.tool_registry import get_tool

    spec = get_tool("data_query")
    assert server._unknown_arguments(spec, {"sql": "select 1", "opportunity_ids": [1], "wait": True}) == []
    assert server._unknown_arguments(spec, {"sql": "select 1", "bogus": 1}) == ["bogus"]
