"""Hydrate Pulse's tables on a deployed environment.

The beat schedule keeps Pulse *current* but does not fill it in: cursors seed
to the present, so tailing only ever captures work from now on. History has to
be pulled deliberately, which is why backfill is a manual one-shot rather than
a scheduled job — it is the expensive path and must never stall the live tail.

    python manage.py pulse_backfill --works              # money spine, all history
    python manage.py pulse_backfill --visits --days 30   # map + ticker
    python manage.py pulse_backfill --countries          # geography for old opps
    python manage.py pulse_backfill --fold               # run retention now
    python manage.py pulse_backfill --all --days 30

Pair ``--fold`` with ``--visits``. A history pull loads visits that are mostly
older than ``PULSE_EVENT_RETENTION_DAYS`` already, so without it those rows sit
at beneficiary level until the next nightly fold -- up to 24 hours, purely
because of when the backfill happened to run.

Cost, measured: ``completed_works`` is ~53 B/row gzipped, so the whole works
history is ~87 MB. ``user_visits`` is ~4.6 KB/row because it ships every form's
full JSON with no way to ask for less, so visit depth is the expensive dial —
30 days is usually plenty for a live-feeling map.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.client import get_client, get_poller_user
from connect_labs.pulse.models import PulseCursor, PulseEvent, PulseGridCell, PulseOpportunity, PulseWork


class Command(BaseCommand):
    help = "Backfill Pulse works, visits and geography from the Connect export API."

    def add_arguments(self, parser):
        parser.add_argument("--works", action="store_true", help="Backfill completed_works (money spine).")
        parser.add_argument("--visits", action="store_true", help="Backfill user_visits (map + ticker).")
        parser.add_argument("--countries", action="store_true", help="Resolve country by sampling one visit per opp.")
        parser.add_argument("--all", action="store_true", help="All three.")
        parser.add_argument("--days", type=int, default=30, help="Visit history depth in days (default 30).")
        parser.add_argument("--fold", action="store_true", help="Fold aged visits into the anonymous grid afterwards.")

    def handle(self, *args, **options):
        do_all = options["all"]
        want_works = options["works"] or do_all
        want_visits = options["visits"] or do_all
        want_countries = options["countries"] or do_all

        # --fold is an action in its own right, not a modifier: it is the only
        # way to run retention on demand, and it needs no Connect call at all.
        # Excluding it from this guard made `pulse_backfill --fold` print
        # "Nothing to do" and exit before reaching the fold below -- silently,
        # with a zero exit code, which reads as "folded nothing" rather than
        # "did nothing". Retention is the wrong thing to no-op quietly: the rows
        # it clears are the only beneficiary-level records Pulse holds.
        want_fold = options["fold"] or do_all
        if not (want_works or want_visits or want_countries or want_fold):
            self.stdout.write(
                self.style.WARNING("Nothing to do — pass --works, --visits, --countries, --fold or --all.")
            )
            return

        if want_works or want_visits or want_countries:
            user = get_poller_user()
            self.stdout.write(self.style.MIGRATE_HEADING(f"Polling as {user.username}"))

            with get_client(timeout=300.0) as client:
                ingest.refresh_opportunities(client)
                ingest.ensure_cursors()

                if want_works:
                    self._run("works", lambda: self._backfill_works(client))
                if want_visits:
                    self._run(f"visits ({options['days']}d)", lambda: self._backfill_visits(client, options["days"]))
                if want_countries:
                    self._run("countries", lambda: ingest.sample_opportunity_countries(client, limit=1000))

        if want_fold:
            self._run("fold to grid", lambda: ingest.fold_events_to_grid()["folded"])

        ingest.refresh_opportunity_countries()
        ingest.resync_service_slugs()
        ingest.rebuild_rollups(since=timezone.now() - timezone.timedelta(days=options["days"]))
        self._report()

    def _run(self, label, fn):
        t0 = time.time()
        self.stdout.write(f"  {label}… ")
        try:
            result = fn()
        except Exception as exc:  # one stage failing must not lose the others
            self.stdout.write(self.style.ERROR(f"  {label} failed after {time.time() - t0:.0f}s: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS(f"  {label}: {result} in {time.time() - t0:.0f}s"))

    def _backfill_works(self, client) -> int:
        total = 0
        for cursor in PulseCursor.objects.filter(endpoint=ingest.WORKS_ENDPOINT).order_by("-newest_sync_ts"):
            try:
                total += ingest.sync_works(client, cursor, max_rows=200000)["stored"]
            except Exception as exc:
                self.stderr.write(f"    opp {cursor.opportunity_id}: {exc}")
        return total

    def _backfill_visits(self, client, days: int) -> int:
        cutoff = timezone.now() - timezone.timedelta(days=days)
        total = 0
        # Most-recently-active first, so the map fills where the story is.
        for cursor in PulseCursor.objects.filter(endpoint=ingest.VISITS_ENDPOINT).order_by("-newest_sync_ts"):
            opp = PulseOpportunity.objects.filter(opportunity_id=cursor.opportunity_id).first()
            endpoint = f"/export/opportunity/{cursor.opportunity_id}/{ingest.VISITS_ENDPOINT}/"
            params = {"cursor_order": "reverse", "page_size": ingest.PAGE_SIZE}
            if cursor.backfill_oldest_id:
                params["last_id"] = cursor.backfill_oldest_id
            try:
                for page in client.paginate(endpoint, params=params, partial_ok=True):
                    if not page:
                        continue
                    stored, off_map = ingest._store_events(page, opp)
                    total += stored
                    if off_map:
                        ingest._bump_off_map(off_map)
                    ids = [r.get("id") for r in page if r.get("id")]
                    if ids:
                        cursor.backfill_oldest_id = min(ids)
                        cursor.last_id = max(cursor.last_id or 0, max(ids))
                    oldest = min((r.get("date_created") or "") for r in page)
                    if oldest and oldest < cutoff.isoformat():
                        break  # declared via partial_ok
                cursor.save(update_fields=["backfill_oldest_id", "last_id"])
            except Exception as exc:
                self.stderr.write(f"    opp {cursor.opportunity_id}: {exc}")
        return total

    def _report(self):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Pulse tables"))
        for label, n in [
            ("opportunities", PulseOpportunity.objects.count()),
            ("works", PulseWork.objects.count()),
            ("events", PulseEvent.objects.count()),
            ("grid cells", PulseGridCell.objects.count()),
        ]:
            self.stdout.write(f"  {label:<16} {n:>10,}")
