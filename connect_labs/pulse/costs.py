"""What a service really cost: per-service pay, plus the fixed costs around it.

Three sources, kept apart because they mean different things:

* **Per-service pay** -- worker pay and the organisation's per-service fee,
  accrued on every completed work (`PulseWork.usd_to_worker` / `usd_to_org`).
  This is what every Pulse figure has always counted.
* **Fixed costs** -- Connect's *custom* invoices (start-up and other costs),
  mirrored in `PulseInvoice`. They never touch a completed work, so until they
  were mirrored no figure could see them: $241k across 25 opportunities.
* **Costs outside Connect** -- `PulseCostEntry`, entered in labs, for money that
  really went to an organisation but never passed through Connect.

Service-delivery invoices are NOT added: they bill the per-service pay that has
already accrued, so adding them would count the same money twice. They are
used only to reconcile, and a service-delivery invoice well above what accrued
is raised as an issue for a person rather than silently counted.

Two views of the same money (`view=`):

* ``separate`` -- per-service pay, with fixed costs reported alongside it.
* ``spread`` -- fixed costs apportioned over the opportunity's approved units
  of work, so a figure for any slice of work (a week, a partner, a program)
  carries its share. An opportunity with fixed costs and no approved work has
  nothing to spread them over; its costs are reported as unallocated in both
  views rather than dropped.

Every invoice's USD figure is resolved here, with the basis recorded, because
Connect's own is sometimes missing or plainly in the wrong currency -- see
`resolve_invoice`. Those are corrected automatically; everything that needs a
person is listed by `cost_issues`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from django.db.models import Q, Sum
from django.utils import timezone

from connect_labs.pulse import groups

VIEW_SEPARATE = "separate"
VIEW_SPREAD = "spread"
VIEWS = (VIEW_SEPARATE, VIEW_SPREAD)

BASIS_CONNECT = "connect"
BASIS_OVERRIDE = "set by a person"
BASIS_EXCLUDED = "excluded by a person"
BASIS_MISSING_USD = "converted: Connect recorded no USD amount"
BASIS_USD_IS_LOCAL = "converted: Connect's USD amount is the local-currency amount"
BASIS_USD_OFF = "converted: Connect's USD amount is off by more than 5x"
BASIS_UNRESOLVABLE = "unresolvable: no USD amount and no exchange rate"

AUTO_BASES = {BASIS_MISSING_USD, BASIS_USD_IS_LOCAL, BASIS_USD_OFF}

ZERO = Decimal("0")


def parse_view(raw) -> str:
    return raw if raw in VIEWS else VIEW_SEPARATE


@dataclass
class ResolvedInvoice:
    opportunity_id: int
    invoice_number: str
    service_delivery: bool
    date: dt.date | None
    amount: Decimal | None
    amount_usd_connect: Decimal | None
    usd: Decimal | None
    basis: str
    disagreement: Decimal | None = None  # Connect USD / converted USD, when both exist


def _fallback_rates() -> dict[str, Decimal]:
    """Median of Connect's own applied rates per currency, for an opportunity
    whose own rate has not been measured."""
    from connect_labs.pulse.models import PulseOpportunity

    by_currency: dict[str, list[Decimal]] = {}
    for currency, rate in PulseOpportunity.objects.exclude(usd_rate=None).values_list("currency", "usd_rate"):
        if currency and rate and rate > 0:
            by_currency.setdefault(currency, []).append(rate)
    rates = {c: Decimal(median(v)) for c, v in by_currency.items()}
    rates["USD"] = Decimal("1")
    return rates


def resolve_invoice(inv, opp, review=None, rates=None) -> ResolvedInvoice:
    """One invoice's USD figure, and on what basis.

    Order: a person's decision; Connect's figure when it is plausible; else the
    local amount converted at the rate Connect itself applied to this
    opportunity's payments (the ratio of accrued USD to accrued local pay), or
    the median of that across the currency.

    Connect's figure is replaced automatically, never silently, in three cases
    measured on prod: it is missing (74 invoices, mostly 2025 naira); it equals
    the local amount on a non-USD opportunity (a naira figure typed into the
    USD field); or it disagrees with the converted amount by more than 5x.
    """
    currency = (getattr(opp, "currency", "") or "").upper()
    rate = getattr(opp, "usd_rate", None) or (rates or {}).get(currency)
    if currency == "USD":
        rate = Decimal("1")
    converted = (inv.amount * rate).quantize(Decimal("0.01")) if inv.amount is not None and rate else None
    base = dict(
        opportunity_id=inv.opportunity_id,
        invoice_number=inv.invoice_number,
        service_delivery=inv.service_delivery,
        date=inv.date,
        amount=inv.amount,
        amount_usd_connect=inv.amount_usd,
    )
    disagreement = (inv.amount_usd / converted) if (inv.amount_usd and converted) else None

    if review is not None and review.exclude:
        return ResolvedInvoice(**base, usd=ZERO, basis=BASIS_EXCLUDED, disagreement=disagreement)
    if review is not None and review.usd_override is not None:
        return ResolvedInvoice(**base, usd=review.usd_override, basis=BASIS_OVERRIDE, disagreement=disagreement)
    if inv.amount_usd is None:
        if converted is None:
            return ResolvedInvoice(**base, usd=None, basis=BASIS_UNRESOLVABLE)
        return ResolvedInvoice(**base, usd=converted, basis=BASIS_MISSING_USD)
    if currency and currency != "USD" and inv.amount is not None and inv.amount_usd == inv.amount and converted:
        if rate < Decimal("0.5"):
            return ResolvedInvoice(**base, usd=converted, basis=BASIS_USD_IS_LOCAL, disagreement=disagreement)
    if disagreement is not None and (disagreement > 5 or disagreement < Decimal("0.2")):
        return ResolvedInvoice(**base, usd=converted, basis=BASIS_USD_OFF, disagreement=disagreement)
    return ResolvedInvoice(**base, usd=inv.amount_usd, basis=BASIS_CONNECT, disagreement=disagreement)


@dataclass
class OppCosts:
    opportunity_id: int
    worker_usd: Decimal = ZERO
    org_usd: Decimal = ZERO
    approved_units: int = 0
    fixed_invoiced_usd: Decimal = ZERO
    fixed_entered_usd: Decimal = ZERO
    org_fee_entered_usd: Decimal = ZERO
    service_invoiced_usd: Decimal = ZERO
    invoices: list = field(default_factory=list)

    @property
    def per_service_usd(self) -> Decimal:
        """Pay accrued per service -- what every Pulse figure counted before."""
        return self.worker_usd + self.org_usd

    @property
    def fixed_usd(self) -> Decimal:
        """Everything paid for this opportunity that no completed work carries."""
        return self.fixed_invoiced_usd + self.fixed_entered_usd + self.org_fee_entered_usd

    @property
    def spreadable(self) -> bool:
        return self.approved_units > 0

    @property
    def fixed_per_unit(self) -> Decimal:
        return (self.fixed_usd / self.approved_units) if self.spreadable else ZERO

    @property
    def total_usd(self) -> Decimal:
        return self.per_service_usd + self.fixed_usd


def opportunity_costs(opp_ids=None) -> dict[int, OppCosts]:
    """Per-opportunity costs for every real opportunity (or the ones given)."""
    from connect_labs.pulse.models import (
        PulseCostEntry,
        PulseInvoice,
        PulseInvoiceReview,
        PulseOpportunity,
        PulseWork,
    )

    opps_qs = PulseOpportunity.objects.filter(is_test=False)
    if opp_ids is not None:
        opps_qs = opps_qs.filter(opportunity_id__in=list(opp_ids))
    opps = {o.opportunity_id: o for o in opps_qs}
    out = {oid: OppCosts(opportunity_id=oid) for oid in opps}

    for row in (
        PulseWork.objects.filter(opportunity_id__in=list(opps))
        .values("opportunity_id")
        .annotate(
            w=Sum("usd_to_worker"),
            o=Sum("usd_to_org"),
            units=Sum("approved_count", filter=Q(status="approved")),
        )
    ):
        c = out[row["opportunity_id"]]
        c.worker_usd, c.org_usd, c.approved_units = row["w"] or ZERO, row["o"] or ZERO, row["units"] or 0

    reviews = {(r.opportunity_id, r.invoice_number): r for r in PulseInvoiceReview.objects.all()}
    rates = _fallback_rates()
    for inv in PulseInvoice.objects.filter(opportunity_id__in=list(opps)):
        resolved = resolve_invoice(
            inv, opps[inv.opportunity_id], reviews.get((inv.opportunity_id, inv.invoice_number)), rates
        )
        c = out[inv.opportunity_id]
        c.invoices.append(resolved)
        if resolved.usd is None:
            continue
        if inv.service_delivery:
            c.service_invoiced_usd += resolved.usd
        else:
            c.fixed_invoiced_usd += resolved.usd

    for entry in PulseCostEntry.objects.filter(opportunity_id__in=list(opps)):
        c = out[entry.opportunity_id]
        if entry.kind == PulseCostEntry.KIND_ORG_FEE:
            c.org_fee_entered_usd += entry.usd
        else:
            c.fixed_entered_usd += entry.usd

    # An engagement's own cost -- one fee for work Connect recorded as many
    # opportunities. Apportioned over its cohorts by approved units, so the
    # cost per unit is the same wherever a unit sits and every figure that
    # reads `fixed_rates()` keeps working on opportunity ids. A cohort with no
    # approved work carries none of it; an engagement with no approved work
    # anywhere lands wholly on its lowest-numbered cohort, which keeps it in
    # `unallocated()` rather than dropping it from every figure.
    for entry in PulseCostEntry.objects.exclude(group=None).select_related("group"):
        ids = [oid for oid in groups.members(entry.group.slug) if oid in out]
        if not ids:
            continue
        units = {oid: out[oid].approved_units for oid in ids}
        total_units = sum(units.values())
        fallback = min(ids)
        for oid in ids:
            if total_units:
                share = entry.usd * Decimal(units[oid]) / Decimal(total_units)
            else:
                share = entry.usd if oid == fallback else ZERO
            if not share:
                continue
            if entry.kind == PulseCostEntry.KIND_ORG_FEE:
                out[oid].org_fee_entered_usd += share
            else:
                out[oid].fixed_entered_usd += share
    return out


def spread_share(costs: dict[int, OppCosts], units_by_opp: dict[int, int]) -> Decimal:
    """Fixed costs carried by a slice of work: each opportunity's fixed costs
    in proportion to the slice's share of its approved units."""
    total = ZERO
    for oid, units in units_by_opp.items():
        c = costs.get(oid)
        if c and c.spreadable and units:
            total += c.fixed_per_unit * units
    return total


