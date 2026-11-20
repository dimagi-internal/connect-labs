"""Pipelines kept up to date in place: each stage carries forward only what changed in its source.

A pipeline computes in stages over a raw slot (``labs_raw_visit_cache``): per-visit fields
(``visits``), per-worker totals (``flw``), per-entity totals (``entity``). The usual way is to
fetch the source whole and rebuild every stage whole when the cache expires. A source with a
CHANGE FEED (``FEEDS``) is instead kept up to date in place, stage by stage:

* **raw** -- the feed writes what changed in the source since its cursor into the slot: rows
  upserted on ``visit_id``, rows that left the source deleted. Every row it writes is stamped with
  ``changed_at``, and the slot's ``RawSlotState`` (cursor, ``changed_through``) is saved in the
  same transaction, so the state never claims rows that are not there.
* **visits** -- recomputes only the raw rows whose ``changed_at`` is after the stage's own
  ``changed_through``, a chunk at a time, replacing those visits' rows.
* **flw / entity** -- recomputes only the workers (or entities) those changed rows belong to.

A slot's ``generation`` moves when it loses a row or a row's content changes, because then a
row may have left a group it was counted in. A per-visit stage drops its rows for visits no
longer in the slot and carries on; a totals stage rebuilds whole (one SQL ``GROUP BY``). A plain
new row, which is most of what a sync brings, never moves it.

Some configs cannot be followed row by row, and are rebuilt whole whenever their slot moved:
window fields (a visit's value depends on its neighbours), joins (it depends on another
pipeline), two-pass worker fields (``pre_aggregate_by``, which can count a visit for another
worker) and grouped entity stages (several key sets).

Every stage's state is saved in the transaction that writes its rows, so a run that is stopped
part way resumes where it stopped. All of it runs on the default connection, which for a
CommCare project kept in a customer's database is that database (``hqdata.redirect``).

Adding a source: a feed has ``catch_up(opportunity_id, slot_id, state, report=None) -> int``
and registers in ``FEEDS``. The stages need nothing more: they read ``changed_at`` and the
slot's state, whoever wrote them. Connect's visits export has no feed yet; its writers stamp
``changed_at`` and mark a whole replacement (``cache.note_replaced``) so that adding one there is the
feed alone.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import (
    ComputedEntityCache,
    ComputedFLWCache,
    ComputedVisitCache,
    RawSlotState,
    RawVisitCache,
    StageState,
)
from connect_labs.labs.analysis.config import CacheStage

logger = logging.getLogger(__name__)

#: The validity label on every row of a slot kept up to date in place. Its rows are not one
#: generation of a whole-source write, so the label-is-the-row-count scheme does not apply; a
#: positive value is what the readers that skip unfinished writes (``visit_count > 0``) read.
MAINTAINED_LABEL = 1
#: Rows of a maintained slot or stage do not expire on the clock: their state says how current they are.
MAINTAINED_EXPIRY = datetime(2999, 1, 1, tzinfo=UTC)
#: Raw rows a per-visit stage computes at a time (its rows are built in Python, so memory is per chunk).
VISIT_CHUNK = 20_000
#: Past this many changed workers or entities a totals stage rebuilds whole: one GROUP BY over everything
#: costs less than an ``IN`` list that long.
DIRTY_KEY_LIMIT = 5_000

#: Source type -> its change feed's class, as ``module.Class`` (imported on use).
FEEDS = {"hq_store": "connect_labs.hqdata.pipeline_feed.StoreFeed"}

VISITS, FLW, ENTITY = "visits", "flw", "entity"


class MaintenanceContinues(Exception):
    """Stopped at its deadline between two chunks: everything done so far is kept, and the next call carries on."""


def check_deadline(deadline: float | None) -> None:
    """Raise ``MaintenanceContinues`` once ``deadline`` (a ``time.monotonic()`` value) has passed."""
    if deadline is not None and time.monotonic() >= deadline:
        raise MaintenanceContinues()


def maintains_in_place(config) -> bool:
    """Whether this config's source is kept up to date in place rather than fetched whole."""
    return config.data_source.type in FEEDS


def feed_for(config):
    module, _, name = FEEDS[config.data_source.type].rpartition(".")
    return getattr(importlib.import_module(module), name)(config.data_source)


