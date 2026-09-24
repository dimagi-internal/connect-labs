"""Find what is new for each subscription, and send it.

One run, on celery beat every five minutes (migration 0012):

  1. For each programme with an active subscription, run the checks ONCE.
  2. For each subscription, compare the matching checks with what it has
     already reported. A check with no open state row is new: record it, and
     queue a notice. A state row whose check has gone is closed, so the check
     is new again if it ever comes back.
  3. For each subscription watching movements, read the ledger past its
     watermark, queue a notice per match, and move the watermark.
  4. Deliver: an immediate subscription gets one email per run with whatever
     is new; a digest gets one email a day.

**Polled, not hooked.** Movements are written from five places (a receipt, a
distribution, an override, a transfer, an ingest), all of them inside
transactions. A hook at each would be five places to forget, and an email
queued inside a transaction that then rolls back reports a movement that never
happened. Reading the ledger by id after the fact sees exactly what committed.

**What the email says.** The check's kind, its subject, its facts, how long it
has been true, and a link to the record. Nothing else -- no advice, no ranking,
no "you should". Wording and priority are a client's job (design doc section
22); this is delivery.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.alerts.models import AlertCheckState, AlertNotice, AlertSubscription
from connect_labs.supply_chain.models import (
    Award,
    Commodity,
    Contract,
    Invoice,
    Item,
    Movement,
    Payment,
    Quote,
    Receipt,
    Round,
    Shipment,
    StockCount,
)
from connect_labs.utils.email import email_enabled, send_labs_email

logger = logging.getLogger(__name__)

DIGEST_INTERVAL = timedelta(hours=24)

# A second run starting while one is still going would find the same new
# checks. The partial unique index on open state rows already stops a double
# report, but not a double email for movements, so runs do not overlap.
_LOCK_KEY = "supply_chain:alerts:run"
_LOCK_SECONDS = 10 * 60


def check_key(check: dict) -> str:
    subject = check.get("subject") or {}
    return f"{check['kind']}:{subject.get('type')}:{subject.get('id')}"


# ---- what a subject touches ---------------------------------------------
#
# A subscription can narrow to a supply point or a commodity, but a check's
# subject is whatever the check is about -- a contract, a shipment, an item.
# These say which points and commodities each kind of subject touches, so the
# filter can be applied to any check, including ones added after this was
# written. A subject type nobody taught this about falls back on the ids a
# check commonly carries in its facts, and failing that does NOT match a
# filtered subscription: telling someone about a thing their filter may not
# cover is the one mistake an alert must not make.


def _contract_scope(contract):
    if contract is None:
        return None
    points = {contract.delivery_supply_point_id} - {None}
    return points, {contract.commodity_id}


def _point_commodities(point_id):
    moved = Movement.objects.filter(Q(to_supply_point_id=point_id) | Q(from_supply_point_id=point_id))
    counted = StockCount.objects.filter(supply_point_id=point_id)
    return set(moved.values_list("commodity_id", flat=True)) | set(counted.values_list("commodity_id", flat=True))


def _round_commodities(round_):
    slugs = [line.get("commodity_slug") for line in (round_.lines or []) if line.get("commodity_slug")]
    return set(Commodity.objects.filter(slug__in=slugs).values_list("pk", flat=True))


def _subject_scope(subject_type, subject_id, facts):
    """(supply point ids, commodity ids) this subject touches, or None if unknown."""
    if subject_id is None:
        return None
    if subject_type == "supply_point":
        return {subject_id}, _point_commodities(subject_id)
    if subject_type == "contract":
        return _contract_scope(Contract.objects.filter(pk=subject_id).first())
    if subject_type == "shipment":
        shipment = Shipment.objects.filter(pk=subject_id).select_related("contract").first()
        return _contract_scope(shipment.contract if shipment else None)
    if subject_type == "invoice":
        invoice = Invoice.objects.filter(pk=subject_id).select_related("contract").first()
        return _contract_scope(invoice.contract if invoice else None)
    if subject_type == "payment":
        payment = Payment.objects.filter(pk=subject_id).select_related("invoice__contract").first()
        return _contract_scope(payment.invoice.contract if payment else None)
    if subject_type == "receipt":
        receipt = Receipt.objects.filter(pk=subject_id).select_related("contract").first()
        if receipt is None:
            return None
        commodities = {receipt.contract.commodity_id} if receipt.contract_id else set()
        return {receipt.supply_point_id}, commodities
    if subject_type in ("award", "quote"):
        model = Award if subject_type == "award" else Quote
        row = model.objects.filter(pk=subject_id).first()
        return (set(), {row.commodity_id}) if row else None
    if subject_type == "item":
        item = Item.objects.filter(pk=subject_id).first()
        return (set(), {item.commodity_id}) if item else None
    if subject_type == "commodity":
        return set(), {subject_id}
    if subject_type == "round":
        round_ = Round.objects.filter(pk=subject_id).first()
        return (set(), _round_commodities(round_)) if round_ else None

    facts = facts or {}
    for key, kind in (("contract_id", "contract"), ("shipment_id", "shipment"), ("supply_point_id", "supply_point")):
        if facts.get(key) is not None:
            return _subject_scope(kind, facts[key], {})
    return None


def _within_filters(subscription, scope) -> bool:
    if subscription.supply_point_id is None and subscription.commodity_id is None:
        return True
    if scope is None:
        return False
    points, commodities = scope
    if subscription.supply_point_id is not None and subscription.supply_point_id not in points:
        return False
    if subscription.commodity_id is not None and subscription.commodity_id not in commodities:
        return False
    return True


# ---- where the record lives ---------------------------------------------


def _url(name, *args, program_id):
    try:
        path = reverse(f"supply_chain:{name}", args=args)
    except NoReverseMatch:
        path = reverse("supply_chain:home")
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    # The programme rides on the query string because that is how the labs
    # context middleware selects one, so the link opens in the right scope for
    # a member who has a different programme selected.
    return f"{base}{path}?program_id={program_id}"


def record_url(subject_type, subject_id, facts, program_id) -> str:
    """The screen a person opens to see this subject, or the domain home."""
    facts = facts or {}
    if subject_type == "contract":
        return _url("order_detail", subject_id, program_id=program_id)
    if subject_type in ("shipment", "invoice", "receipt"):
        model = {"shipment": Shipment, "invoice": Invoice, "receipt": Receipt}[subject_type]
        contract_id = model.objects.filter(pk=subject_id).values_list("contract_id", flat=True).first()
        if contract_id:
            return _url("order_detail", contract_id, program_id=program_id)
    if subject_type == "payment":
        contract_id = Payment.objects.filter(pk=subject_id).values_list("invoice__contract_id", flat=True).first()
        if contract_id:
            return _url("order_detail", contract_id, program_id=program_id)
    if subject_type == "quote":
        return _url("procurement_quote_detail", subject_id, program_id=program_id)
    if subject_type == "award":
        round_id = Award.objects.filter(pk=subject_id).values_list("round_id", flat=True).first()
        if round_id:
            return _url("procurement_round_detail", round_id, program_id=program_id)
    if subject_type == "round":
        return _url("procurement_round_detail", subject_id, program_id=program_id)
    if subject_type == "item":
        return _url("item_detail", subject_id, program_id=program_id)
    if subject_type == "commodity":
        slug = Commodity.objects.filter(pk=subject_id).values_list("slug", flat=True).first()
        if slug:
            return _url("product_detail", slug, program_id=program_id)
    if subject_type == "supply_point":
        return _url("stock", program_id=program_id)
    if facts.get("contract_id"):
        return _url("order_detail", facts["contract_id"], program_id=program_id)
    return _url("home", program_id=program_id)


# ---- detection -------------------------------------------------------------


def _parse_date(value):
    from datetime import date

    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def detect_checks(subscription, found: list[dict], now) -> int:
    """Queue a notice for each matching check this subscription has not reported."""
    wanted = set(subscription.check_kinds or [])
    if not wanted:
        return 0

    current = {}
    for check in found:
        if check["kind"] not in wanted:
            continue
        subject = check.get("subject") or {}
        scope = None
        if subscription.supply_point_id is not None or subscription.commodity_id is not None:
            scope = _subject_scope(subject.get("type"), subject.get("id"), check.get("facts"))
        if _within_filters(subscription, scope):
            current[check_key(check)] = check

    open_states = {
        state.check_key: state for state in AlertCheckState.objects.filter(subscription=subscription, cleared_at=None)
    }

    # Gone: close the row, so a return is news.
    gone = [state.pk for key, state in open_states.items() if key not in current]
    if gone:
        AlertCheckState.objects.filter(pk__in=gone).update(cleared_at=now)

    still = [state.pk for key, state in open_states.items() if key in current]
    if still:
        AlertCheckState.objects.filter(pk__in=still).update(last_seen_at=now)

    queued = 0
    for key, check in current.items():
        if key in open_states:
            continue
        subject = check.get("subject") or {}
        try:
            with transaction.atomic():
                AlertCheckState.objects.create(
                    subscription=subscription, check_key=key, first_reported_at=now, last_seen_at=now
                )
                AlertNotice.objects.create(
                    subscription=subscription,
                    program_id=subscription.program_id,
                    kind="check",
                    subject_kind=check["kind"],
                    subject={**subject, "category": check.get("category"), "audience": check.get("audience")},
                    facts=check.get("facts") or {},
                    since=_parse_date(check.get("since")),
                    record_url=record_url(
                        subject.get("type"), subject.get("id"), check.get("facts"), subscription.program_id
                    ),
                    detected_at=now,
                )
        except IntegrityError:
            # Another run reported it between our read and our write.
            continue
        queued += 1
    return queued


def _movement_facts(movement) -> dict:
    return {
        "quantity": str(movement.quantity.normalize()) if movement.quantity is not None else None,
        "quantity_unit": movement.quantity_unit,
        "commodity": movement.commodity.name,
        "item": movement.item.name if movement.item_id else "",
        "batch": movement.batch,
        "from": movement.from_supply_point.name if movement.from_supply_point_id else "",
        "to": movement.to_supply_point.name if movement.to_supply_point_id else "",
        "occurred_on": movement.occurred_on.isoformat(),
        "source": movement.source,
        "recorded_by": movement.recorded_by_org.name if movement.recorded_by_org_id else "",
        "reference": movement.reference,
    }


def detect_movements(subscription, now) -> int:
    """Queue a notice per matching movement past this subscription's watermark."""
    wanted = set(subscription.movement_kinds or [])
    if not wanted:
        return 0
    fresh = (
        Movement.objects.filter(program_id=subscription.program_id, pk__gt=subscription.movements_seen_through)
        .select_related("commodity", "item", "from_supply_point", "to_supply_point", "recorded_by_org")
        .order_by("pk")
    )
    head = subscription.movements_seen_through
    queued = 0
    for movement in fresh:
        head = max(head, movement.pk)
        if movement.kind not in wanted:
            continue
        scope = ({movement.from_supply_point_id, movement.to_supply_point_id} - {None}, {movement.commodity_id})
        if not _within_filters(subscription, scope):
            continue
        point_id = movement.to_supply_point_id or movement.from_supply_point_id
        AlertNotice.objects.create(
            subscription=subscription,
            program_id=subscription.program_id,
            kind="movement",
            subject_kind=movement.kind,
            subject={"type": "movement", "id": movement.pk, "label": f"{movement.kind} of {movement.commodity.name}"},
            facts=_movement_facts(movement),
            since=movement.occurred_on,
            record_url=record_url("supply_point", point_id, {}, subscription.program_id),
            detected_at=now,
        )
        queued += 1
    if head != subscription.movements_seen_through:
        AlertSubscription.objects.filter(pk=subscription.pk).update(movements_seen_through=head)
        subscription.movements_seen_through = head
    return queued