def unallocated(costs: dict[int, OppCosts]) -> Decimal:
    """Fixed costs on opportunities with no approved work to spread them over."""
    return sum((c.fixed_usd for c in costs.values() if not c.spreadable), ZERO)


# ---------------------------------------------------------------------------
# Fixed costs for any slice of work -- what every money figure reads
# ---------------------------------------------------------------------------

_RATE_CACHE_KEY = "pulse:costs:fixed_per_unit:v1"
_RATE_CACHE_SECONDS = 600


def fixed_rates() -> dict:
    """{opportunity_id: fixed USD per approved unit, unallocated: USD}.

    Only the ~25 opportunities that carry fixed costs are ever read, and the
    answer is cached for ten minutes: every money figure on every screen reads
    this, and invoices move on the slow tier's cadence, not per request.
    """
    from django.core.cache import cache

    from connect_labs.pulse.models import PulseCostEntry, PulseInvoice

    hit = cache.get(_RATE_CACHE_KEY)
    if hit is not None:
        return hit
    with_fixed = set(PulseInvoice.objects.filter(service_delivery=False).values_list("opportunity_id", flat=True))
    with_fixed |= {oid for oid in PulseCostEntry.objects.values_list("opportunity_id", flat=True) if oid is not None}
    # An engagement's entry is carried by its cohorts, so they are the
    # opportunities that have a fixed cost to spread.
    for slug in PulseCostEntry.objects.exclude(group=None).values_list("group__slug", flat=True):
        with_fixed |= set(groups.members(slug))
    by = opportunity_costs(with_fixed) if with_fixed else {}
    out = {
        "per_unit": {oid: c.fixed_per_unit for oid, c in by.items() if c.spreadable and c.fixed_usd},
        "unallocated": unallocated(by),
    }
    cache.set(_RATE_CACHE_KEY, out, _RATE_CACHE_SECONDS)
    return out


