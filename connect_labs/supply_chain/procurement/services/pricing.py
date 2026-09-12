"""The one place a procurement money figure is computed.

The invariant (design doc section 6): every comparable number is derived,
never stored, and a derivation whose inputs are unconfirmed returns
Unconfirmed(reasons) rather than a number.

The reasons are written to be read by a supplier-facing human: they name the
missing fact, because questions.py turns them into the next email.
"""

from dataclasses import dataclass
from decimal import Decimal

from connect_labs.supply_chain.models import CommodityRecord, ItemRecord, QuoteRecord, RoundRecord
from connect_labs.supply_chain.values import Derived, Money, confirmed, merge, metric_tonnes_to_base_units, unconfirmed

FIGURE_FIELDS = (
    "usd_per_base_unit",
    "usd_per_pack_normalized",
    "usd_per_course",
    "landed_total_as_quoted",
    "landed_total_for_round_quantity",
    "usd_per_child_treated",
)

# str.format templates over {base_unit} / {pack_unit}: the commodity supplies the
# nouns. A hardcoded "USD per sachet" would render a wrong header for an infant
# scale or a diagnostic test, and this catalogue is generic by design.
FIGURE_LABELS = {
    "usd_per_base_unit": "USD per {base_unit}",
    "usd_per_pack_normalized": "USD per {pack_unit}",
    "usd_per_course": "USD per course",
    "landed_total_as_quoted": "Landed total (as quoted)",
    "landed_total_for_round_quantity": "Landed total (this round)",
    "usd_per_child_treated": "USD per child treated",
}


@dataclass(frozen=True)
class QuoteFigures:
    usd_per_base_unit: Derived
    usd_per_pack_normalized: Derived
    usd_per_course: Derived
    landed_total_as_quoted: Derived
    landed_total_for_round_quantity: Derived
    usd_per_child_treated: Derived

    def as_dict(self) -> dict[str, Derived]:
        return {field: getattr(self, field) for field in FIGURE_FIELDS}


def _usd_amount(quote: QuoteRecord) -> Derived:
    """The quote's headline amount in USD, or why it cannot be known."""
    amount = quote.as_quoted_amount
    if amount is None:
        return unconfirmed("no amount recorded on the quote")

    currency = quote.as_quoted_currency or "USD"
    if currency == "USD":
        return Money(amount)

    rate = quote.fx_rate_to_usd
    if rate is None:
        return unconfirmed(f"quote is in {currency} and no exchange rate was recorded")
    return Money(amount * rate)


def _pack_spec(quote: QuoteRecord, item: ItemRecord | None) -> int | Derived:
    """Base units per pack, from whoever actually stated it.

    Two sources count as a statement, and one does not:

      stated_on_quote      the supplier wrote the number down
      trade_item_confirmed the supplier identified the trade item, which states
                           its pack configuration just as surely
      not_stated           nobody has said

    **The commodity catalogue is never consulted here.** A per-sachet price
    derived from our own assumption about a pack nobody identified would be a
    guess wearing the clothes of a measurement — and the commodity's figure can
    be flatly wrong for a given supplier: one manufacturer packs 150 to the
    carton and the next packs 144.
    """
    source = quote.pack_spec_source

    if source == "stated_on_quote":
        stated = quote.base_per_pack_stated
        if not stated:
            return unconfirmed("pack spec recorded as stated on the quote but no " "units-per-pack value was captured")
        return int(stated)

    if source == "trade_item_confirmed":
        # Fail closed: a quote may claim a confirmed item while the caller
        # passes none, and inventing the pack size at that point is the very
        # substitution this function refuses.
        if item is None:
            return unconfirmed("pack spec not confirmed: quote names a trade item but no item " "record was supplied")
        if not item.base_per_pack:
            return unconfirmed(
                f"pack spec not confirmed: trade item {item.sku or item.id} " "records no units per pack"
            )
        return int(item.base_per_pack)

    return unconfirmed("pack spec not stated on the quote (units per pack)")


def _extras(quote: QuoteRecord) -> Derived:
    """Freight plus duties to add to a lot total, or why that is unknowable."""
    total = Decimal("0")
    reasons: list[str] = []

    for label, basis, amount in (
        ("freight", quote.freight_basis, quote.freight_amount),
        ("duties", quote.duties_basis, quote.duties_amount),
    ):
        if basis == "included":
            continue
        if basis == "excluded":
            if amount is None:
                reasons.append(f"{label} excluded from the quote but no {label} amount recorded")
            else:
                total += amount
            continue
        reasons.append(f"{label} basis not specified on the quote")

    if reasons:
        return unconfirmed(*reasons)
    return Money(total)


def _course_size(commodity: CommodityRecord) -> int | Derived:
    size = commodity.base_units_per_course
    if not size:
        return unconfirmed(f"no course definition set for {commodity.name or commodity.slug} " "(sachets per course)")
    return int(size)


def _lot_subtotal(
    quote: QuoteRecord,
    commodity: CommodityRecord,
    usd: Money,
    per_base_unit: Money,
    units_quoted: Decimal,
) -> Decimal:
    """The pre-extras cost of the quote's own quantity basis.

    When the quote's price and its quantity basis are already denominated in
    the same unit ($/carton priced against a quantity in cartons, say), the
    total is one exact multiplication. Routing that through a per-base-unit
    price instead — dividing by the pack spec, then multiplying back by a
    pack-derived unit count — sends an exact whole-dollar figure through a
    repeating decimal for no reason: 50.00 / 150 has no exact Decimal
    representation, and Decimal's fixed precision does not reliably cancel
    that back out on the way back up. Take the exact path whenever the units
    already line up; fall back to the per-base-unit price only when they do
    not (a genuine unit conversion, where some rounding is unavoidable).
    """
    unit = quote.quantity_basis_unit
    if quote.as_quoted_unit == "per_pack" and unit == commodity.pack_unit:
        return usd.amount * quote.quantity_basis
    if quote.as_quoted_unit == "per_base_unit" and unit == commodity.base_unit:
        return usd.amount * quote.quantity_basis
    return per_base_unit.amount * units_quoted