def ledger_head(program_id) -> int:
    """The newest movement id in a programme, where a new subscription starts reading."""
    return Movement.objects.filter(program_id=program_id).aggregate(head=Max("pk"))["head"] or 0


# ---- delivery ---------------------------------------------------------------


def _digest_due(subscription, now) -> bool:
    last = subscription.last_digest_sent_at or subscription.created_at
    return now - last >= DIGEST_INTERVAL


def render_email(subscription, notices) -> tuple[str, str, str]:
    """(subject, text, html) for one subscription's batch of notices."""
    checks = [n for n in notices if n.kind == "check"]
    movements = [n for n in notices if n.kind == "movement"]
    parts = []
    if checks:
        parts.append(f"{len(checks)} new check{'s' if len(checks) != 1 else ''}")
    if movements:
        parts.append(f"{len(movements)} movement{'s' if len(movements) != 1 else ''}")
    name = subscription.label or f"programme {subscription.program_id}"
    subject = f"Supply alert — {name}: {', '.join(parts)}"
    context = {
        "subscription": subscription,
        "checks": checks,
        "movements": movements,
        "name": name,
        "manage_url": _url("alerts", program_id=subscription.program_id),
        "is_external": not subscription.recipient_user_id,
    }
    text = render_to_string("supply_chain/email/alert.txt", context)
    html = render_to_string("supply_chain/email/alert.html", context)
    return subject, text, html


