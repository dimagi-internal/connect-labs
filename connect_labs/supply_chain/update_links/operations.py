"""Update-link operations: issue, list, revoke.

Registered into the one registry in operations.py. These are programme-member
operations -- the data access they receive has already authorised the
programme -- and they manage the link. What the SUPPLIER does through a link is
not an operation of its own: it is the ordinary operations (`contract_update`,
`shipment_record`, `receipt_record`, `stock_count_record`, `movement_record`,
`document_attach`), called by `update_links/service.py`.
"""

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import AwardApproval, Contract, SupplyPoint
from connect_labs.supply_chain.operations import ID, _data_with, obj, register_operation
from connect_labs.supply_chain.update_links import service, tokens
from connect_labs.supply_chain.update_links.models import (
    COVERAGE_LISTED,
    COVERAGE_ORGANISATION,
    UpdateLink,
)

DEFAULT_EXPIRY_DAYS = 30
MAX_EXPIRY_DAYS = 90

_ISSUE_DATA = _data_with(
    ("org_id",),
    org_id=ID,
    # "listed" (the default): exactly the rows named below, and nothing that
    # appears later. "organisation": everything involving the organisation in
    # this programme, resolved afresh at every request -- orders it supplies,
    # buys or receives at a store it runs; stores it runs; approvals asked of
    # it -- including orders created after the link was issued.
    coverage={"enum": [COVERAGE_LISTED, COVERAGE_ORGANISATION]},
    contract_ids={"type": "array", "items": ID, "uniqueItems": True},
    supply_point_ids={"type": "array", "items": ID, "uniqueItems": True},
    # Approvals asked of this organisation, which it may then answer itself.
    approval_ids={"type": "array", "items": ID, "uniqueItems": True},
    # Bounded, because a link nobody remembers issuing is the one that gets
    # misused. Ninety days covers a procurement cycle; a longer relationship
    # gets a fresh link, which is also a moment to check the scope still fits.
    expires_in_days={"type": "integer", "minimum": 1, "maximum": MAX_EXPIRY_DAYS},
    label={"type": "string"},
    # The PersonalID user the link is being sent to. Not authentication: it is
    # who we SAY the link is for, so what is recorded through it is attributed
    # to them. A listed link also covers that user's own stock point(s).
    connect_username={"type": "string", "maxLength": 150},
    # An Open Chat Studio chatbot (public id) to send the link through, to that
    # user's PersonalID chat, as the person issuing it. The link is created
    # either way; if the send fails the response says why and carries the link
    # to pass on by hand.
    send_via_bot={"type": "string", "maxLength": 64},
    # Make the page prove, before it shows anything, that its user can read the
    # recipient's PersonalID chat (a code sent there). Needs connect_username.
    # Without it the link is open to whoever holds it.
    require_verification={"type": "boolean"},
)


def public_url(raw_token) -> str:
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}{reverse('supply_chain:update_link_public', args=[raw_token])}"


