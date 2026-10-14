"""Connect's profile wins, for an organisation Connect knows.

Connect's Organization now carries most of what the LLO Directory's
Organizations tab does, edited by the organisation itself on its Organization
Home. Once a `LabsOrg` is linked (`labs.connect_orgs`), keeping
two editable copies would let them drift, so for the fields below Connect's
value replaces the sheet's whenever Connect has one. A blank on Connect's side
keeps the sheet's value: an import adds knowledge, never subtracts it.

Both writers call `apply`: the directory import after it writes a row, and the
Connect sync after it links. Either order converges on the same profile.

`has_used_connect` is overlaid only when Connect says True: its column defaults
to False, so a False there is as likely "never filled in" as a fact.

The office pin (`lat`/`lon`) stays the directory's: it is resolved from the
head-office town the sheet records and Connect does not. Where the
organisation WORKS is recomputed from the winning countries and regions.
"""

from __future__ import annotations

from connect_labs.labs.models import ConnectOrganization, LabsOrg
from connect_labs.marketplace.models import OrgProfile

CONNECT_OWNED = (
    "has_used_connect",
    "year_established",
    "team_size",
    "countries",
    "regions",
    "website",
    "office_address",
)


def connect_values(cached: ConnectOrganization) -> dict:
    """Connect's non-blank profile values, in `OrgProfile`'s terms."""
    from connect_labs.marketplace.directory import _split_list
    from connect_labs.microplans.core import iso as iso_codes

    values: dict = {}
    if cached.has_used_connect:
        values["has_used_connect"] = True
    if cached.year_of_establishment:
        values["year_established"] = cached.year_of_establishment
    if cached.team_size:
        values["team_size"] = cached.team_size
    names = [iso_codes.country_name(code) or code for code in cached.countries or []]
    if names:
        values["countries"] = names
    regions = _split_list((cached.regions or "").replace("\n", ";"))
    if regions:
        values["regions"] = regions
    if cached.website:
        values["website"] = cached.website
    if cached.office_address:
        values["office_address"] = cached.office_address
    return values


def _cached_for(org: LabsOrg) -> ConnectOrganization | None:
    if org.connect_organization_id is None:
        return None
    return ConnectOrganization.objects.filter(
        connect_id=org.connect_organization_id, gone_at__isnull=True, has_profile=True
    ).first()


def apply(org: LabsOrg) -> bool:
    """Overlay Connect's profile onto this organisation's directory profile.

    Returns whether anything changed. An organisation with no directory profile
    is left without one: a profile is what makes an organisation a directory
    partner (`pulse.partner_names`), and that is the directory's call.
    """
    cached = _cached_for(org)
    profile = OrgProfile.objects.filter(org=org).first()
    if cached is None or profile is None:
        return False

    changed = {k: v for k, v in connect_values(cached).items() if getattr(profile, k) != v}
    if not changed:
        return False
    if "countries" in changed or "regions" in changed:
        from connect_labs.pulse.hq_location import operating_areas

        countries = changed.get("countries", profile.countries)
        regions = changed.get("regions", profile.regions)
        changed["operating_areas"] = operating_areas(countries, "; ".join(regions or []))
    for name, value in changed.items():
        setattr(profile, name, value)
    profile.save(update_fields=[*changed, "imported_at"])
    return True


def apply_all() -> int:
    """`apply` to every linked organisation with a directory profile."""
    linked = LabsOrg.objects.filter(connect_organization_id__isnull=False, marketplace_profile__isnull=False)
    return sum(1 for org in linked if apply(org))
