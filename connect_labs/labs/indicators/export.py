"""Export a selection as a table plus the documentation that explains it.

The download is a ZIP holding two files: the table as CSV, and a METHODOLOGY.md
naming every source, its vintage, its licence, and the formula behind any derived
column. They travel together deliberately — a spreadsheet of mortality-weighted
birth estimates that has been separated from its provenance is a liability, and
the commonest way that happens is someone emailing on a bare CSV.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime

from connect_labs.labs.indicators import defence, measures
from connect_labs.labs.indicators.models import NON_COMMERCIAL, IndicatorValue
from connect_labs.labs.indicators.resolve import CARRIED_COUNTS, Selection

COLUMNS = [
    ("country", "Country"),
    ("area", "Area"),
    ("level", "Admin level"),
    ("scope", "Row covers"),
    # The value column and its provenance. The keys stay `u5mr_*` because that
    # is what `_rows` builds; the HEADINGS come from the selected measure —
    # see `columns_for`. They used to be literal: an ORS export arrived with a
    # column called "Under-5 mortality (per 1,000)" holding a percentage of
    # treated children, and six more headed "U5MR". That is the same mistake
    # the map tooltip made, in the artifact that leaves the building.
    ("u5mr", "Under-5 mortality (per 1,000)"),
    ("u5mr_ci", "Confidence interval"),
    ("u5mr_within_uncertainty", "Within uncertainty of threshold"),
    ("u5mr_method", "Method"),
    ("u5mr_source", "Source"),
    ("u5mr_source_detail", "Source detail"),
    ("u5mr_year", "Survey year"),
    ("u5mr_adjustment", "Adjustment"),
    ("u5mr_source_url", "Source link"),
    ("u5mr_measured_at", "Measured at"),
    ("expected_deaths", "Est. annual under-5 deaths"),
    ("ors_gap_children", "Children with untreated diarrhoea"),
    # The fortnight figure beside it, over a year. Both travel, because the
    # .zip is the copy a funder reads without the page beside it, and quoting a
    # two-week recall count as an annual one is a twentyfold error made in
    # print. A blank here means the indicator declares no recall window and no
    # episode duration, so no honest conversion exists.
    ("gap_annual", "Unreached per year (annualised)"),
    ("births", "Est. annual births"),
    ("pop_u5", "Population under 5"),
    ("pop_total", "Total population"),
    ("births_complete", "Births complete for all regions"),
]


def _source_name(code: str) -> str:
    # Imported lazily: export is also used by tests that never touch views.
    from connect_labs.labs.indicators.views import source_name

    return source_name(code)


def _method_name(r, selection: Selection) -> str:
    """The method that produced this row, which is not always the one selected.

    The page learned to say this; the CSV is the copy that leaves the building,
    and it is the one a funder reads without the page beside it. A row inherited
    from IGME's national figure must not travel under the heading of the survey
    method that was merely asked for.
    """
    from connect_labs.labs.indicators import methods
    from connect_labs.labs.indicators.views import _row_method_label

    if r is None:
        return ""
    selected = methods.get(selection.method) if selection.method else None
    return _row_method_label(r, selected) or ""


def _cell(value):
    """Blank, not zero, when there is no estimate.

    A 0 in a births column reads as "nobody is born here"; blank reads as "we
    could not work it out", which is what is actually true.
    """
    return "" if value is None else round(value)


def _rows(selection: Selection):
    for a in selection.areas:
        r = a.values.get(selection.indicator)
        yield {
            "country": a.country_name,
            "area": a.name,
            "level": f"ADM{a.admin_level}",
            "scope": (
                f"whole country ({a.units_covered} regions, all above threshold)"
                if a.is_whole_country
                else "single region"
            ),
            "u5mr": round(r.value, 1) if r else "",
            "u5mr_ci": (
                f"{r.ci_low:.1f}-{r.ci_high:.1f}" if r and r.ci_low is not None and r.ci_high is not None else ""
            ),
            "u5mr_within_uncertainty": (
                "yes"
                if r
                and r.ci_low is not None
                and r.ci_high is not None
                and r.ci_low <= selection.threshold <= r.ci_high
                else ""
            ),
            "u5mr_method": _method_name(r, selection),
            "u5mr_source": _source_name(r.source) if r else "",
            "u5mr_source_detail": (r.source_ref or "") if r else "",
            "u5mr_year": r.measured_year if r else "",
            "u5mr_adjustment": (
                f"re-levelled x{r.extra['factor']:.3f} from {r.extra['raw_year']} to {r.year} "
                f"(raw {r.extra['raw_value']:.1f})"
                if r and r.adjusted
                else ""
            ),
            "u5mr_source_url": (r.source_url or "") if r else "",
            "u5mr_measured_at": (
                f"{r.measured_at.name} (ADM{r.measured_at.admin_level})" if r and r.inherited else "this area"
            ),
            "expected_deaths": _cell(a.counts.get("expected_deaths")),
            "ors_gap_children": _cell(a.counts.get("ors_gap_children")),
            "gap_annual": _cell(a.counts.get(f"{selection.indicator}_gap_annual")),
            "births": _cell(a.counts.get("births")),
            "pop_u5": _cell(a.counts.get("pop_u5")),
            "pop_total": _cell(a.counts.get("pop_total")),
            "births_complete": "yes" if a.is_complete("births") else "no",
        }


def columns_for(selection: Selection) -> list[tuple[str, str]]:
    """The columns, with the value column named after the measure selected.

    Everything else is generic on purpose: an export carries one indicator, so
    "Source" is unambiguous and "ORS treatment coverage source detail" is only
    longer.
    """
    m = measures.get(selection.indicator)
    heading = f"{m.label} ({m.unit})" if m.unit else m.label
    return [(k, heading if k == "u5mr" else label) for k, label in COLUMNS]


def to_csv(selection: Selection) -> str:
    buf = io.StringIO()
    cols = columns_for(selection)
    w = csv.DictWriter(buf, fieldnames=[k for k, _ in cols], extrasaction="ignore")
    w.writerow({k: label for k, label in cols})
    for row in _rows(selection):
        w.writerow(row)
    return buf.getvalue()


def _sources_used(selection: Selection) -> list[IndicatorValue]:
    """Distinct (source, ref, licence) actually behind this selection."""
    boundary_ids = [a.boundary.pk for a in selection.areas]
    wanted = [selection.indicator, *CARRIED_COUNTS, "imr", "pop_u1"]
    seen: dict[tuple, IndicatorValue] = {}
    for v in IndicatorValue.objects.filter(boundary_id__in=boundary_ids, indicator__in=wanted).order_by(
        "indicator", "source"
    ):
        seen.setdefault((v.indicator, v.source, v.source_ref, v.license_code), v)
    return list(seen.values())


def _threshold_phrase(value: float, unit: str) -> str:
    """ "50% of population", not "50 % of population" — a percent binds to its number."""
    if unit.startswith("%"):
        return f"{value:g}%{unit[1:]}"
    return f"{value:g} {unit}"


def _rerun(selection: Selection, method_code: str) -> Selection:
    """The same question under a different method, for the comparison table.

    *The same* question — same shape, not merely the same indicator and
    threshold. An alternative re-run with the default rollup and level compares
    fifteen counties against one rolled-up country and calls the difference a
    method spread, when most of it is the shape. The projection year matters for
    the same reason: a count carried to 2027 against one left at 2022 differs by
    growth, not by method.
    """
    from connect_labs.labs.indicators.resolve import select_above

    return select_above(
        indicator=selection.indicator,
        threshold=selection.threshold,
        year=selection.year,
        iso_codes=sorted({a.iso_code for a in selection.areas}) or None,
        method=method_code,
        target_year=selection.projected_to,
        rollup=selection.rolled_up,
        admin_level=selection.pinned_level,
    )


def to_methodology(selection: Selection, *, alternatives: bool = True) -> str:
    m = measures.get(selection.indicator)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    pct = measures.percent_equivalent(selection.indicator, selection.threshold)
    # Only per-1,000 rates have a second reading; a percent indicator does not.
    also = f" ({pct:g}% of live births)" if pct is not None else ""
    worse = "falls below" if selection.indicator in measures.LOWER_IS_WORSE else "exceeds"

    out: list[str] = []
    add = out.append

    add("# Targeting selection — methodology\n")
    add(f"Generated {generated} by Connect Labs targeting (`/labs/targeting/`).\n")

    add("## What this table is\n")
    add(
        f"Every administrative area where **{m.label.lower()}** {worse} "
        f"**{_threshold_phrase(selection.threshold, m.unit)}**{also}, together with "
        "the estimated annual births and population living there.\n"
    )
    add(
        f"It covers **{selection.area_count} rows** spanning "
        f"**{selection.unit_count} ADM1-equivalent units** across "
        f"**{selection.country_count} countries**.\n"
    )

    add("## How rows are aggregated\n")
    add(
        "Rows sit at the coarsest unit that is honestly describable. Where *every* "
        "region of a country clears the threshold, the country appears as a single "
        "row marked `whole country`. Where only some regions clear it, those regions "
        "appear individually. Counts on a country row are summed from its qualifying "
        "regions, never read from a separate national figure, so a country total can "
        "never disagree with the regions beneath it.\n"
    )
    add(
        f"Counts (births, population) are summed. Rates are never summed — {m.label.lower()} "
        f"is aggregated as a mean weighted by `{m.weight_by}`, because a rate is a "
        "property of the group it is measured over rather than of the land area.\n"
    )

    add("## Where the numbers come from\n")
    add("| Indicator | Source | Reference | Year | Licence | Link |")
    add("|---|---|---|---|---|---|")
    for v in _sources_used(selection):
        link = f"<{v.source_url}>" if v.source_url else "—"
        add(
            f"| `{v.indicator}` | {v.get_source_display()} | {v.source_ref or '—'} "
            f"| {v.year} | {v.get_license_code_display()} | {link} |"
        )
    add("")

    add("## Derived quantities\n")
    has_births = any((a.counts.get("births") or 0) for a in selection.areas)
    if has_births:
        add("**Annual births** is derived, not measured:\n")
        add("```")
        add("births = population aged 0-1 / (1 - infant mortality rate / 1000)")
        add("```")
        add(
            "The under-1 population is one birth cohort less the infants who died; "
            "dividing survivorship back out recovers births. The per-row inputs and "
            "their sources are recorded against each value in the database.\n"
        )
    else:
        add("No derived quantities in this selection.\n")

    got, total_units = selection.coverage.get("births", (0, 0))
    if total_units and got < total_units:
        add("## This total is a floor, not a measurement\n")
        add(
            f"**{total_units - got} of {total_units}** selected regions have no births "
            "estimate, because neither an under-1 population nor a "
            "women-of-childbearing-age figure is available for them. Those regions "
            "contribute nothing to the total, so the real number is higher than the "
            "one reported here.\n"
        )
        add(
            "Rows where this applies are marked `no` in the "
            "**Births complete for all regions** column, and their births cell is "
            "blank rather than zero.\n"
        )

    adjusted = [a for a in selection.areas if (v := a.values.get(selection.indicator)) and v.adjusted]
    if adjusted:
        add("## Old surveys are re-levelled to the present\n")
        add(
            f"{len(adjusted)} of {len(selection.areas)} rows come from a survey that has "
            "been re-levelled. A third of Africa's subnational mortality comes from "
            "surveys eight or more years old, and mortality has moved a long way since: "
            "taken raw, Eritrea's 2002 regions read 111–154 against a national rate of "
            "34 today.\n"
        )
        add("```")
        add("factor   = IGME national (latest) / IGME national (survey year)")
        add("adjusted = survey region value x factor")
        add("```")
        add(
            "Both ends of the ratio are IGME's own series, so the factor is a pure "
            "trend and carries no difference of method between IGME and the survey. "
            "**This assumes relative differences between regions persisted while the "
            "level changed** — an assumption that weakens the older the survey. The "
            "`Survey year` and `Adjustment` columns show what was done to "
            "each row, and the unadjusted survey value is kept in the database "
            "alongside.\n"
        )

    # The two sections that argue with the table rather than describing it. They
    # come before the caveats because a reader who has just been shown the numbers
    # wants to know whether to believe them, not yet what to hold in mind.
    add("## Does this survive a sanity check?\n")
    add(
        "Every figure above has an independent second opinion available, and each is "
        "computed for **this** selection rather than asserted in general. A claim that "
        "our numbers were validated, without saying against what, spends credibility "
        "rather than earning it.\n"
    )
    for check in defence.sanity_checks(selection):
        add(f"- **{check.name}** — _{check.verdict}_. {check.detail}")
    add("")

    if alternatives:
        rows = defence.alternatives(selection, run=lambda code: _rerun(selection, code))
        if rows:
            add("## Other methods considered\n")
            add(
                "The same question, asked of every other method that works at this "
                "resolution. Where they agree, the choice of method is not doing the work; "
                "where they disagree, the disagreement is the finding and is reported "
                "rather than resolved by preference.\n"
            )
            add("| Method | Countries it can answer | Areas | Units | What it would report |")
            add("|---|---:|---:|---:|---|")
            for r in rows:
                if not r["countries"]:
                    add(f"| {r['label']} | 0 | — | — | Cannot answer this indicator at all. |")
                    continue
                head = f"{r['headline']:,.0f}" if r.get("headline") else "—"
                add(f"| {r['label']} | {r['countries']} | {r.get('areas', '—')} | {r['units']} | {head} |")
            add("")
            add(
                "Sources deliberately **not** used: IHME's estimates, whose non-commercial "
                "agreement excludes for-profit entities and their employees and forbids "
                "re-hosting; and the Malaria Atlas Project's administrative tables, which "
                "are older than the survey figures used here. Neither exclusion is a "
                "judgement that they are worse — IHME in particular may well be better for "
                "some indicators, and cannot be used to find out.\n"
            )

    add("## Caveats worth carrying\n")
    # Named for the measure that was actually selected. These read as a
    # mortality briefing whatever you exported: an ORS selection arrived
    # warning that "a survey's under-5 mortality rate covers several years
    # before fieldwork", which is true, and about a different number.
    measure = measures.get(selection.indicator)
    add(
        f"- **{measure.label} is measured at ADM1 at best.** Where a region has no "
        "reading of its own it takes a coarser one, and the `Measured at` column says "
        "so per row. No finer variation is implied than the source can support.\n"
    )
    if selection.indicator in measures.LOWER_IS_WORSE or measure.kind is measures.Kind.RATE:
        add(
            "- **Survey figures are period estimates.** A survey describes the window it "
            "asked about, not the year it was published. It is stored against the survey "
            "year because that is how people refer to it.\n"
        )
    add(
        "- **Population is modelled.** WorldPop disaggregates census counts onto a 100 m "
        "grid; totals for small areas carry more uncertainty than national figures.\n"
        f"- **Years are not aligned.** {measure.label} comes from the most recent source "
        "for each country, which differs; population is WorldPop 2020. Both years are "
        "shown.\n"
    )

    non_commercial = [v for v in _sources_used(selection) if v.license_code in NON_COMMERCIAL]
    if non_commercial:
        add("## Licence restriction\n")
        add(
            "**This selection contains non-commercial data.** The following sources "
            "may not be used in a commercial context:\n"
        )
        for v in non_commercial:
            add(f"- `{v.indicator}` — {v.get_license_code_display()}")
        add("")
    else:
        add("## Licence\n")
        add(
            "Every source in this selection permits commercial use with attribution. "
            "No IHME data is included — see the source research for why.\n"
        )

    add("## Attribution\n")
    add(
        "Boundaries: geoBoundaries (CC BY 4.0). Population: WorldPop (CC BY 4.0). "
        "Mortality and fertility: The DHS Program; UN IGME via UNICEF.\n"
    )

    return "\n".join(out)


def to_zip(selection: Selection) -> bytes:
    """The table and its documentation, together."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("targeting_selection.csv", to_csv(selection))
        z.writestr("METHODOLOGY.md", to_methodology(selection))
    return buf.getvalue()


def filename_stem(selection: Selection) -> str:
    """Name the file after the question it answers.

    A coverage measure selects the places BELOW its threshold, so calling every
    export ``gt`` asserted the opposite of the contents for half the catalogue:
    an ORS selection of the fifteen counties under 90% arrived as
    ``targeting_ors_coverage_gt90.zip``.
    """
    direction = "lt" if selection.indicator in measures.LOWER_IS_WORSE else "gt"
    return f"targeting_{selection.indicator}_{direction}{selection.threshold:g}"
