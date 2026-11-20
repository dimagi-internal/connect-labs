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
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from django.utils import timezone

from connect_labs.hqdata.alerts import sync_state_changed
from connect_labs.hqdata.backends.base import BackendUnavailable, SourceBackend
from connect_labs.hqdata.models import LEASE_SECONDS, ChangeNumber, HQCase, HQForm, HQProject, HQSource, HQSyncRun
from connect_labs.hqdata.records import CaseRecord, FormRecord
from connect_labs.hqdata.stores import store_for
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


def drop_labs_cache_copies(project_id: int) -> None:
    """Delete what labs' own database holds of a project's cached rows, whichever store the project uses.

    ``RawVisitCache`` keeps a ``form_json`` copy of every staged row, and the computed caches
    keep what pipelines built from them. A project that moved to a customer's database, or was
    deleted, must not leave those behind in labs. Never raises: purging the rows already happened.
    """
    try:
        from connect_labs.hqdata.scope import hq_scope_id
        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

        SQLCacheManager.delete_all_cache(hq_scope_id(project_id))
    except Exception:
        logger.exception("hqdata could not drop labs' cached copies for project %s", project_id)


def invalidate_project_caches(project_id: int) -> None:
    """Drop every cached pipeline result built from this project's rows.

    For when the rows were replaced in a way their change numbers cannot show (the project moved to
    another store, or its rows were purged): a sync's changes are read in place, so a sync never
    calls this. The caches live where the project's data lives (in a customer's database, theirs),
    so that is where they are dropped. Failing to drop them is loud in the log.
    """
    try:
        from connect_labs.hqdata.redirect import default_connection_is
        from connect_labs.hqdata.scope import hq_scope_id
        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

        project = HQProject.objects.get(pk=project_id)
        with store_for(project).session() as store:
            if store.alias == "default":
                SQLCacheManager.delete_all_cache(hq_scope_id(project_id))
            else:
                with default_connection_is(store.alias):
                    SQLCacheManager.delete_all_cache(hq_scope_id(project_id))
    except Exception:
        logger.exception("hqdata could not clear cached results for project %s", project_id)


class SourceBusy(Exception):
    """Another run holds this source's lease."""


def _upsert(store, source: HQSource, batch: list, now: datetime) -> None:
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
        store.upsert_forms(list(by_id.values()))
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
        store.upsert_cases(list(by_id.values()))


def _record_id(record: FormRecord | CaseRecord) -> str:
    return record.form_id if isinstance(record, FormRecord) else record.case_id


def _record_stamp(record: FormRecord | CaseRecord) -> datetime | None:
    return record.indexed_on


def choose_mode(source: HQSource, now: datetime | None = None) -> str:
    now = now or timezone.now()
    if source.reconcile_started_at is not None:
        return HQSyncRun.MODE_RECONCILE  # one is part way through: carry on, do not start another
    if source.watermark is None:
        return HQSyncRun.MODE_FULL
    if not source.backfill_complete:
        return HQSyncRun.MODE_DELTA  # resume the unfinished backfill from its checkpoint
    if source.last_reconciled_at is None or now - source.last_reconciled_at >= RECONCILE_EVERY:
        return HQSyncRun.MODE_RECONCILE
    return HQSyncRun.MODE_DELTA


def _take_lease(source: HQSource, now: datetime, wait_for_lease_s: float) -> None:
    """Claim the source, waiting up to ``wait_for_lease_s`` for a dead run's lease to lapse.

    A job a deploy killed is redelivered at once, and finds the source still held by the run that was
    killed; waiting out the lease (minutes) is how it carries on instead of giving up.
    """
    if source.claim(now):
        return
    give_up = time.monotonic() + wait_for_lease_s
    while time.monotonic() < give_up:
        time.sleep(5)
        if source.claim(timezone.now()):
            return
    raise SourceBusy(f"{source} is already being synced")


