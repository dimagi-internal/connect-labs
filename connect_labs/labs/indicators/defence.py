"""Cross-checks and alternatives — the part of a methodology that argues with itself.

A methodology that only explains what we did is a description, not a defence. Two
questions decide whether a number survives contact with a sceptical reader, and
neither is answered by describing the pipeline:

  * **Does it survive a sanity check?** Every figure here has an independent
    second opinion already sitting in the database — births derived a second way
    from fertility, population from a second provider, an implied birth rate that
    can be checked against what is demographically possible, and the agency's own
    national estimate. Agreement is worth stating; disagreement is worth stating
    *more*, because a reader who finds it first will not believe the rest.

  * **Would another method have been better?** The honest answer is sometimes
    yes. Running the alternatives and printing what they would have produced
    costs a few seconds and converts "we chose this method" into "we chose this
    method and here is what the others say", which is the difference between an
    assertion and an argument.

Both sections are computed per selection, never boilerplate. A canned paragraph
saying "our figures were validated" is worse than none — it spends credibility
without earning it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.models import IndicatorValue

#: Crude birth rate, live births per 1,000 people per year. Sub-Saharan Africa
#: sits roughly 30-45; nowhere on earth sustains 60, and 15 would be European.
#: A derived birth total that implies something outside this band is wrong in a
#: way no amount of provenance can rescue, so it is checked explicitly.
CBR_PLAUSIBLE = (22.0, 52.0)

#: Divergence between two independent estimates that we call worth watching.
#: Not an error bar — the two methods measure the same thing differently, so some
#: spread is expected — but past this a reader deserves to be told.
DIVERGENCE_WATCH = 0.25


@dataclass
class Check:
    name: str
    verdict: str  # consistent | worth watching | inconsistent | not available
    detail: str


def _pairs(boundary_ids: list[int], a: str, b: str) -> list[tuple[float, float]]:
    """Values for two indicators on the boundaries that carry both."""
    got: dict[int, dict[str, float]] = {}
    for bid, ind, val in IndicatorValue.objects.filter(boundary_id__in=boundary_ids, indicator__in=(a, b)).values_list(
        "boundary_id", "indicator", "value"
    ):
        got.setdefault(bid, {})[ind] = val
    return [(v[a], v[b]) for v in got.values() if a in v and b in v and v[b]]


def _source_pairs(boundary_ids: list[int], indicator: str, src_a: str, src_b: str) -> list[tuple[float, float]]:
    """The same indicator from two providers, on boundaries carrying both."""
    got: dict[int, dict[str, float]] = {}
    for bid, src, val in IndicatorValue.objects.filter(
        boundary_id__in=boundary_ids, indicator=indicator, source__in=(src_a, src_b)
    ).values_list("boundary_id", "source", "value"):
        got.setdefault(bid, {})[src] = val
    return [(v[src_a], v[src_b]) for v in got.values() if src_a in v and src_b in v and v[src_b]]


def _spread(pairs: list[tuple[float, float]]) -> tuple[float, int]:
    """Median relative gap, and how many pairs exceed the watch threshold."""
    rel = [abs(a - b) / b for a, b in pairs if b]
    return (statistics.median(rel) if rel else 0.0, sum(1 for r in rel if r > DIVERGENCE_WATCH))


def _verdict(median_gap: float, over: int, total: int) -> str:
    """A comfortable median hides a fat tail.

    Reporting "consistent" off the median alone called a set with 13 of 28 areas
    diverging by more than a quarter agreement. The share matters as much as the
    centre, so both decide the verdict.
    """
    share = over / total if total else 0.0
    if median_gap > DIVERGENCE_WATCH or share > 0.5:
        return "inconsistent"
    if share > 0.2:
        return "worth watching"
    return "consistent"


def sanity_checks(selection) -> list[Check]:
    """Independent second opinions on the numbers this selection reports."""
    ids = [a.boundary.pk for a in selection.areas]
    checks: list[Check] = []

    # 1. Births, derived twice from different inputs.
    pairs = _pairs(ids, "births", "births_fertility_check")
    if pairs:
        med, over = _spread(pairs)
        checks.append(
            Check(
                "Births, derived two independent ways",
                _verdict(med, over, len(pairs)),
                f"The headline births figure comes from the under-1 population divided by infant "
                f"survivorship. Deriving it again from a completely different input — women aged "
                f"15-49 and the total fertility rate — gives a median difference of {med:.0%} across "
                f"{len(pairs)} areas, with {over} differing by more than {DIVERGENCE_WATCH:.0%}. "
                "The two share no measurement, so agreement is meaningful.",
            )
        )
    else:
        checks.append(
            Check(
                "Births, derived two independent ways",
                "not available",
                "No area in this selection carries both derivations.",
            )
        )

    # 2. Population, two providers.
    pop_pairs = _source_pairs(ids, "pop_total", "worldpop", "hapi")
    if pop_pairs:
        med, over = _spread(pop_pairs)
        checks.append(
            Check(
                "Population, two independent providers",
                _verdict(med, over, len(pop_pairs)),
                f"WorldPop's gridded estimate and HDX HAPI's administrative figures are produced by "
                f"different organisations from different inputs. Where both cover the same area "
                f"({len(pop_pairs)} of them) they differ by a median {med:.0%}, with {over} beyond "
                f"{DIVERGENCE_WATCH:.0%}.",
            )
        )
    else:
        checks.append(
            Check(
                "Population, two independent providers",
                "not available",
                "No area in this selection is covered by both providers.",
            )
        )

    # 3. The implied crude birth rate — a check that needs no second source at all.
    births = selection.totals.get("births")
    pop = selection.totals.get("pop_total")
    if births and pop:
        cbr = births / pop * 1000
        ok = CBR_PLAUSIBLE[0] <= cbr <= CBR_PLAUSIBLE[1]
        checks.append(
            Check(
                "Implied crude birth rate",
                "consistent" if ok else "inconsistent",
                f"Dividing the derived births by the population implies {cbr:.1f} live births per "
                f"1,000 people per year. Sub-Saharan Africa runs roughly 30-45, and no population "
                f"sustains a rate outside {CBR_PLAUSIBLE[0]:.0f}-{CBR_PLAUSIBLE[1]:.0f}. "
                + ("This lands where it should." if ok else "This does NOT, and the births total should not be used."),
            )
        )

    # 4. Coherence against the agency's own national estimate.
    #
    # The naive form of this check — "every selected area should be worse than its
    # country" — is wrong, and cried wolf on the first real query: with a threshold
    # of 80 against a Nigerian national rate near 110, most selected areas are
    # legitimately *better* than the national average. The threshold decides what
    # the relationship ought to be, so the check has to know it. A check that
    # misfires is worse than no check, because it teaches the reader to skip them.
    national = {
        iso: val
        for iso, val in IndicatorValue.objects.filter(
            indicator=selection.indicator, source="igme", admin_level=0
        ).values_list("iso_code", "value")
    }
    if national:
        lower_is_worse = selection.indicator in measures.LOWER_IS_WORSE
        compared = incoherent = 0
        for area in selection.areas:
            nat = national.get(area.iso_code)
            resolved = area.values.get(selection.indicator)
            if nat is None or resolved is None:
                continue
            compared += 1
            # Only when the threshold is itself at least as severe as the national
            # figure must every selected area also beat the national figure.
            threshold_is_severe = selection.threshold <= nat if lower_is_worse else selection.threshold >= nat
            if not threshold_is_severe:
                continue
            if (resolved.value > nat) if lower_is_worse else (resolved.value < nat):
                incoherent += 1
        if compared:
            worse_than_national = sum(
                1
                for area in selection.areas
                if (nat := national.get(area.iso_code)) is not None
                and (r := area.values.get(selection.indicator)) is not None
                and ((r.value < nat) if lower_is_worse else (r.value > nat))
            )
            checks.append(
                Check(
                    "Coherent with UN IGME's national estimates",
                    "consistent" if incoherent == 0 else "inconsistent",
                    f"Of {compared} selected areas that can be compared against their own country's "
                    f"national estimate — which comes from UN IGME, not from anything used to build "
                    f"this table — {worse_than_national} are worse than the national figure and "
                    f"{compared - worse_than_national} are better. Both are expected: the threshold "
                    "here is not the national average, so an area can clear it while still sitting on "
                    "the better side of its country. What would be incoherent is an area selected "
                    "under a threshold at least as severe as the national figure yet scoring better "
                    f"than it, and there are {incoherent} of those.",
                )
            )
    return checks


def alternatives(selection, *, run) -> list[dict]:
    """What the other methods that can answer this indicator would have said.

    ``run`` is injected rather than imported so this module never depends on the
    resolver's import graph, and so a caller can skip the extra queries.
    """
    from connect_labs.labs.indicators import availability, methods

    out = []
    for method in methods.METHODS.values():
        if method.code == selection.method or method.resolution.value != selection.resolution:
            continue
        supported = [r for r in availability.for_method(method, selection.indicator) if r.available]
        if not supported:
            out.append(
                {
                    "method": method.code,
                    "label": method.label,
                    "countries": 0,
                    "units": None,
                    "note": "cannot answer this indicator at all",
                }
            )
            continue
        other = run(method.code)
        out.append(
            {
                "method": method.code,
                "label": method.label,
                "countries": len(supported),
                "units": other.unit_count,
                "areas": other.area_count,
                # The quantity the question is about, not always births. For a
                # coverage indicator that is the unreached count; births is the
                # headline of a mortality question and reports the wrong spread
                # for any other.
                "headline": (
                    other.totals.get(f"{selection.indicator}_gap")
                    or other.totals.get("births")
                    or other.totals.get("pop_total")
                ),
                "note": method.caveat,
            }
        )
    return out
