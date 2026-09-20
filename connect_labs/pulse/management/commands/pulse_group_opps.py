"""Group the opportunities of engagements Connect recorded as several.

    make manage CMD="pulse_group_opps --dry-run"
    make manage CMD="pulse_group_opps"

The groupings live on the LLO Directory's **Connect Opportunity Groups** tab,
one row per opportunity, each carrying the reason it belongs. That is where the
other verdicts code cannot infer already live -- ``Connect Org Mapping`` for
partner slugs, ``EOI Response Mapping`` for submissions -- and for the same
reason: the people who decide that several engagements were really one do not
review pull requests, and a fact written into the repository drifts from what
they maintain with nothing to detect the drift.

So this command only applies what the sheet says. It writes nothing back, and
an unreadable row is reported rather than guessed at.

The sheet is authoritative for the groups it names: an opportunity listed under
a group joins it, and one that has silently dropped off the tab leaves it. A
group the sheet does not mention at all is left alone and reported, the same
shape as ``marketplace_import`` without ``--prune``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.marketplace import directory
from connect_labs.pulse import costs, groups
from connect_labs.pulse.models import PulseOppGroup, PulseOpportunity


def apply_groups(parsed: dict, *, dry_run: bool = False) -> dict:
    """Make the database say what the sheet says. Returns a report.

    Takes parsed rows rather than a spreadsheet id, so the whole of it is
    testable without a network or a service account.
    """
    report: dict = {"groups": [], "skipped": [], "detached": 0}
    if not parsed:
        raise ValueError(
            f"'{directory.OPPORTUNITY_GROUPS_TAB}' yielded no groupings — refusing to treat "
            "that as an empty sheet. An empty read is a failed read."
        )

    wanted_ids = {oid for group in parsed.values() for oid in group.members}
    opps = {o.opportunity_id: o for o in PulseOpportunity.objects.filter(opportunity_id__in=wanted_ids)}

    plans = []
    for group in parsed.values():
        members = []
        for oid in group.members:
            opp = opps.get(oid)
            if opp is None:
                report["skipped"].append(f"{group.slug}: opportunity {oid} is not in Pulse")
                continue
            if opp.is_test:
                # Scaffolding is out of every Pulse figure already; grouping it
                # would put a UAT run inside a real engagement's membership.
                report["skipped"].append(f"{group.slug}: opportunity {oid} ({opp.name}) is a test — not grouped")
                continue
            if opp.org_slug and group.org_slug and opp.org_slug != group.org_slug:
                report["skipped"].append(
                    f"{group.slug}: opportunity {oid} belongs to {opp.org_slug!r}, not {group.org_slug!r}"
                )
                continue
            members.append(opp)
        plans.append((group, members))
        report["groups"].append(
            {
                "slug": group.slug,
                "name": group.name,
                "org_slug": group.org_slug,
                "members": [o.opportunity_id for o in members],
                "visits": sum(o.lifetime_visit_count or 0 for o in members),
            }
        )

    if dry_run:
        return report

    with transaction.atomic():
        for group, members in plans:
            row, created = PulseOppGroup.objects.get_or_create(
                slug=group.slug,
                defaults={"name": group.name, "org_slug": group.org_slug, "why": group.why},
            )
            if not created:
                row.name, row.org_slug, row.why = group.name, group.org_slug, group.why
                row.save(update_fields=["name", "org_slug", "why", "updated_at"])
            ids = [o.opportunity_id for o in members]
            # Anything that dropped off the tab leaves the group: the sheet is
            # authoritative for the groups it names, in both directions.
            report["detached"] += (
                PulseOpportunity.objects.filter(group=row).exclude(opportunity_id__in=ids).update(group=None)
            )
            PulseOpportunity.objects.filter(opportunity_id__in=ids).update(group=row)

    unmentioned = PulseOppGroup.objects.exclude(slug__in=list(parsed)).values_list("slug", flat=True)
    for slug in unmentioned:
        report["skipped"].append(f"{slug}: in labs but not on the tab — left alone")

    # Both caches key on what just changed, and both are read per request.
    groups.invalidate()
    costs.invalidate()
    return report


class Command(BaseCommand):
    help = "Apply the LLO Directory's opportunity groupings to Pulse."

    def add_arguments(self, parser):
        parser.add_argument("--spreadsheet-id", default=directory.DIRECTORY_ID)
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")

    def handle(self, *args, **opts):
        try:
            rows = directory.read_tab(opts["spreadsheet_id"], directory.OPPORTUNITY_GROUPS_TAB)
        except Exception as exc:  # noqa: BLE001 — the cause is what the operator needs
            raise CommandError(f"could not read '{directory.OPPORTUNITY_GROUPS_TAB}': {exc}") from exc

        parsed, unreadable = directory.parse_opportunity_groups(rows)
        try:
            report = apply_groups(parsed, dry_run=opts["dry_run"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for group in report["groups"]:
            self.stdout.write(
                f"{group['name']} ({group['slug']}) · {group['org_slug']} · "
                f"{len(group['members'])} opportunities · {group['visits']:,} services"
            )
            self.stdout.write(f"  {', '.join(str(i) for i in group['members'])}")

        for line in unreadable + report["skipped"]:
            self.stdout.write(self.style.WARNING(f"  {line}"))

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing written"))
            return
        if report["detached"]:
            self.stdout.write(f"{report['detached']} opportunities left a group the tab no longer lists")
        self.stdout.write(self.style.SUCCESS(f"{len(report['groups'])} groupings applied"))