def serialize_link(link) -> dict:
    """The link, and what it covers NOW.

    For a listed link that is the rows it names. For one that follows its
    organisation it is the live scope at this moment -- the same rows the page
    behind the link would offer -- so the list shows what it reaches today.
    """
    if link.follows_org:
        scope = service.scope_for(link)
        contracts = list(scope.contracts.order_by("-created_at"))
        points = list(scope.supply_points.order_by("name"))
        approvals = list(scope.approvals)
    else:
        contracts = list(link.contracts.all())
        points = list(link.supply_points.all())
        approvals = list(link.approvals.select_related("award__supplier", "award__commodity", "award__quote__item"))
    return {
        "id": link.pk,
        "program_id": link.program_id,
        "org_id": link.org_id,
        "org_name": link.org.name,
        "label": link.label,
        "connect_username": link.connect_username,
        "require_verification": link.require_verification,
        "sent_at": link.sent_at.isoformat() if link.sent_at else None,
        "sent_via_bot": link.sent_via_bot,
        "send_error": link.send_error,
        "coverage": link.coverage,
        "follows_org": link.follows_org,
        "contract_ids": [c.pk for c in contracts],
        "contracts": [{"id": c.pk, "reference": c.reference, "status": c.status} for c in contracts],
        "supply_point_ids": [p.pk for p in points],
        "supply_points": [{"id": p.pk, "name": p.name} for p in points],
        "approval_ids": [a.pk for a in approvals],
        "approvals": [
            {
                "id": a.pk,
                "award_id": a.award_id,
                "role": a.role,
                "status": a.status,
                "supplier": a.award.supplier.name,
                # What the approver is asked about: the trade item awarded, or
                # the product when the award named none.
                "product": a.award.quote.item.name if a.award.quote.item_id else a.award.commodity.name,
            }
            for a in approvals
        ],
        "token_hint": link.token_hint,
        "state": link.state,
        "expires_at": link.expires_at.isoformat(),
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "issued_by_id": link.issued_by_id,
        "last_used_at": link.last_used_at.isoformat() if link.last_used_at else None,
        "submission_count": link.submissions.count(),
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


def _links(access):
    return (
        UpdateLink.objects.filter(program_id=access._require_program())
        .select_related("org")
        .prefetch_related("contracts", "supply_points", "approvals")
    )


def _caller_user(access):
    user = getattr(access, "user", None)
    return user if getattr(user, "pk", None) else None


@register_operation(
    name="update_link_issue",
    summary=(
        "Issue an update link: a signed, expiring, revocable URL that lets ONE organisation with "
        "no labs login confirm orders and payments, record dispatches and receipts, and record stock "
        "counts and releases — for exactly the contracts and supply points named here, and nothing "
        "else; or, with coverage='organisation', for everything involving that organisation in this "
        "programme, now and later (orders it supplies, buys or receives at a store it runs; stores it "
        "runs; approvals asked of it), resolved at every request. Actions stay role-appropriate: only "
        "the supplier on an order can confirm or dispatch it; only a buyer or receiving store can record "
        "a receipt. Every write behind it goes through the ordinary operations, attributed to that "
        "organisation. The raw token is in this response only; it is never stored or shown again."
    ),
    input_schema=obj({"data": _ISSUE_DATA}, required=("data",)),
    is_write=True,
)
def update_link_issue(access, data):
    program_id = access._require_program()
    org = LabsOrg.objects.filter(pk=data["org_id"]).first()
    if org is None:
        raise ValueError(f"organisation {data['org_id']} does not exist")
    from connect_labs.supply_chain.runner import running_org

    runner = running_org(program_id)
    if runner is not None and runner.pk == org.pk:
        # Its people sign in and act as the program; a login-free link in their
        # name would let whoever holds it write as the program too (#2458).
        raise ValueError(f"{org.name} runs this program; its own people sign in rather than use an update link")

    contract_ids = list(data.get("contract_ids") or [])
    point_ids = list(data.get("supply_point_ids") or [])
    approval_ids = list(data.get("approval_ids") or [])
    coverage = data.get("coverage") or COVERAGE_LISTED
    username = (data.get("connect_username") or "").strip()
    if data.get("require_verification") and not username:
        raise ValueError("to require verification, name the PersonalID user it is for (connect_username)")
    if data.get("require_verification") and not _verify_bot(data):
        raise ValueError(
            "to require verification, choose the chatbot the code is sent through (send_via_bot), "
            "or set SUPPLY_LINK_OCS_BOT"
        )
    if (data.get("send_via_bot") or "").strip() and not username:
        raise ValueError("to send the link through PersonalID, name the PersonalID user it is for (connect_username)")
    if username and coverage == COVERAGE_LISTED:
        # A link sent to a worker covers that worker's own stock: the point
        # that holds what they carry. Looked up in this programme, so a name
        # cannot reach a point in another one.
        own = SupplyPoint.objects.filter(
            program_id=program_id, kind="user_held", connect_username=username, status="active"
        )
        point_ids = sorted(set(point_ids) | set(own.values_list("pk", flat=True)))
    if coverage == COVERAGE_ORGANISATION:
        # One or the other, never both: a link that follows its organisation
        # AND names rows would leave nobody sure which of the two it obeys.
        if contract_ids or point_ids or approval_ids:
            raise ValueError(
                "a link that covers everything involving the organisation names no orders, supply points or "
                "approvals of its own — leave them out, or issue a listed link instead"
            )
        return _create(access, program_id, org, data, coverage, [], [], [])
    if not contract_ids and not point_ids and not approval_ids:
        raise ValueError("a link has to cover at least one contract, supply point or approval")
    approvals = list(
        AwardApproval.objects.filter(award__tender__program_id=program_id, pk__in=approval_ids).select_related(
            "approver_org"
        )
    )
    missing = sorted(set(approval_ids) - {a.pk for a in approvals})
    if missing:
        raise ValueError(f"approval {', '.join(map(str, missing))} does not exist in this programme")
    for approval in approvals:
        # An approver answers for itself. A link that could answer an approval
        # asked of somebody else would let one party speak for another.
        if approval.approver_org_id != org.pk:
            raise ValueError(
                f"approval {approval.pk} was asked of {approval.approver_org.name}, not {org.name}: "
                "only the organisation asked can answer it"
            )

    contracts = list(Contract.objects.filter(program_id=program_id, pk__in=contract_ids))
    missing = sorted(set(contract_ids) - {c.pk for c in contracts})
    if missing:
        raise ValueError(f"contract {', '.join(map(str, missing))} does not exist in this programme")
    points = list(SupplyPoint.objects.filter(program_id=program_id, pk__in=point_ids))
    missing = sorted(set(point_ids) - {p.pk for p in points})
    if missing:
        raise ValueError(f"supply point {', '.join(map(str, missing))} does not exist in this programme")

    return _create(access, program_id, org, data, coverage, contracts, points, approvals)


def _verify_bot(data) -> str:
    """The chatbot codes go through for this link: the one chosen to send it, else the default."""
    if not data.get("require_verification"):
        return ""
    from connect_labs.supply_chain.update_links import delivery

    return (data.get("send_via_bot") or "").strip() or delivery.default_bot()


def _create(access, program_id, org, data, coverage, contracts, points, approvals):
    raw = tokens.new_token()
    link = UpdateLink.objects.create(
        program_id=program_id,
        org=org,
        coverage=coverage,
        label=data.get("label") or "",
        connect_username=(data.get("connect_username") or "").strip(),
        require_verification=bool(data.get("require_verification")),
        verify_bot=_verify_bot(data),
        token_hash=tokens.hash_token(raw),
        token_hint=tokens.hint(raw),
        expires_at=timezone.now() + timedelta(days=data.get("expires_in_days") or DEFAULT_EXPIRY_DAYS),
        issued_by=_caller_user(access),
    )
    link.contracts.set(contracts)
    link.supply_points.set(points)
    link.approvals.set(approvals)
    url = public_url(raw)
    delivery = _send(access, link, (data.get("send_via_bot") or "").strip(), url)
    result = {**serialize_link(link), "token": raw, "url": url, "field_url": f"{url}field/"}
    if delivery is not None:
        result["delivery"] = delivery
    return result


def _send(access, link, bot, url):
    """Send the freshly made link to its recipient's PersonalID chat. None when no send was asked for.

    Never raises: the link exists and the issuer holds it, so a refusal (no
    consent yet, OCS not connected) is reported beside it rather than undoing it.
    Synthetic programmes send nothing, as a workflow's coaching send does.
    """
    if not bot:
        return None
    from connect_labs.supply_chain import scopes as synthetic_scopes
    from connect_labs.supply_chain.update_links import delivery
    from connect_labs.supply_chain.update_links.messaging import link_message

    if synthetic_scopes.is_synthetic(link.program_id):
        return {"sent": False, "synthetic": True, "reason": "Synthetic programme: nothing is sent."}
    try:
        delivery.send_text(
            _caller_user(access),
            bot=bot,
            username=link.connect_username,
            text=link_message(url, org_name=link.org.name, label=link.label),
            session_data={"supply_update_link_id": str(link.pk), "program_id": str(link.program_id)},
        )
    except delivery.DeliveryError as e:
        link.send_error = str(e)[:255]
        link.save(update_fields=["send_error", "updated_at"])
        return {"sent": False, "reason": str(e)}
    link.sent_at = timezone.now()
    link.sent_via_bot = bot[:64]
    link.send_error = ""
    link.save(update_fields=["sent_at", "sent_via_bot", "send_error", "updated_at"])
    return {"sent": True}


@register_operation(
    name="update_link_list",
    summary=(
        "List this programme's update links — whose each is, what it covers, when it expires, "
        "whether it has been revoked, and how often it has been used. Never includes a token."
    ),
    input_schema=obj({"include_inactive": {"type": "boolean"}}),
)
def update_link_list(access, include_inactive=True):
    links = [serialize_link(link) for link in _links(access)]
    if not include_inactive:
        links = [link for link in links if link["state"] == "active"]
    return links


@register_operation(
    name="update_link_revoke",
    summary=(
        "Revoke an update link. It stops working at once and cannot be re-enabled — issue a new "
        "one instead. What was recorded through it stays, attributed to the organisation."
    ),
    input_schema=obj({"link_id": ID}, required=("link_id",)),
    is_write=True,
)
def update_link_revoke(access, link_id):
    link = _links(access).filter(pk=link_id).first()
    if link is None:
        raise ValueError(f"update link {link_id} not found in this programme")
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.revoked_by = _caller_user(access)
        link.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    return serialize_link(link)
