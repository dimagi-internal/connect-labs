"""Load the Connect Web worker pool from the CONNECT_WEB_POOL_JSON secret into PoolWorker rows.

    manage.py connect_web_import_pool            # run on labs: gh workflow run run-labs-command.yml ...

The pool is ACE's Connect test workers (canopy capability `ace/test-worker`). Their backup codes are
sealed on canopy-web to agent devices, which labs is not, so the pool is copied once into Secrets
Manager (`labs-jj-app-connect-web-pool`, pinned on the web tier as CONNECT_WEB_POOL_JSON) and this
command upserts it into labs' encrypted columns. It never reads a value from argv and never prints
one. Rerun it after the secret changes (a rotated backup code, a new worker).

The secret is a JSON list: [{"phone": "+7426…", "name": "…", "backup_code": "…", "pin": "…"}, …].
`pin` is optional. Workers already in labs but not in the list are left alone (use the admin to
deactivate one).
"""

from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError

from connect_labs.connect_web.models import PoolWorker

POOL_ENV = "CONNECT_WEB_POOL_JSON"


def parse_pool(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise CommandError(f"{POOL_ENV} is not valid JSON") from None
    if not isinstance(data, list) or not data:
        raise CommandError(f"{POOL_ENV} must be a non-empty JSON list")
    workers = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise CommandError(f"{POOL_ENV}[{i}] is not an object")
        phone = str(item.get("phone") or "").strip()
        name = str(item.get("name") or "").strip()
        code = str(item.get("backup_code") or "").strip()
        if not phone.startswith("+") or not name or not code:
            # Name the entry by position only: its fields may be secrets.
            raise CommandError(f"{POOL_ENV}[{i}] needs phone (E.164), name and backup_code")
        workers.append({"phone": phone, "name": name, "backup_code": code, "pin": str(item.get("pin") or "").strip()})
    return workers


class Command(BaseCommand):
    help = f"Upsert Connect Web pool workers from the {POOL_ENV} secret (values never printed)."

    def handle(self, *args, **options):
        raw = os.environ.get(POOL_ENV, "")
        if not raw.strip():
            raise CommandError(f"{POOL_ENV} is not set on this task (pin labs-jj-app-connect-web-pool in web.json).")
        created = updated = 0
        for entry in parse_pool(raw):
            worker = PoolWorker.objects.filter(phone_number=entry["phone"]).first()
            is_new = worker is None
            worker = worker or PoolWorker(phone_number=entry["phone"])
            worker.display_name = entry["name"]
            worker.active = True
            if is_new or worker.backup_code != entry["backup_code"]:
                worker.backup_code = entry["backup_code"]
                worker.sign_in_blocked_reason = ""
            if entry["pin"]:
                worker.pin = entry["pin"]
            worker.save()
            created += is_new
            updated += not is_new
        self.stdout.write(self.style.SUCCESS(f"connect_web pool: {created} registered, {updated} updated"))
