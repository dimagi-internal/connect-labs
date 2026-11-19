"""The MCP labs-only gate (``server.policy_denial``) sees every opportunity and program
a call names at the top level, singular or plural.

A labs-only opportunity's visits and records are read in labs with no Connect check
behind them, so the gate is the only thing between a caller and another tenant's
opportunity. ``pipeline_preview`` fans a preview out over ``opportunity_ids``; before
this gate read the plural lists, naming your own opportunity as ``opportunity_id`` and
someone else's in ``opportunity_ids`` read theirs.
"""

import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.mcp.server import policy_denial

pytestmark = pytest.mark.django_db

MINE, THEIRS = 21_300, 21_301


@pytest.fixture
def partner(user):
    user.email = "pat@partner.org"
    user.save()
    SyntheticOpportunity.objects.create(opportunity_id=MINE, gdrive_folder_id="mine", labs_only=True, created_by=user)
    SyntheticOpportunity.objects.create(
        opportunity_id=THEIRS, gdrive_folder_id="theirs", labs_only=True, allowed_domains=["@other.org"]
    )
    return user


def _denial(user, **arguments):
    return policy_denial("pipeline_preview", arguments, user, restricted=False)


def test_your_own_opportunity_passes(partner):
    assert _denial(partner, pipeline_id=1, opportunity_id=MINE, opportunity_ids=[MINE]) is None


def test_another_tenants_opportunity_in_opportunity_ids_is_refused(partner):
    reason = _denial(partner, pipeline_id=1, opportunity_id=MINE, opportunity_ids=[THEIRS])
    assert reason == f"labs-only opportunity {THEIRS} is not accessible to your account"


def test_a_stringified_list_is_read_too(partner):
    # MCP arguments can arrive as a string despite the declared schema.
    reason = _denial(partner, pipeline_id=1, opportunity_id=MINE, opportunity_ids=f"[{THEIRS}]")
    assert reason == f"labs-only opportunity {THEIRS} is not accessible to your account"


def test_another_tenants_program_in_program_ids_is_refused(partner):
    reason = policy_denial("data_query", {"program_ids": [THEIRS]}, partner, restricted=False)
    assert reason == f"labs-only program {THEIRS} is not accessible to your account"


def test_real_opportunities_in_the_list_are_left_to_connect(partner):
    assert _denial(partner, pipeline_id=1, opportunity_id=MINE, opportunity_ids=[MINE, 812]) is None


@pytest.mark.parametrize(
    "tool, arguments",
    [
        # Cloning another tenant's workflow out, or writing a clone into their scope.
        ("workflow_clone", {"source_workflow_id": 7, "source_opportunity_id": THEIRS, "target_opportunity_id": MINE}),
        ("workflow_clone", {"source_workflow_id": 7, "source_opportunity_id": MINE, "target_program_id": THEIRS}),
        # Pointing your workflow at a pipeline or registry record that lives in theirs.
        (
            "workflow_add_pipeline_source",
            {"opportunity_id": MINE, "workflow_id": 7, "pipeline_id": 8, "home_scope": {"opportunity_id": THEIRS}},
        ),
        (
            "workflow_update_definition",
            {
                "opportunity_id": MINE,
                "workflow_id": 7,
                "patch": {"registry_source": {"registry_id": 9, "program_id": THEIRS}},
            },
        ),
        (
            "workflow_create",
            {"opportunity_id": MINE, "pipeline_sources": [{"home_scope": {"opportunity_id": THEIRS}}]},
        ),
        # The same, with the nested argument sent as a JSON string.
        (
            "workflow_update_definition",
            {"opportunity_id": MINE, "patch": f'{{"registry_source": {{"opportunity_id": {THEIRS}}}}}'},
        ),
        # A staged edit carries another tool's arguments.
        ("workflow_edit_propose", {"tool": "workflow_clone", "arguments": {"source_opportunity_id": THEIRS}}),
    ],
)
def test_a_scope_named_any_other_way_is_gated_too(partner, tool, arguments):
    assert policy_denial(tool, arguments, partner, restricted=False) is not None


def test_nested_scopes_the_caller_holds_pass(partner):
    arguments = {"opportunity_id": MINE, "pipeline_sources": [{"home_scope": {"opportunity_id": MINE}}]}
    assert policy_denial("workflow_create", arguments, partner, restricted=False) is None


def test_the_record_diagnostics_open_no_commcare_project_scope(partner):
    # A CommCare project's records live in the same table under a negative scope,
    # which the labs-only gate does not see; the diagnostics read that table directly.
    import connect_labs.mcp.tools.synthetic  # noqa: F401 -- registers the tools
    from connect_labs.hqdata.scope import hq_scope_id
    from connect_labs.labs.synthetic.models import LabsLocalRecord
    from connect_labs.mcp.tool_registry import MCPToolError, get_tool

    scope = hq_scope_id(5)
    record = LabsLocalRecord.objects.create(experiment="workflow", type="workflow_definition", opportunity_id=scope)
    assert policy_denial("synthetic_local_record_dump", {"opportunity_id": scope}, partner, restricted=False) is None
    with pytest.raises(MCPToolError):
        get_tool("synthetic_local_record_dump").handler(partner, opportunity_id=scope, record_id=record.id)
    with pytest.raises(MCPToolError):
        get_tool("synthetic_local_records_count").handler(partner, opportunity_id=scope)
    assert get_tool("synthetic_local_records_count").handler(partner, opportunity_id=MINE)["total"] == 0


def test_a_tool_may_name_scopes_its_handler_gates(partner):
    # Following a publicly shared template names the template's home scope; the
    # handler decides (public: read-only follow; private: refused).
    arguments = {
        "workflow_id": 1,
        "opportunity_id": MINE,
        "template_workflow_id": 2,
        "template_opportunity_id": THEIRS,
    }
    assert policy_denial("workflow_follow_template", arguments, partner, restricted=False) is None
    # Every other tool still has it gated.
    assert policy_denial("workflow_clone", {"source_opportunity_id": THEIRS}, partner, restricted=False)
