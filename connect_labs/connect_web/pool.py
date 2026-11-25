"""The Connect Web worker pool: ACE's Connect test workers, loaded from the CONNECT_WEB_POOL_JSON secret.

Their backup codes are sealed on canopy-web to agent devices, which labs is not, so the pool is
copied once into Secrets Manager (`labs-jj-app-connect-web-pool`, pinned on the web tier as
CONNECT_WEB_POOL_JSON) and upserted into PoolWorker's encrypted columns here.

`sync_pool_from_env()` runs when the worker picker loads, at most once per process per secret value,
so a deploy that pins a new or rotated secret takes effect with no manual step (a prod write by hand
is exactly what we don't want). `manage.py connect_web_import_pool` does the same on demand.

The secret is a JSON list: [{"phone": "+7426…", "name": "…", "backup_code": "…", "pin": "…"}, …].
`pin` is optional. Workers in labs but not in the list are left alone (deactivate one in the admin).
No value from the secret is ever logged or put in an error message.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

from .models import PoolWorker

logger = logging.getLogger(__name__)

POOL_ENV = "CONNECT_WEB_POOL_JSON"

_synced_digest: str | None = None
_lock = threading.Lock()


class PoolError(ValueError):
    """The pool secret is malformed. The message never carries a value from it."""


def parse_pool(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise PoolError(f"{POOL_ENV} is not valid JSON") from None
    if not isinstance(data, list) or not data:
        raise PoolError(f"{POOL_ENV} must be a non-empty JSON list")
    workers = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise PoolError(f"{POOL_ENV}[{i}] is not an object")
        phone = str(item.get("phone") or "").strip()
        name = str(item.get("name") or "").strip()
        code = str(item.get("backup_code") or "").strip()
        if not phone.startswith("+") or not name or not code:
            # Name the entry by position only: its fields may be secrets.
            raise PoolError(f"{POOL_ENV}[{i}] needs phone (E.164), name and backup_code")
        workers.append({"phone": phone, "name": name, "backup_code": code, "pin": str(item.get("pin") or "").strip()})
    return workers


def import_pool(raw: str) -> tuple[int, int]:
    """Upsert the pool. Returns (registered, updated). A changed backup code clears a sign-in block."""
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
    return created, updated


def sync_pool_from_env() -> None:
    """Import the pool if the secret is set and has changed since this process last imported it."""
    global _synced_digest
    raw = os.environ.get(POOL_ENV, "")
    if not raw.strip():
        return
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if digest == _synced_digest:
        return
    with _lock:
        if digest == _synced_digest:
            return
        try:
            created, updated = import_pool(raw)
        except PoolError as exc:
            logger.error("connect_web pool: %s", exc)
            _synced_digest = digest  # don't retry a bad secret on every page load
            return
        _synced_digest = digest
        logger.info("connect_web pool: %d registered, %d updated", created, updated)
