"""The comparison table, and its refusal to rank what it cannot compare.

Rule 6 of the design doc, as amended 2026-09-11: quotes yielding a full set
of figures are comparable and ranked; a quote with any Unconfirmed figure
is blocked, never enters the ranking, and becomes a worklist of the
questions that would unblock it. The tool's answer to "who is cheapest" is
then "here is what we can defend, and here is what we cannot yet, and the
questions to send" — which is the point of the app. Suppressing the whole
ranking whenever any candidate is unconfirmed was tried and rejected: it
withholds the comparison the buyer CAN defend along with the one they
cannot.

Ranking is decided here, not in a template: `compare_tender` sorts the
comparable rows by a declared `ranked_by` key (landed total for this tender's
quantity -- the only key that can ever have a comparable row to sort, per
Ruling 22) and records that key and a `provisional` flag — true whenever
anything was left out of the ranking — on the frozen result. "Ranked by X;
provisional because N of M suppliers were blocked" is what makes an award
defensible months after the fact, which is the whole reason the comparison is
frozen at all.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from connect_labs.supply_chain.models import Commodity, Quote, Tender
from connect_labs.supply_chain.procurement.services.compliance import (
    FAIL,
    PASS,
    check_compliance,
    requirement_label,
)
from connect_labs.supply_chain.procurement.services.pricing import (
    COMPARABILITY_FIELDS,
    FIGURE_FIELDS,
    FIGURE_LABELS,
    compute_figures,
    figure_nouns,
)
from connect_labs.supply_chain.procurement.services.questions import (
    INTERNAL,
    MissingFact,
    audience_for_reason,
    key_for_reason,
    missing_facts,
)
from connect_labs.supply_chain.records import course_applies_to_category, freight_and_duties_for_incoterm
from connect_labs.supply_chain.values import (
    Unconfirmed,
    decimal_string,
    destination_phrase,
    merge,
    money_digits,
    quantity_phrase,
    to_wire,
    unconfirmed,
    unit_noun,
)

# What decides whether a quote can be ranked, said where a card lists what is
# "not blocking": the inputs of COMPARABILITY_FIELDS, and nothing else.
RANKING_RULE = (
    "Only what the landed price depends on decides whether a quote can be ranked: its price, "
    "pack size, quantity, freight, duties and exchange rate. Specification figures such as "
    "shelf life are checked, not ranked."
)


def pack_words(base_unit, pack_unit) -> str:
    """ "sachets per carton": the pack figure in the commodity's own units, else "units per pack"."""
    if base_unit and pack_unit:
        return f"{unit_noun(base_unit, 2)} per {unit_noun(pack_unit)}"
    return "units per pack"


def plain_reason(reason: str, base_unit="", pack_unit="") -> str:
    """A pricing reason in the commodity's own words.

    "pack spec not stated on the quote (units per pack)" is the pricing
    module's generic sentence; on a screen about RUTF it reads "sachets per
    carton not stated on the quote". The bracketed generic unit goes, and so
    does "pack spec", which nobody on the program says.
    """
    pack = pack_words(base_unit, pack_unit)
    text = reason.replace(" (units per pack)", "").replace(" (grams per base unit)", "")
    text = text.replace("units-per-pack value", f"{pack} figure").replace("units per pack", pack)
    text = text.replace("pack spec", pack)
    if base_unit:
        text = text.replace("unit weight", f"weight of one {unit_noun(base_unit)}")
    return text


# The short name of each gap, by the question key that answers it -- the
# words the overview's flag uses: "Northwind Foods (freight)".
_GAP_WORDS = {
    "unit_weight": "unit weight",
    "pickup_transport": "our transport cost",
    "fx_rate": "exchange rate",
    "quantity_basis_missing": "quantity",
    "quantity_basis_mismatch": "quantity",
    "amount": "price",
    "tender_configuration": "tender line",
    "as_quoted_unit": "price unit",
}