def sync_source(
    source: HQSource,
    backend: SourceBackend,
    *,
    mode: str | None = None,
    now: datetime | None = None,
    deadline: float | None = None,
    wait_for_lease_s: float = 0,
    progress: Callable | None = None,
) -> HQSyncRun:
    """Run one sync of ``source``.

    ``deadline`` is a ``time.monotonic()`` instant: past it the run stops after its next committed
    batch with status ``partial``, everything it read already stored and checkpointed, and the next
    run carries on. That is what lets a sync of any size run as jobs shorter than the worker's limit.
    Raises SourceBusy if another run holds it; re-raises a failure after recording it.
    """
    now = now or timezone.now()
    _take_lease(source, now, wait_for_lease_s)
    source.refresh_from_db()
    mode = mode or choose_mode(source, now)
    reconciling = mode == HQSyncRun.MODE_RECONCILE
    walks_everything = mode in (HQSyncRun.MODE_FULL, HQSyncRun.MODE_RECONCILE)

    if reconciling and source.reconcile_started_at is None:
        HQSource.objects.filter(pk=source.pk).update(reconcile_started_at=now, reconcile_cursor=None)
        source.reconcile_started_at, source.reconcile_cursor = now, None
    # Every row a reconcile writes carries the moment it began. What still carries an older one at
    # the end was not in what HQ returned: HQ no longer has it.
    stamp = source.reconcile_started_at if reconciling else now

    if reconciling:
        since = source.reconcile_cursor - OVERLAP if source.reconcile_cursor else None
        max_seen: datetime | None = source.reconcile_cursor
    elif walks_everything or source.watermark is None:
        since, max_seen = None, None
    else:
        since, max_seen = source.watermark - OVERLAP, source.watermark
    run = HQSyncRun.objects.create(source=source, mode=mode, started_at=now, since=since)
    # Where this run's checkpoint stood. A run starts a little BEFORE it (the overlap), so it re-reads
    # rows it already has; it must not stop at its deadline until it has stored something newer, or a
    # run that always ran out of time inside the overlap would never move on.
    started_from = max_seen

    batch: list = []
    fetched = 0
    stopped_early = False
    store = None

    def commit_batch() -> None:
        nonlocal batch
        # The rows go to the project's store and the checkpoint to labs: two databases, so two
        # steps, rows first. A crash between them leaves the checkpoint behind the rows, which the
        # next walk simply writes again.
        with store.atomic():
            _upsert(store, source, batch, stamp)
        if max_seen is not None:
            HQSource.objects.filter(pk=source.pk).update(
                **{"reconcile_cursor" if reconciling else "watermark": max_seen}
            )
        batch = []
        source.heartbeat()
        if progress is not None:
            progress(fetched, None, f"{source}: {fetched} rows read")

    try:
        store = store_for(source.project)
        with store.translated():
            store.ensure_schema()
            for record in backend.iter_records(source, since):
                batch.append(record)
                fetched += 1
                point = _record_stamp(record)
                if point and (max_seen is None or point > max_seen):
                    max_seen = point
                if len(batch) >= BATCH_SIZE:
                    commit_batch()
                    if (
                        deadline is not None
                        and time.monotonic() >= deadline
                        and (started_from is None or (max_seen is not None and max_seen > started_from))
                    ):
                        stopped_early = True
                        break
            if batch and not stopped_early:
                commit_batch()

            deleted = 0
            if reconciling and not stopped_early:
                with store.atomic():
                    store.write_lock(source.project_id)
                    deleted = (
                        source.rows(store)
                        .filter(deleted_at__isnull=True, synced_at__lt=stamp)
                        .update(deleted_at=now, change_seq=ChangeNumber())
                    )

        if stopped_early:
            run.status = HQSyncRun.STATUS_PARTIAL
        else:
            updates: dict = {"last_error": ""}
            if reconciling:
                if max_seen is not None and (source.watermark is None or max_seen > source.watermark):
                    updates["watermark"] = max_seen
                updates.update(reconcile_started_at=None, reconcile_cursor=None)
            if walks_everything or not source.backfill_complete:
                # Walked from the beginning (or resumed a walk that began there): complete.
                updates.update(backfill_complete=True, last_full_sync_at=now, last_reconciled_at=now)
            HQSource.objects.filter(pk=source.pk).update(**updates)
            run.status = HQSyncRun.STATUS_OK
            run.deleted = deleted
        run.fetched = fetched
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
        if store is not None:
            store.close()
        # Nothing cached is dropped: a pipeline reads what this run changed the next time it is read
        # (``pipeline_feed``), from the change numbers the batches carry.
    return run


