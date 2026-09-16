"""Carrying pulse's partner rows into the organisation registry.

Kept out of the migrations package entirely — Django's loader treats every
module in there as a migration and rejects one without a Migration class — so
this lives at app level and is tested directly. The labs
database holds real rows: every join date on the network growth curve is a
directory fact Connect has never seen and cannot regenerate, so losing them
would silently flatten the chart this migration exists to preserve.
"""

from __future__ import annotations

PROFILE_KEYS = (
    "joined_at",
    "joined_basis",
    "country_iso3",
    "lat",
    "lon",
    "location_precision",
    "location_label",
)


def carry_partners(rows, aliases) -> dict:
    """Create an organisation and profile per partner row, then its attributions.

    An alias pointing at a partner that is not in ``rows``, or carrying no
    stated reason, is dropped rather than guessed at: a wrong attribution files
    one organisation's history under another organisation's name.
    """
    from connect_labs.marketplace.identity import ensure_org
    from connect_labs.marketplace.models import OrgConnectSlug, OrgProfile

    by_name = {}
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        org = ensure_org(name, short_name=row.get("short") or "")
        by_name[name] = org
        defaults = {key: row[key] for key in PROFILE_KEYS if row.get(key) not in (None, "")}
        OrgProfile.objects.update_or_create(org=org, defaults=defaults)

    carried = 0
    for alias in aliases:
        org = by_name.get(alias.get("partner_name"))
        why = (alias.get("why") or "").strip()
        if org is None or not why:
            continue
        OrgConnectSlug.objects.update_or_create(slug=alias["slug"], defaults={"org": org, "why": why})
        carried += 1

    return {"organisations": len(by_name), "attributions": carried}