def gap_word(reason: str, base_unit="", pack_unit="") -> str:
    """One Unconfirmed reason as the few words the overview names it by.

    Freight and duties read as `pricing.basis_gaps` reads them ("freight",
    "duties amount"), so the overview's flag and its earlier basis flag
    cannot word one gap two ways.
    """
    lowered = (reason or "").lower()
    key = key_for_reason(reason)
    if key == "pack_spec":
        return pack_words(base_unit, pack_unit)
    if key in ("freight_basis", "duties_basis"):
        leg = key.removesuffix("_basis")
        return f"{leg} amount" if f"no {leg} amount recorded" in lowered else leg
    if key == "unit_weight" and base_unit:
        return f"{unit_noun(base_unit)} weight"
    if key in _GAP_WORDS:
        return _GAP_WORDS[key]
    if "kit composition" in lowered:
        return "kit contents"
    return " ".join(lowered.split()[:4])


@dataclass(frozen=True)
class ComparisonColumn:
    key: str
    label: str
    rankable: bool
    blocked_by: tuple[str, ...] = ()


@dataclass
class ComparisonRow:
    quote_id: int
    supplier_id: int
    supplier_name: str
    figures: dict
    compliance: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    is_comparable: bool = False
    # What one unit of the quoted trade item holds, when it is a kit; empty
    # for an ordinary item and None when the quote names no item at all.
    composition: list | None = None
    # The trade item quoted, by name. One distributor offering several options
    # is one supplier with several rows, and without this they read as the
    # same name twice, told apart only by the price being compared.
    item_name: str = ""
    # The unit `composition` describes ("kit", "co-pack"), and the contents
    # restated per base unit -- what kits are compared on, so 50 tablets per
    # kit of 50 and 1 tablet per test are the same kit, and 50 per kit and 50
    # per test are not.
    composition_unit: str = ""
    composition_key: tuple | None = None
    # Who typed the quote in, and whether its supplier arrived from the
    # marketplace with nobody on the program team having looked at it yet.
    # Shown, never used to rank or hide: a hidden bid is a silent failure.
    entered_by: str = "program"
    supplier_awaiting_review: bool = False
    # How this bid reaches the buyer, in words: "to Kano, Nigeria" or
    # "collected from Our warehouse, Kano". Two bids from one supplier for
    # two places read as two different offers, which they are.
    delivery: str = ""
    # The commodity's own nouns, so a gap reads "sachets per carton" rather
    # than the pricing module's generic "units per pack".
    base_unit: str = ""
    pack_unit: str = ""
    # The price as the supplier stated it ("42.50 USD per carton") and the
    # day the quote arrived: a blocked card still says what was offered.
    as_quoted: str = ""
    received_on: str = ""
    # Said after a price stated per base unit, which the ranking reads per
    # pack: the figure it converts to, "= USD 43.50 per carton", or, while the
    # pack is the missing fact, "(per carton once sachets per carton is
    # known)". "" otherwise.
    as_quoted_note: str = ""
    # What the specification requires of the pack figure: "150 sachets per
    # carton", and the figure alone ("150", "at least 150"), for the line
    # under a pack blocker: "Sachets per carton: not stated (tender requires 150)".
    pack_requirement: str = ""
    pack_requirement_figure: str = ""
    # What the landed figures assume, and whose word it is: "Delivered to
    # Kano · freight included · duties included, per quote".
    landed_basis: str = ""
    # The quantity the quote's landed total is for: "2,000 cartons".
    quantity_quoted: str = ""

    @property
    def gaps(self) -> list[str]:
        """Every gap that keeps this offer out of the ranking, in a few words each, once.

        "freight", "duties amount", "sachets per carton": read from the same
        comparability figures `is_comparable` is judged on, so a list of
        these across a tender names exactly the offers the comparison
        blocks, and why.
        """
        out = []
        for key in COMPARABILITY_FIELDS:
            value = self.figures.get(key)
            if not isinstance(value, Unconfirmed):
                continue
            for reason in value.reasons:
                word = gap_word(reason, self.base_unit, self.pack_unit)
                if word not in out:
                    out.append(word)
        return out

    @property
    def blocking(self) -> dict | None:
        """The first of `blockers`; None for a row nothing blocks."""
        blockers = self.blockers
        return blockers[0] if blockers else None

    @property
    def blockers(self) -> list[dict]:
        """Every gap that keeps this offer out of the ranking, each with the question that clears it.

        One per gap word, in the order and with the dedupe `gaps` uses -- so
        the card says exactly what the overview's "Can't compare yet" flag
        says for the same quote. It showed only the first, and a quote
        missing freight AND its quantity read as missing freight on the card.
        """
        out, seen = [], set()
        for key in COMPARABILITY_FIELDS:
            value = self.figures.get(key)
            if not isinstance(value, Unconfirmed):
                continue
            for reason in value.reasons:
                label = gap_word(reason, self.base_unit, self.pack_unit)
                if label in seen:
                    continue
                seen.add(label)
                wanted = key_for_reason(reason) or ("kit_composition" if "kit composition" in reason else None)
                question = next((q for q in self.questions if q.key == wanted), None)
                fact = plain_reason(reason, self.base_unit, self.pack_unit)
                out.append(
                    {
                        "fact": fact[:1].upper() + fact[1:],
                        "question": (question.as_dict() if question is not None else None),
                        # What the gap is called, so the card can leave it out of
                        # "also not stated" -- and the specification's figure for
                        # it, said beside the blocker as a chip.
                        "label": label,
                        "spec": self.pack_requirement if wanted == "pack_spec" else "",
                        # The same, as the grey line the specification's other figures
                        # read in: "Sachets per carton: not stated (tender requires 150)".
                        "spec_line": (
                            f"{label[:1].upper() + label[1:]}: not stated "
                            f"(tender requires {self.pack_requirement_figure})"
                            if wanted == "pack_spec" and self.pack_requirement_figure
                            else ""
                        ),
                    }
                )
        return out

    @property
    def specification(self) -> dict | None:
        """This offer against the product's specification, summarised.

        A statement, not a ranking input: an offer that fails stays where its
        price puts it, and the row says what it fails. Whether a failing
        offer is still worth buying is the buyer's decision -- and the award
        freezes this summary beside the reason they gave.
        """
        if not self.compliance:
            return None
        outcomes = [r.outcome for r in self.compliance]
        failures = [r.message for r in self.compliance if r.outcome == FAIL]
        if failures:
            outcome, summary = FAIL, f"{len(failures)} of {len(outcomes)} fail"
        elif all(o == PASS for o in outcomes):
            outcome, summary = PASS, f"Meets all {len(outcomes)}"
        else:
            # Which requirements, by name: "Not stated: shelf life". A count
            # ("1 of 2 not stated") sent the reader looking for which.
            outcome, summary = "not_stated", "Not stated: " + ", ".join(
                requirement_label(r.field, r.requirement.get("unit", "")).lower()
                for r in self.compliance
                if r.outcome != PASS
            )
        # Where each answered figure came from, so "meets all 2" can be read
        # as "because the quote says so" or "because the trade item does" --
        # a supplier's statement and a product's specification sheet are not
        # the same evidence.
        stated_on_quote, confirmed_by_item, stated_values = [], [], []
        for result in self.compliance:
            if result.outcome == "not_stated":
                continue
            unit = result.requirement.get("unit", "")
            label = requirement_label(result.field, unit).lower()
            if result.spec_origin == "quote":
                stated_on_quote.append(label)
                # With its figure: "Sachets per carton: 150", which the reader
                # can hold against the specification beside it.
                if result.stated_value not in (None, ""):
                    figure = f"{decimal_string(result.stated_value)} {unit}".strip()
                    stated_values.append(
                        {"label": label, "text": f"{requirement_label(result.field, unit)}: {figure}"}
                    )
            elif result.spec_origin == "item":
                confirmed_by_item.append(label)
        return {
            "outcome": outcome,
            "summary": summary,
            # The requirements nobody has stated a figure for, by name: a
            # blocked card lists the ones its blocker is not, as not blocking.
            "not_stated": [
                requirement_label(r.field, r.requirement.get("unit", "")).lower()
                for r in self.compliance
                if r.outcome == "not_stated"
            ],
            "failures": failures,
            "stated_on_quote": stated_on_quote,
            "stated_values": stated_values,
            "confirmed_by_item": confirmed_by_item,
        }


