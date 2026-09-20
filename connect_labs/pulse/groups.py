"""Several opportunities, read as the one engagement they were.

Connect has no grouping for opportunities, so the Interviews work arrived as 72
of them across two partners. This module is the whole of the mapping: a cohort
to its engagement's key, a key back to its cohorts, and a fold that turns
per-cohort rows into one row. Every surface calls these rather than inventing
its own rule — the partner grouping in ``api`` is the same shape — because the
one thing that must not happen is two surfaces disagreeing about what an
engagement's figures are.

A key is either a group's **slug** or an ungrouped opportunity's **id**. Digits
mean an opportunity and anything else means a group, so no prefix is needed and
every existing link keeps working.

Cached for a minute like ``partner_names``: resolution runs on every scoped
read and cannot afford a query per opportunity. Call ``invalidate()`` after any
write — the seeding command and the tests both do.
"""

from __future__ import annotations

import time

_CACHE_TTL_SECONDS = 60
_cache: dict = {"loaded_at": 0.0, "members": {}, "key_of": {}, "names": {}}

# Figures that are counts or money, and are therefore summed when cohorts fold.
# Rates are NOT here: they are recomputed from the summed parts below.
_SUMMED = (
    "visits",
    "works",
    "approved",
    "flagged",
    "events",
    "workers",
    "usd",
    "usd_org",
    "usd_total",
    "per_service_usd",
    "fixed_usd",
)


def invalidate() -> None:
    """Drop the cache. Called after a write, and by tests that seed groups."""
    _cache["loaded_at"] = 0.0


def _load() -> None:
    if _cache["loaded_at"] and (time.monotonic() - _cache["loaded_at"]) < _CACHE_TTL_SECONDS:
        return
    from connect_labs.pulse.models import PulseOppGroup, PulseOpportunity

    members: dict = {}
    key_of: dict = {}
    for oid, slug in PulseOpportunity.objects.exclude(group=None).values_list("opportunity_id", "group__slug"):
        members.setdefault(slug, []).append(oid)
        key_of[oid] = slug
    _cache["members"] = members
    _cache["key_of"] = key_of
    _cache["names"] = dict(PulseOppGroup.objects.values_list("slug", "name"))
    _cache["loaded_at"] = time.monotonic()


def any_groups() -> bool:
    """Whether anything is grouped at all — lets a caller skip the work."""
    _load()
    return bool(_cache["members"])


def name_of(key) -> str:
    _load()
    return _cache["names"].get(key, "")


def key_for(opportunity_id: int):
    """The engagement a cohort belongs to, or the cohort itself."""
    _load()
    return _cache["key_of"].get(opportunity_id, opportunity_id)


def members(key) -> list:
    """Every opportunity id a key covers."""
    _load()
    if isinstance(key, str) and not key.isdigit():
        return sorted(_cache["members"].get(key, []))
    return [int(key)]


def key_of(thing) -> str | int | None:
    """The key naming a resolved group or opportunity."""
    if thing is None:
        return None
    return getattr(thing, "slug", None) or getattr(thing, "opportunity_id", None)


def resolve(raw):
    """What ``?opportunity=`` names: a group, an opportunity, or nothing.

    A member's own id resolves to its GROUP. A cohort and its engagement are
    the same work, and two links to it reporting different figures is the
    defect this rule prevents; the per-cohort breakdown lives on the group's
    page instead.
    """
    from connect_labs.pulse.models import PulseOppGroup, PulseOpportunity

    raw = raw.strip() if isinstance(raw, str) else raw
    if raw in (None, ""):
        return None
    if str(raw).isdigit():
        opp = PulseOpportunity.objects.filter(opportunity_id=int(raw)).first()
        if opp is None:
            return None
        return opp.group if opp.group_id is not None else opp
    return PulseOppGroup.objects.filter(slug=str(raw)).first()


def collapse(rows: list, id_key: str = "id") -> list:
    """Fold per-cohort rows into one row per engagement, order preserved.

    The first member seen seeds the fold, so identity fields (delivery type,
    country, end date) come from it — callers sort by volume first, which makes
    that the busiest cohort. Counts and money are summed; rates are recomputed
    from the summed numerator and denominator, never averaged, or a cohort of
    two interviews would weigh the same as one of two thousand.
    """
    _load()
    if not _cache["members"]:
        return rows

    out: list = []
    lead_of: dict = {}
    for row in rows:
        key = key_for(row[id_key])
        if key == row[id_key]:
            out.append(row)
            continue
        lead = lead_of.get(key)
        if lead is None:
            lead = dict(row)
            lead[id_key] = key
            lead["name"] = _cache["names"].get(key, str(key))
            lead["members"] = [row[id_key]]
            lead_of[key] = lead
            out.append(lead)
            continue
        lead["members"].append(row[id_key])
        for figure in _SUMMED:
            if figure in lead and figure in row:
                lead[figure] = (lead[figure] or 0) + (row[figure] or 0)
        if lead.get("spark") and row.get("spark"):
            lead["spark"] = [a + b for a, b in zip(lead["spark"], row["spark"])]
        if row.get("last_ts") and (lead.get("last_ts") or 0) < row["last_ts"]:
            lead["last_ts"] = row["last_ts"]
        if "active" in lead:
            lead["active"] = bool(lead.get("active")) or bool(row.get("active"))

    for lead in lead_of.values():
        lead["members"].sort()
        approved, works = lead.get("approved") or 0, lead.get("works") or 0
        if "approval_rate" in lead:
            lead["approval_rate"] = (approved / works) if works else None
        if "rate" in lead:
            lead["rate"] = ((lead.get("usd_total") or 0) / approved) if approved else None
        if "flag_rate" in lead:
            events = lead.get("events") or 0
            lead["flag_rate"] = ((lead.get("flagged") or 0) / events) if events else None
    return out
