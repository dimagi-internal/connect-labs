"""The invoices Connect would hold had every payment for an opportunity gone through it.

Connect records what an opportunity paid per service, and the custom invoices raised in it. Dimagi
also pays organisations outside Connect (setup, milestones, close-out) and buys supplies for a
programme directly (commodities, freight, storage). Those payments are in the accounting ledger,
listed line by line on the costs ledger sheet (``pulse.ledger``) with the organisation and programme
each paid for. This module turns them into ``PulseReconstructedInvoice`` rows: per opportunity, the
entry Connect is missing.

The rules, in order (they are the README of the sheet too):

1. **A pair** is a programme (as the ledger names it) and an organisation. Its opportunities are the
   organisation's opportunities in the Connect programmes the Programmes tab lists for it.
2. **What Connect missed for a pair** is what the ledger paid the organisation (service and fixed)
   less what Connect recorded for its opportunities (``costs.recorded_usd``: per-service pay, Connect's
   custom invoices, costs entered in labs). Only when every opportunity of the pair is closed: while one
   is running, Connect's figure is still growing and the gap means nothing. A negative gap (Connect
   recorded more than the ledger paid) is reported, never invoiced.
3. **It is split** over the pair's opportunities by people reached; by recorded cost when the pair
   reached nobody; equally when neither is known.
4. **Direct costs** booked to a programme are split over all that programme's opportunities (every
   organisation) by people reached, the same way. Only closed opportunities are invoiced; an active
   opportunity's share is reported as held.
5. **A line that cannot be placed** (no Connect organisation, no programme, a programme the Programmes
   tab does not map, an organisation with no opportunity in it) is listed as unattributed, never spread.

Pure: every input is passed in and nothing here touches the database, so the rules are tested on
plain values (``pulse/tests/test_reconstruct.py``). ``pulse.ledger`` saves what this returns.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")

LLO_CLASSES = ("llo_service", "llo_fixed")
DIRECT = "direct_cost"

KIND_LLO = "llo_payment"
KIND_DIRECT = "direct_cost"

WHY_NO_ORG = "no Connect organisation"
WHY_NO_PROGRAMME = "no programme"
WHY_PROGRAMME_UNMAPPED = "programme not on the Programmes tab"
WHY_NO_OPPORTUNITY = "the organisation has no opportunity in this programme"
WHY_PROGRAMME_EMPTY = "no Connect opportunity in this programme"


@dataclass(frozen=True)
class Line:
    key: str
    usd: Decimal
    line_class: str
    org_slug: str = ""
    programme: str = ""
    date: dt.date | None = None


@dataclass(frozen=True)
class Opp:
    id: int
    org_slug: str
    program_id: int | None
    active: bool
    recorded_usd: Decimal
    reached: int = 0


@dataclass
class Invoice:
    opportunity_id: int
    kind: str
    amount_usd: Decimal
    sources: dict[str, Decimal]
    basis: dict
    start_date: dt.date | None = None
    end_date: dt.date | None = None

    @property
    def invoice_number(self) -> str:
        return invoice_number(self.opportunity_id, self.kind)


@dataclass
class Result:
    invoices: list[Invoice] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)
    programmes: list[dict] = field(default_factory=list)
    unattributed: list[dict] = field(default_factory=list)


def invoice_number(opportunity_id: int, kind: str) -> str:
    """Stable across imports, and plainly not one of Connect's (Connect's are 10 hex characters)."""
    digest = hashlib.sha1(f"{opportunity_id}:{kind}".encode()).hexdigest()[:8].upper()
    return f"LABS-{digest}"


def _weights(opps: list[Opp]) -> tuple[dict[int, Decimal], str]:
    """Each opportunity's share of a split, and the basis it was split on."""
    reached = sum(o.reached for o in opps)
    if reached > 0:
        return {o.id: Decimal(o.reached) / Decimal(reached) for o in opps}, "people reached"
    recorded = sum((o.recorded_usd for o in opps), ZERO)
    if recorded > 0:
        return {o.id: o.recorded_usd / recorded for o in opps}, "recorded cost"
    return {o.id: Decimal(1) / Decimal(len(opps)) for o in opps}, "equally"


def _span(lines: list[Line]) -> tuple[dt.date | None, dt.date | None]:
    dates = [ln.date for ln in lines if ln.date]
    return (min(dates), max(dates)) if dates else (None, None)


def _add(invoices: dict, opp_id: int, kind: str, amount: Decimal, sources: dict, basis: dict, lines: list[Line]):
    inv = invoices.get((opp_id, kind))
    if inv is None:
        inv = invoices[(opp_id, kind)] = Invoice(opp_id, kind, ZERO, {}, {"parts": []})
    inv.amount_usd += amount
    for key, usd in sources.items():
        inv.sources[key] = inv.sources.get(key, ZERO) + usd
    inv.basis["parts"].append(basis)
    start, end = _span(lines)
    if start and (inv.start_date is None or start < inv.start_date):
        inv.start_date = start
    if end and (inv.end_date is None or end > inv.end_date):
        inv.end_date = end