def deliver(now, program_id=None) -> int:
    """Send what is due -- for one programme when named. Returns the number of emails queued."""
    pending = (
        AlertNotice.objects.filter(delivery="pending", subscription__active=True)
        .select_related("subscription__recipient_user")
        .order_by("detected_at", "pk")
    )
    if program_id is not None:
        pending = pending.filter(program_id=program_id)
    batches = defaultdict(list)
    subscriptions = {}
    for notice in pending:
        batches[notice.subscription_id].append(notice)
        subscriptions[notice.subscription_id] = notice.subscription

    emails = 0
    for subscription_id, notices in batches.items():
        subscription = subscriptions[subscription_id]
        if subscription.cadence == "daily_digest" and not _digest_due(subscription, now):
            continue
        address = subscription.recipient_address
        if not address:
            status = "no_address"
        elif not email_enabled():
            # Recorded as not sent, and not left pending: when mail is turned
            # back on, a backlog of stale alerts is not what anybody wants.
            status = "email_disabled"
        else:
            subject, text, html = render_email(subscription, notices)
            send_labs_email(subject=subject, message=text, recipient_list=[address], html_message=html)
            status = "queued"
            emails += 1
        AlertNotice.objects.filter(pk__in=[n.pk for n in notices]).update(
            delivery=status, sent_at=now, sent_to=address or ""
        )
        if subscription.cadence == "daily_digest":
            AlertSubscription.objects.filter(pk=subscription.pk).update(last_digest_sent_at=now)
    return emails


