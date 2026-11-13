"""Reading a selection question from a request: the one place a query string becomes an indicators selection.

The indicators page, its API, the download, the methodology and the pages built on the baseline (Market size) all ask
the same kind of question: *where is this indicator past this threshold, by this method, in these countries, at this
level?* They share this module so they cannot answer differently. Every reader degrades to a default rather than
raising: a misspelled ISO, an impossible level or a year of 1066 still gets an answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.indicators import availability
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import measures, methods
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.resolve import Selection, select_above

DEFAULT_THRESHOLD = 80.0
DEFAULT_INDICATOR = "u5mr"
DEFAULT_METHOD = methods.default_for(methods.Resolution.SUBNATIONAL).code


def method_param(request, indicator: str = "u5mr") -> str:
    """The requested method, or a default that can actually answer this indicator.

    An explicit choice is honoured even when it has no data — the surface then
    says so, which is the point. Only the default adapts.
    """
    code = request.GET.get("method")
    if code and code in methods.METHODS:
        return code
    resolution = request.GET.get("resolution")
    res = methods.Resolution(resolution) if resolution in (r.value for r in methods.Resolution) else None
    # No resolution asked for still means "the subnational default", but which
    # method that is depends on the indicator. The flat constant is right for
    # the mortality measures this began as and answers no country at all for,
    # say, improved drinking water — so a link without ?method= landed on a
    # method with nothing behind it.
    return availability.default_method_for(indicator, res or methods.Resolution.SUBNATIONAL).code


def float_param(request, key, default):
    try:
        return float(request.GET.get(key, default))
    except (TypeError, ValueError):
        return default


def indicator_param(request, default: str = DEFAULT_INDICATOR) -> str:
    """The requested indicator, or the default if it names nothing.

    Every other query parameter already degrades: a misspelled ISO, an
    impossible admin level, a year of 1066 all fall back and the page still
    answers. The indicator did not — `?indicator=NOT_A_REAL_INDICATOR` reached
    `measures.get()` and raised, so one endpoint returned 500 while the others
    returned 200 for the same request. Falling back keeps the surface usable
    and consistent with everything beside it.
    """
    code = request.GET.get("indicator")
    if code and code in measures.MEASURES:
        return code
    return default


def iso_codes_param(request) -> list[str]:
    """The countries in scope, defaulting to the whole continent.

    Scoping is what turns this from a continental scan into a country
    proposal. Unknown codes are dropped rather than passed through: a
    misspelled ISO would otherwise silently return an empty selection, which
    reads as "nowhere qualifies" rather than "that is not a country".
    """
    raw = request.GET.get("iso", "")
    wanted = [c.strip().upper() for c in raw.replace(" ", ",").split(",") if c.strip()]
    kept = [c for c in wanted if c in ISO_CODES]
    return kept or list(ISO_CODES)


def admin_level_param(request) -> int | None:
    """The level to evaluate at, or None to take the deepest carrying values.

    Deepest is not always most informative. Liberia measures ORS coverage for
    its fifteen counties and no district, so all 136 districts inherit one of
    fifteen numbers -- a finer grid over the same information. Pinning the
    level is how a reader asks for the units that were actually measured.
    """
    v = request.GET.get("admin_level")
    if v and v.isdigit() and int(v) in boundary_set.LEVELS:
        return int(v)
    return None


def rollup_param(request) -> bool:
    """Whether a wholly-selected country collapses into one row.

    On, a country every one of whose regions clears the threshold is stated as
    the country -- truer and shorter. Off, the regions are listed, which is
    what a ranking question wants: you cannot rank fifteen counties against
    each other if they have been added together first.
    """
    return request.GET.get("rollup", "1").lower() not in ("0", "false", "no", "off")


def target_year_param(request) -> int | None:
    """The year the programme runs, if one was named.

    Counts are carried to it at each country's own growth rate; rates are left
    alone, because nothing here models how coverage or mortality moves and a
    projected rate would be invention.
    """
    v = request.GET.get("target_year")
    if v and v.isdigit() and 2000 <= int(v) <= 2050:
        return int(v)
    return None


@dataclass(frozen=True)
class Question:
    """A fully-read selection question: what the page, the API and the download all answer from."""

    indicator: str
    threshold: float
    year: int | None
    iso_codes: tuple[str, ...]
    method: str
    target_year: int | None
    rollup: bool
    admin_level: int | None

    @classmethod
    def from_request(cls, request, *, indicator: str | None = None, threshold: float | None = None) -> Question:
        """Read the question from ``request``; ``indicator`` and ``threshold`` override what the query says."""
        code = indicator or indicator_param(request)
        measure = measures.get(code)
        y = request.GET.get("year")
        return cls(
            indicator=code,
            threshold=(
                threshold if threshold is not None else float_param(request, "threshold", measure.threshold_default)
            ),
            year=int(y) if y and y.isdigit() else None,
            iso_codes=tuple(iso_codes_param(request)),
            method=method_param(request, code),
            target_year=target_year_param(request),
            rollup=rollup_param(request),
            admin_level=admin_level_param(request),
        )

    def select(self, extra_counts: tuple[str, ...] = ()) -> Selection:
        """The selection, with any extra count measures carried (a sizing needs its own population count)."""
        return select_above(
            indicator=self.indicator,
            threshold=self.threshold,
            year=self.year,
            iso_codes=list(self.iso_codes),
            method=self.method,
            extra_counts=extra_counts,
            target_year=self.target_year,
            rollup=self.rollup,
            admin_level=self.admin_level,
        )

    def cache_key(self, *extra) -> tuple:
        """What the answer depends on, for keeping it: every field, sorted where order does not matter."""
        return (
            self.indicator,
            self.threshold,
            self.year,
            tuple(sorted(self.iso_codes)),
            self.method,
            self.target_year,
            self.rollup,
            self.admin_level,
            *extra,
        )
