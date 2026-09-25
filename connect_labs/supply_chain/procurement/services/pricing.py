"""The one place a procurement money figure is computed.

The invariant (design doc section 6): every comparable number is derived,
never stored, and a derivation whose inputs are unconfirmed returns
Unconfirmed(reasons) rather than a number.

The reasons are written to be read by a supplier-facing human: they name the
missing fact, because questions.py turns them into the next email.
"""

from dataclasses import dataclass
from decimal import Decimal

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Commodity, Item, Quote, Tender
from connect_labs.supply_chain.values import (
    Derived,
    Money,
    confirmed,
    merge,
    metric_tonnes_to_base_units,
    unconfirmed,
    unit_noun,
)

FIGURE_FIELDS = (
    "usd_per_base_unit",
    "usd_per_pack_normalized",
    "usd_per_course",
    "landed_total_as_quoted",
    "landed_total_for_tender_quantity",
    "usd_per_child_treated",
)

# Which figures decide whether a quote can be COMPARED, as opposed to merely
# being shown.
#
# Not all of them, and the difference matters. `usd_per_course` and
# `usd_per_child_treated` need the commodity's ration table -- sachets per day
# and days per course -- which is the PROGRAMME's treatment protocol and not
# anything a supplier states. Gating comparability on them meant a supplier
# who answered every question we could possibly ask still came back
# "blocked", on a gap they had no way to close. Measured: a fully specified
# quote against a commodity with no ration table reported 0 of 1 comparable,
# and the only outstanding question had audience `internal`.
#
# So comparability is gated on the figures whose inputs are facts a supplier
# or the tender supplies. The course figures stay in FIGURE_FIELDS, stay
# visible, and stay Unconfirmed with their reason -- they are simply not a
# reason to refuse to rank. The ranking is on
# landed_total_for_tender_quantity, which needs no ration table.
COMPARABILITY_FIELDS = (
    "usd_per_base_unit",
    "usd_per_pack_normalized",
    "landed_total_as_quoted",
    "landed_total_for_tender_quantity",
)

# str.format templates over {base_unit} / {pack_unit}: the commodity supplies the
# nouns. A hardcoded "USD per sachet" would render a wrong header for an infant
# scale or a diagnostic test, and this catalogue is generic by design.
FIGURE_LABELS = {
    "usd_per_base_unit": "USD per {base_unit}",
    "usd_per_pack_normalized": "USD per {pack_unit}",
    "usd_per_course": "USD per course",
    "landed_total_as_quoted": "Landed total (as quoted)",
    "landed_total_for_tender_quantity": "Landed total (this tender)",
    "usd_per_child_treated": "USD per child treated",
}


def figure_nouns(base_unit, pack_unit) -> dict:
    """The nouns FIGURE_LABELS is formatted with, as a person writes a unit.

    "jerry_can" is a stored code; a column header reads "USD per jerry can".
    One helper, so the comparison's columns and the quote page's rows name a
    figure the same way. A commodity with no unit reads "unit" or "pack".
    """
    return {"base_unit": unit_noun(base_unit) or "unit", "pack_unit": unit_noun(pack_unit) or "pack"}


@dataclass(frozen=True)
class QuoteFigures:
    usd_per_base_unit: Derived
    usd_per_pack_normalized: Derived
    usd_per_course: Derived
    landed_total_as_quoted: Derived
    landed_total_for_tender_quantity: Derived
    usd_per_child_treated: Derived

    def as_dict(self) -> dict[str, Derived]:
        return {field: getattr(self, field) for field in FIGURE_FIELDS}


def _usd_amount(quote: Quote) -> Derived:
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


def _pack_spec(quote: Quote, item: Item | None) -> int | Derived:
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
            return unconfirmed("pack spec recorded as stated on the quote but no units-per-pack value was captured")
        return int(stated)

    if source == "trade_item_confirmed":
        # Fail closed: a quote may claim a confirmed item while the caller
        # passes none, and inventing the pack size at that point is the very
        # substitution this function refuses.
        if item is None:
            return unconfirmed("pack spec not confirmed: quote names a trade item but no item record was supplied")
        if not item.base_per_pack:
            return unconfirmed(f"pack spec not confirmed: trade item {item.sku or item.id} records no units per pack")
        return int(item.base_per_pack)

    return unconfirmed("pack spec not stated on the quote (units per pack)")


def _base_unit_grams(quote: Quote, item: Item | None) -> int | Derived:
    """Grams per base unit, from whoever actually stated it.

    Mirrors `_pack_spec`'s shape, because it is the same rule: the quote
    itself, or a confirmed trade item, count as a statement; the commodity
    catalogue does not. A supplier's actual sachet can weigh 100 g against a
    catalogue default of 92 g, and that is exactly as load-bearing for a
    per-tonne conversion as a pack-spec mismatch is for a per-carton one —
    **the commodity catalogue is never consulted here** either.
    """
    stated = quote.base_unit_grams_stated
    if stated:
        return int(stated)
    if item is not None and item.base_unit_grams:
        return int(item.base_unit_grams)
    return unconfirmed("unit weight not stated on the quote (grams per base unit)")


