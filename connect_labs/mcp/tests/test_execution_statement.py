"""Canopy's Canopy-Execution statement: verified once per delegated call, and what real data waits on.

The SDK's verification itself (signature, issuer, audience, lifetime, jti, DPoP and
access-token binding) is tested in canopy-web against the real thing; here a fake SDK
module stands in so the tests do not depend on which dimagi-canopy is installed.
"""

import sys
import types
from dataclasses import dataclass, field
from unittest import mock

import pytest

from connect_labs.labs import canopy_panel
from connect_labs.mcp import execution


@dataclass(frozen=True)
class FakeExecution:
    agent: str = "labsdata"
    declaration: dict | None = field(
        default_factory=lambda: {"data": "sensitive", "capability": "data", "declared_by": "a person"}
    )
    runner: dict = field(
        default_factory=lambda: {"id": 7, "name": "sensitive-data-1", "flags": [{"flag": "sensitive_data"}]}
    )
    runner_posture: dict = field(default_factory=lambda: {"data_mode": "sensitive_data"})
    requirements: tuple = ("sensitive_data",)
    turn: str = "t1"
    session: str = "s1"
    refusal: str | None = None

    @property
    def runner_flags(self):
        return frozenset(f["flag"] for f in self.runner["flags"])

    def sensitive_clearance(self, *, agents, require=("sensitive_data",)):
        if self.agent not in set(agents):
            return "agent_not_allowed"
        if self.refusal:
            return self.refusal
        # As the SDK: the conversation itself must have required every flag.
        return None if set(require) <= set(self.requirements) else "requirements"


class FakeRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@pytest.fixture
def sdk(monkeypatch):
    """A canopy SDK that can verify statements; ``sdk.verify`` decides what it returns."""
    module = types.ModuleType("canopy_sdk.django.execution")
    module.ExecutionRefused = FakeRefused
    module.calls = []

    def verify_execution(headers, access_token=None):
        module.calls.append((headers, access_token))
        if "canopy-execution" not in headers:
            raise FakeRefused("no_statement")
        return module.result

    module.verify_execution = verify_execution
    module.result = FakeExecution()
    monkeypatch.setitem(sys.modules, "canopy_sdk.django.execution", module)
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True)
    return module


@pytest.fixture
def labs_allows(settings):
    """Labs lets labsdata read real data ("Real data" ticked), and so requires sensitive-data runners of it."""
    settings.CANOPY_SENSITIVE_AGENTS = ["labsdata"]
    settings.CANOPY_DATA_AGENT_SLUG = "labsdata"
    settings.CANOPY_RUNNER_REQUIREMENTS = []


def test_without_an_sdk_that_verifies_nothing_is_vouched(monkeypatch):
    import canopy_sdk.django

    # An SDK older than 0.12: no canopy_sdk.django.execution to import.
    monkeypatch.setitem(sys.modules, "canopy_sdk.django.execution", None)
    monkeypatch.delattr(canopy_sdk.django, "execution", raising=False)
    assert execution.supported() is False
    assert execution.verify({"canopy-execution": "x"}, "tok").refused == "sdk_unsupported"


def test_a_missing_or_bad_statement_is_a_refusal_never_an_error(sdk):
    assert execution.verify({}, "tok").refused == "no_statement"
    sdk.verify_execution = mock.Mock(side_effect=RuntimeError("boom"))
    assert execution.verify({"canopy-execution": "x"}, "tok").refused == "verify_error"


def test_outside_a_delegated_call_there_is_no_vouching(labs_allows, sdk):
    clearance = canopy_panel.call_real_data_clearance()
    assert not clearance.granted and "no statement" in clearance.reason


def test_a_verified_sensitive_call_on_a_sensitive_data_runner_is_cleared(labs_allows, sdk):
    call = execution.verify({"canopy-execution": "jwt"}, "tok")
    with execution.bound(call):
        assert canopy_panel.call_real_data_clearance().granted
    # And not once the call is over.
    assert not canopy_panel.call_real_data_clearance().granted


