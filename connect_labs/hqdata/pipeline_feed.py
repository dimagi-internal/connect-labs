"""A CommCare project's stored rows as a change feed for a pipeline's raw slot (``incremental``).

The slot holds the project's forms (or cases) in the shape ``pipeline_source`` gives them, written
straight from the store's table by one SQL statement per chunk: nothing passes through Python, so
there is no ceiling on how many rows a pipeline reads.

What changed is read from ``change_seq`` (``models.ChangeNumber``), the transaction that last wrote
a row, in ``(change_seq, id)`` order from where the slot's cursor stopped. Writes to a project's
rows take turns (``Store.write_lock``), so a batch still being written will commit with a number
above everything already committed, and the cursor never passes a row that commits later. Rows
from before change numbers existed read as 0 and come first.

A row the source no longer offers (deleted or archived) is deleted from the slot. A
row whose content is unchanged is left alone, so the overlap a delta sync re-reads costs nothing
downstream.
"""

from __future__ import annotations

import logging

from django.db import connection, transaction

from connect_labs.labs.analysis.backends.sql.incremental import (
    MAINTAINED_EXPIRY,
    MAINTAINED_LABEL,
    check_deadline,
    next_stamp,
)

logger = logging.getLogger(__name__)

#: Source rows per chunk: one transaction, one INSERT ... SELECT.
CHUNK = 50_000

_RAW_COLUMNS = (
    "opportunity_id, pipeline_id, visit_count, expires_at, created_at, visit_id, username, deliver_unit, "
    "deliver_unit_id, entity_id, entity_name, visit_date, status, reason, location, flagged, flag_reason, "
    "form_json, completed_work, status_modified_date, review_status, review_created_on, justification, "
    "date_created, completed_work_id, images, images_fetched, user_id, user_visit_id, changed_at"
)


def _iso(column: str) -> str:
    """A timestamp as Python's ``isoformat`` writes it in UTC with ``Z`` (``pipeline_source._iso``)."""
    return (
        f"CASE WHEN {column} IS NULL THEN '' ELSE to_char({column} AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS') || CASE WHEN date_part('microseconds', "
        f"{column})::bigint %% 1000000 = 0 THEN '' ELSE to_char({column} AT TIME ZONE 'UTC', '.US') END || 'Z' END"
    )


#: Per store kind: the table, the id the slot keys on, which rows the source reads at all (its app / form / case
#: type: what the slot is keyed by, so it never changes for a row), which of those it offers now (not deleted or
#: archived), and the SELECT list producing a raw row (what the ``cchq_forms`` / ``cchq_cases`` normalisers build).
_KINDS = {
    "forms": {
        "table": "labs_commcare_form",
        "id": "form_id",
        "narrowed": "(%(app)s = '' OR s.app_id = %(app)s) AND (%(xmlns)s = '' OR s.xmlns = %(xmlns)s)",
        "offered": "s.deleted_at IS NULL AND NOT s.archived",
        "select": (
            "s.form_id, "
            "COALESCE(NULLIF(s.form_json #>> '{form,meta,username}', ''), s.form_json #>> '{form,meta,userID}', ''), "
            "'', NULL, '', '', (s.received_on AT TIME ZONE 'UTC')::date, 'approved', '', '', false, '{}'::jsonb, "
            "s.form_json, '{}'::jsonb, NULL, '', NULL, '', s.received_on, NULL, '[]'::jsonb, false, '', ''"
        ),
    },
    "cases": {
        "table": "labs_commcare_case",
        "id": "case_id",
        "narrowed": "(%(case_type)s = '' OR s.case_type = %(case_type)s)",
        "offered": "s.deleted_at IS NULL",
        "select": (
            "s.case_id, '', '', NULL, s.case_id, s.case_name, "
            "(COALESCE(s.last_modified, s.date_opened) AT TIME ZONE 'UTC')::date, 'approved', '', '', false, "
            "'{}'::jsonb, "
            "jsonb_build_object('case', jsonb_build_object("
            "'case_id', s.case_id, 'case_type', s.case_type, 'case_name', s.case_name, "
            "'external_id', s.external_id, 'owner_id', s.owner_id, "
            f"'date_opened', {_iso('s.date_opened')}, 'last_modified', {_iso('s.last_modified')}, "
            f"'date_modified', {_iso('s.last_modified')}, "
            f"'server_last_modified', {_iso('s.server_last_modified')}, 'indexed_on', {_iso('s.indexed_on')}, "
            "'closed', s.closed, 'properties', s.properties, 'indices', s.indices)), "
            "'{}'::jsonb, NULL, '', NULL, '', s.date_opened, NULL, '[]'::jsonb, false, '', ''"
        ),
    },
}

_SPAN = (
    "s.project_id = %(project)s "
    "AND (COALESCE(s.change_seq, 0), s.id) > (%(lo_seq)s, %(lo_id)s) "
    "AND (COALESCE(s.change_seq, 0), s.id) <= (%(up_seq)s, %(up_id)s)"
)

#: Columns a re-written row may have changed. A row with none changed is not touched, so it neither
#: moves ``changed_at`` nor counts as an update.
_CHANGES = ("username", "entity_id", "entity_name", "visit_date", "date_created", "form_json")


