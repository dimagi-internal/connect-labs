"""Phase 1 of the relationship ACL, observed: what `access_relations.scope` WOULD hide, logged and never enforced.

Modelled on the `--warn` rollout of ACE's tenancy guard: record the would-be
refusal, do not refuse. It runs after a read operation has produced its result,
for a caller with PARTNER standing only, and writes one log line of counts. It
never changes a result, never raises, and costs nothing when the flag is off.

Cost when on, per sampled read: the caller's standing and organisations (both
already cached on the access object for the request, and read by every write) and
ONE extra COUNT -- the rows of the operation's model, in the programme, that the
caller's relationships would not reach. Only one read in `SUPPLY_ACL_SHADOW_SAMPLE`
is observed (default 20; 1 observes every read; tests set 1). The COUNT runs inside
a savepoint so a failure cannot poison the caller's transaction.

What is logged (INFO, logger `connect_labs.supply_chain.access_shadow`), no PII --
numbers and names of code things only, no organisation, user, row or programme names:

    supply acl shadow: op=quote_list model=Quote standing=partner would_hide=7 program=20996

`program` is the numeric programme id. Nothing else is recorded.
"""

from __future__ import annotations

import logging
import random

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

# Read operations whose result is the rows of one modelled table, so "what would
# scope() hide" is well defined for them. Other reads (summaries, aggregates) are
# phase 4's problem: an aggregate leaks totals, not rows. The `*_get` operations
# are single-row reads of the same tables.
READ_OPERATION_MODELS = {
    "tender_list": "Tender",
    "tender_get": "Tender",
    "supplier_list": "Supplier",
    "supplier_get": "Supplier",
    "quote_list": "Quote",
    "quote_get": "Quote",
    "award_list": "Award",
    "approval_list": "AwardApproval",
    "contract_list": "Contract",
    "contract_get": "Contract",
    "shipment_list": "Shipment",
    "shipment_get": "Shipment",
    "charge_list": "Charge",
    "receipt_list": "Receipt",
    "invoice_list": "Invoice",
    "commitment_list": "Commitment",
    "supply_point_list": "SupplyPoint",
    "supply_point_get": "SupplyPoint",
    "movement_list": "Movement",
    "outreach_list": "Outreach",
}


def enabled() -> bool:
    return bool(getattr(settings, "SUPPLY_ACL_SHADOW", False))


def _sampled() -> bool:
    every = max(int(getattr(settings, "SUPPLY_ACL_SHADOW_SAMPLE", 20) or 1), 1)
    return every == 1 or random.randrange(every) == 0


def would_hide(access, model) -> int | None:
    """How many rows of `model` in the programme the caller's relationships would not reach; None if n/a.

    None for a caller who is the programme (nothing is hidden from them), has no
    programme, or whose model is not modelled. One COUNT.
    """
    from connect_labs.supply_chain import access_relations, runner

    program_id = getattr(access, "program_id", None)
    if program_id in (None, "") or not access_relations.modelled(model) or runner.is_system(access):
        return None
    if runner.standing(access).is_programme:
        return None
    reached = access_relations.scope(access, model)
    everything = access_relations.programme_rows(model, program_id)
    return everything.exclude(pk__in=reached.values("pk")).count()


def observe_read(operation, access) -> None:
    """Log what the relationship scope would hide from this read. Never raises, never alters anything."""
    try:
        if not enabled():
            return
        name = READ_OPERATION_MODELS.get(operation.name)
        if name is None or not _sampled():
            return
        from connect_labs.supply_chain import access_enforce

        if access_enforce.is_enforced(operation.name, access):
            return  # already scoped for real: what scope() would hide is hidden, there is nothing to observe
        from connect_labs.supply_chain import models

        model = getattr(models, name)
        with transaction.atomic():
            hidden = would_hide(access, model)
        if hidden is None:
            return
        logger.info(
            "supply acl shadow: op=%s model=%s standing=partner would_hide=%d program=%s",
            operation.name,
            name,
            hidden,
            getattr(access, "program_id", None),
        )
    except Exception:  # noqa: BLE001 -- observation must never change a read
        logger.debug("supply acl shadow: skipped", exc_info=True)