def summarize(results: dict) -> dict:
    """What a pass over a project's sources came to: ``complete`` is False while any has more to read."""
    runs = [r for r in results.values() if isinstance(r, HQSyncRun)]
    errors = [str(r) for r in results.values() if isinstance(r, Exception)]
    return {
        "complete": not errors and all(r.status == HQSyncRun.STATUS_OK for r in runs) and len(runs) == len(results),
        "more_to_read": any(r.status == HQSyncRun.STATUS_PARTIAL for r in runs) or None in results.values(),
        "rows": sum(r.fetched for r in runs),
        "removed": sum(r.deleted for r in runs),
        "errors": errors,
    }


def sync_project(
    project: HQProject,
    backend: SourceBackend | None = None,
    *,
    mode: str | None = None,
    deadline: float | None = None,
    wait_for_lease_s: float = 0,
    progress: Callable | None = None,
) -> dict:
    """Sync every enabled source of a project; record the outcome on the project.

    Returns ``{source_id: HQSyncRun | Exception | None}`` so one failing source does not hide the
    rest. ``None`` is a source not reached before the deadline.
    """
    from connect_labs.hqdata.backends.factory import backend_for

    results: dict = {}
    was = HQProject.objects.filter(pk=project.pk).values_list("status", flat=True).first() or project.status
    try:
        backend = backend or backend_for(project)
    except BackendUnavailable as exc:
        sync_state_changed(project.pk, was, HQProject.STATUS_NEEDS_REAUTH, str(exc))
        HQProject.objects.filter(pk=project.pk).update(
            status=HQProject.STATUS_NEEDS_REAUTH, last_error=str(exc)[:2000]
        )
        return {None: exc}

    failures: list[Exception] = []
    for source in project.sources.filter(enabled=True).order_by("pk"):
        # Always start the first source: a job that read nothing would hand on a continuation that
        # reads nothing either. After that the deadline decides what is left for the next job.
        if deadline is not None and results and time.monotonic() >= deadline:
            results[source.pk] = None
            continue
        try:
            results[source.pk] = sync_source(
                source, backend, mode=mode, deadline=deadline, wait_for_lease_s=wait_for_lease_s, progress=progress
            )
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
    if not failures and summarize(results)["complete"]:
        updates["last_synced_at"] = timezone.now()
    HQProject.objects.filter(pk=project.pk).update(**updates)
    sync_state_changed(project.pk, was, status, error)
    return results


def reap_interrupted_runs(now: datetime | None = None) -> int:
    """Close the history of runs whose worker died: still "running", and nobody holds their source.

    A deploy hard-kills a worker; its run row never learns. The data is fine (every batch committed
    with its checkpoint, and the job is redelivered), so this only keeps the history honest.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=LEASE_SECONDS)
    stuck = HQSyncRun.objects.filter(status=HQSyncRun.STATUS_RUNNING, started_at__lt=cutoff).exclude(
        source__running_since__gt=cutoff
    )
    return stuck.update(
        status=HQSyncRun.STATUS_ERROR,
        finished_at=now,
        error="Interrupted (the worker running it was stopped). It carries on from its last checkpoint.",
    )