def build(lines: list[Line], programmes: dict[str, list[int]], opps: list[Opp]) -> Result:
    """The reconstructed invoices, the pair and programme tables, and what could not be placed."""
    result = Result()
    invoices: dict[tuple[int, str], Invoice] = {}

    by_pair: dict[tuple[str, str], list[Line]] = defaultdict(list)
    by_programme: dict[str, list[Line]] = defaultdict(list)

    def unplaced(line: Line, why: str) -> None:
        result.unattributed.append(
            {"line_key": line.key, "usd": line.usd, "class": line.line_class, "org_slug": line.org_slug,
             "programme": line.programme, "why": why}
        )  # fmt: skip

    def programme_opps(name: str) -> list[Opp]:
        ids = set(programmes.get(name) or ())
        return [o for o in opps if o.program_id in ids]

    for line in lines:
        if line.line_class not in LLO_CLASSES and line.line_class != DIRECT:
            continue
        if not line.programme:
            unplaced(line, WHY_NO_PROGRAMME)
            continue
        if line.programme not in programmes:
            unplaced(line, WHY_PROGRAMME_UNMAPPED)
            continue
        if line.line_class == DIRECT:
            if not programme_opps(line.programme):
                unplaced(line, WHY_PROGRAMME_EMPTY)
                continue
            by_programme[line.programme].append(line)
            continue
        if not line.org_slug:
            unplaced(line, WHY_NO_ORG)
            continue
        if not [o for o in programme_opps(line.programme) if o.org_slug == line.org_slug]:
            unplaced(line, WHY_NO_OPPORTUNITY)
            continue
        by_pair[(line.programme, line.org_slug)].append(line)

    for (programme, org_slug), pair_lines in sorted(by_pair.items()):
        pair_opps = [o for o in programme_opps(programme) if o.org_slug == org_slug]
        paid = sum((ln.usd for ln in pair_lines), ZERO)
        recorded = sum((o.recorded_usd for o in pair_opps), ZERO)
        gap = paid - recorded
        closed = all(not o.active for o in pair_opps)
        row = {
            "programme": programme,
            "org_slug": org_slug,
            "opportunities": len(pair_opps),
            "closed": sum(1 for o in pair_opps if not o.active),
            "ledger_service_usd": sum((ln.usd for ln in pair_lines if ln.line_class == "llo_service"), ZERO),
            "ledger_fixed_usd": sum((ln.usd for ln in pair_lines if ln.line_class == "llo_fixed"), ZERO),
            "ledger_usd": paid,
            "connect_usd": recorded,
            "gap_usd": gap,
            "invoiced_usd": ZERO,
            "status": "",
        }
        if not closed:
            row["status"] = "held: an opportunity is still running"
        elif gap <= 0:
            row["status"] = "nothing missing" if gap == 0 else "Connect recorded more than the ledger paid"
        else:
            weights, how = _weights(pair_opps)
            for opp in pair_opps:
                share = gap * weights[opp.id]
                sources = {ln.key: ln.usd * gap / paid * weights[opp.id] for ln in pair_lines}
                basis = {"rule": "pair gap", "programme": programme, "org_slug": org_slug, "paid_usd": str(paid),
                         "recorded_usd": str(recorded), "gap_usd": str(gap), "split_by": how,
                         "share": str(weights[opp.id].quantize(Decimal("0.0001")))}  # fmt: skip
                _add(invoices, opp.id, KIND_LLO, share, sources, basis, pair_lines)
            row["invoiced_usd"] = gap
            row["status"] = "invoiced"
        result.pairs.append(row)

    for programme, prog_lines in sorted(by_programme.items()):
        prog_opps = programme_opps(programme)
        total = sum((ln.usd for ln in prog_lines), ZERO)
        weights, how = _weights(prog_opps)
        invoiced = held = ZERO
        for opp in prog_opps:
            share = total * weights[opp.id]
            if opp.active:
                held += share
                continue
            sources = {ln.key: ln.usd * weights[opp.id] for ln in prog_lines}
            basis = {"rule": "programme direct cost", "programme": programme, "total_usd": str(total),
                     "split_by": how, "share": str(weights[opp.id].quantize(Decimal("0.0001")))}  # fmt: skip
            _add(invoices, opp.id, KIND_DIRECT, share, sources, basis, prog_lines)
            invoiced += share
        result.programmes.append(
            {"programme": programme, "opportunities": len(prog_opps), "direct_cost_usd": total,
             "invoiced_usd": invoiced, "held_for_active_usd": held}
        )  # fmt: skip

    for inv in sorted(invoices.values(), key=lambda i: (i.opportunity_id, i.kind)):
        inv.amount_usd = inv.amount_usd.quantize(CENT, rounding=ROUND_HALF_UP)
        inv.sources = {k: v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) for k, v in inv.sources.items()}
        if inv.amount_usd > 0:
            result.invoices.append(inv)
    return result