def _base_units_quoted(quote: QuoteRecord, commodity: CommodityRecord, pack_spec: int | Derived) -> Decimal | Derived:
    """How many base units the quote's own quantity basis covers."""
    quantity = quote.quantity_basis
    if quantity is None:
        return unconfirmed("no quantity basis recorded on the quote")

    unit = quote.quantity_basis_unit
    if unit == commodity.base_unit:
        return quantity
    if unit == commodity.pack_unit:
        blocked = merge(confirmed(pack_spec))
        if blocked:
            return blocked
        return quantity * Decimal(pack_spec)
    if unit == "metric_tonne":
        grams = quote.base_unit_grams_stated or commodity.base_unit_grams
        if not grams:
            return unconfirmed("quote is in metric tonnes and no unit weight is known")
        return metric_tonnes_to_base_units(quantity, int(grams))
    return unconfirmed(f"quantity basis unit {unit!r} is not on this commodity's unit ladder")


def compute_figures(
    quote: QuoteRecord,
    commodity: CommodityRecord,
    round_: RoundRecord,
    item: ItemRecord | None = None,
) -> QuoteFigures:
    """Derive every comparable figure for one quote, or say why it cannot be.

    `item` is the trade item the quote names, when it names one. It is optional
    so a caller with no item loaded still gets honest figures rather than an
    exception — a quote claiming a confirmed item without one supplied comes
    back Unconfirmed, not guessed.
    """
    usd = _usd_amount(quote)
    pack_spec = _pack_spec(quote, item)
    extras = _extras(quote)
    course = _course_size(commodity)

    # --- per base unit and per pack -------------------------------------
    per_base_unit: Derived
    per_pack: Derived

    blocked = merge(usd)
    if blocked:
        per_base_unit = per_pack = blocked
    elif quote.as_quoted_unit == "per_base_unit":
        per_base_unit = usd
        spec_blocked = merge(confirmed(pack_spec))
        per_pack = spec_blocked or Money(usd.amount * Decimal(pack_spec))
    elif quote.as_quoted_unit == "per_pack":
        per_pack = usd
        spec_blocked = merge(confirmed(pack_spec))
        per_base_unit = spec_blocked or Money(usd.amount / Decimal(pack_spec))
    elif quote.as_quoted_unit == "per_metric_tonne":
        grams = (
            quote.base_unit_grams_stated
            or (item.base_unit_grams if item is not None else None)
            or commodity.base_unit_grams
        )
        if not grams:
            per_base_unit = per_pack = unconfirmed("quote is per metric tonne and no unit weight is known")
        else:
            units = metric_tonnes_to_base_units(Decimal("1"), int(grams))
            per_base_unit = Money(usd.amount / units)
            spec_blocked = merge(confirmed(pack_spec))
            per_pack = spec_blocked or Money(per_base_unit.amount * Decimal(pack_spec))
    elif quote.as_quoted_unit == "per_lot_total":
        units = _base_units_quoted(quote, commodity, pack_spec)
        unit_blocked = merge(confirmed(units))
        if unit_blocked:
            per_base_unit = per_pack = unit_blocked
        else:
            per_base_unit = Money(usd.amount / units)
            spec_blocked = merge(confirmed(pack_spec))
            per_pack = spec_blocked or Money(per_base_unit.amount * Decimal(pack_spec))
    else:
        per_base_unit = per_pack = unconfirmed(f"as-quoted unit {quote.as_quoted_unit!r} is not recognised")

    # --- per course and per child ---------------------------------------
    course_blocked = merge(per_base_unit, confirmed(course))
    if course_blocked:
        per_course: Derived = course_blocked
    else:
        per_course = Money(per_base_unit.amount * Decimal(course))
    per_child = per_course

    # --- landed totals ---------------------------------------------------
    units_quoted = _base_units_quoted(quote, commodity, pack_spec)
    landed_blocked = merge(per_base_unit, extras, confirmed(units_quoted))
    if landed_blocked:
        landed_as_quoted: Derived = landed_blocked
    else:
        subtotal = _lot_subtotal(quote, commodity, usd, per_base_unit, units_quoted)
        landed_as_quoted = Money(subtotal + extras.amount)

    landed_for_round: Derived
    round_quantity = round_.quantity_for(commodity.slug)
    if round_quantity is None:
        landed_for_round = unconfirmed(f"this round has no line for {commodity.slug}")
    elif quote.quantity_basis is None or quote.quantity_basis_unit != round_quantity[1]:
        landed_for_round = unconfirmed(
            f"quote covers {quote.quantity_basis} {quote.quantity_basis_unit}; "
            f"round is {round_quantity[0]} {round_quantity[1]}"
        )
    elif quote.quantity_basis != round_quantity[0]:
        landed_for_round = unconfirmed(
            f"quote covers {quote.quantity_basis} {quote.quantity_basis_unit}; "
            f"round is {round_quantity[0]} {round_quantity[1]}"
        )
    else:
        landed_for_round = landed_as_quoted

    return QuoteFigures(
        usd_per_base_unit=per_base_unit,
        usd_per_pack_normalized=per_pack,
        usd_per_course=per_course,
        landed_total_as_quoted=landed_as_quoted,
        landed_total_for_round_quantity=landed_for_round,
        usd_per_child_treated=per_child,
    )
