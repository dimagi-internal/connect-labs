"""Who we think can supply a product, and on what evidence.

There is no "supplies" table, and there deliberately is not one. A row saying
"Ariel Foods supplies RUTF" would be an assertion nobody made and nothing
could date: it would survive the supplier going quiet for a year, and it
would read the same whether it came from a signed contract or from somebody
typing a name into a box.

So the supply base is DERIVED, and every entry carries the record it was
derived from. Six kinds of evidence, and they are not equivalent -- a
contract is a commitment, a quote is an offer, an invitation is a hope --
so each is named and dated rather than collapsed into one "supplier of RUTF"
badge. `strength` exists to sort them; it is not a score, just the order the
kinds sit in.

The manufacturer match is the one weak link and is labelled as such wherever
it is shown. `Item.manufacturer` is free text with no foreign key, so
matching it to a supplier is a string comparison and nothing more; it is
folded to a trimmed casefold and must match exactly. A near-match is left
unmatched on purpose -- claiming that "Ariel Foods" and "Ariel Foods FZE
(Lagos)" are the same company is a judgement, and getting it wrong attaches
one company's prices to another's name.
"""

from dataclasses import dataclass, field
from datetime import date

# Strongest first. The order is the whole reason these are separate kinds:
# "we have a contract with them" and "we emailed them once" are both true
# statements about a supplier and a product, and showing them as one thing
# is how a shortlist quietly fills up with companies that never replied.
EVIDENCE_KINDS = (
    "contracted",
    "awarded",
    "quoted",
    "quoted_superseded",
    "invited",
    "named_as_manufacturer",
)
_STRENGTH = {kind: len(EVIDENCE_KINDS) - i for i, kind in enumerate(EVIDENCE_KINDS)}


@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str
    on: date | None = None
    quote_id: int | None = None
    round_id: int | None = None
    contract_id: int | None = None
    item_id: int | None = None

    @property
    def strength(self) -> int:
        return _STRENGTH.get(self.kind, 0)


@dataclass
class SupplyClaim:
    """One supplier, and everything that connects them to this product."""

    supplier_id: int
    supplier_name: str
    supplier_country: str = ""
    supplier_status: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def strongest(self) -> Evidence | None:
        return max(self.evidence, key=lambda e: e.strength, default=None)

    @property
    def basis(self) -> str:
        """The strongest kind of evidence, which is what the row is sorted on."""
        strongest = self.strongest
        return strongest.kind if strongest else ""

    @property
    def last_heard(self) -> date | None:
        """The most recent dated evidence, or None when nothing is dated.

        Separate from `strongest` because they answer different questions: one
        is how firm the relationship is, the other is whether it is still
        warm. A supplier can be the only one ever contracted and also not have
        been in touch for eight months.
        """
        dates = [e.on for e in self.evidence if e.on]
        return max(dates) if dates else None


def _live(quote) -> bool:
    """A quote that still stands: not voided, not replaced by a correction."""
    return not quote.voided and quote.superseded_by_id is None


def _commodity_on_round(round_, commodity_slug) -> bool:
    return any((line or {}).get("commodity_slug") == commodity_slug for line in (round_.lines or []))