@dataclass
class Comparison:
    tender_id: int
    columns: list[ComparisonColumn]
    comparable: list[ComparisonRow]
    blocked: list[ComparisonRow]
    generated_at: str
    ranked_by: str | None
    provisional: bool
    # Figures no row could compute, with the reason -- a gap in OUR
    # configuration rather than any supplier's answer. Reported separately
    # from `blocked` because conflating the two is what made a supplier who
    # answered everything look like the problem.
    unavailable: dict = field(default_factory=dict)
    commodity_name: str = ""
    # Offers whose kit contents are not the contents the tender buys. Terminal,
    # not missing information: there is nothing to ask the supplier and
    # nothing that could make them comparable, so they are neither "blocked"
    # (which reads as needs-info and makes the ranking provisional) nor ranked.
    not_comparable: list[ComparisonRow] = field(default_factory=list)
    # The contents the tender's line says it buys, when it says.
    tender_contents: list | None = None

    @property
    def all_rows(self) -> list[ComparisonRow]:
        """Everything, comparable first — for callers that want every supplier's
        row regardless of state (e.g. tender_outstanding_questions in Task 10).

        Named `all_rows`, not `rows`, on purpose: a ranked-table render that
        pulled from this instead of `.comparable` would seat a blocked
        supplier's partial figures beside a comparable one's — the exact false
        comparison this module exists to refuse. The name is deliberately in
        the way at the call site.
        """
        return [*self.comparable, *self.blocked, *self.not_comparable]

    @property
    def comparable_count(self) -> int:
        return len(self.comparable)

    @property
    def total_count(self) -> int:
        return len(self.comparable) + len(self.blocked) + len(self.not_comparable)

    def to_snapshot(self) -> dict:
        """A JSON-serialisable freeze, for award.comparison_snapshot.

        Carries the partition and the counts, not just the cells: an award has to
        stay reconstructible, and "we ranked two of five" is part of what was
        decided.
        """

        def row_dict(row):
            return {
                "quote_id": row.quote_id,
                "supplier_id": row.supplier_id,
                "supplier_name": row.supplier_name,
                "is_comparable": row.is_comparable,
                "figures": {key: to_wire(value) for key, value in row.figures.items()},
                "compliance": [
                    {
                        "field": r.field,
                        "outcome": r.outcome,
                        "message": r.message,
                        "spec_origin": r.spec_origin,
                        "claim_conflict": r.claim_conflict,
                    }
                    for r in row.compliance
                ],
                "questions": [f.as_dict() for f in row.questions],
                "composition": row.composition,
                "composition_unit": row.composition_unit,
                "item_name": row.item_name,
                "specification": row.specification,
                "blocking": row.blocking,
                "blockers": row.blockers,
                "quantity_quoted": row.quantity_quoted,
                "entered_by": row.entered_by,
                "supplier_awaiting_review": row.supplier_awaiting_review,
                "delivery": row.delivery,
                "landed_basis": row.landed_basis,
                "gaps": row.gaps,
                "base_unit": row.base_unit,
                "pack_unit": row.pack_unit,
                "as_quoted": row.as_quoted,
                "as_quoted_note": row.as_quoted_note,
                "received_on": row.received_on,
            }

        return {
            "tender_id": self.tender_id,
            "commodity_name": self.commodity_name,
            "generated_at": self.generated_at,
            "comparable_count": self.comparable_count,
            "total_count": self.total_count,
            "ranked_by": self.ranked_by,
            "unavailable": self.unavailable,
            "provisional": self.provisional,
            "columns": [
                {
                    "key": column.key,
                    "label": column.label,
                    "rankable": column.rankable,
                    "blocked_by": list(column.blocked_by),
                }
                for column in self.columns
            ],
            "comparable": [row_dict(row) for row in self.comparable],
            "blocked": [row_dict(row) for row in self.blocked],
            "not_comparable": [row_dict(row) for row in self.not_comparable],
            "tender_contents": self.tender_contents,
            # Flat view for consumers that legitimately need every supplier's
            # row regardless of state (e.g. Task 10's tender_outstanding_questions).
            # Named all_rows, not rows: see Comparison.all_rows's docstring.
            "all_rows": [row_dict(row) for row in self.all_rows],
        }