def _int31(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:7], 16)


@contextmanager
def slot_lock(opportunity_id: int, slot_id: int):
    """One writer per slot at a time: the feed and every stage built on the slot run under it."""
    key = (74_201, _int31(f"{opportunity_id}:{slot_id}"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", key)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", key)


def next_stamp(previous: datetime | None) -> datetime:
    """A ``changed_at`` strictly after ``previous``, so each write to a slot has its own."""
    now = timezone.now()
    if previous is not None and now <= previous:
        now = previous + timedelta(microseconds=1)
    return now


def slot_state(opportunity_id: int, slot_id: int) -> RawSlotState:
    """The slot's state; a slot with none is emptied first, so rows written some other way never mix in."""
    state = RawSlotState.objects.filter(opportunity_id=opportunity_id, slot_id=slot_id).first()
    if state is not None:
        return state
    with transaction.atomic():
        _drop_slot(opportunity_id, slot_id)
        return RawSlotState.objects.create(opportunity_id=opportunity_id, slot_id=slot_id)


def _drop_slot(opportunity_id: int, slot_id: int) -> None:
    """The slot's rows, its state, and every stage computed from it, with their rows."""
    RawVisitCache.objects.filter(opportunity_id=opportunity_id, pipeline_id=slot_id).delete()
    RawSlotState.objects.filter(opportunity_id=opportunity_id, slot_id=slot_id).delete()
    stages = StageState.objects.filter(opportunity_id=opportunity_id, slot_id=slot_id)
    for pipeline_id, config_hash in set(stages.values_list("pipeline_id", "config_hash")):
        for model in (ComputedVisitCache, ComputedFLWCache, ComputedEntityCache):
            model.objects.filter(
                opportunity_id=opportunity_id, pipeline_id=pipeline_id, config_hash=config_hash
            ).delete()
    stages.delete()


def maintain(config, opportunity_id: int, *, reset: bool = False, report=None, deadline=None) -> dict:
    """Bring ``config``'s slot up to date with its source, then carry that through its stages.

    ``reset`` starts the slot and its stages over (what ``?refresh=1`` asks for). With a ``deadline``
    (``time.monotonic()``) it stops between chunks once that has passed, raising
    ``MaintenanceContinues`` with the work so far kept. Returns what was done, for the log and the tests.
    """
    slot_id = config.raw_slot_id
    with slot_lock(opportunity_id, slot_id):
        if reset:
            with transaction.atomic():
                _drop_slot(opportunity_id, slot_id)
        state = slot_state(opportunity_id, slot_id)
        read = feed_for(config).catch_up(opportunity_id, slot_id, state, report=report, deadline=deadline)
        state.refresh_from_db()
        done = maintain_stages(config, opportunity_id, state, report=report, deadline=deadline)
    return {"read": read, **done}


def maintain_raw(config, opportunity_id: int, *, report=None, deadline=None) -> int:
    """Bring just ``config``'s raw slot up to date (what the semantic layer reads: it computes from the slot
    itself, not from a stage). Returns the source rows read."""
    slot_id = config.raw_slot_id
    with slot_lock(opportunity_id, slot_id):
        state = slot_state(opportunity_id, slot_id)
        return feed_for(config).catch_up(opportunity_id, slot_id, state, report=report, deadline=deadline)


# -----------------------------------------------------------------------------
# Stages
# -----------------------------------------------------------------------------


def maintain_stages(config, opportunity_id: int, slot: RawSlotState, *, report=None, deadline=None) -> dict:
    """Carry the slot's changes through the stages ``config`` reads. Call under ``slot_lock``."""
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    stage = config.terminal_stage
    done = {}
    if stage in (CacheStage.VISIT_LEVEL, CacheStage.AGGREGATED) or (stage == CacheStage.ENTITY and config.feeds_joins):
        done[VISITS] = _maintain_visits(
            config, opportunity_id, slot, full_shape=stage == CacheStage.VISIT_LEVEL, report=report, deadline=deadline
        )
    elif stage == CacheStage.ENTITY:
        # Nothing reads an entity pipeline's per-visit rows unless a JOIN does (backend._process_entity_level).
        SQLCacheManager(opportunity_id, config).delete_computed_visits()
        _stages(config, opportunity_id).filter(stage=VISITS).delete()
    if stage == CacheStage.AGGREGATED:
        done[FLW] = _maintain_flw(config, opportunity_id, slot)
    elif stage == CacheStage.ENTITY:
        done[ENTITY] = _maintain_entity(config, opportunity_id, slot)

    manager = SQLCacheManager(opportunity_id, config)
    if _terminal_rows(config, opportunity_id).exists():
        manager.clear_empty_marker()
    else:
        manager.store_empty_marker()
    return done


def _stages(config, opportunity_id: int):
    from connect_labs.labs.analysis.utils import get_config_hash

    return StageState.objects.filter(
        opportunity_id=opportunity_id, pipeline_id=config.pipeline_id, config_hash=get_config_hash(config)
    )


def stage_state_of(config, opportunity_id: int, stage: str) -> StageState | None:
    """The state of one of ``config``'s stages, or None when it has never been built."""
    return _stages(config, opportunity_id).filter(stage=stage).first()


def _stage_state(config, opportunity_id: int, slot: RawSlotState, stage: str) -> tuple[StageState, bool]:
    """The stage's state, and whether it is new (nothing built yet: build whole)."""
    from connect_labs.labs.analysis.utils import get_config_hash

    state, created = StageState.objects.get_or_create(
        opportunity_id=opportunity_id,
        pipeline_id=config.pipeline_id,
        config_hash=get_config_hash(config),
        stage=stage,
        defaults={"slot_id": slot.slot_id, "generation": slot.generation},
    )
    if not created and state.slot_id != slot.slot_id:
        # The config now reads another slot (its source changed): nothing it has carries over.
        state.slot_id, state.changed_through, state.cursor = slot.slot_id, None, {}
        state.save(update_fields=["slot_id", "changed_through", "cursor", "updated_at"])
    return state, created or state.changed_through is None and not state.cursor


def _current(state: StageState, slot: RawSlotState) -> bool:
    return state.generation == slot.generation and state.changed_through == slot.changed_through and not state.cursor


def _scope(opportunity_id: int, slot_id: int, alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return f"{p}opportunity_id = {int(opportunity_id)} AND {p}pipeline_id = {int(slot_id)} AND {p}visit_count > 0"


def _ts(value: datetime | str) -> str:
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    return "'" + text.replace("'", "''") + "'::timestamptz"


def _text(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


#: Below every real ``changed_at``: the lower bound of a stage that has carried nothing yet.
_BEGINNING = {"at": "1970-01-01T00:00:00+00:00", "id": ""}


def _lower(state: StageState) -> dict:
    if state.cursor:
        return state.cursor
    if state.changed_through is not None:
        # Every row stamped at or before changed_through is carried: the bound is the instant, not a row.
        return {"at": state.changed_through.isoformat(), "id": None}
    return _BEGINNING


def _after(bound: dict) -> str:
    """Rows strictly after ``bound`` in (changed_at, visit_id) order."""
    if bound.get("id") is None:
        return f"changed_at > {_ts(bound['at'])}"
    return f"(changed_at, visit_id) > ({_ts(bound['at'])}, {_text(bound['id'])})"


def _through(bound: dict) -> str:
    """Rows at or before ``bound`` in (changed_at, visit_id) order."""
    if bound.get("id") is None:
        return f"changed_at <= {_ts(bound['at'])}"
    return f"(changed_at, visit_id) <= ({_ts(bound['at'])}, {_text(bound['id'])})"


def _chunk_end(opportunity_id: int, slot_id: int, lower: dict, through: datetime, size: int) -> dict | None:
    """The (changed_at, visit_id) of the ``size``-th changed row after ``lower`` (or the last one), or None."""
    where = f"{_scope(opportunity_id, slot_id)} AND {_after(lower)} AND changed_at <= {_ts(through)}"
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT changed_at, visit_id FROM labs_raw_visit_cache WHERE {where} "
            f"ORDER BY changed_at, visit_id OFFSET {size - 1} LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                f"SELECT changed_at, visit_id FROM labs_raw_visit_cache WHERE {where} "
                "ORDER BY changed_at DESC, visit_id DESC LIMIT 1"
            )
            row = cursor.fetchone()
    return None if row is None else {"at": row[0].isoformat(), "id": row[1]}


def _maintain_visits(
    config, opportunity_id: int, slot: RawSlotState, *, full_shape: bool, report=None, deadline=None
) -> dict:
    from connect_labs.labs.analysis.backends.sql.backend import (
        slim_visit_cache_dicts,
        visit_cache_dicts,
        visit_rows_from_extraction,
    )
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.analysis.backends.sql.query_builder import execute_visit_extraction

    state, new = _stage_state(config, opportunity_id, slot, VISITS)
    if not new and _current(state, slot):
        return {"mode": "current"}
    manager = SQLCacheManager(opportunity_id, config)
    rows_of = ComputedVisitCache.objects.filter(
        opportunity_id=opportunity_id, pipeline_id=config.pipeline_id, config_hash=manager.config_hash
    )

    def build(extra_where=()):
        data, names = execute_visit_extraction(config, opportunity_id, extra_where=extra_where)
        dicts = (
            visit_cache_dicts(visit_rows_from_extraction(config, data, names))
            if full_shape
            else slim_visit_cache_dicts(data, names)
        )
        return manager.computed_visit_rows(dicts, MAINTAINED_LABEL, MAINTAINED_EXPIRY)

    if config.window_fields or config.joins:
        rows = build()
        with transaction.atomic():
            rows_of.delete()
            ComputedVisitCache.objects.bulk_create(rows, batch_size=1000)
            _finish(state, slot)
        return {"mode": "rebuilt", "rows": len(rows)}

    mode = "rebuilt" if new else "incremental"
    if new:
        rows_of.delete()
    elif state.generation != slot.generation:
        # The slot lost rows: drop this stage's rows for visits that are no longer there.
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM labs_computed_visit_cache c WHERE c.opportunity_id = %s AND c.pipeline_id IS NOT "
                "DISTINCT FROM %s AND c.config_hash = %s AND NOT EXISTS (SELECT 1 FROM labs_raw_visit_cache r "
                f"WHERE {_scope(opportunity_id, slot.slot_id, 'r')} AND r.visit_id = c.visit_id)",
                [opportunity_id, config.pipeline_id, manager.config_hash],
            )
    state.generation = slot.generation
    state.save(update_fields=["generation", "updated_at"])

    carried = 0
    if slot.changed_through is not None:
        lower = _lower(state)
        while True:
            upper = _chunk_end(opportunity_id, slot.slot_id, lower, slot.changed_through, VISIT_CHUNK)
            if upper is None:
                break
            span = f"{_after(lower)} AND {_through(upper)}"
            rows = build(extra_where=(span,))
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM labs_computed_visit_cache WHERE opportunity_id = %s AND pipeline_id IS NOT "
                        "DISTINCT FROM %s AND config_hash = %s AND visit_id IN (SELECT visit_id FROM "
                        f"labs_raw_visit_cache WHERE {_scope(opportunity_id, slot.slot_id)} AND {span})",
                        [opportunity_id, config.pipeline_id, manager.config_hash],
                    )
                ComputedVisitCache.objects.bulk_create(rows, batch_size=1000)
                state.cursor = upper
                state.save(update_fields=["cursor", "updated_at"])
            carried += len(rows)
            lower = upper
            if report:
                report(f"Computing each record's fields: {carried:,} done", stage_name="visits", processed=carried)
            check_deadline(deadline)
    with transaction.atomic():
        _finish(state, slot)
    return {"mode": mode, "rows": carried}


