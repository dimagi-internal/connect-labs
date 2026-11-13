"""The Implementations page's view: the cached ranking with the what-if applied, filtered, ordered and rolled up."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.build import cached_ranking
from connect_labs.effectiveness.implementation.totals import programmes, rollup
from connect_labs.effectiveness.implementation.whatif import (
    apply_what_if,
    filters_from,
    passes,
    rank_rows,
    what_if_from,
)


def view(params, data: dict | None = None) -> dict:
    """The page's view under ``params``: what-if applied, filtered, ranked, with its roll-up and programme split."""
    data = data if data is not None else cached_ranking()
    what_if = what_if_from(params, data["bar_x"])
    flt = filters_from(params)
    rows = [r for r in apply_what_if(data["rows"], what_if) if passes(r, flt)]
    rows = rank_rows(rows, what_if["rank"])
    return {
        "rows": rows,
        "totals": rollup(rows),
        "programmes": programmes(rows),
        "what_if": what_if,
        "filters": flt,
        "bar_x": what_if["bar"],
    }