# The figures that only exist for something given as a course of treatment.
COURSE_FIGURES = frozenset({"usd_per_course", "usd_per_child_treated"})


def _is_live(quote: Quote) -> bool:
    return not quote.voided and not quote.superseded_by_quote_id


def _unavailable_figures(rows: list[ComparisonRow], figure_fields=FIGURE_FIELDS) -> dict:
    """Figures no supplier could supply, because the gap is OURS.

    Decided by the audience of the reasons, not by how many rows are missing
    the figure. An earlier version used "Unconfirmed on every row", which is
    trivially true when a tender has one quote -- so that supplier's own
    missing pack specification came back reported as our gap. `audience` is
    the domain's existing answer to whose a gap is, and it is the same table
    the supplier questions are built from.

    A figure appears here only when EVERY reason blocking it, on every row, is
    internal. One supplier-facing reason and it belongs in that supplier's
    question list instead.
    """
    if not rows:
        return {}
    out = {}
    for key in figure_fields:
        values = [row.figures.get(key) for row in rows]
        if not all(isinstance(value, Unconfirmed) for value in values):
            continue
        reasons: list[str] = []
        for value in values:
            for reason in value.reasons:
                if reason not in reasons:
                    reasons.append(reason)
        if all(audience_for_reason(reason) == "internal" for reason in reasons):
            out[key] = {"label": FIGURE_LABELS.get(key, key), "reasons": reasons}
    return out


