"""Import Connect Workers' past Open Chat Studio conversations into labs Messaging.

    manage.py import_ocs_history amina123 bello42     # these workers, now
    manage.py import_ocs_history --all --queue         # every worker labs holds, as Celery tasks

Idempotent: a session already imported gains only its new messages. Needs OCS_API_KEY.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit_trail.context import audit_context


class Command(BaseCommand):
    help = "Import Connect Workers' past Open Chat Studio conversations as Messaging threads."

    def add_arguments(self, parser):
        parser.add_argument("usernames", nargs="*", help="PersonalID usernames")
        parser.add_argument("--all", action="store_true", help="every Connect Worker labs holds")
        parser.add_argument("--queue", action="store_true", help="queue one Celery task per worker")

    def handle(self, *args, **options):
        from connect_labs.messaging.canopy_bridge import ocs_import, tasks
        from connect_labs.workers.models import ConnectWorker
        from connect_labs.workers.ocs_link import OCSNotConfigured

        workers = ConnectWorker.objects.filter(erased_at__isnull=True)
        if options["all"]:
            pass
        elif options["usernames"]:
            workers = workers.filter(username__in=options["usernames"])
        else:
            raise CommandError("name usernames, or pass --all")
        total = {"workers": 0, "sessions": 0, "threads": 0, "messages": 0, "errors": 0}
        with audit_context(source="script"):
            for worker in workers.order_by("username").iterator():
                total["workers"] += 1
                if options["queue"]:
                    tasks.import_ocs_history.delay(worker.pk)
                    continue
                try:
                    counts = ocs_import.import_worker(worker)
                except OCSNotConfigured as exc:
                    raise CommandError(str(exc)) from exc
                for key, value in counts.items():
                    total[key] += value
                self.stdout.write(f"{worker.username}: {counts}")
        self.stdout.write(self.style.SUCCESS(f"done: {total}"))