class StoreFeed:
    """The change feed of an ``hq_store`` source (``data_source.type == "hq_store"``)."""

    def __init__(self, data_source):
        self.data_source = data_source
        self.kind = _KINDS[data_source.store_kind]

    def _params(self) -> dict:
        ds = self.data_source
        return {
            "project": ds.project_id,
            "app": ds.app_id or "",
            "xmlns": ds.xmlns or "",
            "case_type": ds.case_type or "",
        }

    def _upper(self, cursor, params: dict, size: int):
        """The (change_seq, id) of the ``size``-th row after the cursor, or the last one, or None."""
        table = self.kind["table"]
        where = (
            "s.project_id = %(project)s AND (COALESCE(s.change_seq, 0), s.id) > (%(lo_seq)s, %(lo_id)s) "
            f"AND {self.kind['narrowed']}"
        )
        cursor.execute(
            f"SELECT COALESCE(s.change_seq, 0), s.id FROM {table} s WHERE {where} "
            f"ORDER BY COALESCE(s.change_seq, 0), s.id OFFSET {size - 1} LIMIT 1",
            params,
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                f"SELECT COALESCE(s.change_seq, 0), s.id FROM {table} s WHERE {where} "
                "ORDER BY COALESCE(s.change_seq, 0) DESC, s.id DESC LIMIT 1",
                params,
            )
            row = cursor.fetchone()
        return row

    def remaining(self, cursor: dict) -> int:
        """How many of the source's rows come after ``cursor`` (a slot's ``source_cursor``): what a catch-up reads."""
        params = {**self._params(), "lo_seq": (cursor or {}).get("seq", -1), "lo_id": (cursor or {}).get("id", 0)}
        with connection.cursor() as db:
            db.execute(
                f"SELECT count(*) FROM {self.kind['table']} s WHERE s.project_id = %(project)s "
                f"AND (COALESCE(s.change_seq, 0), s.id) > (%(lo_seq)s, %(lo_id)s) AND {self.kind['narrowed']}",
                params,
            )
            return db.fetchone()[0]

    def catch_up(self, opportunity_id: int, slot_id: int, state, report=None, deadline=None) -> int:
        """Write what changed in the store since ``state``'s cursor into the slot. Returns the source rows read."""
        kind, table = self.kind, self.kind["table"]
        params = {**self._params(), "opp": opportunity_id, "slot": slot_id, "label": MAINTAINED_LABEL}
        params["expiry"] = MAINTAINED_EXPIRY
        position = state.source_cursor or {}
        params["lo_seq"], params["lo_id"] = position.get("seq", -1), position.get("id", 0)
        remaining = self.remaining(position)

        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in (*_CHANGES, "changed_at"))
        differs = " OR ".join(f"r.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in _CHANGES)
        insert = (
            f"INSERT INTO labs_raw_visit_cache AS r ({_RAW_COLUMNS}) "
            f"SELECT %(opp)s, %(slot)s, %(label)s, %(expiry)s, now(), {kind['select']}, %(stamp)s "
            f"FROM {table} s WHERE {_SPAN} AND {kind['narrowed']} AND {kind['offered']} "
            "ON CONFLICT (opportunity_id, pipeline_id, visit_count, visit_id) "
            f"DO UPDATE SET {update_set} WHERE {differs} "
            "RETURNING (xmax = 0)"
        )
        remove = (
            f"DELETE FROM labs_raw_visit_cache r USING {table} s "
            "WHERE r.opportunity_id = %(opp)s AND r.pipeline_id = %(slot)s AND r.visit_count = %(label)s "
            f"AND r.visit_id = s.{kind['id']} AND {_SPAN} AND {kind['narrowed']} AND NOT ({kind['offered']}) "
            "RETURNING 1"
        )

        read = 0
        while True:
            with connection.cursor() as cursor:
                upper = self._upper(cursor, params, CHUNK)
            if upper is None:
                break
            params["up_seq"], params["up_id"] = upper
            params["stamp"] = stamp = next_stamp(state.changed_through)
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(insert, params)
                written = [row[0] for row in cursor.fetchall()]
                cursor.execute(remove, params)
                removed = cursor.rowcount
                cursor.execute(f"SELECT count(*) FROM {table} s WHERE {_SPAN} AND {kind['narrowed']}", params)
                read += cursor.fetchone()[0]
                changed = bool(written) or removed > 0
                if (written and not all(written)) or removed > 0:
                    state.generation += 1
                if changed:
                    state.changed_through = stamp
                state.source_cursor = {"seq": upper[0], "id": upper[1]}
                state.save(update_fields=["source_cursor", "changed_through", "generation", "updated_at"])
            params["lo_seq"], params["lo_id"] = upper
            check_deadline(deadline)
            if report:
                report(
                    f"Reading the project's stored rows: {min(read, remaining):,} of {remaining:,}",
                    stage_name="raw",
                    processed=min(read, remaining),
                    total=remaining,
                )
        if read:
            logger.info(
                "[hq feed] project %s slot %s: read %s changed rows", self.data_source.project_id, slot_id, read
            )
        return read