def _extras(quote: Quote) -> Derived:
    """Freight plus duties to add to a lot total, or why that is unknowable.

    The quote's own flags speak first, and its Incoterm speaks when they are
    silent. An Incoterm is the trade's standard statement about exactly these
    two costs -- DDP means the seller clears the import, EXW means the buyer
    carries everything -- so refusing to cost a DDP quote because a separate
    `duties_basis` was left alone is a refusal about our data entry, not
    about what the supplier told us.

    Three rules. The third is not an exception to the first -- it replaces
    it wherever the two could disagree, which is why they are written in this
    order:

      1. where only ONE of the two speaks -- a recorded basis with no
         recognised Incoterm, or an Incoterm with no recorded basis -- that
         one is used;
      2. where the Incoterm is what spoke, any reason it produces says so,
         because "excluded, read from EXW" is weaker evidence than
         "excluded" typed by somebody reading the supplier's email, and a
         reader chasing the gap should know which they are chasing;
      3. where BOTH speak and they disagree IN THE DIRECTION THAT COSTS
         MONEY, neither is used and the disagreement is reported.

    The asymmetry in the third rule is the subtle part, and it was found by
    an existing fixture rather than reasoned out in advance.

    A record of "excluded" under a term that says "included" is dangerous: it
    means somebody is about to add a cost the seller has already covered, and
    the buyer pays twice. That is reported.

    The opposite -- "included" recorded under a term that says the buyer
    pays -- is routine and usually right. A health programme is very often
    duty-exempt, so there is nothing to add; the schema carries
    `duty_relief_claimed` for exactly that. It may also simply have been
    negotiated. Either way the recorded figure adds nothing, so no total is
    harmed by believing it, and refusing to cost a quote over it would be
    this domain withholding a number it actually has.

    Whether that exemption case deserves to be recorded as its own basis
    rather than borrowing "included" is a real open question, and a
    separate one from costing.
    """
    total = Decimal("0")
    reasons: list[str] = []

    from_term = dict(zip(("freight", "duties"), records.freight_and_duties_for_incoterm(quote.incoterm), strict=True))

    for label, basis, amount in (
        ("freight", quote.freight_basis, quote.freight_amount),
        ("duties", quote.duties_basis, quote.duties_amount),
    ):
        implied = from_term[label]

        if basis == "excluded" and implied == "included":
            # The costly direction only: the seller's term covers this and
            # the record says to add it on top, so believing the record bills
            # the buyer twice. The other direction is left alone -- see the
            # docstring.
            reasons.append(
                f"the quote says {label} excluded but its Incoterm {quote.incoterm} means "
                f"{label} included; adding it would charge for it twice"
            )
            continue

        if basis not in ("included", "excluded") and implied:
            basis = implied

        if basis == "included":
            continue
        if basis == "excluded":
            if amount is None:
                # Named as read-from-the-term where that is where it came
                # from, so a reader chasing the gap looks in the right place:
                # "excluded" from a bare EXW is not the same evidence as
                # "excluded" typed by somebody reading the supplier's email.
                via = f" (read from Incoterm {quote.incoterm})" if implied and implied == basis else ""
                reasons.append(f"{label} excluded from the quote but no {label} amount recorded{via}")
            else:
                total += amount
            continue
        reasons.append(f"{label} basis not specified on the quote")

    if reasons:
        return unconfirmed(*reasons)
    return Money(total)


def _course_size(commodity: Commodity, item: Item | None = None, pack_spec: int | Derived = None) -> int | Derived:
    """Base units per treatment course.

    The item speaks first when it says it IS a course: a three-day packet or
    a co-pack made as one course needs no ration table, because the
    manufacturer packed the protocol. `one_course_is="pack"` counts the pack
    through the same pack specification every other per-pack figure uses --
    never the commodity's nominal one. Otherwise the programme's own ration
    table decides, and its absence is our gap, not a supplier's.
    """
    if item is not None and item.one_course_is == "base_unit":
        return 1
    if item is not None and item.one_course_is == "pack":
        return pack_spec
    size = commodity.base_units_per_course
    if not size:
        base_unit = commodity.base_unit or "unit"
        return unconfirmed(
            f"no course definition set for {commodity.name or commodity.slug} ({base_unit}s per course)"
        )
    return int(size)