def _finish(state: StageState, slot: RawSlotState) -> None:
    state.generation, state.changed_through, state.cursor = slot.generation, slot.changed_through, {}
    state.save(update_fields=["generation", "changed_through", "cursor", "updated_at"])


def _changed_keys(opportunity_id: int, slot_id: int, state: StageState, through, key_sql: str) -> list[str]:
    """The distinct keys (as the cache stores them: text, ``""`` for none) of the rows changed since ``state``."""
    where = f"{_scope(opportunity_id, slot_id)} AND {_after(_lower(state))} AND changed_at <= {_ts(through)}"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT DISTINCT ({key_sql})::text FROM labs_raw_visit_cache WHERE {where}")
        return sorted({row[0] or "" for row in cursor.fetchall()})


def _maintain_flw(config, opportunity_id: int, slot: RawSlotState) -> dict:
    from connect_labs.labs.analysis.backends.sql.backend import _flw_data_to_rows, flw_cache_dicts
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.analysis.backends.sql.query_builder import execute_flw_aggregation

    state, new = _stage_state(config, opportunity_id, slot, FLW)
    if not new and _current(state, slot):
        return {"mode": "current"}
    manager = SQLCacheManager(opportunity_id, config)
    rows_of = ComputedFLWCache.objects.filter(
        opportunity_id=opportunity_id, pipeline_id=config.pipeline_id, config_hash=manager.config_hash
    )
    followable = not config.joins and not any(f.pre_aggregate_by for f in config.fields)
    keys = None
    if not new and followable and state.generation == slot.generation and slot.changed_through is not None:
        keys = _changed_keys(opportunity_id, slot.slot_id, state, slot.changed_through, "username")
        if len(keys) > DIRTY_KEY_LIMIT:
            keys = None
    if keys == []:
        with transaction.atomic():
            _finish(state, slot)
        return {"mode": "incremental", "groups": 0}
    data = execute_flw_aggregation(config, opportunity_id, only_usernames=keys)
    rows = manager.flw_rows(flw_cache_dicts(_flw_data_to_rows(config, data)), MAINTAINED_LABEL, MAINTAINED_EXPIRY)
    with transaction.atomic():
        (rows_of if keys is None else rows_of.filter(username__in=keys)).delete()
        ComputedFLWCache.objects.bulk_create(rows, batch_size=1000)
        _finish(state, slot)
    return {"mode": "rebuilt" if keys is None else "incremental", "groups": len(rows)}


