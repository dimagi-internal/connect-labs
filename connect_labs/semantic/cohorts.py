"""Peer cohorts for a frontline worker: who is this worker fairly compared against.

A worker's raw indicator value answers "how are they doing" only once you know
who else it is measured against. Three ways of drawing that comparison group are
useful and they disagree with each other on purpose:

  opportunity   the people they actually work alongside, same supervisor, same
                catchment. The within-team read.
  start_month   everyone who began the same month, anywhere in the programme.
                A FIXED key -- you are always in the 2026-03 intake -- which is
                why it is used here in preference to a tenure BAND. A band
                ("under 3 months") silently reclassifies people as time passes,
                so this month's benchmark stops being comparable to last
                month's, and a worker appears to move without doing anything.
  caseload      workers carrying about the same number of cases, banded by
                quantile over the population. Volume drives most indicators, so
                comparing a 12-case worker against a 200-case worker mostly
                measures volume.

WHAT THIS MODULE IS NOT. Nothing here is a disclosure control by itself. Within
one programme report the reader can already see every worker by name, so the
floors below are about whether a comparison MEANS anything -- a percentile over
four people is noise -- not about hiding anyone. The same functions are reused
by the cross-opportunity publication path, where the floors are load-bearing and
`connect_labs.benchmarks.disclosure` adds the rules that actually protect
identity. Do not read a passing floor here as a privacy clearance there.
"""

from __future__ import annotations

import math

# A slice smaller than this cannot support a percentile anyone should act on.
MIN_COHORT = 8
# No published histogram bin holds fewer than this many workers.
MIN_BIN = 5

DIMENSIONS = ("opportunity", "start_month", "caseload")


def start_month(cases) -> str | None:
    """The month a worker first appeared, as `YYYY-MM`.

    Earliest of each case's `first_visit_date`, falling back to `reg_date` --
    a case registered but never visited still evidences the worker was active.
    Dates arrive as ISO strings (or dates); anything shorter than `YYYY-MM` is
    not a month and is ignored rather than truncated into a wrong one.
    """
    best = None
    for c in cases or []:
        raw = c.get("first_visit_date") or c.get("reg_date")
        if not raw:
            continue
        s = str(raw)[:7]
        if len(s) < 7 or s[4] != "-":
            continue
        if best is None or s < best:
            best = s
    return best


def quantile_edges(values, bands: int = 3) -> list[float]:
    """Interior cut-points splitting `values` into `bands` roughly equal groups.

    Returns `bands - 1` edges. Ties are NOT broken: if a third of the population
    carries exactly 40 cases, the edge lands on 40 and the bands come out
    lopsided. That is the honest outcome -- moving the edge off a tie would put
    two workers with identical caseloads in different cohorts.
    """
    vals = sorted(v for v in values if v is not None)
    if bands < 2 or len(vals) < bands:
        return []
    out = []
    for i in range(1, bands):
        pos = i * len(vals) / bands
        out.append(float(vals[min(len(vals) - 1, int(math.floor(pos)))]))
    return out


def band_index(value, edges) -> int:
    """Which band `value` falls in, given `quantile_edges` output. 0-based."""
    i = 0
    for e in edges:
        if value is None or value < e:
            break
        i += 1
    return i


CASELOAD_LABELS = ("lightest", "middle", "heaviest")


def caseload_label(idx: int, bands: int = 3) -> str:
    if bands == 3 and 0 <= idx < 3:
        return CASELOAD_LABELS[idx]
    return f"band {idx + 1}"


def cohort_key(flw: dict, dimension: str, *, edges=None) -> str | None:
    """The cohort this worker belongs to along `dimension`, or None if unknown.

    None means "cannot be placed" -- no start month recorded, no opportunity --
    and such a worker is left OUT of every cohort rather than pooled into a
    catch-all. A catch-all bucket of unplaceable workers is not a peer group.
    """
    if dimension == "opportunity":
        opp = flw.get("opp")
        return f"opportunity:{opp}" if opp is not None else None
    if dimension == "start_month":
        sm = flw.get("startMonth")
        return f"start_month:{sm}" if sm else None
    if dimension == "caseload":
        n = flw.get("n")
        if n is None:
            return None
        return f"caseload:{band_index(n, edges or [])}"
    raise ValueError(f"unknown cohort dimension: {dimension}")


def percentile_rank(values, value) -> float | None:
    """Share of the cohort strictly below `value`, 0-100.

    Strictly below, so a worker tied with everyone reads 0 rather than 50 --
    "nobody is worse" is the true statement, and the tie is visible in the
    spread the caller draws alongside.
    """
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return 100.0 * sum(1 for v in vals if v < value) / len(vals)


def summarise(values, value, *, min_cohort: int = MIN_COHORT) -> dict | None:
    """Where `value` sits in `values`: rank, quartiles, extent.

    None when the cohort is under `min_cohort` -- the caller must render "too
    few to compare", never a percentile computed over a handful of people.
    """
    vals = sorted(v for v in values if v is not None)
    if len(vals) < min_cohort:
        return None

    def q(p):
        return vals[min(len(vals) - 1, int(math.floor(p * len(vals))))]

    return {
        "n": len(vals),
        "rank": percentile_rank(vals, value),
        "min": vals[0],
        "q1": q(0.25),
        "median": q(0.5),
        "q3": q(0.75),
        "max": vals[-1],
    }


def histogram(values, *, bins: int = 10, min_bin: int = MIN_BIN, min_total: int = MIN_COHORT):
    """Counts per value band, with thin bands merged into a neighbour.

    Merging (rather than dropping) keeps the counts summing to the population:
    a dropped bin would make the histogram lie about its own total. Tail bins
    merge inward toward the nearest neighbour, so the extremes WIDEN rather
    than disappear -- an outlier stays visible as a wide sparse band.

    Returns None when the population is under `min_total`.
    """
    vals = sorted(v for v in values if v is not None)
    if len(vals) < min_total:
        return None
    lo, hi = vals[0], vals[-1]
    if hi - lo < 1e-9:
        return [{"lo": lo, "hi": hi, "count": len(vals)}]
    width = (hi - lo) / bins
    out = []
    for i in range(bins):
        a = lo + i * width
        b = hi if i == bins - 1 else lo + (i + 1) * width
        n = sum(1 for v in vals if (a <= v < b) or (i == bins - 1 and v == b))
        out.append({"lo": a, "hi": b, "count": n})

    # Merge any non-empty bin under the floor into its smaller neighbour, until
    # every surviving bin clears it. Empty bins are left alone -- a genuine gap
    # in the distribution is information, and merging across it would invent a
    # population that is not there.
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i, b in enumerate(out):
            if 0 < b["count"] < min_bin:
                if i == 0:
                    j = 1
                elif i == len(out) - 1:
                    j = i - 1
                else:
                    j = i - 1 if out[i - 1]["count"] <= out[i + 1]["count"] else i + 1
                lo_i, hi_i = min(i, j), max(i, j)
                out[lo_i] = {
                    "lo": min(out[lo_i]["lo"], out[hi_i]["lo"]),
                    "hi": max(out[lo_i]["hi"], out[hi_i]["hi"]),
                    "count": out[lo_i]["count"] + out[hi_i]["count"],
                }
                del out[hi_i]
                changed = True
                break
    return out
