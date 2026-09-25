"""The supplier marketplace: what a supplier may see, and the bids it may make.

A supplier is not a member of the program whose round it bids on, and must
not become one -- so nothing here goes through a program-scoped
`SupplyDataAccess` on the supplier's behalf. Reads are queries over the
models that return only what the market shows: open rounds that are public or
that the organisation was invited to, and the organisation's OWN quotes.
Bids are sealed; no function here returns another supplier's quote.

Writes go through the ordinary operations (`quote_record`, `quote_correct`,
`quote_void`, `supplier_create`) under the `SYSTEM` caller -- the same
schemas that validate the program team's entries validate the supplier's --
and only after this module has re-read, from the database, that the round is
open and visible to the organisation and that the quote is the
organisation's. The form offering a choice is not the check; this is, the same
rule `update_links/service.py` follows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q

from connect_labs.audit_trail.models import Action
from connect_labs.audit_trail.service import record as audit_record
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import records
from connect_labs.supply_chain import scopes as synthetic_scopes
from connect_labs.supply_chain.models import (
    Award,
    Commodity,
    Outreach,
    Quote,
    Round,
    Supplier,
    SupplierOffering,
    SupplierProfile,
    scope_key,
)
from connect_labs.supply_chain.operations import call_operation


class NotAvailable(Exception):
    """The round or quote is not one this organisation may act on.

    One exception for "does not exist", "is private and you were not invited"
    and "is not yours", so a caller cannot tell them apart.
    """


class NeedsProfile(Exception):
    """The organisation has no supplier profile yet, so it cannot bid."""


@dataclass
class Line:
    commodity_slug: str
    commodity: Commodity | None
    quantity: Decimal | None
    quantity_unit: str
    matches: bool = False

    @property
    def name(self) -> str:
        return self.commodity.name if self.commodity else self.commodity_slug

    @property
    def requirements(self) -> list[str]:
        """The product's specification, in words: "Minimum graduation at most 20 g"."""
        from connect_labs.supply_chain.procurement.services.compliance import OPERATOR_WORDS, requirement_label

        out = []
        for req in (self.commodity.spec_requirements if self.commodity else None) or []:
            unit = req.get("unit") or ""
            words = OPERATOR_WORDS.get(req.get("operator"), req.get("operator") or "")
            out.append(f"{requirement_label(req.get('field'), unit)} {words} {req.get('value')} {unit}".strip())
        return out


@dataclass
class ListedRound:
    round: Round
    lines: list[Line]
    invited: bool
    own_quotes: list[Quote] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return any(line.matches for line in self.lines)


# ---- visibility ---------------------------------------------------------


def _invited_round_ids(orgs) -> set[int]:
    if not orgs:
        return set()
    return set(Outreach.objects.filter(supplier__org__in=orgs).values_list("round_id", flat=True))


def _visible(orgs) -> Q:
    return Q(status="open") & (Q(visibility="public") | Q(pk__in=_invited_round_ids(orgs)))


def listed_rounds(orgs=()) -> list[ListedRound]:
    """Every open round this visitor may see, those matching what they offer first."""
    invited = _invited_round_ids(orgs)
    rounds = list(Round.objects.filter(_visible(orgs)).order_by("response_deadline", "-opened_at", "-pk"))
    offerings = _offerings(orgs)
    listed = [ListedRound(r, _lines(r, offerings), r.pk in invited) for r in rounds]
    listed.sort(key=lambda item: not item.matches)
    return listed


def visible_round(round_id, orgs=()) -> ListedRound:
    """One round, if this visitor may see it. `NotAvailable` otherwise."""
    found = Round.objects.filter(_visible(orgs), pk=round_id).first()
    if found is None:
        raise NotAvailable("no such round")
    listed = ListedRound(found, _lines(found, _offerings(orgs)), found.pk in _invited_round_ids(orgs))
    listed.own_quotes = [_worded(q) for q in _own_quotes(orgs).filter(round=found)]
    return listed


