"""What a run's opportunities are called, for an agent reading the run.

The run tools hand an agent opportunity ids; a person asks about partners by name
("how is NAMA doing?"). These are the names Labs already shows the same person --
their own org tree (``labs_context`` / the opportunity picker): a labs-only
opportunity's registry row (``SyntheticOpportunity.label`` and its organisation),
else the opportunity as the caller's Connect org data lists it. No new data is
read: an id the caller's tree does not name is returned with ``name: None``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_opportunities(user, opportunity_ids, access_token: str | None = None) -> list[dict]:
    """``[{id, name, organization, synthetic}]`` for each id, sorted by id.

    ``synthetic`` is whether the opportunity is a registered (enabled) synthetic
    opportunity. A labs-only one is named only when it is visible to ``user`` (the
    rule ``labs_context`` keeps); any other id is named from the caller's Connect org
    data, fetched (cached) only when one is left unnamed. Best-effort: a name is a
    label and never fails the read it rides on.
    """
    ids = sorted({int(i) for i in opportunity_ids or []})
    out = {i: {"id": i, "name": None, "organization": None, "synthetic": False} for i in ids}
    if not ids:
        return []
    try:
        from connect_labs.labs.synthetic.models import SyntheticOpportunity
        from connect_labs.labs.synthetic.org_tree import synthetic_org_name

        for row in SyntheticOpportunity.objects.filter(opportunity_id__in=ids, enabled=True).select_related(
            "labs_org__workspace"
        ):
            entry = out[row.opportunity_id]
            entry["synthetic"] = True
            if row.labs_only and row.is_visible_to(user):
                entry["name"] = row.label or None
                entry["organization"] = synthetic_org_name(row)
    except Exception:  # noqa: BLE001
        logger.warning("could not read synthetic opportunity names for %s", ids, exc_info=True)

    unnamed = {i for i, e in out.items() if not e["name"]}
    if unnamed and access_token:
        try:
            from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

            data = fetch_user_organization_data(access_token, owner=getattr(user, "username", None)) or {}
            orgs = {o.get("slug"): o.get("name") for o in data.get("organizations") or [] if o.get("slug")}
            for o in data.get("opportunities") or []:
                try:
                    oid = int(o.get("id"))
                except (TypeError, ValueError):
                    continue
                if oid in unnamed:
                    out[oid]["name"] = o.get("name") or None
                    slug = o.get("organization")
                    out[oid]["organization"] = orgs.get(slug) or slug or None
        except Exception:  # noqa: BLE001
            logger.warning("could not read opportunity names from org data", exc_info=True)
    return [out[i] for i in ids]
