"""Bringing a project pipeline up to date: inline when little changed, in the background when much did.

A pipeline over a project is kept up to date in place (``analysis.backends.sql.incremental``): a read first carries
what the project's syncs changed. Usually that is a few hundred forms and takes a moment. The first read of a
pipeline over a large project is millions, which no web request should hold a worker for. So a read asks how much
there is to carry (``backlog``): up to ``INLINE_ROWS`` it is carried in the read; more, and it is carried by a
background task (``tasks.catch_up_pipeline``) in parts of at most ``PART_SECONDS``, each kept, while the read raises
``ComputedWarming`` (the page answers 202 and asks again, as it does for a slow computed source).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from connect_labs.hqdata.computed import ComputedWarming
from connect_labs.hqdata.models import HQProject

logger = logging.getLogger(__name__)

#: Rows a read carries itself; past this it is carried in the background.
INLINE_ROWS = 50_000
#: How long one background part runs before handing over to the next (inside the task's time limit).
PART_SECONDS = 25 * 60


@contextmanager
def _on_store(project: HQProject):
    """For the block, the default connection is the project's store (labs', or the customer's)."""
    from connect_labs.hqdata.redirect import default_connection_is
    from connect_labs.hqdata.stores import store_for

    with store_for(project).session() as store:
        if store.alias == "default":
            yield
        else:
            store.ensure_schema()
            with default_connection_is(store.alias):
                yield


def backlog(config, scope: int) -> int:
    """How many rows a read of ``config`` would carry before answering: what the store changed since the slot's
    cursor, plus the slot's rows its stage has not carried yet (all of them for a stage never built)."""
    from connect_labs.hqdata.pipeline_feed import StoreFeed
    from connect_labs.labs.analysis.backends.sql.incremental import VISITS, slot_rows_after, stage_state_of
    from connect_labs.labs.analysis.backends.sql.models import RawSlotState
    from connect_labs.labs.analysis.config import CacheStage

    slot = RawSlotState.objects.filter(opportunity_id=scope, slot_id=config.raw_slot_id).first()
    pending = StoreFeed(config.data_source).remaining(slot.source_cursor if slot else {})
    stage = {CacheStage.AGGREGATED: "flw", CacheStage.ENTITY: "entity"}.get(config.terminal_stage, VISITS)
    state = stage_state_of(config, scope, stage)
    since = state.changed_through if state is not None and (state.changed_through or state.cursor) else None
    return pending + slot_rows_after(scope, config.raw_slot_id, since)


def _slot(project: HQProject, config, token=None):
    from connect_labs.labs.analysis.utils import get_config_hash
    from connect_labs.workflow.cache_warm import WarmSlot

    key = f"hqdata:catchup:{project.pk}:{config.raw_slot_id}:{config.pipeline_id}:{get_config_hash(config)}"
    return WarmSlot(key, token)


def ensure_current(user, project: HQProject, stamped: dict, config, *, reset: bool = False) -> None:
    """Return when the read may carry what changed itself; else start (or join) the background catch-up and raise
    ``ComputedWarming``. A failed background part is said once, as a ``PipelineProblem``."""
    from connect_labs.hqdata.pipelines import PipelineProblem

    slot = _slot(project, config)
    state = slot.state()
    if state is not None:
        raise ComputedWarming(state, "Bringing this pipeline up to date with the project's data.")
    outcome = slot.outcome()
    if outcome and outcome.get("error"):
        slot.take_outcome()  # spent: the next read tries again
        raise PipelineProblem(outcome["error"])
    from connect_labs.hqdata.pipeline_feed import StoreFeed

    with _on_store(project):
        # Starting over reads the whole source again.
        pending = StoreFeed(config.data_source).remaining({}) if reset else backlog(config, project.scope_id)
    if pending <= INLINE_ROWS:
        return

    def enqueue(token: str):
        from connect_labs.hqdata.tasks import catch_up_pipeline

        catch_up_pipeline.delay(project.pk, user.pk, stamped, config.pipeline_id, reset, token)

    slot.queue_once(enqueue)
    message = (
        f"Reading {pending:,} of the project's rows into this pipeline. This happens once; after it the pipeline "
        "keeps up with each sync."
    )
    slot.report(message)  # what the page shows until the task reports its own progress
    raise ComputedWarming(slot.state(), message)


def run(user, project: HQProject, stamped: dict, pipeline_id, reset: bool, token: str) -> None:
    """One background part of a catch-up (``tasks.catch_up_pipeline``): carry what it can, keep it, stop in time."""
    from connect_labs.hqdata.pipelines import config_for
    from connect_labs.labs.analysis.backends.sql.incremental import MaintenanceContinues, maintain

    config = config_for(stamped, pipeline_id)
    slot = _slot(project, config, token)
    if slot.superseded():
        return
    with slot.running() as outcome:
        try:
            with _on_store(project):
                maintain(
                    config,
                    project.scope_id,
                    reset=reset,
                    report=slot.report,
                    deadline=time.monotonic() + PART_SECONDS,
                )
        except MaintenanceContinues:
            return  # kept: the next read starts the next part
        except Exception as exc:  # noqa: BLE001 -- said to the next reader, and logged
            logger.exception("hqdata: catching up pipeline %s of project %s failed", pipeline_id, project.pk)
            outcome["error"] = f"Bringing the pipeline up to date failed ({type(exc).__name__})."
