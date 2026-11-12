"""Which MCP tools have a relationship-scope decision, as a ratchet (phases 1 and 2).

THIS REPOSITORY IS PUBLIC.

Every generated tool (`agent_operations()`) is one of:

  ENFORCED   a read whose rows are one modelled table. `access_shadow` observes it and, since phase 2, a partner's
             read of it is scoped to its own relationships when the programme has `acl_enforce_reads` on
             (`access_enforce`). test_access_relations.py proves the scope of each table row by row, and
             test_access_enforce.py proves the cross-organisation leaks are closed, here, per tool.
  RUNNER_ONLY  `access_preview`: a runner-only read of counts. It enforces nothing and exposes nothing to a partner.
  UNCOVERED  not yet. Listed by name below, which is the whole point: later phases cannot move scoping onto the
             tools and forget one, because this list is what is left to do. The writes are phase 3's.

A NEW tool belongs to none of these and FAILS here until someone decides: model its rows and enforce it, or add it
to the list as a conscious "not yet". A tool that becomes covered must LEAVE the list (the ratchet only tightens),
and a name that no longer exists must be removed.

TODO (phase 4, the next PR -- NOT done here, on purpose): aggregates (stock position, landed cost, comparison,
overview figures), history/audit (`OperationCall`, `Revision`, the timeline) and exports are not filtered per viewer
yet. Scoping rows without scoping totals is a known, listed gap; see the spec's "Still NOT filtered".
"""

import connect_labs.supply_chain.mcp_tools  # noqa: F401,E402 -- registers every operation
from connect_labs.supply_chain import access_enforce, access_relations, access_shadow, models
from connect_labs.supply_chain.operations import agent_operations

ENFORCED = frozenset(access_shadow.READ_OPERATION_MODELS)
RUNNER_ONLY = frozenset({"access_preview"})
COVERED = ENFORCED | RUNNER_ONLY

UNCOVERED_TODAY = frozenset(
    {
        "alert_log_list",
        "alert_partner_setup",
        "alert_subscription_create",
        "alert_subscription_delete",
        "alert_subscription_list",
        "alert_subscription_update",
        "approval_decide",
        "approval_request",
        "award_create",
        "award_orders_create",
        "award_orders_progress",
        "chain_summary",
        "charge_record",
        "checks_list",
        "commitment_record",
        "commitment_reply_sent",
        "commitment_resolve",
        "commodity_list",
        "commodity_market_offers",
        "commodity_supply_base",
        "commodity_upsert",
        "consignment_dispatch",
        "consignment_list",
        "consignment_receive",
        "contract_create",
        "contract_holds",
        "contract_landed_cost",
        "contract_match",
        "contract_update",
        "custodian_assign",
        "custodian_list",
        "dispensing_rule_get",
        "dispensing_rule_list",
        "dispensing_rule_upsert",
        "distribution_confirm",
        "distribution_get",
        "distribution_list",
        "distribution_message_match",
        "distribution_plan",
        "distribution_receipt_confirm",
        "distribution_receipt_confirm_all",
        "distribution_record",
        "document_attach",
        "document_list",
        "followup_render",
        "invoice_dispute",
        "invoice_record",
        "invoice_update",
        "item_get",
        "item_list",
        "item_offer_from_quote",
        "item_upsert",
        "movement_record",
        "movement_remove",
        "network_stock",
        "network_tree",
        "org_list",
        "org_merge",
        "org_upsert",
        "outreach_delete",
        "outreach_log",
        "outreach_update",
        "partner_places_assign",
        "partner_plan_list",
        "partner_plan_set",
        "partner_profile_list",
        "partner_profile_set",
        "payment_confirm",
        "payment_list",
        "payment_record",
        "programme_runner_get",
        "programme_runner_set",
        "programme_team_add",
        "programme_team_list",
        "programme_team_remove",
        "purchase_sizing",
        "quote_correct",
        "quote_record",
        "quote_void",
        "receipt_record",
        "reminder_render",
        "request_render",
        "resupply_plan",
        "selection_drafts_render",
        "shipment_record",
        "shipment_update",
        "stock_by_batch",
        "stock_count_list",
        "stock_count_record",
        "stock_delivery_suggestion",
        "stock_flow",
        "stock_forecast",
        "stock_issue_follow_up_add",
        "stock_issue_list",
        "stock_issue_report",
        "stock_issue_resolve",
        "stock_issue_summary",
        "stock_issue_update",
        "stock_on_hand",
        "stock_plan_get",
        "stock_plan_set",
        "stock_plan_view",
        "stock_position",
        "supplier_create",
        "supplier_mark_reviewed",
        "supplier_market_invite",
        "supplier_performance",
        "supplier_update",
        "supply_point_upsert",
        "tender_close",
        "tender_compare",
        "tender_create",
        "tender_drafts_render",
        "tender_invite_org",
        "tender_open",
        "tender_outstanding_questions",
        "tender_set_duty_terms",
        "tender_set_import_estimates",
        "tender_uninvite_org",
        "tender_update",
        "update_link_issue",
        "update_link_list",
        "update_link_revoke",
        "view_list",
        "view_pin",
        "view_unpin",
        "worker_stock",
        "worker_stock_get",
    }
)