def invalidate() -> None:
    from django.core.cache import cache

    cache.delete(_RATE_CACHE_KEY)


def fixed_for(works, key: str | None = None):
    """The fixed costs a slice of work carries, by its share of approved units.

    ``works`` is any `PulseWork` queryset already narrowed to the slice (a
    program, a partner, a week). With ``key`` the answer is a dict keyed by
    that column (``service_slug``, ``country``, ``org_slug``,
    ``opportunity_id``); without, a single total.

    The same share is used in both views. What differs is only whether a
    figure presents it alongside per-service pay (``separate``) or inside it
    (``spread``) -- see `apply_view`.
    """
    per_unit = fixed_rates()["per_unit"]
    if not per_unit:
        return {} if key else 0.0
    qs = works.filter(status="approved", opportunity_id__in=list(per_unit))
    if key is None:
        rows = qs.values("opportunity_id").annotate(u=Sum("approved_count"))
        return float(sum(per_unit[r["opportunity_id"]] * (r["u"] or 0) for r in rows))
    out: dict = {}
    fields = [key] if key == "opportunity_id" else [key, "opportunity_id"]
    for r in qs.values(*fields).annotate(u=Sum("approved_count")):
        out[r[key]] = out.get(r[key], 0.0) + float(per_unit[r["opportunity_id"]] * (r["u"] or 0))
    return out


