"""Sync Connect Workers from Connect now, for one worker, one opportunity, or every due opportunity.

    manage.py connect_worker_sync --worker <username> [--discover] [--visits]
    manage.py connect_worker_sync --opportunity <id> [--visits]
    manage.py connect_worker_sync --refresh          # which opportunities the sync identity reads
    manage.py connect_worker_sync --due              # what the beat would run now

``--discover`` searches every opportunity the sync identity can read for the worker
(one request per opportunity). ``--visits`` lets the facade stream the user_visits
export when labs holds no visit cache for an opportunity (slow: it carries every form).
"""

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.workers.identity import WorkerSyncAuthError


class Command(BaseCommand):
    help = "Sync Connect Workers from Connect (one worker, one opportunity, or every due opportunity)."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--worker", help="PersonalID username")
        target.add_argument("--opportunity", type=int, help="Connect opportunity id")
        target.add_argument("--refresh", action="store_true", help="Refresh the opportunities the sync can read")
        target.add_argument("--due", action="store_true", help="Run the beat's sync of due opportunities")
        parser.add_argument("--discover", action="store_true", help="Search every readable opportunity")
        parser.add_argument("--visits", action="store_true", help="Allow streaming user_visits when no cache")

    def handle(self, *args, **opts):
        from connect_labs.workers import sync, tasks
        from connect_labs.workers.connect_facade import get_source

        try:
            if opts["due"]:
                result = tasks.sync_due()
            else:
                with get_source(visit_fetch=opts["visits"]) as source:
                    if opts["refresh"]:
                        result = sync.refresh_opportunities(source)
                    elif opts["opportunity"]:
                        result = sync.sync_opportunity(source, sync.opportunity_for(opts["opportunity"]))
                    else:
                        result = sync.sync_worker(opts["worker"], source=source, discover=opts["discover"])
        except WorkerSyncAuthError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, indent=2, default=str))
