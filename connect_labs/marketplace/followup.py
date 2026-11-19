"""Which program-specific follow-up form each applicant to the CHP RFI gets.

Specific to ``chp-rfi-2026`` on purpose: the people running that round are
sending every applicant a follow-up form, one per program, and need to see on
the round page which form each applicant is meant to get, whether it should go
out in French, and (soon) which was sent and when.

The recommendation is data, not a computation. It was worked out once from the
submissions by a fixed decision tree and saved in ``data/chp_rfi_2026_followup.json``
keyed by response id; it holds no names, contacts or answers. The tree, in the
order it applies:

1. Kangaroo Care: any applicant that ranked Kangaroo Care gets the Kangaroo Care form.
2. Child Health Campaigns: of the rest, one applicant per state (or country, when no
   state is given) that ranked Child Health Campaigns, those that ranked it first preferred.
3. Everyone else is split evenly across Chlorine dispensers, Lead exposure, Mother Baby
   Wellness, Poverty Graduation and RUTF wasting treatment, each by its highest-ranked program.

French or English follows the form the applicant filled in, not a guess from its country.
"""

import json
from functools import lru_cache
from pathlib import Path

ROUND_SLUG = "chp-rfi-2026"
DATA_FILE = Path(__file__).parent / "data" / "chp_rfi_2026_followup.json"

# Key -> label, in the order the filter shows them.
FORMS = {
    "kc": "Kangaroo Care",
    "chc": "Child Health Campaigns",
    "chlorine": "Chlorine dispensers",
    "lead": "Lead exposure",
    "mbw": "Mother Baby Wellness",
    "povgrad": "Poverty Graduation",
    "rutf": "RUTF wasting treatment",
}


@lru_cache(maxsize=1)
def _load() -> dict:
    with DATA_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def applies_to(round_slug: str) -> bool:
    return round_slug == ROUND_SLUG


def _index() -> tuple[dict[int, dict], dict[str, list[dict]]]:
    """Entries by response id (a merged resubmission answers to the same entry), and by organisation slug."""
    by_response: dict[int, dict] = {}
    by_org: dict[str, list[dict]] = {}
    for entry in _load()["entries"]:
        by_response[entry["response_id"]] = entry
        for other in entry.get("also_response_ids", []):
            by_response[other] = entry
        if entry.get("org_slug"):
            by_org.setdefault(entry["org_slug"], []).append(entry)
    return by_response, by_org


def attach(applicants: list[dict]) -> None:
    """Give each applicant row a ``followup``: its form, French or not, why, and any other form its org was given.

    A row is one organisation, shown with its latest submission; an organisation that submitted more than once
    can have been given different forms by different submissions, and those are listed as ``others`` rather
    than silently dropped.
    """
    by_response, by_org = _index()
    for row in applicants:
        entry = by_response.get(row["response"].pk)
        if entry is None:
            row["followup"] = None
            continue
        siblings = by_org.get(entry.get("org_slug") or "", [])
        others = sorted({FORMS[s["form"]] for s in siblings if s is not entry and s["form"] != entry["form"]})
        row["followup"] = {
            "form": entry["form"],
            "label": FORMS[entry["form"]],
            "french": entry["french"],
            "rule": entry["rule"],
            "flags": entry["flags"],
            "others": others,
        }


def filter_rows(applicants: list[dict], form: str = "", french: str = "") -> list[dict]:
    """The rows for one form and/or one language. An unrecognised value filters nothing."""
    rows = applicants
    if form == "none":
        rows = [a for a in rows if not a.get("followup")]
    elif form in FORMS:
        rows = [a for a in rows if a.get("followup") and a["followup"]["form"] == form]
    if french in ("yes", "no"):
        want = french == "yes"
        rows = [a for a in rows if a.get("followup") and a["followup"]["french"] == want]
    return rows


def form_counts(applicants: list[dict]) -> list[dict]:
    """How many rows each form has, in the filter's order."""
    counts = {key: 0 for key in FORMS}
    for a in applicants:
        if a.get("followup"):
            counts[a["followup"]["form"]] += 1
    return [{"key": key, "label": FORMS[key], "count": counts[key]} for key in FORMS]