def apply_view(row: dict, fixed: float, view: str, *, total="usd_total", rate=None, units=None) -> dict:
    """Stamp one money object with its fixed costs, in the chosen view.

    Always adds ``fixed_usd`` (and ``per_service_usd``, the figure before any
    fixed cost), so either view can say what the other would. In ``spread``
    the total -- and the per-unit rate, when named -- include the fixed share.
    """
    row["fixed_usd"] = round(fixed, 2)
    row["per_service_usd"] = row.get(total, 0) or 0
    if view == VIEW_SPREAD and fixed:
        row[total] = (row.get(total) or 0) + fixed
        if rate and units:
            row[rate] = row[total] / units
    return row


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

WHO_AUTO = "adjusted automatically"
WHO_PERSON = "needs a person"

ISSUE_TYPES = {
    "missing_usd": (WHO_AUTO, "Invoice has no USD amount — converted from the local amount"),
    "usd_is_local": (WHO_AUTO, "Invoice's USD amount is the local-currency amount — converted"),
    "usd_off": (WHO_AUTO, "Invoice's USD amount is off by more than 5x — converted"),
    "no_org_pay": (
        WHO_PERSON,
        "Delivery with no organisation pay anywhere in Connect — was the organisation paid outside it?",
    ),
    "service_invoices_exceed_accrual": (
        WHO_PERSON,
        "Service-delivery invoices well above the per-service pay that accrued — what else was billed?",
    ),
    "fixed_cost_without_work": (
        WHO_PERSON,
        "Startup and supplies invoiced on an opportunity with no approved work — which work do they belong to?",
    ),
    "accrued_not_invoiced": (
        WHO_PERSON,
        "Finished, with per-service pay accrued and no service-delivery invoice in Connect",
    ),
    "usd_disagrees": (
        WHO_PERSON,
        "Invoice's USD amount disagrees with the local amount at Connect's rate by 1.5x or more",
    ),
    "unresolvable": (WHO_PERSON, "Invoice with no USD amount and no exchange rate to convert it"),
    "possible_duplicate": (
        WHO_PERSON,
        "Two invoices with the same number and amount — the same bill entered twice?",
    ),
    "invoices_unread": (
        WHO_PERSON,
        "Connect will not show labs this opportunity's invoices — the Pulse poller account needs access "
        "to the workspace",
    ),
}


