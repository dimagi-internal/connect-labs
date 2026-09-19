"""Building an organisation the way a test wants one.

Pulse's tests used to construct ``PulsePartner`` rows directly. Partner identity
now lives in ``LabsOrg`` plus ``OrgProfile``, which is two writes instead of one,
so this exists to keep the seeding line in those tests a single call — and to
keep their assertions untouched, which is the property the collapse is judged
on.

Not in a conftest: ``marketplace`` owns these tables, and a helper that builds
them belongs with them rather than with whichever app happened to need it first.
"""

from __future__ import annotations

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.identity import ensure_org
from connect_labs.marketplace.models import OrgProfile
from connect_labs.pulse.partner_names import invalidate as invalidate_partner_cache


def make_partner(name: str, short: str = "", *, delivers=(), **profile_fields) -> LabsOrg:
    """An organisation with a profile, from the fields a pulse test cares about.

    Accepts the ``PulsePartner`` field names the pulse tests already use —
    ``joined_at``, ``lat``, ``lon``, ``country_iso3``, ``location_precision``,
    ``location_label``, ``joined_basis`` — so a seeding line ports across by
    changing only the call, never its arguments.

    ``delivers`` names Connect delivery types this organisation has run, and
    builds the pulse opportunity that makes that true. Saying it in one word
    keeps the test about the behaviour rather than about the three rows the
    program filter reads.
    """
    org = ensure_org(name, short_name=short)
    OrgProfile.objects.update_or_create(org=org, defaults=profile_fields)

    if delivers:
        from connect_labs.pulse.models import PulseOpportunity

        for index, service in enumerate(delivers):
            # A deterministic id, not hash(): PYTHONHASHSEED varies per process
            # and an id that moves between runs makes a failure unreproducible.
            existing = PulseOpportunity.objects.order_by("-opportunity_id").values_list("opportunity_id", flat=True)
            PulseOpportunity.objects.update_or_create(
                opportunity_id=(existing.first() or 0) + 1,
                defaults={
                    "name": f"{service.upper()} — {name}",
                    "org_slug": org.slug,
                    "service_slug": service,
                    "lifetime_visit_count": 10 + index,
                },
            )
    # `partner_names` caches the registry for a minute, so an organisation
    # created after another test warmed that cache would be invisible to
    # `resolve()` — and a test that passes alone but fails in a suite is worse
    # than one that fails. `marketplace_import` does exactly this after a run.
    invalidate_partner_cache()
    # Delivery and workspace resolution are cached too, and a partner created
    # after that cache warmed would not be seen as delivering.
    from connect_labs.marketplace import queries

    queries.invalidate()
    return org
