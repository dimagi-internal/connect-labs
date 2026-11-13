"""Bring a source up to date: delta, full, or reconcile.

Every walk is ordered by HQ's own ``indexed_on``, so after each committed batch everything
at or before the newest ``indexed_on`` seen is stored, and that is the checkpoint (the
model commcare-export uses, which commcare-sync schedules). A run that dies half way
leaves the watermark at its last committed batch and the next run resumes from there; a
backfill that never finished resumes the same way. The next walk starts a little BEFORE the
watermark (``OVERLAP``): HQ's search index can make a row visible after rows with a later
``indexed_on``. Every write is an upsert on the row's id, so seeing a row twice costs
nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from connect_labs.hqdata.backends.base import BackendUnavailable, SourceBackend
from connect_labs.hqdata.models import HQCase, HQForm, HQProject, HQSource, HQSyncRun
from connect_labs.hqdata.records import CaseRecord, FormRecord
from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
# How far before the watermark a delta starts. HQ indexes asynchronously, so a row can
# become visible after rows with a later indexed_on; this much lag is absorbed here, and
# anything older is caught by the weekly reconcile.
OVERLAP = timedelta(minutes=10)
# A purge is the one change HQ never announces, so a full walk that marks what it did not
# see is the only way to learn of it. Weekly keeps that bounded without a daily full walk.
RECONCILE_EVERY = timedelta(days=7)

_FORM_FIELDS = [
    "xmlns",
    "app_id",
    "build_id",
    "received_on",
    "server_modified_on",
    "indexed_on",
    "username",
    "user_id",
    "archived",
    "form_json",
    "deleted_at",
    "synced_at",
]
_CASE_FIELDS = [
    "case_type",
    "case_name",
    "owner_id",
    "external_id",
    "closed",
    "date_opened",
    "last_modified",
    "server_last_modified",
    "indexed_on",
    "properties",
    "indices",
    "deleted_at",
    "synced_at",
]


class SourceBusy(Exception):
    """Another run holds this source's lease."""


def choose_mode(source: HQSource, now: datetime | None = None) -> str:
    now = now or timezone.now()
    if source.watermark is None:
        return HQSyncRun.MODE_FULL
    if not source.backfill_complete:
        return HQSyncRun.MODE_DELTA  # resume the unfinished backfill from its checkpoint
    if source.last_reconciled_at is None or now - source.last_reconciled_at >= RECONCILE_EVERY:
        return HQSyncRun.MODE_RECONCILE
    return HQSyncRun.MODE_DELTA


def _upsert(source: HQSource, batch: list, now: datetime) -> None:
    project_id = source.project_id
    if source.kind == HQSource.KIND_FORMS:
        by_id = {
            r.form_id: HQForm(
                project_id=project_id,
                form_id=r.form_id,
                xmlns=r.xmlns,
                app_id=r.app_id,
                build_id=r.build_id,
                received_on=r.received_on,
                server_modified_on=r.server_modified_on,
                indexed_on=r.indexed_on,
                username=r.username,
                user_id=r.user_id,
                archived=r.archived,
                form_json=r.form_json,
                deleted_at=None,
                synced_at=now,
            )
            for r in batch
        }
        HQForm.objects.bulk_create(
            by_id.values(),
            update_conflicts=True,
            unique_fields=["project_id", "form_id"],
            update_fields=_FORM_FIELDS,
        )
    else:
        by_id = {
            r.case_id: HQCase(
                project_id=project_id,
                case_id=r.case_id,
                case_type=r.case_type,
                case_name=r.case_name,
                owner_id=r.owner_id,
                external_id=r.external_id,
                closed=r.closed,
                date_opened=r.date_opened,
                last_modified=r.last_modified,
                server_last_modified=r.server_last_modified,
                indexed_on=r.indexed_on,
                properties=r.properties,
                indices=r.indices,
                deleted_at=None,
                synced_at=now,
            )
            for r in batch
        }
        HQCase.objects.bulk_create(
            by_id.values(),
            update_conflicts=True,
            unique_fields=["project_id", "case_id"],
            update_fields=_CASE_FIELDS,
        )


def _record_id(record: FormRecord | CaseRecord) -> str:
    return record.form_id if isinstance(record, FormRecord) else record.case_id


def _record_stamp(record: FormRecord | CaseRecord) -> datetime | None:
    return record.indexed_on


def _mark_unseen_deleted(source: HQSource, seen: set[str], now: datetime) -> int:
    """Rows the source covers that a full walk did not return were purged on the other side."""
    id_field = "form_id" if source.kind == HQSource.KIND_FORMS else "case_id"
    live = source.rows().filter(deleted_at__isnull=True)
    stale = [i for i in live.values_list(id_field, flat=True) if i not in seen]
    marked = 0
    for start in range(0, len(stale), 1000):
        marked += live.filter(**{f"{id_field}__in": stale[start : start + 1000]}).update(deleted_at=now)
    return marked


