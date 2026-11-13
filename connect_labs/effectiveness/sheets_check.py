"""Has a live model Sheet moved away from the vendored spec?

Each Connect model lives in a Google Sheet its owner edits by hand (Shayoni Mazumdar for the three
models). Labs runs a vendored copy of each model's formulas (``effectiveness/data/<model>.json``), so a hand
edit to a Sheet is invisible to Labs until the specs are refreshed. This compares what the live Sheet's
Model tab *computes*, cell by cell, with what the vendored spec computes for the same column, and says
exactly where they differ.

It checks results, not formulas: two different formulas that give the same numbers in every column pass.
That is deliberate. It is the number a reader sees, and the spec's grey GiveWell check columns are part
of what is compared. A text-only edit (a label, a source, a note) does not show; the Sheets' own
``Sources`` tab is where those live.

Pure: ``compare`` takes the Sheet's rows. ``effectiveness_check_sheets`` fetches them.
"""

from __future__ import annotations

import math

from connect_labs.effectiveness import engine

#: Relative tolerance: the Sheet and the spec are both floating point, but the Sheet rounds nothing.
REL_TOL = 1e-6
ABS_TOL = 1e-9


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare(model: str, rows: list[list]) -> dict:
    """Compare the Sheet's Model tab (``rows``, unformatted values) with the vendored spec's results.

    Columns line up by position: the spec's ``results`` are ordered like the Sheet's (current inputs, the
    scenarios, then GiveWell's check columns), and rows are matched by their label.
    """
    spec = engine.spec(model)
    # The same label can head more than one row (the headline result at the top and the working row it
    # reads), so pair the n-th spec row with a label with the n-th live row with that label.
    live_by_label: dict[str, list] = {}
    for r in rows:
        if r and isinstance(r[0], str) and r[0].strip():
            live_by_label.setdefault(r[0].strip(), []).append(r)
    seen: dict[str, int] = {}
    pairing = {}
    for spec_row in spec["rows"]:
        if spec_row.get("text"):
            continue
        label = (spec_row.get("label") or "").strip()
        n = seen.get(label, 0)
        seen[label] = n + 1
        candidates = live_by_label.get(label, [])
        pairing[spec_row["key"]] = (label, candidates[n] if n < len(candidates) else None)
    columns = list(spec["results"])
    compared, differing, missing = 0, [], set()
    for ci, column in enumerate(columns):
        for key, expected in spec["results"][column].items():
            label, live_row = pairing.get(key, (key, None))
            if live_row is None:
                missing.add(label or key)
                continue
            live = live_row[1 + ci] if len(live_row) > 1 + ci else None
            a, b = _number(expected), _number(live)
            if a is None or b is None:
                continue
            compared += 1
            if not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL):
                differing.append({"column": column, "row": label or key, "vendored": a, "live": b})
    return {
        "model": model,
        "columns": len(columns),
        "cells_compared": compared,
        "differing": differing,
        "rows_missing_in_live": sorted(missing),
        "in_sync": compared > 0 and not differing and not missing,
    }