def found_so_far(program_id) -> dict:
    """What this programme's active alerts have already found, and how each left.

    Read after a "Check now" that found nothing new, so the page can say what
    IS true -- the notices already in the log, and whether each was sent --
    rather than "nothing new" directly above the rows it had found before.
    Counts by delivery status, and the latest time a notice was sent.
    """
    notices = AlertNotice.objects.filter(program_id=program_id, subscription__active=True)
    by_delivery = defaultdict(int)
    for delivery in notices.values_list("delivery", flat=True):
        by_delivery[delivery] += 1
    return {
        "total": sum(by_delivery.values()),
        "by_delivery": dict(by_delivery),
        "latest_sent_at": notices.filter(delivery="queued").aggregate(latest=Max("sent_at"))["latest"],
    }


def run_alerts(now=None, program_id=None) -> dict:
    """One full pass: detect for every active subscription, then deliver.

    `program_id` narrows the pass to one programme's subscriptions -- the
    "Check now" button on /supply/alerts/, so a person can see a new alert
    work without waiting for beat. Same detection, same new-only diff, same
    log, same lock: a check already reported is not reported again because it
    was asked for twice, and a press during a beat run waits its turn.
    """
    from connect_labs.supply_chain.checks import run_checks
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    now = now or timezone.now()
    if not cache.add(_LOCK_KEY, "1", _LOCK_SECONDS):
        logger.info("supply alerts: a run is already in progress; skipping")
        return {"skipped": True}
    try:
        by_program = defaultdict(list)
        active = AlertSubscription.objects.filter(active=True).order_by("pk")
        # `only` rather than `program_id` below: the loop that follows binds
        # program_id to each programme in turn, and delivery must not then be
        # narrowed to whichever programme happened to come last.
        only = program_id
        if only is not None:
            active = active.filter(program_id=only)
        for subscription in active:
            by_program[subscription.program_id].append(subscription)

        new_checks = new_movements = 0
        for program_id, subscriptions in by_program.items():
            found = []
            if any(s.check_kinds for s in subscriptions):
                # A beat task has no user: SYSTEM is the declared escape (see
                # labs/access/scopes.py). It reads only the programmes that
                # already hold a subscription, which a member created.
                access = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
                try:
                    found = run_checks(access)
                except Exception:
                    logger.exception("supply alerts: checks failed for programme %s", program_id)
                    continue
            for subscription in subscriptions:
                new_checks += detect_checks(subscription, found, now)
                new_movements += detect_movements(subscription, now)
        emails = deliver(now, program_id=only)
        return {
            "programmes": len(by_program),
            "subscriptions": sum(len(s) for s in by_program.values()),
            "new_checks": new_checks,
            "new_movements": new_movements,
            "emails": emails,
        }
    finally:
        cache.delete(_LOCK_KEY)


__all__ = ["run_alerts", "detect_checks", "detect_movements", "deliver", "record_url", "ledger_head"]
