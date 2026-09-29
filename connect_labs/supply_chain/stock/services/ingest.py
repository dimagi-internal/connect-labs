"""Turning worker-submitted form data into reported stock counts.

A field worker's stock on hand arrives the way everything else about that
worker arrives: they answer a question on the CommCare deliver form, the
submission reaches Connect, and labs reads it. Nobody keys a stock report.

Three decisions this module makes, each of which would be wrong the other
way tender.

**Idempotent on the submission id.** A machine re-reading the same export is
normal -- a nightly job, a retried task, a widened date window -- and a second
read must not double-post. This is not labs policing its clients (a human
entering the same quote twice is theirs to void); a form submission is an
identifier for one report, and honouring it is the ingestion contract.

**An unknown worker is reported, not invented.** A username with no
`user_held` supply point comes back in `unmatched` rather than quietly
creating one, because a typo'd username would become a phantom worker holding
phantom stock, and the network would be wrong in a way nothing later could
detect. Creating them is available, but only if the caller asks.

**Reported, never posted.** An ingested figure is a `self_reported` count. It
sits beside the ledger balance so the variance is visible; it does not move
the ledger. A worker's phone is not a warehouse system, and a form answer
overwriting a ledger would destroy the one signal that says something is off.
An override is a deliberate, reasoned act by a person -- see
`stock.services.posting.post_override`.
"""

import logging

from django.db import transaction

from connect_labs.supply_chain.models import StockCount
from connect_labs.supply_chain.stock.services.workers import worker_slug

logger = logging.getLogger(__name__)


def existing_submission_ids(program_id, submission_ids, kind="self_reported", item=None) -> set[str]:
    """Which of these submissions already produced a count of this kind, in one query.

    Kind-aware because one Stock Management form carries both a receipt and a
    balance under the same submission id; keyed on the id alone, the second
    was skipped as "already ingested".
    """
    if not submission_ids:
        return set()
    counts = StockCount.objects.filter(program_id=program_id, kind=kind, form_submission_id__in=list(submission_ids))
    if item is not None:
        counts = counts.filter(item=item)
    return set(counts.values_list("form_submission_id", flat=True))


@transaction.atomic
def ingest_stock_reports(
    access,
    *,
    rows,
    commodity_slug,
    quantity_unit,
    opportunity_id,
    item_id=None,
    create_missing_points=False,
    kind="self_reported",
) -> dict:
    """Record a batch of worker-reported stock figures.

    Returns a report rather than raising on the awkward rows: an import of
    400 workers where 3 usernames are unknown should land the 397 and name
    the 3, not fail wholesale and leave the operator guessing which.
    """
    commodity = access._require_commodity(commodity_slug)
    item = access._resolve_item(item_id)
    program_id = access._require_program()

    already = existing_submission_ids(
        program_id, [r.get("form_submission_id") for r in rows if r.get("form_submission_id")], kind=kind, item=item
    )

    created, skipped, unmatched, created_points = [], 0, [], []

    for row in rows:
        username = (row.get("connect_username") or "").strip()
        if not username:
            unmatched.append({"connect_username": "", "reason": "the row names no worker"})
            continue

        submission_id = row.get("form_submission_id") or ""
        if submission_id and submission_id in already:
            skipped += 1
            continue

        point = access.get_supply_point_by_username(username, opportunity_id=opportunity_id)
        if point is None:
            if not create_missing_points:
                unmatched.append(
                    {
                        "connect_username": username,
                        "reason": (
                            "no supply point holds stock for this worker on this opportunity. "
                            "Create one of kind user_held, or re-run with create_missing_points "
                            "if these usernames are known to be right."
                        ),
                    }
                )
                continue
            point = access.upsert_supply_point(
                {
                    "slug": worker_slug(opportunity_id, username),
                    "name": username,
                    "kind": "user_held",
                    "opportunity_id": opportunity_id,
                    "connect_username": username,
                    "source": "commcare_form",
                }
            )
            created_points.append(point.pk)

        count = StockCount.objects.create(
            program_id=program_id,
            opportunity_id=opportunity_id,
            supply_point=point,
            commodity=commodity,
            item=item,
            kind=kind,
            counted_on=row["counted_on"],
            quantity=row["quantity"],
            quantity_unit=quantity_unit,
            batch=row.get("batch") or "",
            connect_username=username,
            form_submission_id=submission_id,
            visit_id=row.get("visit_id") or "",
            source="commcare_form",
        )
        created.append(count.pk)
        if submission_id:
            already.add(submission_id)

    if unmatched:
        logger.warning(
            "supply stock ingest: %s row(s) named a worker with no supply point on opportunity %s",
            len(unmatched),
            opportunity_id,
        )

    return {
        "created": len(created),
        "skipped_already_ingested": skipped,
        "unmatched": unmatched,
        "created_supply_points": created_points,
        "stock_count_ids": created,
    }


def extract_rows(visits, *, quantity_path, username_key="username", date_key="visit_date"):
    """Pull the reported quantity out of visit rows.

    The path is a form_json path (`form.stock.cartons_on_hand`): export and
    cache rows keep a submission's answers under `form_json`, so a path
    applied to the visit dict itself -- as this once did -- reads nothing, and
    "nobody reported" looks identical to nobody having reported.

    A visit that does not answer is skipped rather than read as zero. The
    submission id is the xform id (one form, one report); a row carrying none
    falls back to its visit id so a re-read still cannot double-post.
    """
    out = []
    for visit in visits:
        form_json = visit.get("form_json") if isinstance(visit.get("form_json"), dict) else {}
        raw = _dig(form_json, quantity_path)
        if raw in (None, ""):
            continue
        visit_id = str(visit.get("id") or "")
        xform_id = visit.get("xform_id") or form_json.get("id") or ""
        out.append(
            {
                "connect_username": visit.get(username_key) or visit.get("flw_username"),
                "quantity": raw,
                "counted_on": visit.get(date_key) or visit.get("visit_date"),
                "form_submission_id": str(xform_id) if xform_id else f"visit-{visit_id}",
                "visit_id": visit_id,
            }
        )
    return out


def _dig(row, dotted_path):
    """Walk a dotted path into nested form JSON, returning None if it is absent."""
    node = row
    for part in dotted_path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node