def test_every_tool_is_covered_or_consciously_uncovered():
    tools = set(agent_operations())
    unaccounted = sorted(tools - COVERED - UNCOVERED_TODAY)
    assert not unaccounted, (
        f"new MCP tool(s) {unaccounted} have no relationship-scope decision: model their rows in "
        "access_relations.MODELS and add them to access_shadow.READ_OPERATION_MODELS, or list them in "
        "UNCOVERED_TODAY here as a deliberate 'not yet'"
    )


# TODO(phase 4 -- aggregates; deliberately NOT enforced in phase 2). These reads sum or derive across rows, so scoping
# the rows does not scope the totals: each needs a decision (hide, per-viewer figure, runner-only) before it can leave
# UNCOVERED_TODAY. History/audit (OperationCall, Revision, the contract timeline) and exports are not MCP tools of this
# registry and are tracked in the spec instead. Pages that draw from these (the Stock page's points, Overview figures,
# the Map) are pinned as KNOWN GAPS in test_access_enforce_pages.py and fail when somebody closes them.
PHASE_4_AGGREGATES = frozenset(
    {
        "award_orders_progress",
        "chain_summary",
        "checks_list",
        "contract_holds",
        "contract_landed_cost",
        "contract_match",
        "network_stock",
        "network_tree",
        "purchase_sizing",
        "resupply_plan",
        "stock_by_batch",
        "stock_delivery_suggestion",
        "stock_flow",
        "stock_forecast",
        "stock_issue_summary",
        "stock_on_hand",
        "stock_position",
        "supplier_performance",
        "tender_compare",
        "tender_outstanding_questions",
        "worker_stock",
        "worker_stock_get",
    }
)


def test_the_phase_four_aggregates_are_real_reads_still_waiting_on_a_decision():
    tools = agent_operations()
    for name in PHASE_4_AGGREGATES:
        assert name in tools and not tools[name].is_write, name
        assert name in UNCOVERED_TODAY, f"{name} left UNCOVERED_TODAY: remove it from PHASE_4_AGGREGATES too"


def test_the_uncovered_list_only_shrinks():
    tools = set(agent_operations())
    assert not sorted(UNCOVERED_TODAY - tools), "a listed tool no longer exists: remove it from UNCOVERED_TODAY"
    assert not sorted(UNCOVERED_TODAY & COVERED), "a covered tool is still listed as uncovered: remove it"


def test_an_enforced_tool_is_a_real_read_of_a_modelled_table():
    tools = agent_operations()
    for name in ENFORCED:
        assert name in tools, name
        assert not tools[name].is_write, name
        assert access_relations.modelled(getattr(models, access_shadow.READ_OPERATION_MODELS[name])), name
        assert access_enforce.enforced_model(name) is getattr(models, access_shadow.READ_OPERATION_MODELS[name])


def test_a_runner_only_tool_is_a_read_that_refuses_a_partner():
    tools = agent_operations()
    for name in RUNNER_ONLY:
        assert name in tools and not tools[name].is_write, name


def test_the_numbers_phase_two_leaves():
    tools = set(agent_operations())
    writes = {n for n, op in agent_operations().items() if op.is_write}
    assert len(tools) == len(COVERED) + len(UNCOVERED_TODAY)
    assert (len(ENFORCED), len(RUNNER_ONLY), len(UNCOVERED_TODAY)) == (20, 1, 128)
    assert writes <= UNCOVERED_TODAY, "writes are phase 3's (per-object write capabilities); none is covered yet"


