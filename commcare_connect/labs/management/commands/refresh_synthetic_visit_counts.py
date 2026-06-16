"""Backfill / refresh the cached user_visits count on SyntheticOpportunity rows.

The labs-context opportunity picker shows ``opp.visit_count``; this command loads
each opp's GDrive ``user_visits.json`` fixture and stores the count so the picker
reads it cheaply (instead of the hardcoded 0). Run after a deploy that adds the
field, or after regenerating an opp's fixtures.

    python manage.py refresh_synthetic_visit_counts            # all enabled opps
    python manage.py refresh_synthetic_visit_counts --opp 10010
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.visit_count import refresh_visit_count


class Command(BaseCommand):
    help = "Refresh the cached user_visits count for synthetic opportunities."

    def add_arguments(self, parser):
        parser.add_argument(
            "--opp",
            type=int,
            default=None,
            help="Only refresh this opportunity_id (default: all enabled synthetic opps).",
        )

    def handle(self, *args, **options):
        qs = SyntheticOpportunity.objects.filter(enabled=True)
        if options["opp"] is not None:
            qs = qs.filter(opportunity_id=options["opp"])

        opps = list(qs)
        if not opps:
            self.stdout.write(self.style.WARNING("No matching synthetic opportunities."))
            return

        for opp in opps:
            count = refresh_visit_count(opp)
            if count is None:
                self.stdout.write(self.style.ERROR(f"opp {opp.opportunity_id} ({opp.label}): FAILED (see logs)"))
            else:
                self.stdout.write(self.style.SUCCESS(f"opp {opp.opportunity_id} ({opp.label}): visit_count = {count}"))
