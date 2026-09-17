"""Turning the directory's country cell into countries that actually exist.

The cell is free text typed by several people over several years, and reading
it naively produced a filter with four separate entries for one country:

    "Congo, the Democratic Republic of the"   quoted, read whole          ✓
    Congo, the Democratic Republic of the     unquoted, split on the comma
    Nigeria, Chad, Niger, "Congo, …"          quoted INSIDE a list
    DRC                                       a shorthand

The third is the one a hand-rolled split cannot survive: the old rule took a
cell whole if the WHOLE cell was quoted, so a quoted name inside a list broke
into `"Congo` and `the Democratic Republic of the`. That is exactly what CSV
quoting means, so `csv` parses it rather than another regex.

Every token is then resolved against ISO 3166 — the same 249-country table and
the same alias list `pulse.hq_location` already uses to place an organisation on
the globe, so the filter and the map cannot disagree about what a country is.

Nothing here guesses. A token that does not resolve is returned separately and
reported against its row, because a country nobody can identify is a fact about
the sheet and should be fixed there rather than papered over here.
"""

from __future__ import annotations

import csv
import io

from connect_labs.microplans.core import iso as iso_codes
from connect_labs.pulse.hq_location import country_to_iso3


def _tokens(raw: str) -> list[str]:
    """Split the cell, honouring quotes the way a spreadsheet does."""
    value = (raw or "").strip()
    if not value:
        return []
    try:
        row = next(csv.reader(io.StringIO(value), skipinitialspace=True))
    except (csv.Error, StopIteration):
        row = value.split(",")
    return [part.strip().strip('"').strip() for part in row if part.strip(' "')]


def _missing_comma(token: str) -> list[str] | None:
    """Two country names run together, as in `Pakistan Turkey`.

    Accepted only when BOTH halves are exact ISO matches — that is a typist's
    missing comma, not an inference. Anything looser would start inventing
    countries out of ordinary multi-word names, and `Sierra Leone` is one.
    """
    words = token.split()
    for cut in range(1, len(words)):
        left, right = " ".join(words[:cut]), " ".join(words[cut:])
        if country_to_iso3(left) and country_to_iso3(right):
            return [left, right]
    return None


def parse_cell(raw: str) -> tuple[list[str], list[str]]:
    """(canonical country names, tokens nothing could resolve).

    Names come back in ISO 3166's own words, so "DRC", "DR Congo" and
    "Congo, the Democratic Republic of the" all arrive as one country and one
    filter entry.
    """
    # A cell that is ITSELF a country name is one country, not a list. This has
    # to be tried before any comma split, because several ISO names contain a
    # comma and splitting one leaves a fragment that resolves to a DIFFERENT
    # country: unquoted "Congo, the Democratic Republic of the" yields "Congo",
    # which is the other Congo, and looks entirely correct on the page.
    whole = country_to_iso3((raw or "").strip().strip('"').strip())
    if whole:
        return [iso_codes.country_name(whole) or raw.strip()], []

    names: list[str] = []
    unresolved: list[str] = []
    for token in _tokens(raw):
        for part in [token] if country_to_iso3(token) else (_missing_comma(token) or [token]):
            iso3 = country_to_iso3(part)
            if not iso3:
                unresolved.append(part)
                continue
            name = iso_codes.country_name(iso3) or part
            if name not in names:
                names.append(name)
    return names, unresolved
