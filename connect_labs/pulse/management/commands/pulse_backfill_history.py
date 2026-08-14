"""Walk Pulse's full visit history, one opportunity at a time, resumably.

Separate from ``pulse_backfill`` on purpose. That command is a hydrate-a-fresh
environment tool with a days-deep dial; this one is the long haul: pull
everything, politely, in slices, and be safe to interrupt and re-run.

    python manage.py pulse_backfill_history --status
    python manage.py pulse_backfill_history --max-seconds 900
    python manage.py pulse_backfill_history --opp 765 --sleep 0
    python manage.py pulse_backfill_history --loop --max-seconds 900

**Resumability is per page, not per opportunity.** Each opportunity has its own
cursor recording how far back it has been walked, and that cursor is committed
after every page — so an interrupted run continues from where it stopped rather
than restarting the opportunity. On the largest programme (~1.25M visits) that
distinction is the difference between a run that finishes and one that cannot.

**Pacing is the default, not an option.** A full walk is ~1.6M rows off a
production endpoint that serialises every visit's form JSON. ``--sleep``
controls the gap between requests; it defaults to PULSE_BACKFILL_PAGE_PAUSE
rather than to zero, so the polite behaviour is what you get by not thinking
about it.

**Retention interacts with this.** Rows older than PULSE_EVENT_RETENTION_DAYS
are folded into the anonymous grid and deleted on the nightly job. Pulling deep
history while retention is 30 days means the rows are aggregated (into rollups
and grid cells, both permanent) and then discarded. That is a legitimate way to
run this — the aggregates are what the reports read — but if the intent is to
keep the visit rows as a local fact store, set PULSE_EVENT_RETENTION_DAYS=0
first. The command says which mode it is in before it starts.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from connect_labs.pulse import ingest, tasks
from connect_labs.pulse.client import PulseAuthError, get_poller_user
from connect_labs.pulse.models import PulseCursor, PulseEvent, PulseOpportunity

# Deep enough to mean "everything" without pretending to be infinite.
ALL_HISTORY_DAYS = 3650


class Command(BaseCommand):
    help = "Backfill the full visit history opportunity by opportunity, resumably."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="Report progress and exit.")
        parser.add_argument("--opp", type=int, action="append", help="Limit to opportunity id (repeatable).")
        parser.add_argument("--sleep", type=float, default=None, help="Seconds between pages.")
        parser.add_argument("--max-seconds", type=int, default=None, help="Stop after roughly this long.")
        parser.add_argument("--max-pages-per-opp", type=int, default=None, help="Bound each opportunity per pass.")
        parser.add_argument("--days", type=int, default=ALL_HISTORY_DAYS, help="History depth (default: all).")
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep running slices until every opportunity is complete.",
        )

    def handle(self, *args, **options):
        if options["status"]:
            self._status()
            return

        try:
            user = get_poller_user()
        except PulseAuthError as exc:
            self.stderr.write(self.style.ERROR(f"No poller: {exc}"))
            return

        retention = getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30)
        pause = options["sleep"]
        if pause is None:
            pause = float(getattr(settings, "PULSE_BACKFILL_PAGE_PAUSE", 0.25))

        self.stdout.write(self.style.MIGRATE_HEADING(f"Polling as {user.username}"))
        if retention in (None, 0, ""):
            self.stdout.write("  Retention disabled — visit rows will be KEPT as a local fact store.")
        else:
            self.stdout.write(
                f"  Retention {retention}d — rows older than that are aggregated into rollups and\n"
                f"  grid cells (both permanent) and then deleted. Set PULSE_EVENT_RETENTION_DAYS=0\n"
                f"  first if you want to keep the visit rows themselves."
            )
        self.stdout.write(f"  Pacing {pause}s between pages.\n")

        self._status()

        while True:
            started = time.monotonic()
            result = tasks.backfill_visits(
                days=options["days"],
                opportunity_ids=options["opp"],
                page_pause=pause,
                max_seconds=options["max_seconds"],
                max_pages_per_opp=options["max_pages_per_opp"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    "  stored {stored:,} rows · {opportunities_completed} opps done · "
                    "{opportunities_failed} failed · {opportunities_remaining} remaining "
                    "({elapsed_seconds}s)".format(**result)
                )
            )

            if not options["loop"]:
                break
            if not result["opportunities_remaining"]:
                self.stdout.write(self.style.SUCCESS("  All opportunities complete."))
                break
            # A pass that completes nothing and stores nothing will not complete
            # anything next time either -- looping on it would spin against prod
            # forever. Stop and let an operator look.
            if not result["stored"] and not result["opportunities_completed"]:
                self.stderr.write(self.style.WARNING("  A pass made no progress; stopping rather than spinning."))
                break
            if time.monotonic() - started < 1:
                break

        self._status()

    def _status(self):
        qs = PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT)
        total, complete = qs.count(), qs.filter(backfill_complete=True).count()
        held = PulseEvent.objects.count()
        lifetime = sum(PulseOpportunity.objects.values_list("lifetime_visit_count", flat=True))
        oldest = PulseEvent.objects.order_by("field_ts").values_list("field_ts", flat=True).first()

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Backfill status"))
        self.stdout.write(f"  opportunities : {complete}/{total} complete")
        self.stdout.write(f"  events held   : {held:,}")
        if lifetime:
            self.stdout.write(f"  lifetime      : {lifetime:,} visits ({held / lifetime:.1%} held)")
        if oldest:
            self.stdout.write(f"  oldest held   : {oldest:%Y-%m-%d}")
        self.stdout.write(f"  checked at    : {timezone.now():%Y-%m-%d %H:%M} UTC")
        self.stdout.write("")