def _composition(item) -> list | None:
    """A kit's contents in a canonical order, so two lists compare by value."""
    if item is None:
        return None
    return sorted(
        (
            {
                "commodity_slug": component.get("commodity_slug"),
                "quantity": decimal_string(Decimal(str(component.get("quantity")))),
                "base_unit": component.get("base_unit") or "",
            }
            for component in item.components or []
        ),
        key=lambda component: (component["commodity_slug"] or "", component["base_unit"]),
    )


def _composition_key(item):
    """What kits are compared on: the contents per base unit (see Item)."""
    if item is None:
        return None
    return tuple(item.components_per_base_unit())


def _composition_phrase(composition, unit="") -> str:
    if composition is None:
        return "an unnamed trade item, so its contents are not known"
    if not composition:
        return "a single product, not a kit"
    contents = " + ".join(f"{c['quantity']} {c['base_unit']} {c['commodity_slug']}" for c in composition)
    return f"{contents} per {unit}" if unit else contents


def _canonical(components) -> list:
    """A list of components in the same canonical shape `_composition` gives."""
    return sorted(
        (
            {
                "commodity_slug": component.get("commodity_slug"),
                "quantity": decimal_string(Decimal(str(component.get("quantity")))),
                "base_unit": component.get("base_unit") or "",
            }
            for component in components or []
        ),
        key=lambda component: (component["commodity_slug"] or "", component["base_unit"]),
    )


def _tender_contents(tender, commodity) -> list | None:
    """The kit contents this tender's line for `commodity` says it buys, if it says."""
    for line in getattr(tender, "lines", None) or []:
        if isinstance(line, dict) and line.get("commodity_slug") == commodity.slug and line.get("components"):
            return _canonical(line["components"])
    return None


def _refuse_other_contents(rows, wanted):
    """The tender has decided the contents: rank those, refuse the rest, and say why.

    This is the decision `_separate_differing_kits` asks us for, taken once on
    the tender rather than by voiding offers one at a time -- so an offer with
    other contents stays on the page, refused a ranking in plain view, instead
    of disappearing from it.

    Returns (kept, refused). A refusal is terminal: the offer carries no
    questions, because no answer from anyone makes other contents the ones
    the tender buys -- and asking the supplier their minimum order on it read
    as if one could.
    """
    keep, refused = [], []
    for row in rows:
        if row.composition is None or row.composition == wanted:
            keep.append(row)
            continue
        reason = (
            f"not the contents this tender buys: this offer is {_composition_phrase(row.composition)}; "
            f"the tender buys {_composition_phrase(wanted)}"
        )
        row.figures["landed_total_for_tender_quantity"] = merge(
            unconfirmed(reason), row.figures["landed_total_for_tender_quantity"]
        )
        row.is_comparable = False
        row.questions = []
        refused.append(row)
    return keep, refused


