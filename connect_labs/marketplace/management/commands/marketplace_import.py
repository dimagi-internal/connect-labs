"""Load the organisation registry from the LLO Directory.

    make manage CMD="marketplace_import --dry-run"
    make manage CMD="marketplace_import"

This replaces ``pulse_partner_import``, which read two columns of the same sheet
to put names on Connect slugs. The directory is the master organisation list —
an organisation that answered an EOI and was not selected never gets a Connect
row at all — so reading only the columns Connect could already explain was
reading it backwards.

Not destructive by default: organisations absent from the sheet are left alone
unless ``--prune`` says the sheet is authoritative. That mirrors
``targeting_import`` and exists for the same reason — a half-loaded sheet should
not silently delete an organisation mid-run.

The sheet is never written. Import is a read-only path over the master registry,
so a parsing or matching bug cannot reshape what people maintain by hand.
"""

from __future__ import annotations

import collections

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import directory
from connect_labs.marketplace.identity import ensure_org
from connect_labs.marketplace.models import OrgConnectSlug, OrgContact, OrgProfile
from connect_labs.pulse.hq_location import resolve as resolve_hq

PROFILE_FIELDS = (
    "has_used_connect",
    "year_established",
    "team_size",
    "flws_managed",
    "countries",
    "regions",
    "sectors",
    "website",
    "office_address",
    "notes",
    "msa_link",
    "work_order_link",
)


def import_directory(org_rows, contact_rows, date_rows, map_rows, *, prune: bool = False) -> dict:
    """Upsert the registry from four tabs' rows. Returns a stats dict.

    Takes rows rather than a spreadsheet id so the whole of it is testable
    without a network or a service account.
    """
    orgs = directory.parse_organizations(org_rows)
    if not orgs:
        raise ValueError(
            f"'{directory.ORGANIZATIONS_TAB}' yielded no organisations — refusing to "
            "treat that as an empty registry. An empty read is a failed read."
        )

    dates = directory.parse_dates(date_rows)
    known = {o.name for o in orgs}
    mapped, skipped = directory.parse_mapping(map_rows, known)
    contacts = directory.parse_contacts(contact_rows)

    stats: dict = {
        "organisations": len(orgs),
        "contacts": 0,
        "attributions": 0,
        "skipped": list(skipped),
    }
    tiers: collections.Counter = collections.Counter()

    with transaction.atomic():
        by_name = {}
        for row in orgs:
            org = ensure_org(row.name, short_name=row.short_name)
            by_name[row.name] = org

            fields = {name: getattr(row, name) for name in PROFILE_FIELDS}
            fields["source_row"] = row.source_row

            located = resolve_hq(row.raw_countries, row.raw_regions, row.office_address)
            if located:
                fields.update(
                    country_iso3=located.iso3,
                    lat=located.lat,
                    lon=located.lon,
                    location_precision=located.precision,
                    location_label=located.label,
                )
                tiers[located.precision] += 1

            joined = dates.get(row.name)
            if joined:
                fields["joined_at"], fields["joined_basis"] = joined

            OrgProfile.objects.update_or_create(org=org, defaults=fields)

        seen_emails: list[str] = []
        for contact in contacts:
            org = by_name.get(contact.org_name)
            if org is None:
                # Never mint an organisation from a contact row: a typo on the
                # Contacts tab would create one that does not exist.
                stats["skipped"].append(
                    f"contact {contact.email} → {contact.org_name!r} (not on the "
                    f"{directory.ORGANIZATIONS_TAB} tab)"
                )
                continue
            # An email is unique per organisation, but a person can move between
            # them. Clear any stale row elsewhere before writing this one.
            OrgContact.objects.filter(email=contact.email).exclude(org=org).delete()
            OrgContact.objects.update_or_create(
                org=org,
                email=contact.email,
                defaults={
                    "full_name": contact.full_name,
                    "role_title": contact.role_title,
                    "is_main_poc": contact.is_main_poc,
                    "phone": contact.phone,
                    "notes": contact.notes,
                    "source_row": contact.source_row,
                },
            )
            seen_emails.append(contact.email)
            stats["contacts"] += 1

        for slug, (target, why) in mapped.items():
            OrgConnectSlug.objects.update_or_create(slug=slug, defaults={"org": by_name[target], "why": why})
            stats["attributions"] += 1

        if prune:
            stale_orgs = LabsOrg.objects.filter(marketplace_profile__isnull=False).exclude(name__in=known)
            stats["pruned_organisations"] = stale_orgs.count()
            stale_orgs.delete()

            stats["pruned_contacts"] = OrgContact.objects.exclude(email__in=seen_emails).delete()[0]
            stats["pruned_attributions"] = OrgConnectSlug.objects.exclude(slug__in=mapped).delete()[0]

    stats["located"] = dict(tiers)
    return stats


class Command(BaseCommand):
    help = "Import the organisation registry from the LLO Directory"

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=directory.DIRECTORY_ID)
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Treat the sheet as authoritative: delete organisations, contacts and attributions it does not carry",
        )

    def handle(self, *args, **opts):
        sid = opts["spreadsheet_id"]
        try:
            org_rows = directory.read_tab(sid, directory.ORGANIZATIONS_TAB)
            contact_rows = directory.read_tab(sid, directory.CONTACTS_TAB)
            date_rows = directory.read_tab(sid, directory.DATES_TAB)
            map_rows = directory.read_tab(sid, directory.MAPPING_TAB)
        except Exception as exc:  # noqa: BLE001 — surfaced verbatim, the causes are many
            raise CommandError(
                f"Could not read the directory: {exc}\n"
                "Set LABS_SYNTHETIC_GDRIVE_SA_KEY, and confirm the service account "
                "has been shared the sheet."
            ) from exc

        if opts["dry_run"]:
            orgs = directory.parse_organizations(org_rows)
            contacts = directory.parse_contacts(contact_rows)
            mapped, skipped = directory.parse_mapping(map_rows, {o.name for o in orgs})
            self.stdout.write(
                f"would import {len(orgs)} organisations, {len(contacts)} contacts, "
                f"{len(mapped)} slug attributions"
            )
            for line in skipped:
                self.stdout.write(self.style.WARNING(f"  skipped {line}"))
            return

        try:
            stats = import_directory(org_rows, contact_rows, date_rows, map_rows, prune=opts["prune"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for line in stats["skipped"]:
            self.stdout.write(self.style.WARNING(f"  skipped {line}"))
        located = ", ".join(f"{n} {tier}" for tier, n in stats["located"].items())
        self.stdout.write(
            self.style.SUCCESS(
                f"imported {stats['organisations']} organisations, {stats['contacts']} contacts, "
                f"{stats['attributions']} slug attributions ({located or 'none located'})"
            )
        )

        from connect_labs.pulse.partner_names import invalidate

        invalidate()