@pytest.mark.parametrize(
    "stated, reason",
    [
        (FakeExecution(agent="ace"), 'tick "Real data"'),
        (FakeExecution(requirements=()), "requirements"),
        (FakeExecution(refusal="runner_flags"), "runner_flags"),
        (FakeExecution(refusal="undeclared"), "undeclared"),
    ],
)
def test_each_condition_on_the_statement_fails_closed(labs_allows, sdk, stated, reason):
    sdk.result = stated
    with execution.bound(execution.verify({"canopy-execution": "jwt"}, "tok")):
        clearance = canopy_panel.call_real_data_clearance()
    assert not clearance.granted and reason in clearance.reason


def test_labs_requires_sensitive_data_of_every_agent_it_lets_read_real_data(labs_allows, settings, sdk):
    # No site-wide requirement and no data-agent setting: labs' own policy still holds
    # an agent it lets read real data to sensitive-data runners (it is only ever offered on them),
    # so the call clears -- and a statement whose conversation does not require sensitive-data runners is
    # refused (test_each_condition_on_the_statement_fails_closed).
    settings.CANOPY_DATA_AGENT_SLUG = ""
    settings.CANOPY_RUNNER_REQUIREMENTS = []
    with execution.bound(execution.verify({"canopy-execution": "jwt"}, "tok")):
        assert canopy_panel.call_real_data_clearance().granted


def test_a_refused_statement_names_why(labs_allows, sdk):
    with execution.bound(execution.verify({}, "tok")):
        clearance = canopy_panel.call_real_data_clearance()
    assert not clearance.granted and "no_statement" in clearance.reason


def test_the_audit_records_who_executed_never_the_whole_claims(sdk):
    audit = execution.verify({"canopy-execution": "jwt"}, "tok").audit()
    assert audit["agent"] == "labsdata" and "agent_sha" not in audit
    assert audit["declaration"]["declared_by"] == "a person"
    assert audit["runner"] == {"id": 7, "name": "sensitive-data-1", "flags": ["sensitive_data"]}
    assert audit["runner_data_mode"] == "sensitive_data" and audit["requirements"] == ["sensitive_data"]
    assert execution.verify({}, "tok").audit() == {"refused": "no_statement"}


@pytest.mark.django_db
def test_a_delegated_tool_call_verifies_once_and_audits_the_statement(labs_allows, sdk, django_user_model):
    """The statement is single-use: one verification per call, however many checks read it."""
    from connect_labs.mcp import server
    from connect_labs.mcp.models import MCPAuditLog
    from connect_labs.mcp.tool_registry import get_tool

    user = django_user_model.objects.create_user(username="exec-visitor", password="x")
    access = mock.Mock(token="tok", client_id="canopy", claims={"auth_method": "delegated"})
    seen = []

    def handler(user, **kwargs):
        seen.append(canopy_panel.call_real_data_clearance().granted)
        seen.append(canopy_panel.call_real_data_clearance().granted)
        return {"ok": True}

    import dataclasses

    spec = dataclasses.replace(get_tool("data_validate"), handler=handler)
    with (
        mock.patch.object(server, "get_access_token", return_value=access),
        mock.patch.object(server, "current_user", return_value=user),
        mock.patch.object(server, "allowed_tools", return_value=None),
        mock.patch.object(server, "restricted_call", return_value=False),
        mock.patch("fastmcp.server.dependencies.get_http_headers", return_value={"canopy-execution": "jwt"}),
    ):
        server._run_registry_tool(spec, {"sql": "SELECT 1 FROM visits"})
    assert seen == [True, True]
    assert len(sdk.calls) == 1
    row = MCPAuditLog.objects.filter(tool_name="data_validate").latest("id")
    assert row.execution["agent"] == "labsdata" and row.client_id == "canopy"
