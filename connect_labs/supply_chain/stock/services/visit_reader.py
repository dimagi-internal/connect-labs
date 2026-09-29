"""Turning submitted visits into stock leaving each worker's bag (design 2026-09-28 §4).

For each visit on or after a rule's `active_from`:

- resolve the worker's point, making it on first sight (workers.WorkerIndex);
- for each active rule, sum the rule's lines over the visit's answers
  (dispensing.evaluate) and post ONE consumption per item, dated the visit's
  day. Already posted: skip -- the rule may have been edited since, and a
  posted visit is not re-read;
- a visit `rejected`, `duplicate`, or flagged as a duplicate, whose
  consumption stands: post its reversal. A visit first seen rejected is not
  posted;
- no answer, an answer a value map does not know, a unit the item cannot
  count in: no movement; counted in the report, and remembered on the
  WorkerVisit. A rule whose forms do not include this visit's form is not
  applicable -- neither posted nor "unanswered".

Counted on arrival whatever the approval status (the owner's call,
2026-09-28): the sachets left the bag whether or not the visit is paid. How
much of a figure rests on unapproved visits is shown beside it, never folded
in.

`until` replays history: visits after it are left for later, and a
rejection recorded after it (status_modified_date) is read as still pending.
The synthetic seeder uses it to build week-by-week history an as-of page can
walk back through.

A re-read of visits already read costs the same handful of queries however
many visits there are: every lookup is loaded once up front, and an
unchanged visit writes nothing -- not even a revision.
"""

import hashlib
import logging

from django.db import connection, transaction
from django.utils import timezone

from connect_labs.supply_chain.models import DispensingRule, Movement, WorkerVisit
from connect_labs.supply_chain.stock.services import ingest, ledger, posting
from connect_labs.supply_chain.stock.services.dispensing import (
    DISPENSED,
    NO_ANSWER,
    NOT_APPLICABLE,
    NOTHING_GIVEN,
    UNIT_REFUSED,
    UNMAPPED,
    Dispensed,
    base_unit,
    evaluate,
    read_date,
    read_reports,
)
from connect_labs.supply_chain.stock.services.workers import WorkerIndex

logger = logging.getLogger(__name__)

REVERSING_STATUSES = ("rejected", "duplicate")
# over_limit is work reviewed clean that went past a payment cap: approved.
APPROVED_STATUSES = ("approved", "over_limit")

# Outcomes the reader records beyond dispensing's own.
SKIPPED = "skipped"
REVERSED = "reversed"
NOT_COUNTED = "not_counted"

NO_WORKER = "the visit names no worker (no username or user id)"
UUID_ONLY = (
    "the visit names only a Connect user id that no worker point carries, and a point is never made "
    "from an id alone"
)


def outcome_key(item_id) -> str:
    return f"item-{item_id}"


def _flagged_duplicate(visit) -> bool:
    reason = visit.get("flag_reason")
    flags = reason.get("flags") if isinstance(reason, dict) else None
    for flag in flags or []:
        code = flag[0] if isinstance(flag, list | tuple) and flag else flag
        if "duplicate" in str(code).lower():
            return True
    return False


def visit_status(visit, until=None) -> str:
    """The visit's status, lower-cased -- as it stood on `until`, when given."""
    status = str(visit.get("status") or "").strip().lower()
    # A duplicate flag is a hint for the reviewer. Once a reviewer has approved
    # the visit anyway they have judged it genuine, so the flag no longer
    # reverses it; it still does on a visit nobody has cleared.
    if status not in REVERSING_STATUSES and status not in APPROVED_STATUSES and _flagged_duplicate(visit):
        status = "duplicate"
    if until is not None and status in REVERSING_STATUSES:
        changed = read_date(visit.get("status_modified_date"))
        if changed is not None and changed > until:
            return "pending"
    return status


def _report(opportunity_id, visits, rules) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "rules": len(rules),
        "visits_read": len(visits),
        "posted": 0,
        "estimated": 0,
        "reversed": 0,
        "skipped_already_posted": 0,
        "skipped_already_reversed": 0,
        "rejected_unposted": 0,
        "no_answer": 0,
        "nothing_given": 0,
        "not_applicable": 0,
        "before_active_from": 0,
        "after_until": 0,
        "future": 0,
        "undated": 0,
        "unmatched": [],
        "unmapped": [],
        "unit_refused": [],
        "skipped": [],
        "reinstated_after_reversal": [],
        "balances_recorded": 0,
        "receipts_recorded": 0,
        "reports_already_recorded": 0,
        "created_supply_points": [],
    }


