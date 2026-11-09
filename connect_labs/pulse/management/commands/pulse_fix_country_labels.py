"""Fix the country labels labs has stored: each visit's, from labs' real borders, then each opportunity's.

``normalize.country_for`` used rough rectangles that overlap at borders, so
visits near one were labelled with the neighbouring country (Kenema, Sierra
Leone as Liberia; Bangui, Central African Republic as DR Congo). It now uses
labs' country borders, but labels already stored keep the old answer until
this runs once.

    python manage.py pulse_fix_country_labels --dry-run
    python manage.py pulse_fix_country_labels

Three steps:

1. Every stored visit with GPS gets ``country_for`` again (only changed rows are written).
2. Each opportunity takes the modal country of its visits, and its works follow
   (``ingest.refresh_opportunity_countries``, the same path the poller runs).
3. An opportunity with no stored visits (it stopped delivering before the visit
   window) has its most recent visit re-sampled from Connect, the same way it got
   a country in the first place (``ingest.sample_opportunity_countries``).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseWork
from connect_labs.pulse.normalize import _country_shapes, country_for, reset_country_shapes

BATCH = 5000


class Command(BaseCommand):
    help = "Re-label stored visits and opportunities with labs' real country borders."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what would change and stop.")
        parser.add_argument(
            "--skip-sample", action="store_true", help="Do not re-sample opportunities with no stored visits."
        )

    def handle(self, *args, **options):
        reset_country_shapes()
        if not _country_shapes():
            raise CommandError("No country borders loaded (AdminBoundary level 0 is empty); nothing would change.")

        changed, seen, moves = [], 0, {}
        qs = PulseEvent.objects.exclude(lat__isnull=True).exclude(lon__isnull=True).only("id", "lat", "lon", "country")
        for ev in qs.iterator(chunk_size=BATCH):
            seen += 1
            new = country_for(ev.lat, ev.lon)
            if new and new != ev.country:
                moves[(ev.country, new)] = moves.get((ev.country, new), 0) + 1
                ev.country = new
                changed.append(ev)
                if len(changed) >= BATCH and not options["dry_run"]:
                    PulseEvent.objects.bulk_update(changed, ["country"], batch_size=BATCH)
                    changed = []
        if changed and not options["dry_run"]:
            PulseEvent.objects.bulk_update(changed, ["country"], batch_size=BATCH)
        total_moved = sum(moves.values())
        self.stdout.write(f"visits with GPS: {seen:,}; relabelled: {total_moved:,}")
        for (old, new), n in sorted(moves.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {old or '(none)'} -> {new}: {n:,}")

        with_events = set(PulseEvent.objects.values_list("opportunity_id", flat=True).distinct())
        no_events = list(
            PulseOpportunity.objects.exclude(opportunity_id__in=with_events)
            .filter(opportunity_id__in=PulseWork.objects.values("opportunity_id"))
            .values_list("opportunity_id", flat=True)
        )
        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(f"dry run: would refresh opportunities and re-sample {len(no_events):,}")
            )
            return

        before = dict(PulseOpportunity.objects.values_list("opportunity_id", "country"))
        ingest.refresh_opportunity_countries()
        if not options["skip_sample"] and no_events:
            from connect_labs.pulse.client import get_client

            with get_client(timeout=120.0) as client:
                ingest.sample_opportunity_countries(client, limit=len(no_events), opp_ids=no_events)
            # Works denormalise country from their opportunity; push the re-sampled ones.
            ingest.refresh_opportunity_countries()
        after = dict(PulseOpportunity.objects.values_list("opportunity_id", "country"))
        moved = [(i, before.get(i), after[i]) for i in after if before.get(i) != after[i]]
        self.stdout.write(self.style.SUCCESS(f"opportunities whose country changed: {len(moved)}"))
        for i, old, new in sorted(moved):
            self.stdout.write(f"  {i}: {old or '(none)'} -> {new or '(none)'}")