def _lot_subtotal(
    quote: Quote,
    commodity: Commodity,
    usd: Money,
    per_base_unit: Money,
    units_quoted: Decimal,
) -> Decimal:
    """The pre-extras cost of the quote's own quantity basis.

    When the quote's price and its quantity basis are already denominated in
    the same unit ($/carton priced against a quantity in cartons, $/tonne
    against a quantity in tonnes, a lot total against itself), the total is
    one exact multiplication — or, for a lot total, no arithmetic at all.
    Routing that through a per-base-unit price instead — dividing by a pack
    spec or a unit weight, then multiplying back by a derived unit count —
    sends an exact whole-dollar figure through a repeating decimal for no
    reason: 50.00 / 150 has no exact Decimal representation, and Decimal's
    fixed precision does not reliably cancel that back out on the way back
    up. Take the exact path whenever the units already line up; fall back to
    the per-base-unit price only for a genuine unit conversion, where some
    rounding is unavoidable.
    """
    unit = quote.quantity_basis_unit
    same_unit_as_price = (
        (quote.as_quoted_unit == "per_pack" and unit == commodity.pack_unit)
        or (quote.as_quoted_unit == "per_base_unit" and unit == commodity.base_unit)
        or (quote.as_quoted_unit == "per_metric_tonne" and unit == "metric_tonne")
    )
    if same_unit_as_price:
        return usd.amount * quote.quantity_basis
    if quote.as_quoted_unit == "per_lot_total":
        return usd.amount
    return per_base_unit.amount * units_quoted


def _base_units_quoted(
    quote: Quote,
    commodity: Commodity,
    pack_spec: int | Derived,
    item: Item | None,
) -> Decimal | Derived:
    """How many base units the quote's own quantity basis covers."""
    quantity = quote.quantity_basis
    if quantity is None:
        return unconfirmed("no quantity basis recorded on the quote")

    unit = quote.quantity_basis_unit
    if unit is None:
        return unconfirmed("no quantity basis unit recorded on the quote")
    if unit == commodity.base_unit:
        return quantity
    if unit == commodity.pack_unit:
        blocked = merge(confirmed(pack_spec))
        if blocked:
            return blocked
        return quantity * Decimal(pack_spec)
    if unit == "metric_tonne":
        grams = _base_unit_grams(quote, item)
        blocked = merge(confirmed(grams))
        if blocked:
            return blocked
        return metric_tonnes_to_base_units(quantity, grams)
    return unconfirmed(f"quantity basis unit {unit!r} is not on this commodity's unit ladder")


def compute_figures(
    quote: Quote,
    commodity: Commodity,
    tender: Tender,
    item: Item | None = None,
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
    course = _course_size(commodity, item, pack_spec)

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
        grams = _base_unit_grams(quote, item)
        grams_blocked = merge(confirmed(grams))
        if grams_blocked:
            per_base_unit = per_pack = grams_blocked
        else:
            units = metric_tonnes_to_base_units(Decimal("1"), grams)
            per_base_unit = Money(usd.amount / units)
            spec_blocked = merge(confirmed(pack_spec))
            per_pack = spec_blocked or Money(per_base_unit.amount * Decimal(pack_spec))
    elif quote.as_quoted_unit == "per_lot_total":
        units = _base_units_quoted(quote, commodity, pack_spec, item)
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
    # Phase 1a treats one course as one child treated -- there is no
    # courses_per_child on the commodity, and the programme's course
    # definition (base_units_per_day * days_per_course) already defines a
    # full treatment course per the protocol. usd_per_child_treated is kept
    # as its own FIGURE_FIELDS entry (not dropped in favour of usd_per_course)
    # because the two answer different questions on the comparison screen --
    # "what does treating a child cost" reads differently from "what does a
    # course cost" even when, today, they are the same number.
    per_child = per_course

    # --- landed totals ---------------------------------------------------
    units_quoted = _base_units_quoted(quote, commodity, pack_spec, item)
    landed_blocked = merge(per_base_unit, extras, confirmed(units_quoted))
    if landed_blocked:
        landed_as_quoted: Derived = landed_blocked
    else:
        subtotal = _lot_subtotal(quote, commodity, usd, per_base_unit, units_quoted)
        landed_as_quoted = Money(subtotal + extras.amount)

    landed_for_tender: Derived
    tender_quantity = tender.quantity_for(commodity.slug)
    if tender_quantity is None:
        landed_for_tender = unconfirmed(f"this tender has no line for {commodity.slug}")
    elif quote.quantity_basis is None:
        # A dedicated message: the generic "quote covers None carton" below
        # would leak internal absence-representation into a supplier-facing
        # reason instead of naming the missing fact.
        landed_for_tender = unconfirmed(
            f"no quantity basis recorded on the quote; this tender is " f"{tender_quantity[0]} {tender_quantity[1]}"
        )
    elif quote.quantity_basis_unit != tender_quantity[1] or quote.quantity_basis != tender_quantity[0]:
        landed_for_tender = unconfirmed(
            f"quote covers {quote.quantity_basis} {quote.quantity_basis_unit}; "
            f"tender is {tender_quantity[0]} {tender_quantity[1]}"
        )
    else:
        landed_for_tender = landed_as_quoted

    return QuoteFigures(
        usd_per_base_unit=per_base_unit,
        usd_per_pack_normalized=per_pack,
        usd_per_course=per_course,
        landed_total_as_quoted=landed_as_quoted,
        landed_total_for_tender_quantity=landed_for_tender,
        usd_per_child_treated=per_child,
    )
