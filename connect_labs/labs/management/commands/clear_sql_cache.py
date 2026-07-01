"""
Django management command to clear expired SQL cache entries.

This is a local version of the cleanup_expired_sql_cache Celery task.

Usage:
    python manage.py clear_sql_cache
"""

from django.core.management.base import BaseCommand

from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache, RawVisitCache


class Command(BaseCommand):
    help = "Clear expired SQL cache entries (RawVisitCache, ComputedVisitCache, ComputedFLWCache)"

    def handle(self, *args, **options):
        self.stdout.write("Cleaning up expired SQL cache entries...")

        raw_deleted = RawVisitCache.cleanup_expired()
        visit_deleted = ComputedVisitCache.cleanup_expired()
        flw_deleted = ComputedFLWCache.cleanup_expired()

        total = raw_deleted + visit_deleted + flw_deleted

        if total > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully cleaned up {total} expired entries:\n"
                    f"  - Raw visits: {raw_deleted}\n"
                    f"  - Computed visits: {visit_deleted}\n"
                    f"  - FLW results: {flw_deleted}"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("No expired cache entries found."))