def _maintain_entity(config, opportunity_id: int, slot: RawSlotState) -> dict:
    from connect_labs.labs.analysis.backends.sql.backend import (
        entity_cache_dicts,
        entity_rows_from_aggregation,
        grouped_entity_rows,
    )
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.analysis.backends.sql.query_builder import (
        entity_key_sql,
        execute_entity_aggregation,
        execute_grouped_aggregation,
    )

    state, new = _stage_state(config, opportunity_id, slot, ENTITY)
    if not new and _current(state, slot):
        return {"mode": "current"}
    manager = SQLCacheManager(opportunity_id, config)
    rows_of = ComputedEntityCache.objects.filter(
        opportunity_id=opportunity_id, pipeline_id=config.pipeline_id, config_hash=manager.config_hash
    )
    grouped = bool(config.effective_groupings())
    keys = None
    if (
        not new
        and not grouped
        and not config.joins
        and state.generation == slot.generation
        and slot.changed_through is not None
    ):
        keys = _changed_keys(opportunity_id, slot.slot_id, state, slot.changed_through, entity_key_sql(config))
        if len(keys) > DIRTY_KEY_LIMIT:
            keys = None
    if keys == []:
        with transaction.atomic():
            _finish(state, slot)
        return {"mode": "incremental", "groups": 0}
    if grouped:
        entity_rows = grouped_entity_rows(execute_grouped_aggregation(config, opportunity_id))
    else:
        entity_rows = entity_rows_from_aggregation(
            config, execute_entity_aggregation(config, opportunity_id, only_entity_ids=keys)
        )
    rows = manager.entity_rows(entity_cache_dicts(entity_rows), MAINTAINED_LABEL, MAINTAINED_EXPIRY)
    with transaction.atomic():
        (rows_of if keys is None else rows_of.filter(entity_id__in=keys)).delete()
        ComputedEntityCache.objects.bulk_create(rows, batch_size=1000)
        _finish(state, slot)
    return {"mode": "rebuilt" if keys is None else "incremental", "groups": len(rows)}


def _terminal_rows(config, opportunity_id: int):
    from connect_labs.labs.analysis.utils import get_config_hash

    model = {
        CacheStage.AGGREGATED: ComputedFLWCache,
        CacheStage.ENTITY: ComputedEntityCache,
    }.get(config.terminal_stage, ComputedVisitCache)
    return model.objects.filter(
        opportunity_id=opportunity_id, pipeline_id=config.pipeline_id, config_hash=get_config_hash(config)
    )