def _evaluate(rule, form_json) -> Dispensed:
    """dispensing.evaluate, with an item that cannot be counted at all refused rather than raised.

    base_unit raises when the item states no single unit. That is one rule's
    problem on every visit, not a reason to abandon the run for every other
    item and opportunity.
    """
    try:
        return evaluate(rule.lines, form_json, rule.item, rule_forms=tuple(rule.forms or ()))
    except ValueError as error:
        return Dispensed(UNIT_REFUSED, reasons=(str(error),))


def _lock_opportunity(opportunity_id) -> None:
    """Serialise runs per opportunity for the rest of this transaction.

    Two overlapping runs (the beat and an operator's command) would both see a
    visit as unposted, and the loser would hit the one-consumption constraint
    and roll back its whole batch. A transaction-scoped advisory lock makes
    the second wait, then read what the first posted.
    """
    digest = hashlib.sha256(f"visit_consumption_ingest:{opportunity_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


@transaction.atomic
def ingest_visit_consumption(access, *, opportunity_id, visits, until=None, today=None) -> dict:
    program_id = access._require_program()
    today = today or timezone.localdate()
    _lock_opportunity(opportunity_id)
    rules = list(
        DispensingRule.objects.filter(program_id=program_id, opportunity_id=opportunity_id, status="active")
        .select_related("item__commodity", "resupply_point")
        .order_by("pk")
    )
    report = _report(opportunity_id, visits, rules)
    earliest = min((rule.active_from for rule in rules), default=None)

    # Everything a visit needs to be decided, loaded once: a per-visit query
    # would make every hourly re-read grow with the opportunity.
    ids = [str(v["id"]) for v in visits if v.get("id") not in (None, "")]
    posted = {
        (m.visit_id, m.item_id): m
        for m in Movement.objects.filter(
            program_id=program_id, visit_id__in=ids, kind="consumption", reverses__isnull=True
        )
    }
    by_visit: dict[str, list] = {}
    for (visit_id, _), movement in posted.items():
        by_visit.setdefault(visit_id, []).append(movement)
    reversed_ = set(
        Movement.objects.filter(program_id=program_id, visit_id__in=ids, reverses__isnull=False).values_list(
            "visit_id", "item_id"
        )
    )
    seen = {w.visit_id: w for w in WorkerVisit.objects.filter(program_id=program_id, visit_id__in=ids)}
    index = WorkerIndex(access, opportunity_id)
    pending_reports: dict[int, dict] = {}

    for visit in visits:
        visit_id = str(visit.get("id") or "")
        if not visit_id:
            report["undated"] += 1
            continue
        status = visit_status(visit, until)

        # Reversals first, from what was posted -- not from the rules. A rule
        # deactivated, or its active_from moved, after its visits were posted
        # must not leave a rejected visit's consumption standing; nor may a
        # worker who no longer resolves. The reversal credits the point the
        # consumption debited.
        if status in REVERSING_STATUSES and visit_id in by_visit:
            _reverse_visit(report, seen, visit, visit_id, status, by_visit[visit_id], reversed_)

        on = read_date(visit.get("visit_date"))
        if on is None:
            report["undated"] += 1
            continue
        if on > today:
            report["future"] += 1
            continue
        if until is not None and on > until:
            report["after_until"] += 1
            continue
        if earliest is None:
            continue
        if on < earliest:
            report["before_active_from"] += 1
            continue

        form_json = visit.get("form_json") if isinstance(visit.get("form_json"), dict) else {}
        form = form_json.get("form") if isinstance(form_json.get("form"), dict) else {}
        form_name = str(form.get("@name") or "")
        dated = [rule for rule in rules if on >= rule.active_from]

        username = str(visit.get("username") or "")
        user_uuid = str(visit.get("user_id") or "")
        point = index.ensure(username, user_uuid, parent=dated[0].resupply_point)
        if point is None:
            reason = UUID_ONLY if user_uuid.strip() else NO_WORKER
            report["unmatched"].append({"visit_id": visit_id, "reason": reason})
            continue

        if status not in REVERSING_STATUSES:
            _collect_reports(pending_reports, dated, visit, visit_id, form_json, point, on)

        outcomes, answers = {}, {}
        for rule in dated:
            key = (visit_id, rule.item_id)
            standing = posted.get(key)
            outcome = None
            if status in REVERSING_STATUSES:
                # A posted item was reversed above; only the never-posted are left.
                # Reported only where the rule reads this form at all.
                if standing is None and _evaluate(rule, form_json).outcome != NOT_APPLICABLE:
                    report["rejected_unposted"] += 1
                    outcome = NOT_COUNTED
            elif standing is not None:
                if key in reversed_:
                    report["reinstated_after_reversal"].append(visit_id)
                else:
                    report["skipped_already_posted"] += 1
            else:
                outcome, movement = _read_one(
                    report, rule, form_json, answers, program_id, opportunity_id, point, on, visit_id
                )
                if movement is not None:
                    posted[key] = movement
            if outcome is not None:
                outcomes[outcome_key(rule.item_id)] = outcome

        _remember(seen, program_id, opportunity_id, visit, visit_id, point, on, status, form_name, outcomes, answers)

    _record_reports(access, report, pending_reports, opportunity_id)
    report["created_supply_points"] = list(index.created)
    _log(report)
    return report


def _collect_reports(pending, rules, visit, visit_id, form_json, point, on):
    """Gather what the worker's app says on this visit, per rule that reads reports.

    A rejected or duplicate visit is not collected: its figures are disowned,
    and a disowned balance must not become a worker's last count.
    """
    submission = str(visit.get("xform_id") or form_json.get("id") or f"visit-{visit_id}")
    common = {"connect_username": point.connect_username, "form_submission_id": submission, "visit_id": visit_id}
    for rule in rules:
        if not rule.reports:
            continue
        said = read_reports(rule.reports, form_json)
        rows = pending.setdefault(rule.pk, {"rule": rule, "balance": [], "receipt": []})
        if said["balance"] is not None:
            rows["balance"].append({**common, "quantity": str(said["balance"]), "counted_on": on.isoformat()})
        if said["received"] is not None:
            counted_on = (said["received_on"] or on).isoformat()
            rows["receipt"].append({**common, "quantity": str(said["received"]), "counted_on": counted_on})


def _record_reports(access, report, pending, opportunity_id):
    """Record the collected balances and receipts as counts beside the ledger, never movements.

    Through the existing stock_report_ingest path (design 2026-09-28 3.4):
    idempotent on (submission, kind), so a re-read records nothing twice, and
    a form carrying both a receipt and a balance records both.
    """
    for rows in pending.values():
        rule = rows["rule"]
        try:
            unit = base_unit(rule.item)
        except ValueError as error:
            # One rule's item lost its unit: refuse that rule's reports, not the run.
            report["unit_refused"].append({"rule_id": rule.pk, "item_id": rule.item_id, "reasons": [str(error)]})
            continue
        for kind, counter, batch in (
            ("self_reported", "balances_recorded", rows["balance"]),
            ("reported_receipt", "receipts_recorded", rows["receipt"]),
        ):
            if not batch:
                continue
            result = ingest.ingest_stock_reports(
                access,
                rows=batch,
                commodity_slug=rule.item.commodity.slug,
                quantity_unit=unit,
                opportunity_id=opportunity_id,
                item_id=rule.item_id,
                kind=kind,
            )
            report[counter] += result["created"]
            report["reports_already_recorded"] += result["skipped_already_ingested"]


def _reverse_visit(report, seen, visit, visit_id, status, standing, reversed_):
    """Reverse every item this visit posted that is not reversed yet, and say so on its WorkerVisit."""
    outcomes = {}
    for movement in standing:
        key = (visit_id, movement.item_id)
        if key in reversed_:
            report["skipped_already_reversed"] += 1
            continue
        posting.post_visit_reversal(movement, reason=f"visit {status}")
        reversed_.add(key)
        report["reversed"] += 1
        outcomes[outcome_key(movement.item_id)] = REVERSED
    existing = seen.get(visit_id)
    if existing is None:
        # Posted before the reader remembered visits: nothing to update.
        return
    changed = []
    if existing.status != status[:32]:
        existing.status = status[:32]
        changed.append("status")
    merged = {**existing.outcomes, **outcomes}
    if merged != existing.outcomes:
        existing.outcomes = merged
        changed.append("outcomes")
    if changed:
        existing.save(update_fields=[*changed, "updated_at"])


def _read_one(report, rule, form_json, answers, program_id, opportunity_id, point, on, visit_id):
    """Evaluate one rule over one unposted visit and post it if it gave something.

    Returns (outcome, movement): outcome None when the rule does not read this
    form; movement set only when one was posted.
    """
    result = _evaluate(rule, form_json)
    if result.outcome == NOT_APPLICABLE:
        report["not_applicable"] += 1
        return None, None
    answers.update(result.answers)
    where = {"visit_id": visit_id, "item_id": rule.item_id}
    if result.outcome == DISPENSED:
        if result.quantity <= 0:
            # A trace answer that quantizes to 0.0000: the ledger holds no
            # zero-quantity consumption, and posting a guess is worse.
            report["skipped"].append(
                {
                    **where,
                    "reason": (
                        f"what it gave rounds to nothing at the ledger's precision "
                        f"({ledger.QUANTITY_SCALE} {result.unit})"
                    ),
                }
            )
            return SKIPPED, None
        movement = posting.post_visit_consumption(
            program_id=program_id,
            opportunity_id=opportunity_id,
            point=point,
            item=rule.item,
            quantity=result.quantity,
            unit=result.unit,
            occurred_on=on,
            visit_id=visit_id,
            estimated=result.estimated,
        )
        report["posted"] += 1
        report["estimated"] += int(result.estimated)
        return DISPENSED, movement
    if result.outcome == NO_ANSWER:
        report["no_answer"] += 1
    elif result.outcome == NOTHING_GIVEN:
        report["nothing_given"] += 1
    elif result.outcome == UNMAPPED:
        report["unmapped"].append(
            {**where, "answers": [{"path": path, "answer": answer} for path, answer in result.unmapped]}
        )
    elif result.outcome == UNIT_REFUSED:
        report["unit_refused"].append({**where, "reasons": list(result.reasons)})
    return result.outcome, None


def _log(report):
    problems = {
        name: len(report[name])
        for name in ("unmatched", "unmapped", "unit_refused", "skipped", "reinstated_after_reversal")
    }
    if any(problems.values()):
        logger.warning(
            "supply visit reader, opportunity %s: %s",
            report["opportunity_id"],
            ", ".join(f"{count} {name}" for name, count in problems.items() if count),
        )


def _remember(seen, program_id, opportunity_id, visit, visit_id, point, on, status, form_name, outcomes, answers):
    """Create or update the WorkerVisit, saving only when something changed.

    An unchanged re-read writes nothing -- not even a revision -- and reads
    nothing either: `seen` was loaded once, and the point is compared by id so
    no foreign key is fetched. A changed visit is saved on its own, because a
    queryset update would send no post_save and so leave no Revision.
    """
    existing = seen.get(visit_id)
    form_json = visit.get("form_json") if isinstance(visit.get("form_json"), dict) else {}
    fields = {
        "xform_id": str(visit.get("xform_id") or form_json.get("id") or "")[:64],
        "connect_username": str(visit.get("username") or "")[:150],
        "supply_point_id": point.pk,
        "visit_date": on,
        "status": status[:32],
        "form_name": form_name[:255],
    }
    if existing is None:
        seen[visit_id] = WorkerVisit.objects.create(
            program_id=program_id,
            opportunity_id=opportunity_id,
            visit_id=visit_id,
            outcomes=outcomes,
            answers=answers,
            **fields,
        )
        return
    changed = [name for name, value in fields.items() if getattr(existing, name) != value]
    for name in changed:
        setattr(existing, name, fields[name])
    merged_outcomes = {**existing.outcomes, **outcomes}
    merged_answers = {**existing.answers, **answers}
    if merged_outcomes != existing.outcomes:
        existing.outcomes = merged_outcomes
        changed.append("outcomes")
    if merged_answers != existing.answers:
        existing.answers = merged_answers
        changed.append("answers")
    if changed:
        existing.save(update_fields=[*changed, "updated_at"])


def run_scheduled() -> dict:
    """The beat task's body, synthetic programmes only.

    Every opportunity with an active rule -- and every opportunity still
    holding a visit's consumption that stands unreversed, rule or no rule, so
    a visit rejected after its rule was switched off is still reversed.

    A real programme is skipped by name, not read: nothing reads a real
    programme's visits into the ledger until the product owner says so.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.operations import call_operation

    results = {}
    ruled = DispensingRule.objects.filter(status="active").values_list("program_id", "opportunity_id").distinct()
    standing = (
        Movement.objects.filter(kind="consumption", source="connect_visit", opportunity_id__isnull=False)
        .standing_consumption()
        .exclude(visit_id="")
        .values_list("program_id", "opportunity_id")
        .distinct()
    )
    pairs = sorted(set(ruled) | set(standing))
    for program_id, opportunity_id in pairs:
        key = str(opportunity_id)
        if not scopes.is_synthetic(program_id):
            results[key] = {"skipped": "not a synthetic programme"}
            continue
        problem = scopes.opportunity_problem(program_id, opportunity_id)
        if problem:
            # Nothing is fetched: a rule naming a real (or another programme's)
            # opportunity is refused here as well as when it was saved.
            results[key] = {"skipped": problem}
            continue
        access = SupplyDataAccess(program_id=program_id, opportunity_id=opportunity_id, caller=SYSTEM)
        try:
            report = call_operation(
                "visit_consumption_ingest", access, {"opportunity_id": opportunity_id}, channel="command"
            )
        except Exception as error:  # one opportunity's failure must not stop the rest
            logger.exception("supply visit reader failed for opportunity %s", opportunity_id)
            results[key] = {"error": str(error)}
            continue
        results[key] = {
            **{name: report[name] for name in ("visits_read", "posted", "reversed", "no_answer")},
            **{name: len(report[name]) for name in ("unmatched", "unmapped", "unit_refused")},
        }
    return results