def supply_base(
    *,
    commodity_slug: str,
    suppliers,
    quotes=(),
    awards=(),
    contracts=(),
    outreach=(),
    rounds=(),
    items=(),
    item_id: int | None = None,
) -> list[SupplyClaim]:
    """Everyone connected to this product, strongest evidence first.

    Pass `item_id` to narrow from a product to one manufacturer's version of
    it: quotes, awards and contracts must then name that trade item, and the
    invitation evidence drops out entirely. An RFQ is issued against a
    commodity line, so an invitation says nothing about which trade item the
    supplier would have offered -- carrying it down to the item page would
    invent a specificity the record does not have.
    """
    by_id: dict[int, SupplyClaim] = {}
    suppliers_by_id = {s.pk: s for s in suppliers}

    def claim_for(supplier_id):
        supplier = suppliers_by_id.get(supplier_id)
        if supplier is None:
            # A supplier outside this scope cannot be named, and inventing a
            # placeholder row would put an unidentifiable company on a page
            # about who can supply something.
            return None
        if supplier_id not in by_id:
            by_id[supplier_id] = SupplyClaim(
                supplier_id=supplier_id,
                supplier_name=supplier.name,
                supplier_country=supplier.country,
                supplier_status=supplier.status,
            )
        return by_id[supplier_id]

    def add(supplier_id, evidence):
        claim = claim_for(supplier_id)
        if claim is not None:
            claim.evidence.append(evidence)

    def concerns(record) -> bool:
        if record.commodity.slug != commodity_slug:
            return False
        return record.item_id == item_id if item_id is not None else True

    rounds_by_id = {r.pk: r for r in rounds}

    for contract in contracts:
        if not concerns(contract):
            continue
        add(
            contract.supplier_id,
            Evidence(
                kind="contracted",
                detail=contract.reference or f"contract {contract.pk}",
                on=contract.signed_on,
                contract_id=contract.pk,
                item_id=contract.item_id,
            ),
        )

    quotes_by_id = {q.pk: q for q in quotes}
    for award in awards:
        quote = quotes_by_id.get(award.quote_id)
        # An award points at the quote it accepted, and the trade item lives
        # on the quote rather than the award -- so narrowing to one item means
        # reading through to it. An award whose quote is out of scope is
        # skipped rather than counted against every item.
        if award.commodity.slug != commodity_slug:
            continue
        if item_id is not None and (quote is None or quote.item_id != item_id):
            continue
        add(
            award.supplier_id,
            Evidence(
                kind="awarded",
                detail="provisional award" if award.provisional else "awarded",
                on=award.decided_on,
                quote_id=award.quote_id,
                round_id=award.round_id,
                item_id=quote.item_id if quote else None,
            ),
        )

    quoted_at_all: set[tuple[int, int]] = set()
    for quote in quotes:
        if not concerns(quote):
            continue
        quoted_at_all.add((quote.supplier_id, quote.round_id))
        add(
            quote.supplier_id,
            Evidence(
                kind="quoted" if _live(quote) else "quoted_superseded",
                detail=(
                    f"{quote.as_quoted_currency} {quote.as_quoted_amount} "
                    f"{(quote.as_quoted_unit or '').replace('_', ' ')}"
                ).strip()
                if quote.as_quoted_amount is not None
                else "quote recorded, no price on it",
                on=quote.received_on,
                quote_id=quote.pk,
                round_id=quote.round_id,
                item_id=quote.item_id,
            ),
        )

    if item_id is None:
        for invitation in outreach:
            round_ = rounds_by_id.get(invitation.round_id)
            if round_ is None or not _commodity_on_round(round_, commodity_slug):
                continue
            # An invitation that was answered is already represented by the
            # quote it produced. Keeping both would show "quoted" and "invited,
            # no reply" side by side about the same exchange.
            if (invitation.supplier_id, invitation.round_id) in quoted_at_all:
                continue
            add(
                invitation.supplier_id,
                Evidence(
                    kind="invited",
                    detail=("replied, no quote recorded" if invitation.responded else "no reply yet"),
                    on=invitation.sent_on,
                    round_id=invitation.round_id,
                ),
            )

    by_name = {}
    for supplier in suppliers:
        key = (supplier.name or "").strip().casefold()
        if key:
            by_name.setdefault(key, supplier.pk)
    for item in items:
        if item.commodity.slug != commodity_slug:
            continue
        if item_id is not None and item.pk != item_id:
            continue
        supplier_id = by_name.get((item.manufacturer or "").strip().casefold())
        if supplier_id is None:
            continue
        add(
            supplier_id,
            Evidence(
                kind="named_as_manufacturer",
                detail=f"named as the manufacturer of {item.sku}",
                item_id=item.pk,
            ),
        )

    for claim in by_id.values():
        # Strongest kind first, and within a kind the most recent first, so
        # "quoted" reads newest-quote-down rather than in insertion order.
        claim.evidence.sort(key=lambda e: (-e.strength, -(e.on.toordinal() if e.on else 0)))

    # Strongest basis first; then the warmest, so a page reads down from
    # "contracted last month" to "emailed in April, never replied".
    return sorted(
        by_id.values(),
        key=lambda c: (
            -(c.strongest.strength if c.strongest else 0),
            -(c.last_heard.toordinal() if c.last_heard else 0),
            c.supplier_name.casefold(),
        ),
    )


def wire(claim: SupplyClaim) -> dict:
    """One claim as JSON, for the operation and the template alike."""
    return {
        "supplier_id": claim.supplier_id,
        "supplier_name": claim.supplier_name,
        "supplier_country": claim.supplier_country,
        "supplier_status": claim.supplier_status,
        "basis": claim.basis,
        "last_heard": claim.last_heard.isoformat() if claim.last_heard else None,
        "evidence": [
            {
                "kind": e.kind,
                "detail": e.detail,
                "on": e.on.isoformat() if e.on else None,
                "quote_id": e.quote_id,
                "round_id": e.round_id,
                "contract_id": e.contract_id,
                "item_id": e.item_id,
            }
            for e in claim.evidence
        ],
    }
