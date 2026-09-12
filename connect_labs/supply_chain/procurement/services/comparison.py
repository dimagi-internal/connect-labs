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

from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.procurement.services.pricing import FIGURE_FIELDS, FIGURE_LABELS, compute_figures
from connect_labs.supply_chain.procurement.services.questions import missing_facts
from connect_labs.supply_chain.proxies import CommodityRecord, QuoteRecord, RoundRecord
from connect_labs.supply_chain.values import Unconfirmed, to_wire


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


@dataclass
class Comparison:
    round_id: int
    columns: list[ComparisonColumn]
    comparable: list[ComparisonRow]
    blocked: list[ComparisonRow]
    generated_at: str
    ranked_by: str | None
    provisional: bool

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
        return [*self.comparable, *self.blocked]

    @property
    def comparable_count(self) -> int:
        return len(self.comparable)

    @property
    def total_count(self) -> int:
        return len(self.comparable) + len(self.blocked)

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
            }

        return {
            "round_id": self.round_id,
            "generated_at": self.generated_at,
            "comparable_count": self.comparable_count,
            "total_count": self.total_count,
            "ranked_by": self.ranked_by,
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
            # Flat view for consumers that legitimately need every supplier's
            # row regardless of state (e.g. Task 10's round_outstanding_questions).
            # Named all_rows, not rows: see Comparison.all_rows's docstring.
            "all_rows": [row_dict(row) for row in self.all_rows],
        }


def _is_live(quote: QuoteRecord) -> bool:
    return not quote.voided and not quote.superseded_by_quote_id


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
    round_: RoundRecord,
    commodity: CommodityRecord,
    quotes: list[QuoteRecord],
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
            is_comparable=not any(isinstance(v, Unconfirmed) for v in figures.values()),
        )
        (comparable if row.is_comparable else blocked).append(row)

    ranked_by = _ranking_key(comparable)
    # Cheapest first: comparable[0] is the leader the screen names, and it is
    # provisional (below) whenever anything was left out of the ranking. Every
    # comparable row is guaranteed a Money here — is_comparable already
    # required all of FIGURE_FIELDS, to be confirmed. Sorting an empty list
    # never evaluates the key function, so a `None` ranked_by (nothing
    # comparable) is never actually indexed.
    if ranked_by is not None:
        comparable.sort(key=lambda row: row.figures[ranked_by].amount)

    columns: list[ComparisonColumn] = []
    for key in FIGURE_FIELDS:
        # blocked_by names every supplier missing this figure, so the template can
        # say what a blocked row is short of — but rankability is judged over the
        # comparable subset alone (rule 6 as amended). Deduped: a supplier with
        # two live blocked quotes on one round must not appear twice.
        short = tuple(
            dict.fromkeys(
                row.supplier_name for row in (*comparable, *blocked) if isinstance(row.figures.get(key), Unconfirmed)
            )
        )
        columns.append(
            ComparisonColumn(
                key=key,
                label=FIGURE_LABELS[key].format(**nouns),
                rankable=bool(comparable),
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
        provisional=bool(blocked),
    )
