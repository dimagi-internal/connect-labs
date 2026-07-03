"""
Django management command to repair stale django-celery-beat PeriodicTask rows
left over from the ``commcare_connect`` -> ``connect_labs`` package rename (PR #797).

This deployment schedules periodic work with the django-celery-beat
DatabaseScheduler, so schedule state lives in the ``django_celery_beat_periodictask``
table. A code rename does not touch DB rows, so any PeriodicTask persisted before
the rename still references a ``commcare_connect.*`` task path (e.g.
``commcare_connect.labs.tasks.cleanup_expired_sql_cache``). Embedded beat keeps
enqueuing those messages under the old name, which the worker no longer registers
-- crashing the consumer and dropping other in-flight tasks.

This command rewrites the ``commcare_connect.`` prefix to ``connect_labs.`` on any
such row. It is idempotent (rows already pointing at ``connect_labs.`` are skipped)
and prints exactly what it changes. Run once on the labs deployment after deploy:

    python manage.py fix_stale_beat_tasks            # apply
    python manage.py fix_stale_beat_tasks --dry-run  # preview only
"""

from django.core.management.base import BaseCommand

LEGACY_TASK_PREFIX = "commcare_connect."
CURRENT_TASK_PREFIX = "connect_labs."


class Command(BaseCommand):
    help = "Rewrite stale 'commcare_connect.*' django-celery-beat PeriodicTask names to 'connect_labs.*'."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to the database.",
        )

    def handle(self, *args, **options):
        from django_celery_beat.models import PeriodicTask

        dry_run = options["dry_run"]

        stale = PeriodicTask.objects.filter(task__startswith=LEGACY_TASK_PREFIX)
        count = stale.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS("No stale 'commcare_connect.*' PeriodicTask rows found. Nothing to do.")
            )
            return

        self.stdout.write(f"Found {count} stale PeriodicTask row(s):")

        changed = 0
        for pt in stale:
            new_task = CURRENT_TASK_PREFIX + pt.task[len(LEGACY_TASK_PREFIX) :]
            self.stdout.write(f"  [{pt.pk}] {pt.name!r}: {pt.task}  ->  {new_task}")
            if not dry_run:
                pt.task = new_task
                pt.save(update_fields=["task"])
                changed += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run: {count} row(s) would be updated. No changes written."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated {changed} PeriodicTask row(s)."))
