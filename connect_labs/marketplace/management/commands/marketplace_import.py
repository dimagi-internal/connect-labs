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
from django.db.models import ProtectedError
from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import directory, eoi
from connect_labs.marketplace.identity import ensure_org
from connect_labs.marketplace.models import OrgConnectSlug, OrgContact, OrgProfile
from connect_labs.marketplace.quality import audit, findings_tab_rows
from connect_labs.pulse.hq_location import resolve as resolve_hq

PROFILE_FIELDS = (
    "has_used_connect",
    "year_established",
    "team_size",
    "countries",
    "regions",
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

        # An organisation the directory never dated still belongs on the network
        # growth curve. Stamp the first date we saw it and then leave it alone:
        # writing it down once, here, is what stops the whole undated cohort
        # sliding forward every day the beat runs. Carried over from
        # pulse_partner_import, where the curve first needed it.
        stamped = OrgProfile.objects.filter(joined_at__isnull=True).update(joined_at=timezone.localdate())
        stats["stamped_with_today"] = stamped

        for slug, (target, why) in mapped.items():
            OrgConnectSlug.objects.update_or_create(slug=slug, defaults={"org": by_name[target], "why": why})
            stats["attributions"] += 1

        if prune:
            stale_orgs = list(LabsOrg.objects.filter(marketplace_profile__isnull=False).exclude(name__in=known))
            stats["pruned_organisations"] = 0
            stats["kept_in_use"] = 0
            for org in stale_orgs:
                # An organisation other domains depend on -- a supplier with
                # quotes and orders, a store's operator, a payee -- is still a
                # company after it leaves the directory. Only the directory's
                # own profile goes; deleting the organisation would either be
                # refused (crashing the import) or cascade into records that
                # are not the directory's.
                try:
                    with transaction.atomic():
                        org.delete()
                    stats["pruned_organisations"] += 1
                except ProtectedError:
                    OrgProfile.objects.filter(org=org).delete()
                    stats["kept_in_use"] += 1

            stats["pruned_contacts"] = OrgContact.objects.exclude(email__in=seen_emails).delete()[0]
            stats["pruned_attributions"] = OrgConnectSlug.objects.exclude(slug__in=mapped).delete()[0]

    stats["located"] = dict(tiers)
    return stats


class Command(BaseCommand):
    def _report_findings(self, org_rows, contact_rows, orgs, contacts, skipped):
        """Print what could not be read cleanly, with a row number each.

        The directory is hand-maintained and confidence in it is patchy, so a
        run whose only output is a success count hides the rows that need a
        person.
        """
        findings = audit(org_rows, contact_rows, orgs, contacts, skipped)
        if not findings:
            self.stdout.write(self.style.SUCCESS("no data-quality findings"))
            return findings, ""
        self.stdout.write(self.style.WARNING(f"\n{len(findings)} data-quality findings:"))
        by_kind: dict[str, int] = {}
        for finding in findings:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
            self.stdout.write(f"  {finding}")
        counts = ", ".join(f"{n} {kind}" for kind, n in sorted(by_kind.items()))
        self.stdout.write("  " + counts)
        return findings, counts

    help = "Import the organisation registry from the LLO Directory"

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=directory.DIRECTORY_ID)
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
        parser.add_argument(
            "--check-access",
            action="store_true",
            help="Verify a real read of every round's response sheet and record the result",
        )
        parser.add_argument(
            "--eoi",
            action="store_true",
            help="Also import EOI/RFP rounds and their submissions",
        )
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
            self._report_findings(org_rows, contact_rows, orgs, contacts, skipped)
            return

        try:
            stats = import_directory(org_rows, contact_rows, date_rows, map_rows, prune=opts["prune"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        findings, counts = self._report_findings(
            org_rows,
            contact_rows,
            directory.parse_organizations(org_rows),
            directory.parse_contacts(contact_rows),
            stats["skipped"],
        )
        # To the sheet as well as to the log: the people who can fix these work
        # in the spreadsheet, and a findings list that only reaches CloudWatch
        # is one nobody who maintains the directory will ever read.
        try:
            directory.write_tab(
                sid,
                directory.FINDINGS_TAB,
                findings_tab_rows(findings, when=timezone.now().strftime("%Y-%m-%d %H:%M UTC"), counts_line=counts),
            )
            self.stdout.write(f"  findings published to the '{directory.FINDINGS_TAB}' tab")
        except Exception as exc:  # noqa: BLE001 — never fail an import over its own report
            self.stdout.write(self.style.WARNING(f"  could not publish findings: {exc}"))
        located = ", ".join(f"{n} {tier}" for tier, n in stats["located"].items())
        self.stdout.write(
            self.style.SUCCESS(
                f"imported {stats['organisations']} organisations, {stats['contacts']} contacts, "
                f"{stats['attributions']} slug attributions ({located or 'none located'})"
            )
        )

        from connect_labs.pulse.partner_names import invalidate

        invalidate()

        if opts["eoi"] or opts["check_access"]:
            self._rounds(sid, opts)

    def _rounds(self, sid, opts):
        """Import the EOI/RFP tab, then each round's submissions."""
        round_rows = directory.read_tab(sid, directory.ROUNDS_TAB)
        rounds, skipped = directory.parse_rounds(round_rows)
        for line in skipped:
            self.stdout.write(self.style.WARNING(f"  round skipped: {line}"))
        stats = eoi.upsert_rounds(rounds)
        self.stdout.write(self.style.SUCCESS(f"{stats['rounds']} rounds"))

        results = eoi.check_access(directory.read_tab, write_back_to=rounds, spreadsheet_id=sid)
        readable = [r for r in results if r["state"] == "ok"]
        for result in results:
            if result["state"] != "ok":
                self.stdout.write(self.style.WARNING(f"  {result['slug']}: {result['state']} — {result['detail']}"))
        self.stdout.write(f"{len(readable)} of {len(results)} rounds readable")

        if not opts["eoi"]:
            return

        # Verdicts a person already reached, which outrank every inference.
        known = {o.name for o in directory.parse_organizations(directory.read_tab(sid, directory.ORGANIZATIONS_TAB))}
        try:
            verdict_rows = directory.read_tab(sid, directory.RESPONSE_MAPPING_TAB)
        except Exception:  # noqa: BLE001 — the tab is optional until someone needs it
            verdict_rows = []
            self.stdout.write(f"  (no {directory.RESPONSE_MAPPING_TAB} tab yet — nothing to apply)")
        verdicts, verdict_skips = directory.parse_response_mapping(verdict_rows, known)
        for line in verdict_skips:
            self.stdout.write(self.style.WARNING(f"  verdict refused: {line}"))
        if verdicts:
            self.stdout.write(f"  {len(verdicts)} human verdicts to apply")

        totals = {"submissions": 0, "matched": 0, "unmatched": 0}
        for result in readable:
            round_ = eoi.Solicitation.objects.get(slug=result["slug"])
            rows = directory.read_tab(round_.response_spreadsheet_id, round_.response_tab or "Form Responses 1")
            got = eoi.ingest_round(round_, rows, human_verdicts=verdicts)
            for key in totals:
                totals[key] += got[key]
            self.stdout.write(
                f"  {round_.slug}: {got['submissions']} submissions, "
                f"{got['matched']} matched, {got['unmatched']} unmatched"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{totals['submissions']} submissions — {totals['matched']} matched to an organisation, "
                f"{totals['unmatched']} awaiting a human verdict"
            )
        )