# ---- every enforced tool, against a real cross-organisation leak ---------------------------------------------

import pytest  # noqa: E402

from connect_labs.labs.access.scopes import SYSTEM  # noqa: E402
from connect_labs.scope_config.models import ScopeConfig  # noqa: E402
from connect_labs.supply_chain import identity  # noqa: E402
from connect_labs.supply_chain.data_access import SupplyDataAccess  # noqa: E402
from connect_labs.supply_chain.operations import call_operation  # noqa: E402
from connect_labs.supply_chain.tests import access_relations_world as worlds  # noqa: E402

# tool -> (programme read in, the world row it returns, the organisation that must NOT see that row, payload key)
LEAKS = {
    "tender_list": (worlds.B, "tender_b", "W", None),
    "tender_get": (worlds.B, "tender_b", "W", "tender_id"),
    "supplier_list": (worlds.B, "supplier_bx", "W", None),
    "supplier_get": (worlds.B, "supplier_bx", "W", "supplier_id"),
    "quote_list": (worlds.B, "quote_bx", "W", None),
    "quote_get": (worlds.B, "quote_bx", "W", "quote_id"),
    "award_list": (worlds.B, "award_b", "W", None),
    "approval_list": (worlds.B, "approval_b", "X", None),
    "contract_list": (worlds.C, "contract_cz", "X", None),
    "contract_get": (worlds.C, "contract_cz", "X", "contract_id"),
    "shipment_list": (worlds.C, "shipment_cz", "X", None),
    "shipment_get": (worlds.C, "shipment_cz", "X", "shipment_id"),
    "charge_list": (worlds.C, "charge_cz", "X", None),
    "receipt_list": (worlds.C, "receipt_cx", "N", None),
    "invoice_list": (worlds.C, "invoice_cz", "X", None),
    "commitment_list": (worlds.C, "commitment_cw", "X", None),
    "supply_point_list": (worlds.C, "store_z", "X", None),
    "supply_point_get": (worlds.C, "store_z", "X", "supply_point_id"),
    "movement_list": (worlds.C, "movement_cx", "N", None),
    "outreach_list": (worlds.B, "outreach_bx", "W", None),
}


def test_every_enforced_tool_has_a_leak_test_below():
    assert set(LEAKS) == set(ENFORCED)


def _partner(program_id, connect_id):
    da = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
    da.caller = None
    da.user = None
    da.connect_orgs = [{"id": connect_id, "slug": f"conn-{connect_id}"}]
    return da


def _rows(result):
    if result is None:
        return set()
    if isinstance(result, dict):
        return {(result.get("quote") or result)["id"]}
    return {row["id"] for row in result}


@pytest.mark.django_db
@pytest.mark.real_standing
@pytest.mark.parametrize("name", sorted(LEAKS))
def test_org_x_does_not_see_org_ys_rows_through_this_tool_once_enforcement_is_on(name, monkeypatch):
    from connect_labs.supply_chain import models as m

    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))
    world = worlds.build()
    tender_b = world.rows["tender_b"]
    world.rows["outreach_bx"] = m.Outreach.objects.create(tender=tender_b, supplier=world.rows["supplier_bx"])
    program, key, outsider, id_key = LEAKS[name]
    row = world.rows[key]
    connect = {"X": 501, "Y": 502, "Z": 503, "W": 504, "N": 505}
    payload = {id_key: row.pk} if id_key else {}

    # Before: the outsider, a partner, reads the row (the programme-wide behaviour phase 2 exists to close).
    before = _rows(call_operation(name, _partner(program, connect[outsider]), payload))
    assert row.pk in before, f"{name}: the leak is not there to close (the test is vacuous)"
    ScopeConfig.objects.create(
        scope_type="program", scope_key=str(program), namespace="supply", data={"acl_enforce_reads": True}
    )
    after = _rows(call_operation(name, _partner(program, connect[outsider]), payload))
    assert row.pk not in after, f"{name}: org {outsider} still reads a row it holds no relation to"
    # And the runner of that programme still reads it.
    runner = {worlds.B: 502, worlds.C: 503}[program]
    assert row.pk in _rows(call_operation(name, _partner(program, runner), payload)), name
