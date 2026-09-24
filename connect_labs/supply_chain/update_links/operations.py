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
        approvals = list(link.approvals.select_related("award__supplier"))
    return {
        "id": link.pk,
        "program_id": link.program_id,
        "org_id": link.org_id,
        "org_name": link.org.name,
        "label": link.label,
        "coverage": link.coverage,
        "follows_org": link.follows_org,
        "contract_ids": [c.pk for c in contracts],
        "contracts": [{"id": c.pk, "reference": c.reference, "status": c.status} for c in contracts],
        "supply_point_ids": [p.pk for p in points],
        "supply_points": [{"id": p.pk, "name": p.name} for p in points],
        "approval_ids": [a.pk for a in approvals],
        "approvals": [
            {"id": a.pk, "award_id": a.award_id, "role": a.role, "status": a.status, "supplier": a.award.supplier.name}
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
        "Issue a supplier update link: a signed, expiring, revocable URL that lets ONE organisation with "
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

    contract_ids = list(data.get("contract_ids") or [])
    point_ids = list(data.get("supply_point_ids") or [])
    approval_ids = list(data.get("approval_ids") or [])
    coverage = data.get("coverage") or COVERAGE_LISTED
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
        AwardApproval.objects.filter(award__round__program_id=program_id, pk__in=approval_ids).select_related(
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


def _create(access, program_id, org, data, coverage, contracts, points, approvals):
    raw = tokens.new_token()
    link = UpdateLink.objects.create(
        program_id=program_id,
        org=org,
        coverage=coverage,
        label=data.get("label") or "",
        token_hash=tokens.hash_token(raw),
        token_hint=tokens.hint(raw),
        expires_at=timezone.now() + timedelta(days=data.get("expires_in_days") or DEFAULT_EXPIRY_DAYS),
        issued_by=_caller_user(access),
    )
    link.contracts.set(contracts)
    link.supply_points.set(points)
    link.approvals.set(approvals)
    return {**serialize_link(link), "token": raw, "url": public_url(raw)}


@register_operation(
    name="update_link_list",
    summary=(
        "List this programme's supplier update links — whose each is, what it covers, when it expires, "
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
        "Revoke a supplier update link. It stops working at once and cannot be re-enabled — issue a new "
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
