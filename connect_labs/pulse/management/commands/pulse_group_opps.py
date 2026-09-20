"""Group the opportunities of one engagement Connect recorded as several.

    python manage.py pulse_group_opps --org cowacdi-interviews --service interview \
        --name "COWACDI Interviews" --why "..." --dry-run
    python manage.py pulse_group_opps --org cowacdi-interviews --service interview \
        --name "COWACDI Interviews" --why "..."

The selection is how the members were FOUND, not a standing rule. The matched
ids are written down, and an opportunity created later does not join by itself:
the engagements this exists for are finished, and a rule that kept sweeping
would one day sweep in something nobody looked at.

Prints every match before writing, and ``--dry-run`` writes nothing — the same
shape as ``marketplace_import``, and for the same reason: a grouping states
that several engagements were really one, and it should be read by a person
before it is true.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from connect_labs.marketplace.identity import slug_for
from connect_labs.pulse import costs, groups
from connect_labs.pulse.models import PulseOppGroup, PulseOpportunity


class Command(BaseCommand):
    help = "Group an engagement's Connect opportunities so Pulse reads them as one."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Connect org slug whose opportunities to group")
        parser.add_argument("--service", default="", help="Delivery type to match, e.g. interview")
        parser.add_argument("--name", required=True, help="What to call the engagement")
        parser.add_argument("--why", required=True, help="Why these are one engagement. Required.")
        parser.add_argument("--slug", default="", help="Defaults to a slug of the name")
        parser.add_argument("--match", default="", help="Only opportunities whose name contains this")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")

    def handle(self, *args, **opts):
        org, service = opts["org"].strip(), opts["service"].strip()
        if not org:
            raise CommandError("--org is required: a group belongs to one organisation")

        found = PulseOpportunity.objects.filter(org_slug=org)
        if service:
            found = found.filter(service_slug=service)
        if opts["match"]:
            found = found.filter(name__icontains=opts["match"])
        found = list(found.order_by("-lifetime_visit_count"))
        if not found:
            raise CommandError(f"no opportunities matched org={org!r} service={service!r}")

        slug = opts["slug"].strip() or slug_for(opts["name"])
        taken = [o for o in found if o.group_id is not None and o.group.slug != slug]
        if taken:
            names = ", ".join(f"{o.opportunity_id} (in {o.group.slug})" for o in taken[:5])
            raise CommandError(
                f"{len(taken)} of these already belong to another engagement: {names}. "
                "An opportunity is in one engagement or none."
            )

        self.stdout.write(f"{len(found)} opportunities → {opts['name']} ({slug})")
        for o in found:
            self.stdout.write(f"  {o.opportunity_id:>6}  {o.lifetime_visit_count:>8,}  {o.name}")
        total = sum(o.lifetime_visit_count or 0 for o in found)
        self.stdout.write(f"  {'':>6}  {total:>8,}  services in total")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing written"))
            return

        with transaction.atomic():
            group, created = PulseOppGroup.objects.get_or_create(
                slug=slug,
                defaults={"name": opts["name"], "org_slug": org, "why": opts["why"]},
            )
            if not created:
                group.name, group.org_slug, group.why = opts["name"], org, opts["why"]
                group.save(update_fields=["name", "org_slug", "why", "updated_at"])
            PulseOpportunity.objects.filter(opportunity_id__in=[o.opportunity_id for o in found]).update(group=group)

        # Both caches key on what just changed, and both are read per request.
        groups.invalidate()
        costs.invalidate()
        self.stdout.write(self.style.SUCCESS(f"{'created' if created else 'updated'} {slug}: {len(found)} members"))
