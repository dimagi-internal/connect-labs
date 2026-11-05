"""Which MCP tools have a relationship-scope test, as a ratchet (phase 1).

THIS REPOSITORY IS PUBLIC.

Every generated tool (`agent_operations()`) is one of:

  COVERED    its rows are a modelled table, `access_shadow` observes it, and
             test_access_relations.py proves the scope of that table row by row
             (a row is in scope exactly when the viewer holds a relation to it).
  UNCOVERED  not yet. Listed by name below, which is the whole point: phase 2
             cannot swap scoping onto the tools and forget one, because this
             list is what is left to do.

A NEW tool belongs to neither list and FAILS here until someone decides: model
its rows and shadow it (COVERED), or add it to the list as a conscious "not yet".
A tool that becomes covered must LEAVE the list (the ratchet only tightens),
and a name that no longer exists must be removed.
"""

import connect_labs.supply_chain.mcp_tools  # noqa: F401,E402 -- registers every operation
from connect_labs.supply_chain import access_relations, access_shadow, models
from connect_labs.supply_chain.operations import agent_operations

COVERED = frozenset(access_shadow.READ_OPERATION_MODELS)

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
        "network_stock",
        "network_tree",
        "org_list",
        "org_merge",
        "org_upsert",
        "outreach_delete",
        "outreach_list",
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


def test_the_uncovered_list_only_shrinks():
    tools = set(agent_operations())
    assert not sorted(UNCOVERED_TODAY - tools), "a listed tool no longer exists: remove it from UNCOVERED_TODAY"
    assert not sorted(UNCOVERED_TODAY & COVERED), "a covered tool is still listed as uncovered: remove it"


def test_a_covered_tool_is_a_real_read_of_a_modelled_table():
    tools = agent_operations()
    for name in COVERED:
        assert name in tools, name
        assert not tools[name].is_write, name
        assert access_relations.modelled(getattr(models, access_shadow.READ_OPERATION_MODELS[name])), name


def test_the_numbers_phase_two_starts_from():
    tools = set(agent_operations())
    writes = {n for n, op in agent_operations().items() if op.is_write}
    assert len(tools) == len(COVERED) + len(UNCOVERED_TODAY)
    assert (
        writes <= UNCOVERED_TODAY
    ), "writes are phase 3's (per-object write capabilities); none is covered in phase 1"
