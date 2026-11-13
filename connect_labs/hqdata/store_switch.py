"""Move a project's data between stores."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from connect_labs.hqdata.external import check_connection, passed
from connect_labs.hqdata.models import LEASE_SECONDS, HQExternalDatabase, HQProject, HQSource
from connect_labs.hqdata.stores import StoreUnavailable, store_for
from connect_labs.hqdata.sync import invalidate_project_caches


class SwitchRefused(Exception):
    """The project's store was not changed. The message says why, for a person."""


def _busy(project: HQProject) -> bool:
    cutoff = timezone.now() - timedelta(seconds=LEASE_SECONDS)
    return project.sources.filter(running_since__gt=cutoff).exists()


def switch_store(project: HQProject, to: str) -> dict:
    """Make ``to`` ("labs" or "external") the project's store, and start it from scratch.

    HQ is the source of truth, so a switch does not copy rows across: it points the project at the
    new store, forgets every source's checkpoint (the next sync reads everything again, into the
    new place) and then removes the rows from the old one, so a project that chose a customer's
    database keeps nothing in labs. Refused while a sync is running, and refused for an external
    store that has not just passed every check.
    """
    if to not in (HQProject.STORE_LABS, HQProject.STORE_EXTERNAL):
        raise SwitchRefused("Unknown store.")
    if project.store == to:
        return {"changed": False}
    if _busy(project):
        raise SwitchRefused("A sync is running. Try again in a minute.")

    if to == HQProject.STORE_EXTERNAL:
        record = HQExternalDatabase.objects.filter(project=project).first()
        if record is None:
            raise SwitchRefused("Set up the database first.")
        checks = check_connection(record)
        if not passed(checks):
            failed = next(c for c in checks if not c.ok)
            raise SwitchRefused(f"{failed.name}: {failed.detail or 'failed'}")

    old_store = store_for(project)
    with transaction.atomic():
        HQProject.objects.filter(pk=project.pk).update(store=to)
        HQSource.objects.filter(project=project).update(
            watermark=None, backfill_complete=False, last_full_sync_at=None, last_reconciled_at=None, last_error=""
        )
        if to == HQProject.STORE_EXTERNAL:
            HQExternalDatabase.objects.filter(project=project).update(verified_at=timezone.now(), last_error="")
    project.refresh_from_db()

    purged = None
    try:
        with old_store.session() as store:
            purged = store.purge(project.pk)
    except StoreUnavailable:
        purged = None  # their database is unreachable: the rows there are theirs to remove
    invalidate_project_caches(project.pk)
    return {"changed": True, "purged": purged}
