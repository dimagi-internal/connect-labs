"""Alert operations: subscribe, change, stop, and read what was sent.

Registered into the one registry in operations.py, so the HTTP API, the MCP
server and the screens under /supply/alerts/ all reach the same five.

**Check kinds are validated against the live list.** `checks.KIND_CATEGORIES`
is the closed set `run_checks` can emit, and it grows by deploy -- another
change is adding five kinds as this is written. The schema therefore takes any
string and the handler checks it against the dict as it stands at call time,
so a new check is subscribable the moment it exists, and a typo is refused by
name rather than subscribed to a kind that can never fire.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from connect_labs.supply_chain import records
from connect_labs.supply_chain.alerts.models import CADENCES, AlertNotice, AlertSubscription
from connect_labs.supply_chain.operations import ID, NULLABLE_ID, _data_with, obj, register_operation

_SUBSCRIPTION_DATA = _data_with(
    label={"type": "string"},
    supply_point_id=NULLABLE_ID,
    commodity_slug={"type": ["string", "null"]},
    check_kinds={"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
    movement_kinds={"type": "array", "items": {"enum": list(records.MOVEMENT_KINDS)}, "uniqueItems": True},
    recipient_user_id=NULLABLE_ID,
    recipient_email={"type": "string", "format": "email"},
    cadence={"enum": list(CADENCES)},
    active={"type": "boolean"},
)


def serialize_subscription(sub) -> dict:
    return {
        "id": sub.pk,
        "program_id": sub.program_id,
        "label": sub.label,
        "supply_point_id": sub.supply_point_id,
        "supply_point_name": sub.supply_point.name if sub.supply_point_id else None,
        "commodity_slug": sub.commodity.slug if sub.commodity_id else None,
        "commodity_name": sub.commodity.name if sub.commodity_id else None,
        "check_kinds": list(sub.check_kinds or []),
        "movement_kinds": list(sub.movement_kinds or []),
        "recipient_user_id": sub.recipient_user_id,
        "recipient_email": sub.recipient_email,
        "recipient": sub.recipient_label,
        "cadence": sub.cadence,
        "active": sub.active,
        "created_by_id": sub.created_by_id,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
        "last_digest_sent_at": sub.last_digest_sent_at.isoformat() if sub.last_digest_sent_at else None,
    }


def serialize_notice(notice) -> dict:
    return {
        "id": notice.pk,
        "subscription_id": notice.subscription_id,
        "kind": notice.kind,
        "subject_kind": notice.subject_kind,
        "subject": notice.subject,
        "facts": notice.facts,
        "since": notice.since.isoformat() if notice.since else None,
        "record_url": notice.record_url,
        "detected_at": notice.detected_at.isoformat(),
        "delivery": notice.delivery,
        "sent_at": notice.sent_at.isoformat() if notice.sent_at else None,
        "sent_to": notice.sent_to,
    }


# ---- the rules a subscription must satisfy -------------------------------


def _subscriptions(access):
    return AlertSubscription.objects.filter(program_id=access._require_program()).select_related(
        "supply_point", "commodity", "recipient_user"
    )


def _require_subscription(access, subscription_id):
    found = _subscriptions(access).filter(pk=subscription_id).first()
    if found is None:
        raise ValueError(f"alert subscription {subscription_id} not found in this programme")
    return found


def _validate_check_kinds(kinds):
    from connect_labs.supply_chain.checks import KIND_CATEGORIES

    unknown = [kind for kind in kinds or [] if kind not in KIND_CATEGORIES]
    if unknown:
        raise ValueError(
            f"no check is called {', '.join(repr(k) for k in unknown)}. The checks that exist are: "
            + ", ".join(sorted(KIND_CATEGORIES))
        )


def _caller_user(access):
    user = getattr(access, "user", None)
    return user if getattr(user, "pk", None) else None


def _apply(access, sub, data, creating):
    """Set the fields `data` names, validating each against the programme."""
    if "label" in data:
        sub.label = data["label"] or ""
    if "check_kinds" in data:
        _validate_check_kinds(data["check_kinds"])
        sub.check_kinds = list(data["check_kinds"] or [])
    if "movement_kinds" in data:
        sub.movement_kinds = list(data["movement_kinds"] or [])
    if "cadence" in data:
        sub.cadence = data["cadence"]
    if "active" in data:
        sub.active = bool(data["active"])

    if "supply_point_id" in data:
        sub.supply_point = (
            access._require_supply_point(data["supply_point_id"], "supply point to watch")
            if data["supply_point_id"] is not None
            else None
        )
    if "commodity_slug" in data:
        slug = data["commodity_slug"]
        if slug:
            commodity = access.get_commodity(slug)
            if commodity is None:
                raise ValueError(f"no commodity {slug!r} in this programme's catalogue")
            sub.commodity = commodity
        else:
            sub.commodity = None

    if creating or "recipient_user_id" in data or "recipient_email" in data:
        # On an update, naming one kind of recipient replaces the other, so
        # switching from a user to an address is one field, not two.
        user_id, email = sub.recipient_user_id, sub.recipient_email
        if "recipient_user_id" in data:
            user_id = data["recipient_user_id"]
            if user_id and "recipient_email" not in data:
                email = ""
        if "recipient_email" in data:
            email = (data["recipient_email"] or "").strip()
            if email:
                try:
                    validate_email(email)
                except ValidationError as error:
                    raise ValueError(f"{email!r} is not an email address") from error
            if email and "recipient_user_id" not in data:
                user_id = None
        if bool(user_id) == bool(email):
            raise ValueError("an alert goes to exactly one recipient: a labs user or an email address, not both")
        if user_id:
            caller = _caller_user(access)
            # A member may subscribe themselves. Anyone else is reached by
            # typing their address, which is a visible, deliberate act --
            # rather than by an id that quietly signs another person up.
            if caller is not None and caller.pk != user_id:
                raise ValueError(
                    "you can send alerts to yourself or to an email address; to alert a colleague, "
                    "use their email address"
                )
            recipient = get_user_model().objects.filter(pk=user_id).first()
            if recipient is None:
                raise ValueError(f"user {user_id} does not exist")
            sub.recipient_user = recipient
            sub.recipient_email = ""
        else:
            sub.recipient_user = None
            sub.recipient_email = email

    if not (sub.check_kinds or sub.movement_kinds):
        raise ValueError("an alert has to watch something: name at least one check kind or movement kind")
    return sub


# ---- operations ------------------------------------------------------------


@register_operation(
    name="alert_subscription_list",
    summary=(
        "List this programme's alert subscriptions: what each watches (check kinds, movement kinds, "
        "an optional supply point or commodity), who it emails, and how often."
    ),
    input_schema=obj({"include_inactive": {"type": "boolean"}}),
)
def alert_subscription_list(access, include_inactive=True):
    qs = _subscriptions(access)
    if not include_inactive:
        qs = qs.filter(active=True)
    return [serialize_subscription(s) for s in qs]


@register_operation(
    name="alert_subscription_create",
    summary=(
        "Subscribe a labs user (yourself) or any email address — an upstream donor, a partner — to "
        "alerts. check_kinds are checks_list kinds: only checks that are NEW since the last alert are "
        "sent, so a check that stays true is one email. movement_kinds send each matching ledger "
        "movement recorded from now on. cadence is immediate or daily_digest. Emails state the fact "
        "and link to the record; they never advise."
    ),
    input_schema=obj({"data": _SUBSCRIPTION_DATA}, required=("data",)),
    is_write=True,
)
def alert_subscription_create(access, data):
    from connect_labs.supply_chain.alerts.service import ledger_head

    program_id = access._require_program()
    sub = AlertSubscription(program_id=program_id, created_by=_caller_user(access))
    _apply(access, sub, data, creating=True)
    # Start reading the ledger from now: subscribing is not a request for the
    # programme's history.
    sub.movements_seen_through = ledger_head(program_id)
    sub.save()
    return serialize_subscription(_require_subscription(access, sub.pk))


@register_operation(
    name="alert_subscription_update",
    summary=(
        "Change an alert subscription — what it watches, its recipient, its cadence — or pause it "
        "with active=false. Fields you leave out are unchanged."
    ),
    input_schema=obj({"subscription_id": ID, "data": _SUBSCRIPTION_DATA}, required=("subscription_id", "data")),
    is_write=True,
)
def alert_subscription_update(access, subscription_id, data):
    sub = _require_subscription(access, subscription_id)
    _apply(access, sub, data, creating=False)
    sub.save()
    return serialize_subscription(_require_subscription(access, sub.pk))


@register_operation(
    name="alert_subscription_delete",
    summary="Stop and delete an alert subscription, with its record of what it has sent.",
    input_schema=obj({"subscription_id": ID}, required=("subscription_id",)),
    is_write=True,
)
def alert_subscription_delete(access, subscription_id):
    sub = _require_subscription(access, subscription_id)
    sub.delete()
    return {"deleted": subscription_id}


@register_operation(
    name="alert_log_list",
    summary=(
        "What the alerts found and whether it went out: each notice's check or movement, its facts, "
        "the link it carried, and its delivery — queued, pending (a digest not yet due), "
        "email_disabled or no_address. Newest first."
    ),
    input_schema=obj(
        {
            "subscription_id": ID,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        }
    ),
)
def alert_log_list(access, subscription_id=None, limit=100):
    qs = AlertNotice.objects.filter(program_id=access._require_program())
    if subscription_id is not None:
        qs = qs.filter(subscription_id=subscription_id)
    return [serialize_notice(n) for n in qs[:limit]]