def sync_source(
    source: HQSource,
    backend: SourceBackend,
    *,
    mode: str | None = None,
    now: datetime | None = None,
) -> HQSyncRun:
    """Run one sync of ``source``.

    Raises SourceBusy if another run holds it; re-raises a failure after recording it.
    """
    now = now or timezone.now()
    if not source.claim(now):
        raise SourceBusy(f"{source} is already being synced")
    source.refresh_from_db()
    mode = mode or choose_mode(source, now)
    walks_everything = mode in (HQSyncRun.MODE_FULL, HQSyncRun.MODE_RECONCILE)
    since = None if walks_everything or source.watermark is None else source.watermark - OVERLAP
    run = HQSyncRun.objects.create(source=source, mode=mode, started_at=now, since=since)

    seen: set[str] = set()
    batch: list = []
    max_seen: datetime | None = source.watermark if mode == HQSyncRun.MODE_DELTA else None
    fetched = 0

    def commit_batch() -> None:
        nonlocal batch
        with transaction.atomic():
            _upsert(source, batch, now)
            # The checkpoint moves with the batch it covers. A reconcile keeps its own
            # until it has seen everything, so a half-finished one never claims progress.
            if mode != HQSyncRun.MODE_RECONCILE and max_seen is not None:
                HQSource.objects.filter(pk=source.pk).update(watermark=max_seen)
        batch = []
        source.heartbeat()

    try:
        for record in backend.iter_records(source, since):
            batch.append(record)
            fetched += 1
            if mode == HQSyncRun.MODE_RECONCILE:
                seen.add(_record_id(record))
            stamp = _record_stamp(record)
            if stamp and (max_seen is None or stamp > max_seen):
                max_seen = stamp
            if len(batch) >= BATCH_SIZE:
                commit_batch()
        if batch:
            commit_batch()

        deleted = 0
        if mode == HQSyncRun.MODE_RECONCILE:
            deleted = _mark_unseen_deleted(source, seen, now)

        updates: dict = {"last_error": ""}
        if mode == HQSyncRun.MODE_RECONCILE and max_seen is not None:
            updates["watermark"] = max_seen
        if walks_everything or not source.backfill_complete:
            # Walked from the beginning (or resumed a walk that began there): complete.
            updates.update(backfill_complete=True, last_full_sync_at=now, last_reconciled_at=now)
        HQSource.objects.filter(pk=source.pk).update(**updates)

        run.status = HQSyncRun.STATUS_OK
        run.fetched = fetched
        run.deleted = deleted
    except Exception as exc:
        run.status = HQSyncRun.STATUS_ERROR
        run.fetched = fetched
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        HQSource.objects.filter(pk=source.pk).update(last_error=run.error)
        logger.exception("hqdata sync failed for %s", source)
        raise
    finally:
        run.finished_at = timezone.now()
        run.save()
        source.release()
    return run


def sync_project(project: HQProject, backend: SourceBackend | None = None, *, mode: str | None = None) -> dict:
    """Sync every enabled source of a project; record the outcome on the project.

    Returns ``{source_id: HQSyncRun | Exception}`` so one failing source does not hide the rest.
    """
    from connect_labs.hqdata.backends.factory import backend_for

    results: dict = {}
    try:
        backend = backend or backend_for(project)
    except BackendUnavailable as exc:
        HQProject.objects.filter(pk=project.pk).update(
            status=HQProject.STATUS_NEEDS_REAUTH, last_error=str(exc)[:2000]
        )
        return {None: exc}

    failures: list[Exception] = []
    for source in project.sources.filter(enabled=True):
        try:
            results[source.pk] = sync_source(source, backend, mode=mode)
        except SourceBusy as exc:
            results[source.pk] = exc
        except Exception as exc:  # recorded on the run and source by sync_source
            results[source.pk] = exc
            failures.append(exc)

    auth_failed = any(isinstance(e, CCHQAuthError) for e in failures)
    if auth_failed:
        status, error = HQProject.STATUS_NEEDS_REAUTH, str(next(e for e in failures if isinstance(e, CCHQAuthError)))
    elif failures:
        status, error = HQProject.STATUS_ERROR, str(failures[0])
    else:
        status, error = HQProject.STATUS_ACTIVE, ""
    updates = {"status": status, "last_error": error[:2000]}
    if not failures:
        updates["last_synced_at"] = timezone.now()
    HQProject.objects.filter(pk=project.pk).update(**updates)
    return results
