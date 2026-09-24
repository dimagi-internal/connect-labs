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

Ranking is decided here, not in a template: `compare_round` sorts the
comparable rows by a declared `ranked_by` key (landed total for this round's
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

from connect_labs.supply_chain.models import Commodity, Quote, Round
from connect_labs.supply_chain.procurement.services.compliance import FAIL, PASS, check_compliance
from connect_labs.supply_chain.procurement.services.pricing import (
    COMPARABILITY_FIELDS,
    FIGURE_FIELDS,
    FIGURE_LABELS,
    compute_figures,
)
from connect_labs.supply_chain.procurement.services.questions import (
    INTERNAL,
    MissingFact,
    audience_for_reason,
    missing_facts,
)
from connect_labs.supply_chain.records import course_applies_to_category
from connect_labs.supply_chain.values import Unconfirmed, decimal_string, merge, to_wire, unconfirmed


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
            unstated = len([o for o in outcomes if o != PASS])
            outcome, summary = "not_stated", f"{unstated} of {len(outcomes)} not stated"
        return {"outcome": outcome, "summary": summary, "failures": failures}


@dataclass
class Comparison:
    round_id: int
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
    # Offers whose kit contents are not the contents the round buys. Terminal,
    # not missing information: there is nothing to ask the supplier and
    # nothing that could make them comparable, so they are neither "blocked"
    # (which reads as needs-info and makes the ranking provisional) nor ranked.
    not_comparable: list[ComparisonRow] = field(default_factory=list)
    # The contents the round's line says it buys, when it says.
    round_contents: list | None = None

    @property
    def all_rows(self) -> list[ComparisonRow]:
        """Everything, comparable first — for callers that want every supplier's
        row regardless of state (e.g. round_outstanding_questions in Task 10).

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
                "questions": [{"key": f.key, "question": f.question, "audience": f.audience} for f in row.questions],
                "composition": row.composition,
                "composition_unit": row.composition_unit,
                "item_name": row.item_name,
                "specification": row.specification,
            }

        return {
            "round_id": self.round_id,
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
            "round_contents": self.round_contents,
            # Flat view for consumers that legitimately need every supplier's
            # row regardless of state (e.g. Task 10's round_outstanding_questions).
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
    trivially true when a round has one quote -- so that supplier's own
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


def _round_contents(round_, commodity) -> list | None:
    """The kit contents this round's line for `commodity` says it buys, if it says."""
    for line in getattr(round_, "lines", None) or []:
        if isinstance(line, dict) and line.get("commodity_slug") == commodity.slug and line.get("components"):
            return _canonical(line["components"])
    return None


def _refuse_other_contents(rows, wanted):
    """The round has decided the contents: rank those, refuse the rest, and say why.

    This is the decision `_separate_differing_kits` asks us for, taken once on
    the round rather than by voiding offers one at a time -- so an offer with
    other contents stays on the page, refused a ranking in plain view, instead
    of disappearing from it.

    Returns (kept, refused). A refusal is terminal: the offer carries no
    questions, because no answer from anyone makes other contents the ones
    the round buys -- and asking the supplier their minimum order on it read
    as if one could.
    """
    keep, refused = [], []
    for row in rows:
        if row.composition is None or row.composition == wanted:
            keep.append(row)
            continue
        reason = (
            f"not the contents this round buys: this offer is {_composition_phrase(row.composition)}; "
            f"the round buys {_composition_phrase(wanted)}"
        )
        row.figures["landed_total_for_round_quantity"] = merge(
            unconfirmed(reason), row.figures["landed_total_for_round_quantity"]
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
        row.figures["landed_total_for_round_quantity"] = merge(
            unconfirmed(reason), row.figures["landed_total_for_round_quantity"]
        )
        row.questions = [
            *row.questions,
            MissingFact(
                key="kit_composition",
                question=(
                    f"This offer holds {this}, which differs from "
                    + "; ".join(others)
                    + ". Decide which contents the round is for; offers are ranked only against "
                    "the same contents."
                ),
                audience=INTERNAL,
            ),
        ]
        row.is_comparable = False
    return [], [*blocked, *comparable]


def _ranking_key(comparable: list[ComparisonRow]) -> str | None:
    """Which figure decided the leader — a declared, round-level fact.

    Landed total for this round's own quantity is the only figure the
    ranking is ever chosen on, and it doubles as the comparability gate:
    `is_comparable` requires every one of FIGURE_FIELDS to be confirmed,
    landed_total_for_round_quantity among them, and that figure is
    Unconfirmed for every quote whenever the round has no line for this
    commodity. So a nonempty `comparable` list already proves the round has
    a line — there is no reachable case where something is comparable AND
    the round is missing one (Ruling 22 dropped the "usd_per_pack_normalized"
    fallback that used to cover that case: it was dead, because whenever the
    round had no line, `comparable` was always empty and the fallback ranked
    nothing). When `comparable` IS empty, the honest answer is that there is
    nothing to rank by — `None`, not a quieter figure that never had a
    chance to actually order anything.
    """
    if not comparable:
        return None
    return "landed_total_for_round_quantity"


def compare_round(
    round_: Round,
    commodity: Commodity,
    quotes: list[Quote],
    suppliers_by_id: dict,
    items_by_id: dict | None = None,
) -> Comparison:
    """Partition a round's live quotes into comparable and blocked.

    A quote is comparable when every one of its figures is a Money. One
    Unconfirmed figure blocks it — not because the figure is useless, but
    because putting it in a ranking beside a complete quote is the false
    comparison this app exists to refuse.
    """
    items_by_id = items_by_id or {}
    nouns = {
        "base_unit": commodity.base_unit or "unit",
        "pack_unit": commodity.pack_unit or "pack",
    }

    comparable: list[ComparisonRow] = []
    blocked: list[ComparisonRow] = []
    # A test kit or a dispenser is not administered over a course, so "per
    # course" and "per child treated" are not unknown for it -- they do not
    # exist. Offering them as columns, and asking us for a treatment protocol
    # to fill them, reported a category's absence of the concept as our gap;
    # the checks feed and the product page already knew better.
    course_applies = course_applies_to_category(commodity.category)
    figure_fields = [key for key in FIGURE_FIELDS if course_applies or key not in COURSE_FIGURES]

    for quote in quotes:
        if not _is_live(quote):
            continue
        supplier = suppliers_by_id.get(quote.supplier_id)
        item = items_by_id.get(quote.item_id) if quote.item_id else None
        figures = compute_figures(quote, commodity, round_, item=item).as_dict()
        row = ComparisonRow(
            quote_id=quote.id,
            supplier_id=quote.supplier_id,
            supplier_name=supplier.name if supplier else f"supplier {quote.supplier_id}",
            figures=figures,
            compliance=check_compliance(quote, commodity, item=item),
            questions=missing_facts(quote, commodity, round_, item=item),
            # Only the figures a supplier or the round determines. See
            # COMPARABILITY_FIELDS: gating on the course figures blocked
            # suppliers for our own missing ration table.
            is_comparable=not any(isinstance(figures[key], Unconfirmed) for key in COMPARABILITY_FIELDS),
            composition=_composition(item) if quote.item_id else None,
            item_name=item.name if item is not None else "",
            composition_unit=item.components_unit if item is not None and item.components else "",
            composition_key=_composition_key(item) if quote.item_id else None,
        )
        if not course_applies:
            row.figures = {key: value for key, value in figures.items() if key not in COURSE_FIGURES}
            row.questions = [q for q in row.questions if q.key != "course_definition"]
        (comparable if row.is_comparable else blocked).append(row)

    wanted = _round_contents(round_, commodity)
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
        # two live blocked quotes on one round must not appear twice.
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
        round_id=round_.id,
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
        round_contents=wanted,
    )
