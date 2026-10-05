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


# The LLO Directory's header rows, verbatim from the live sheet (2026-10-05).
# The parser finds every column by its header, so a fixture with an invented or
# truncated header row tests nothing real -- build fixtures on these instead.
SHEET_ORG_HEADER = [
    "Organization Name",
    "Short Name",
    "Has Used Connect",
    "Year of Establishment",
    "Org Team Size",
    "No. of FLWs Managed",
    "Countries of Operation",
    "Regions/States of Operation",
    "Primary Sector(s)",
    "Website",
    "Office Address",
    "Email addresses from Contacts sheet",
    "EOIs Applied for (Add Link to EOI)",
    "Organization Notes",
    "Latest MSA Link",
    "Latest Work Order Link",
]
SHEET_CONTACT_HEADER = [
    "Contact Full Name",
    "Organization Name",
    "Role / Title",
    "Main POC?",
    "Email Address",
    "Phone Number",
    "Contact Notes (e.g. best way to contact)",
]
SHEET_DATES_HEADER = ["Organization Name", "Joined Connect Network", "Joined — basis"]
SHEET_MAPPING_HEADER = [
    "Connect Org Slug",
    "Connect Org Name",
    "Resolved Partner",
    "Partner Short Name",
    "How it resolved",
    "Why",
    "Lifetime Works",
    "Confirmed? (y/n)",
    "Corrected Partner (overrides)",
]
SHEET_ROUNDS_HEADER = [
    "Slug",
    "Program / Initiative Name",
    "Announcement Type (EOI/RFP)",
    "Status",
    "Published Date",
    "Application Deadline",
    "Selection Decision Date",
    "Program Start Date",
    "Program End Date",
    "Target Countries / Regions",
    "Announcement Link",
    "Form Link",
    "Response Sheet Link",
    "Response Tab",
    "Column Map (JSON, blank = auto-detect)",
    "Labs Access",
    "Labs Access Checked",
    "Notes",
    "Connect Programme (delivery_type)",
    "Next Step (written by labs — do not edit)",
]