def _lines(round_: Round, offerings) -> list[Line]:
    slugs = [line.get("commodity_slug") for line in round_.lines or [] if line.get("commodity_slug")]
    commodities = {c.slug: c for c in Commodity.objects.filter(scope_key=scope_key(round_.program_id), slug__in=slugs)}
    lines = []
    for raw in round_.lines or []:
        slug = raw.get("commodity_slug")
        if not slug:
            continue
        commodity = commodities.get(slug)
        lines.append(
            Line(
                commodity_slug=slug,
                commodity=commodity,
                quantity=_decimal(raw.get("quantity")),
                quantity_unit=raw.get("quantity_unit") or "",
                matches=commodity is not None and any(offering_matches(o, commodity) for o in offerings),
            )
        )
    return lines


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


# ---- what a supplier offers ---------------------------------------------


def _offerings(orgs) -> list[SupplierOffering]:
    if not orgs:
        return []
    return list(SupplierOffering.objects.filter(profile__org__in=orgs))


def offering_matches(offering: SupplierOffering, commodity: Commodity, items=None) -> bool:
    """Whether a supplier's stated offering is this program's product.

    Exactly, on a UNICEF material number or a GTIN; otherwise by category,
    which is the weaker match and is labelled as such where it is shown.
    """
    return offering_match_kind(offering, commodity, items) is not None


def offering_match_kind(offering: SupplierOffering, commodity: Commodity, items=None) -> str | None:
    number = (offering.unicef_material_number or "").strip()
    if number and number == (commodity.unicef_material_number or "").strip():
        return "exact"
    gtin = (offering.gtin or "").strip()
    if gtin:
        for item in items if items is not None else commodity.items.all():
            if gtin in (item.gtin_base, item.gtin_pack, item.gtin_case):
                return "exact"
    if offering.category and offering.category == commodity.category:
        return "category"
    return None


# ---- a supplier's own quotes --------------------------------------------


def _own_quotes(orgs):
    return (
        Quote.objects.filter(supplier__org__in=orgs)
        .select_related("round", "commodity", "supplier__org", "item")
        .order_by("-created_at")
    )


@dataclass
class OwnQuote:
    quote: Quote
    standing: str
    needs: list[str]


def own_quotes(orgs) -> list[OwnQuote]:
    """The organisation's quotes, each with where it stands and what the buyer still needs.

    "What the buyer still needs" is `questions.missing_facts` filtered to the
    supplier's audience -- the same questions the comparison asks, so the
    supplier can answer them by revising its bid instead of being emailed.
    Shown only for a live quote on an open round: a closed round is no longer
    listening.
    """
    from connect_labs.supply_chain.procurement.services.questions import missing_facts

    out = []
    for quote in _own_quotes(orgs):
        live = not quote.voided and quote.superseded_by_id is None
        if not live:
            continue
        needs = []
        if quote.round.status == "open":
            needs = [
                fact.question
                for fact in missing_facts(quote, quote.commodity, quote.round, item=quote.item)
                if fact.audience == "supplier"
            ]
        out.append(OwnQuote(quote=_worded(quote), standing=_standing(quote), needs=needs))
    return out


def _standing(quote: Quote) -> str:
    round_ = quote.round
    if round_.status == "open":
        return "open"
    if round_.status == "awarded" or Award.objects.filter(round=round_, commodity=quote.commodity).exists():
        won = Award.objects.filter(round=round_, commodity=quote.commodity, supplier=quote.supplier).exists()
        return "awarded" if won else "not_awarded"
    return "closed"


def own_quote(quote_id, orgs) -> Quote:
    quote = _own_quotes(orgs).filter(pk=quote_id).first()
    if quote is None:
        raise NotAvailable("no such quote")
    return quote


# ---- bidding --------------------------------------------------------------


def _access(program_id):
    """A write access for the round's program, under SYSTEM.

    SYSTEM because the supplier is not a member of the program and must not
    become one: the authority here is the checks this module makes before
    calling it, the same arrangement as an update link's token.
    """
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    return SupplyDataAccess(program_id=program_id, caller=SYSTEM)


def program_supplier(round_: Round, org) -> Supplier:
    """The program's supplier for this organisation, linking it in on a first bid.

    A company the program already knows keeps its row. One the program has
    never dealt with arrives as `self_registered`, so the program team sees
    that nobody on their side has looked at it yet.
    """
    key = scope_key(round_.program_id)
    existing = Supplier.objects.filter(scope_key=key, org=org).first()
    if existing is not None:
        return existing
    made = call_operation(
        "supplier_create", _access(round_.program_id), {"data": {"org_id": org.pk, "status": "quoting"}}
    )
    Supplier.objects.filter(pk=made["id"]).update(origin="self_registered")
    return Supplier.objects.get(pk=made["id"])


