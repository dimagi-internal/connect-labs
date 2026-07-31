"""Re-derive the density layer so its cells carry a programme.

Cells used to key on ``(lat_q, lon_q, service_slug)``. The events they were
folded from always carried ``program_id`` — the fold simply never selected it —
so a map filtered to one programme fell back to matching on delivery type and
lit up every country that type operates in. A Nigeria-only programme glowed
across Cameroon and DR Congo beside a header reading "COUNTRIES 1".

The fix is to re-fold, which needs the visits back. They are re-fetchable:
``completed_works`` is the money spine, but ``user_visits`` is the only source
of GPS, and Connect will serve it again.

**Order matters.** Folding is additive — a cell's ``n`` accumulates — so
re-folding without discarding the old cells would double every count on the
map. This command purges first, and refuses to purge unless it is going to
rebuild.

    python manage.py pulse_regrid --dry-run
    python manage.py pulse_regrid --days 400

Between the purge and the fold the map has no density layer, only its live
points. That window is the length of a visit backfill (~10 min for ~175k
events), which is why this is a deliberate one-shot rather than something the
beat schedule does.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseCursor, PulseEvent, PulseGridCell


class Command(BaseCommand):
    help = "Purge and rebuild the grid so density cells carry a programme."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=400, help="Visit history depth to re-fetch (default 400).")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change and stop.")
        parser.add_argument(
            "--skip-backfill",
            action="store_true",
            help="Fold what is already stored, without re-fetching visits first.",
        )

    def handle(self, *args, **options):
        stats = PulseGridCell.objects.aggregate(
            total=Count("id"),
            unattributed=Count("id", filter=Q(program_id=None)),
        )
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"grid cells: {stats['total']:,} ({stats['unattributed']:,} with no programme)")
        )
        self.stdout.write(f"stored events: {PulseEvent.objects.count():,}")

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nWould delete {stats['unattributed']:,} unattributed cells, "
                    f"re-fetch {options['days']}d of visits, then fold."
                )
            )
            return

        # Purge only the cells that lack a programme. Ones already folded with
        # attribution are correct, and re-folding them from re-fetched visits is
        # what would double-count.
        deleted, _ = PulseGridCell.objects.filter(program_id=None).delete()
        self.stdout.write(self.style.SUCCESS(f"purged {deleted:,} unattributed cells"))

        if not options["skip_backfill"]:
            # The backfill pages BACKWARDS from backfill_oldest_id, so a cursor
            # left where the last run stopped would decline to re-read the
            # history that was folded away. Clearing it makes the walk start
            # over.
            reset = PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT).update(backfill_oldest_id=None)
            self.stdout.write(f"reset {reset:,} backfill cursors")

            self.stdout.write("re-fetching visits (the slow part) …")
            # Delegate rather than reimplement: this is the same path that
            # populated the map in the first place, and --fold now keys on
            # programme.
            call_command("pulse_backfill", "--visits", "--fold", days=options["days"])
        else:
            result = ingest.fold_events_to_grid()
            self.stdout.write(
                self.style.SUCCESS(f"folded {result['folded']:,} points, dropped {result['deleted']:,} rows")
            )

        after = PulseGridCell.objects.aggregate(
            total=Count("id"),
            unattributed=Count("id", filter=Q(program_id=None)),
        )
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"grid cells now: {after['total']:,} ({after['unattributed']:,} still unattributed)"
            )
        )
