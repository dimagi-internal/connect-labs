"""Load the Connect Web worker pool from the CONNECT_WEB_POOL_JSON secret into PoolWorker rows.

    manage.py connect_web_import_pool

The worker picker already does this on its own after a deploy (connect_web/pool.py); this is the
on-demand version. It never reads a value from argv and never prints one.
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from connect_labs.connect_web.pool import POOL_ENV, PoolError, import_pool


class Command(BaseCommand):
    help = f"Upsert Connect Web pool workers from the {POOL_ENV} secret (values never printed)."

    def handle(self, *args, **options):
        raw = os.environ.get(POOL_ENV, "")
        if not raw.strip():
            raise CommandError(f"{POOL_ENV} is not set on this task (pin labs-jj-app-connect-web-pool in web.json).")
        try:
            created, updated = import_pool(raw)
        except PoolError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f"connect_web pool: {created} registered, {updated} updated"))