def _require_bidder(org, orgs):
    if org not in orgs:
        raise NotAvailable("you do not act for that organisation")
    if not SupplierProfile.objects.filter(org=org).exists():
        raise NeedsProfile("register your organisation's supplier profile before bidding")


def _require_open_line(round_id, commodity_slug, orgs) -> ListedRound:
    listed = visible_round(round_id, orgs)
    if commodity_slug not in {line.commodity_slug for line in listed.lines}:
        raise NotAvailable("this round is not asking for that product")
    return listed


@transaction.atomic
def bid(round_id, commodity_slug, *, org, orgs, user, data: dict) -> Quote:
    """Record the organisation's bid on one line of an open round."""
    _require_bidder(org, orgs)
    listed = _require_open_line(round_id, commodity_slug, orgs)
    round_ = Round.objects.select_for_update().get(pk=listed.round.pk)
    if round_.status != "open":
        raise NotAvailable("this round has closed")
    supplier = program_supplier(round_, org)
    payload = {
        **data,
        "round_id": round_.pk,
        "commodity_slug": commodity_slug,
        "supplier_id": supplier.pk,
        "received_on": date.today().isoformat(),
    }
    made = call_operation("quote_record", _access(round_.program_id), {"data": payload})
    Quote.objects.filter(pk=made["id"]).update(entered_by="supplier", entered_by_user=user)
    _audit(Action.CREATE, "quote_record", made["id"], round_, org)
    return Quote.objects.get(pk=made["id"])


@transaction.atomic
def revise(quote_id, *, org, orgs, user, data: dict) -> Quote:
    """Replace the organisation's live quote with a new version of it."""
    quote = _live_own_quote(quote_id, org, orgs)
    made = call_operation(
        "quote_correct",
        _access(quote.round.program_id),
        {"quote_id": quote.pk, "data": data, "reason": "revised by the supplier on the marketplace"},
    )
    Quote.objects.filter(pk=made["id"]).update(entered_by="supplier", entered_by_user=user)
    _audit(Action.UPDATE, "quote_correct", made["id"], quote.round, org)
    return Quote.objects.get(pk=made["id"])


@transaction.atomic
def withdraw(quote_id, *, org, orgs, user) -> Quote:
    quote = _live_own_quote(quote_id, org, orgs)
    call_operation(
        "quote_void",
        _access(quote.round.program_id),
        {"quote_id": quote.pk, "reason": "withdrawn by the supplier on the marketplace"},
    )
    _audit(Action.UPDATE, "quote_void", quote.pk, quote.round, org)
    return Quote.objects.get(pk=quote.pk)


def _live_own_quote(quote_id, org, orgs) -> Quote:
    _require_bidder(org, orgs)
    quote = own_quote(quote_id, [org])
    if quote.voided or quote.superseded_by_id is not None:
        raise NotAvailable("that bid has already been replaced or withdrawn")
    # Re-read the round under the same visibility rule a new bid meets: a
    # round that closed, or went private, is no longer taking changes.
    visible_round(quote.round_id, orgs)
    return quote


def _audit(action, operation, resource_id, round_, org):
    audit_record(
        action,
        resource_type=f"supply_{operation}",
        resource_id=resource_id,
        program_id=round_.program_id,
        labs_only=synthetic_scopes.is_synthetic(round_.program_id),
        metadata={"via": "supply_market", "org_id": org.pk, "round_id": round_.pk},
    )


PRICE_PER_WORDS = {
    "per_pack": "per pack",
    "per_base_unit": "per unit",
    "per_lot_total": "for the whole quantity",
    "per_metric_tonne": "per metric tonne",
}


def _worded(quote: Quote) -> Quote:
    quote.per_words = PRICE_PER_WORDS.get(quote.as_quoted_unit, quote.as_quoted_unit or "")
    return quote


def category_label(code) -> str:
    return dict(records.COMMODITY_CATEGORIES).get(code, code or "")