def _separate_differing_kits(comparable, blocked):
    """Kits are ranked only against kits holding the same contents.

    Two suppliers' "co-packs" at 38 and 40 dollars are not the same product
    at two prices if one holds ten zinc tablets and the other twelve, and a
    ranking that sets them side by side says they are. So when the
    comparable rows include a kit and disagree about what is inside, NONE of
    them is ranked -- rule 6 again: never rank across the partition. Picking
    the majority composition as the "real" one would be a judgement about
    what to buy, which is ours to make, so the question each row carries is
    addressed to us: decide the contents, then compare like with like.

    The ranking figure carries the reason, so the cell says why it is not a
    number rather than going blank.
    """
    compositions = {row.composition_key for row in comparable}
    has_a_kit = any(row.composition for row in comparable)
    if not has_a_kit or len(compositions) < 2:
        return comparable, blocked

    for row in comparable:
        others = sorted(
            {
                f"{other.supplier_name}: {_composition_phrase(other.composition, other.composition_unit)}"
                for other in comparable
                if other is not row and other.composition_key != row.composition_key
            }
        )
        this = _composition_phrase(row.composition, row.composition_unit)
        reason = f"kit composition differs: this offer is {this}; " + "; ".join(others)
        row.figures["landed_total_for_tender_quantity"] = merge(
            unconfirmed(reason), row.figures["landed_total_for_tender_quantity"]
        )
        row.questions = [
            *row.questions,
            MissingFact(
                key="kit_composition",
                question=(
                    f"This offer holds {this}, which differs from "
                    + "; ".join(others)
                    + ". Decide which contents the tender is for; offers are ranked only against "
                    "the same contents."
                ),
                audience=INTERNAL,
            ),
        ]
        row.is_comparable = False
    return [], [*blocked, *comparable]


def _ranking_key(comparable: list[ComparisonRow]) -> str | None:
    """Which figure decided the leader — a declared, tender-level fact.

    Landed total for this tender's own quantity is the only figure the
    ranking is ever chosen on, and it doubles as the comparability gate:
    `is_comparable` requires every one of FIGURE_FIELDS to be confirmed,
    landed_total_for_tender_quantity among them, and that figure is
    Unconfirmed for every quote whenever the tender has no line for this
    commodity. So a nonempty `comparable` list already proves the tender has
    a line — there is no reachable case where something is comparable AND
    the tender is missing one (Ruling 22 dropped the "usd_per_pack_normalized"
    fallback that used to cover that case: it was dead, because whenever the
    tender had no line, `comparable` was always empty and the fallback ranked
    nothing). When `comparable` IS empty, the honest answer is that there is
    nothing to rank by — `None`, not a quieter figure that never had a
    chance to actually order anything.
    """
    if not comparable:
        return None
    return "landed_total_for_tender_quantity"


def pack_requirement_words(commodity) -> str:
    """ "150 sachets per carton" (or "at least 150 ..."): the specification's pack figure, or ""."""
    figure = pack_requirement_figure(commodity)
    return f"{figure} {pack_words(commodity.base_unit, commodity.pack_unit)}" if figure else ""


def pack_requirement_figure(commodity) -> str:
    """ "150" (or "at least 150"): the specification's pack figure without its units, or ""."""
    from connect_labs.supply_chain.procurement.services.compliance import is_pack_count_field

    prefixes = {"==": "", ">=": "at least ", "<=": "at most ", ">": "more than ", "<": "fewer than "}
    for requirement in commodity.spec_requirements or []:
        if not isinstance(requirement, dict) or not is_pack_count_field(requirement.get("field"), commodity):
            continue
        prefix = prefixes.get(requirement.get("operator"))
        if prefix is None or requirement.get("value") in (None, ""):
            continue
        return f"{prefix}{requirement['value']}"
    return ""


def per_pack_note(figures, base_unit, pack_unit) -> str:
    """What a price stated per base unit comes to per pack, said after it.

    "= USD 43.50 per carton" when the pack is known; "(per carton once sachets
    per carton is known)" when the pack is the fact missing; otherwise the
    conversion is only named, since its figure cannot be given.
    """
    pack = unit_noun(pack_unit)
    per_pack = (figures or {}).get("usd_per_pack_normalized")
    amount = getattr(per_pack, "amount", None)
    if amount is not None:
        return f"= {getattr(per_pack, 'currency', 'USD') or 'USD'} {money_digits(amount)} per {pack}"
    reasons = getattr(per_pack, "reasons", ()) or ()
    if any(key_for_reason(reason) == "pack_spec" for reason in reasons):
        return f"(per {pack} once {pack_words(base_unit, pack_unit)} is known)"
    return f"(converted to per {pack} for the ranking)"


def as_quoted_words(quote, base_unit="", pack_unit="") -> str:
    """ "42.50 USD per carton": the price as the supplier stated it, before any conversion."""
    if quote.as_quoted_amount is None:
        return "no price stated"
    price = f"{money_digits(quote.as_quoted_amount)} {quote.as_quoted_currency or 'USD'}"
    unit = {"per_pack": pack_unit, "per_base_unit": base_unit}.get(quote.as_quoted_unit or "")
    if unit:
        return f"{price} per {unit_noun(unit)}"
    basis = (quote.as_quoted_unit or "").replace("_", " ")
    return f"{price} {basis}".strip()