@dataclass
class _Unit:
    """What a cost question is asked about: an engagement, not a Connect row.

    Usually the two are the same. Where they are not -- an engagement Connect
    recorded as 37 opportunities -- asking per row gave 37 copies of one
    question, and each cohort's share of the work fell under the thresholds
    that decide whether a question is worth asking at all.
    """

    key: object
    name: str
    org_slug: str
    service_slug: str
    cohorts: int = 1
    worker_usd: Decimal = ZERO
    org_usd: Decimal = ZERO
    approved_units: int = 0
    fixed_usd: Decimal = ZERO
    service_invoiced_usd: Decimal = ZERO
    per_service_usd: Decimal = ZERO
    spreadable: bool = False
    unread: int = 0
    ended: bool = True
    # (the opportunity the invoice belongs to, the resolved invoice)
    invoice_pairs: list = field(default_factory=list)


def _units(opps: dict, costs: dict, today: dt.date) -> dict:
    """Merge per-opportunity costs into the engagements they belong to."""
    units: dict = {}
    for oid, c in costs.items():
        opp = opps[oid]
        key = groups.key_for(oid)
        unit = units.get(key)
        if unit is None:
            unit = _Unit(
                key=key,
                name=(groups.name_of(key) if isinstance(key, str) else "") or opp.name,
                org_slug=opp.org_slug,
                service_slug=opp.service_slug,
                cohorts=0,
            )
            units[key] = unit
        unit.cohorts += 1
        unit.worker_usd += c.worker_usd
        unit.org_usd += c.org_usd
        unit.approved_units += c.approved_units
        unit.fixed_usd += c.fixed_usd
        unit.service_invoiced_usd += c.service_invoiced_usd
        unit.per_service_usd += c.per_service_usd
        unit.spreadable = unit.spreadable or c.spreadable
        unit.unread += 1 if opp.invoices_synced_at is None else 0
        unit.ended = unit.ended and (not opp.is_active or (opp.end_date is not None and opp.end_date < today))
        unit.invoice_pairs.extend((opp, inv) for inv in c.invoices)
    return units


