"""A partner's one-line status for its card on the Partners page: stock, field workers running out, open issues.

Read from the same grouped figures the Network and Issues pages use (`network.network_stock`,
`issues.open_issue_summary`), so the card cannot disagree with them. Nothing here runs the forecast: the
Partners page loads without it.
"""

from collections import defaultdict
from decimal import Decimal

from connect_labs.supply_chain.models import Custodian, Distribution, SupplyPoint
from connect_labs.supply_chain.stock.services import issues, network
from connect_labs.supply_chain.values import Quantity

# Field workers in these states are running out (network classification, not advice).
RUNNING_OUT = ("stockout", "below_min", "negative")
from connect_labs.supply_chain.stock.issue_forms import SECURITY_CHOICES  # noqa: E402

SECURITY_LABELS = dict(SECURITY_CHOICES)
_NOT_PLACES = ("user_held", "in_transit", "supplier_site", "customs")


def _stock_text(totals: dict) -> str:
    if not totals:
        return "no stock recorded"
    return " and ".join(f"{amount:,.0f} {unit}".strip() for unit, amount in sorted(totals.items()))


def partner_status(program_id) -> dict:
    """{org id: {"stock_text", "workers_running_out", "workers", "open_issues"}} for every partner with a place."""
    owner = issues.partner_by_point(program_id)
    totals: dict = defaultdict(lambda: defaultdict(Decimal))
    running_out: dict = defaultdict(int)
    workers: dict = defaultdict(int)
    kinds = {p["id"]: p["kind"] for p in SupplyPoint.objects.filter(program_id=program_id).values("id", "kind")}
    for row in network.network_stock(program_id):
        org = owner.get(row["supply_point_id"])
        if org is None:
            continue
        if kinds.get(row["supply_point_id"]) == "user_held":
            workers[org] += 1
            if row["status"] in RUNNING_OUT:
                running_out[org] += 1
        elif isinstance(row["on_hand"], Quantity):
            totals[org][row["on_hand"].unit or ""] += row["on_hand"].amount
    open_issues = {p["org_id"]: p["total_open"] for p in issues.open_issue_summary(program_id)["by_partner"]}
    orgs = set(totals) | set(workers) | set(open_issues)
    return {
        org: {
            "stock_text": _stock_text(totals.get(org, {})),
            "workers": workers.get(org, 0),
            "workers_running_out": running_out.get(org, 0),
            "open_issues": open_issues.get(org, 0),
        }
        for org in orgs
        if org is not None
    }


def partner_places(program_id) -> dict:
    """{org id: [{"id", "name", "kind", "custodian": {...} | None}]}: each place a partner runs, with its custodian."""
    owner = issues.partner_by_point(program_id)
    custodians = {}
    for c in Custodian.objects.filter(program_id=program_id, ended_on__isnull=True):
        custodians.setdefault(c.supply_point_id, c)
    out: dict = defaultdict(list)
    for point in SupplyPoint.objects.filter(program_id=program_id, status="active").order_by("name"):
        if point.kind in _NOT_PLACES:
            continue
        org = owner.get(point.pk)
        if org is not None:
            custodian = custodians.get(point.pk)
            out[org].append(
                {
                    "id": point.pk,
                    "name": point.name,
                    "kind": point.kind,
                    "custodian": custodian,
                    "kept": (
                        ", ".join(str(SECURITY_LABELS.get(m, m)) for m in (custodian.security_measures or []))
                        if custodian
                        else ""
                    ),
                }
            )
    return out


def handout_rhythm(program_id, org_id, profile) -> str:
    """How often the partner hands out stock and from where, from its stated profile, else from what it recorded."""
    every = profile.handout_every_days if profile else None
    place = profile.handout_from.name if profile and profile.handout_from_id else None
    if every is None:
        owner = issues.partner_by_point(program_id)
        mine = {pk for pk, org in owner.items() if org == org_id}
        runs = list(
            Distribution.objects.filter(program_id=program_id, supply_point_id__in=mine)
            .order_by("-distributed_on")
            .values_list("distributed_on", "supply_point__name")[:4]
        )
        days = sorted({d for d, _ in runs}, reverse=True)
        if len(days) >= 2:
            gaps = [(a - b).days for a, b in zip(days, days[1:])]
            every = round(sum(gaps) / len(gaps))
            place = place or runs[0][1]
            return f"About every {every} days from {place} (from the runs recorded)"
        if len(days) == 1:
            return f"One handout recorded, from {runs[0][1]}"
        return "Not stated yet"
    return f"Every {every} days" + (f" from {place}" if place else "")