def delivery_words(quote, tender) -> str:
    """How a bid reaches the buyer, as a reader says it."""
    if getattr(quote, "delivery_mode", "delivered") == "pickup":
        where = getattr(quote, "pickup_location", "") or "the supplier"
        return f"collected from {where}"
    places = tender.delivery_points or []
    keys = list(getattr(quote, "delivery_point_keys", None) or [])
    if keys:
        places = [p for p in places if p.get("key") in keys]
    elif len(places) > 1:
        return "places not stated"
    return f"to {destination_phrase(places)}" if places else ""


def landed_basis_words(quote, tender) -> str:
    """What a quote's landed figures rest on: where it is delivered, and how freight and duties were counted.

    "Delivered to Kano · freight included · duties included, per quote". Read
    the way `pricing._extras` reads it -- the quote's own basis first, its
    Incoterm where the quote is silent -- and says which of the two it was.
    Only the legs that were counted: a leg still unknown blocks the quote, and
    the blocked card says so.
    """
    parts, sources = [], []
    pickup = getattr(quote, "delivery_mode", "delivered") == "pickup"
    where = delivery_words(quote, tender)
    if where.startswith("to "):
        parts.append(f"Delivered {where}")
    elif where:
        parts.append(where[:1].upper() + where[1:])
    currency = quote.as_quoted_currency or "USD"
    from_term = dict(zip(("freight", "duties"), freight_and_duties_for_incoterm(quote.incoterm), strict=True))
    legs = [("duties", quote.duties_basis, quote.duties_amount)]
    if not pickup:
        legs.insert(0, ("freight", quote.freight_basis, quote.freight_amount))
    for label, basis, amount in legs:
        source = "quote"
        if basis not in ("included", "excluded"):
            basis, source = from_term[label], f"Incoterm {quote.incoterm}"
        if basis == "included":
            parts.append(f"{label} included")
        elif basis == "excluded" and amount is not None:
            parts.append(f"{label} {money_digits(amount)} {currency} added")
        else:
            continue
        if source not in sources:
            sources.append(source)
    if pickup and getattr(quote, "buyer_transport_amount", None) is not None:
        parts.append(f"our transport {money_digits(quote.buyer_transport_amount)} {currency} added")
    text = " · ".join(parts)
    return f"{text}, per {' and '.join(sources)}" if text and sources else text