def cost_issues(today: dt.date | None = None) -> list[dict]:
    """Everything about cost data worth fixing, one row per finding.

    ``who`` separates what labs already corrected (currency) from what needs a
    person's answer. Test and demo opportunities are left out, as they are from
    every figure.

    A finding is filed against an ENGAGEMENT. For all but a handful that is one
    Connect opportunity; for the interview cohorts it is one question about 37
    of them, which is the only way it is answerable -- the fee was agreed once.
    """
    from connect_labs.pulse.models import PulseCostEntry, PulseOpportunity

    today = today or timezone.now().date()
    opps = {o.opportunity_id: o for o in PulseOpportunity.objects.filter(is_test=False)}
    costs = opportunity_costs(opps)
    entered = {groups.key_for(oid) for oid in PulseCostEntry.objects.values_list("opportunity_id", flat=True) if oid}
    entered |= set(PulseCostEntry.objects.exclude(group=None).values_list("group__slug", flat=True))
    out: list[dict] = []

    def add(kind, unit, amount, detail, invoice=""):
        who, title = ISSUE_TYPES[kind]
        out.append(
            dict(
                kind=kind,
                who=who,
                title=title,
                opportunity_id=unit.key,
                opportunity=unit.name,
                org_slug=unit.org_slug,
                service=unit.service_slug,
                # How many Connect opportunities this one question covers.
                cohorts=unit.cohorts,
                invoice=invoice,
                amount_usd=amount,
                detail=detail,
            )
        )

    for unit in _units(opps, costs, today).values():
        for opp, inv in unit.invoice_pairs:
            local = f"{inv.amount:,.2f} {opp.currency}" if inv.amount is not None else "no amount"
            if inv.basis == BASIS_MISSING_USD:
                add("missing_usd", unit, inv.usd, f"{local} → ${inv.usd:,.2f}", inv.invoice_number)
            elif inv.basis == BASIS_USD_IS_LOCAL:
                add(
                    "usd_is_local",
                    unit,
                    inv.usd,
                    f"Connect says ${inv.amount_usd_connect:,.2f} for {local}; converted ${inv.usd:,.2f}",
                    inv.invoice_number,
                )
            elif inv.basis == BASIS_USD_OFF:
                add(
                    "usd_off",
                    unit,
                    inv.usd,
                    f"Connect says ${inv.amount_usd_connect:,.2f} for {local}; converted ${inv.usd:,.2f}",
                    inv.invoice_number,
                )
            elif inv.basis == BASIS_UNRESOLVABLE:
                add("unresolvable", unit, None, local, inv.invoice_number)
            elif inv.basis == BASIS_CONNECT and inv.disagreement is not None:
                if inv.disagreement > Decimal("1.5") or inv.disagreement < Decimal("0.67"):
                    add(
                        "usd_disagrees",
                        unit,
                        inv.usd,
                        f"Connect says ${inv.amount_usd_connect:,.2f} for {local} "
                        f"({inv.disagreement:.1f}x the converted amount)",
                        inv.invoice_number,
                    )

        # The same bill entered twice: numbers equal once punctuation and case
        # are folded ("CWD/NG/25/007" and "CWD/NG/25/007."), and the same
        # amount. Counted in every figure until a person excludes one, so it is
        # raised rather than dropped -- two real invoices can share an amount.
        seen: dict = {}
        for opp, inv in unit.invoice_pairs:
            key = (re.sub(r"[^a-z0-9]", "", inv.invoice_number.lower()), inv.amount, inv.service_delivery)
            if key in seen and inv.basis != BASIS_EXCLUDED and seen[key].basis != BASIS_EXCLUDED:
                add(
                    "possible_duplicate",
                    unit,
                    inv.usd,
                    f"{seen[key].invoice_number!r} and {inv.invoice_number!r}, both "
                    f"{inv.amount:,.2f} {opp.currency}",
                    inv.invoice_number,
                )
            seen.setdefault(key, inv)

        if unit.unread == unit.cohorts:
            add(
                "invoices_unread",
                unit,
                None,
                "Not yet read, or Connect refused the read",
            )
            continue

        has_invoices = bool(unit.invoice_pairs)
        if unit.worker_usd > 200 and unit.org_usd == 0 and not has_invoices and unit.key not in entered:
            add(
                "no_org_pay",
                unit,
                unit.worker_usd,
                f"${unit.worker_usd:,.0f} paid to workers over {unit.approved_units:,} approved units; "
                "no org pay per service, no invoices, nothing entered in labs",
            )
        if unit.service_invoiced_usd > unit.per_service_usd * Decimal("1.3") and (
            unit.service_invoiced_usd - unit.per_service_usd > 2000
        ):
            add(
                "service_invoices_exceed_accrual",
                unit,
                unit.service_invoiced_usd - unit.per_service_usd,
                f"invoiced ${unit.service_invoiced_usd:,.0f} for service delivery; "
                f"${unit.per_service_usd:,.0f} accrued "
                f"(worker ${unit.worker_usd:,.0f} + org ${unit.org_usd:,.0f})",
            )
        if unit.fixed_usd > 0 and not unit.spreadable:
            add(
                "fixed_cost_without_work",
                unit,
                unit.fixed_usd,
                f"${unit.fixed_usd:,.0f} of startup and supplies and no approved work to spread them over",
            )
        if unit.ended and unit.per_service_usd > 1000 and unit.service_invoiced_usd == 0:
            add(
                "accrued_not_invoiced",
                unit,
                unit.per_service_usd,
                f"${unit.per_service_usd:,.0f} accrued; no service-delivery invoice",
            )

    order = {k: i for i, k in enumerate(ISSUE_TYPES)}
    out.sort(key=lambda r: (r["who"] != WHO_PERSON, order[r["kind"]], -(r["amount_usd"] or 0)))
    return out