def compare_tender(
    tender: Tender,
    commodity: Commodity,
    quotes: list[Quote],
    suppliers_by_id: dict,
    items_by_id: dict | None = None,
) -> Comparison:
    """Partition a tender's live quotes into comparable and blocked.

    A quote is comparable when every one of its figures is a Money. One
    Unconfirmed figure blocks it — not because the figure is useless, but
    because putting it in a ranking beside a complete quote is the false
    comparison this app exists to refuse.
    """
    items_by_id = items_by_id or {}
    nouns = figure_nouns(commodity.base_unit, commodity.pack_unit)

    comparable: list[ComparisonRow] = []
    blocked: list[ComparisonRow] = []
    # A test kit or a dispenser is not administered over a course, so "per
    # course" and "per child treated" are not unknown for it -- they do not
    # exist. Offering them as columns, and asking us for a treatment protocol
    # to fill them, reported a category's absence of the concept as our gap;
    # the checks feed and the product page already knew better.
    course_applies = course_applies_to_category(commodity.category)
    figure_fields = [key for key in FIGURE_FIELDS if course_applies or key not in COURSE_FIGURES]
    pack_requirement = pack_requirement_words(commodity)
    pack_figure = pack_requirement_figure(commodity)

    for quote in quotes:
        if not _is_live(quote):
            continue
        supplier = suppliers_by_id.get(quote.supplier_id)
        item = items_by_id.get(quote.item_id) if quote.item_id else None
        figures = compute_figures(quote, commodity, tender, item=item).as_dict()
        row = ComparisonRow(
            quote_id=quote.id,
            supplier_id=quote.supplier_id,
            supplier_name=supplier.name if supplier else f"supplier {quote.supplier_id}",
            figures=figures,
            compliance=check_compliance(quote, commodity, item=item),
            questions=missing_facts(quote, commodity, tender, item=item),
            # Only the figures a supplier or the tender determines. See
            # COMPARABILITY_FIELDS: gating on the course figures blocked
            # suppliers for our own missing ration table.
            is_comparable=not any(isinstance(figures[key], Unconfirmed) for key in COMPARABILITY_FIELDS),
            composition=_composition(item) if quote.item_id else None,
            item_name=item.name if item is not None else "",
            composition_unit=item.components_unit if item is not None and item.components else "",
            composition_key=_composition_key(item) if quote.item_id else None,
            entered_by=getattr(quote, "entered_by", "program") or "program",
            supplier_awaiting_review=bool(getattr(supplier, "awaiting_review", False)),
            delivery=delivery_words(quote, tender),
            base_unit=(item.base_unit if item is not None and item.base_unit else "") or commodity.base_unit or "",
            pack_unit=(item.pack_unit if item is not None and item.pack_unit else "") or commodity.pack_unit or "",
            received_on=str(quote.received_on)[:10] if quote.received_on else "",
            pack_requirement=pack_requirement,
            pack_requirement_figure=pack_figure,
        )
        row.as_quoted = as_quoted_words(quote, row.base_unit, row.pack_unit)
        if quote.as_quoted_amount is not None and quote.as_quoted_unit == "per_base_unit" and row.pack_unit:
            row.as_quoted_note = per_pack_note(figures, row.base_unit, row.pack_unit)
        row.landed_basis = landed_basis_words(quote, tender)
        if quote.quantity_basis is not None and quote.quantity_basis_unit:
            row.quantity_quoted = quantity_phrase(quote.quantity_basis, quote.quantity_basis_unit)
        if not course_applies:
            row.figures = {key: value for key, value in figures.items() if key not in COURSE_FIGURES}
            row.questions = [q for q in row.questions if q.key != "course_definition"]
        (comparable if row.is_comparable else blocked).append(row)

    wanted = _tender_contents(tender, commodity)
    not_comparable: list[ComparisonRow] = []
    if wanted:
        comparable, refused_ranked = _refuse_other_contents(comparable, wanted)
        blocked, refused_blocked = _refuse_other_contents(blocked, wanted)
        not_comparable = [*refused_ranked, *refused_blocked]
    comparable, blocked = _separate_differing_kits(comparable, blocked)

    ranked_by = _ranking_key(comparable)
    # Cheapest first: comparable[0] is the leader the screen names, and it is
    # provisional (below) whenever anything was left out of the ranking. Every
    # comparable row is guaranteed a Money here — is_comparable already
    # required all of FIGURE_FIELDS, to be confirmed. Sorting an empty list
    # never evaluates the key function, so a `None` ranked_by (nothing
    # comparable) is never actually indexed.
    if ranked_by is not None:
        comparable.sort(key=lambda row: row.figures[ranked_by].amount)

    unavailable = _unavailable_figures(comparable + blocked + not_comparable, figure_fields)
    columns: list[ComparisonColumn] = []
    for key in figure_fields:
        # blocked_by names every supplier missing this figure, so the template can
        # say what a blocked row is short of — but rankability is judged over the
        # comparable subset alone (rule 6 as amended). Deduped: a supplier with
        # two live blocked quotes on one tender must not appear twice.
        short = tuple(
            dict.fromkeys(
                row.supplier_name
                for row in (*comparable, *blocked, *not_comparable)
                if isinstance(row.figures.get(key), Unconfirmed)
            )
        )
        columns.append(
            ComparisonColumn(
                key=key,
                label=FIGURE_LABELS[key].format(**nouns),
                # A column no row could compute cannot order anything, however
                # many rows are otherwise comparable. Saying `rankable` of it
                # would offer a sort that silently does nothing.
                rankable=bool(comparable) and key not in unavailable,
                blocked_by=short,
            )
        )

    return Comparison(
        tender_id=tender.id,
        columns=columns,
        comparable=comparable,
        blocked=blocked,
        generated_at=datetime.now(UTC).isoformat(),
        ranked_by=ranked_by,
        # Only what could still be compared makes a ranking provisional: an
        # offer with other contents can never beat it.
        provisional=bool(blocked),
        unavailable=unavailable,
        commodity_name=commodity.name,
        not_comparable=not_comparable,
        tender_contents=wanted,
    )
