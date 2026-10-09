"""Reading cases' visits from the visit cache, as the person: what ``case_coaching`` classifies.

One SQL pass over the explorer's ``visits`` relation (``explorer/engine.visits_cte``:
the raw user_visits cache, one generation per opportunity) pulls just the fields a
case story reads -- each from the paths in the workflow's ``config.case_coaching``,
bound as parameters (a path is data, never SQL) -- for one case, one worker's cases,
or every case in the opportunities. The danger-sign checklist is read as a handful of
short text columns, not as the whole form, so a programme's 35,000 visits stay small.

Authorisation is the explorer's (``explorer/scope.resolve``): every opportunity is one
the caller holds, or nothing is read. Every read is written to the audit trail.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from django.db import connection, transaction

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "case_coaching_visits"
STATEMENT_TIMEOUT_MS = 30_000


class CaseDataError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def _path(dotted: str) -> list[str]:
    return [p for p in str(dotted).split(".") if p]


def _first_of(paths: list[str], params: list, base: str = "form_json") -> str:
    """``COALESCE(base #>> %s, ...)`` over ``paths``, appending each path to ``params``."""
    if not paths:
        return "NULL::text"
    parts = []
    for p in paths:
        parts.append(f"NULLIF({base} #>> %s::text[], '')")
        params.append(_path(p))
    return f"COALESCE({', '.join(parts)})"


def build_sql(
    config: dict, *, case_id: str | None, username: str | None, as_of: str | None = None
) -> tuple[str, list]:
    """The SELECT over ``visits`` (prefixed by the caller with the CTE), and its parameters.

    Columns: opportunity_id, visit_id, visit_date, username, entity_name, form_name,
    case_id, weight, registration_weight, birth_weight, skin_to_skin, referred,
    has_checklist, q_<sign> per danger question, l_<sign> per danger label."""
    # Parameters are appended in the order their placeholders appear in the SQL.
    inner_params: list = []
    whens = []
    for form_name, paths in (config.get("case_id_paths_by_form") or {}).items():
        inner_params.append(form_name)
        whens.append("WHEN form_json #>> '{form,@name}' = %s THEN " + _first_of(list(paths), inner_params))
    default_case = _first_of(list(config.get("case_id_paths") or []), inner_params)
    # A form with its own paths reads ONLY those: on a design-B registration form the
    # default `form.case.@case_id` is the mother, not the baby.
    chosen = f"CASE {' '.join(whens)} ELSE {default_case} END" if whens else default_case
    case_expr = f"COALESCE({chosen}, NULLIF(entity_id, ''))"

    cols = [
        "opportunity_id",
        "visit_id",
        "visit_date",
        "username",
        "entity_name",
        "status",
        "form_json #>> '{form,@name}' AS form_name",
        f"{case_expr} AS case_id",
    ]
    for name, key in (
        ("weight", "weight_paths"),
        ("registration_weight", "registration_weight_paths"),
        ("birth_weight", "birth_weight_paths"),
        ("skin_to_skin", "skin_to_skin_paths"),
    ):
        cols.append(f"{_first_of(list(config.get(key) or []), inner_params)} AS {name}")
    group_parts = []
    for p in config.get("danger_groups") or []:
        group_parts.append("form_json #> %s::text[]")
        inner_params.append(_path(p))
    group_expr = f"COALESCE({', '.join(group_parts)})" if group_parts else "NULL::jsonb"
    cols.append(f"{group_expr} AS dgroup")
    inner = f"SELECT {', '.join(cols)} FROM visits"

    # The outer columns come BEFORE the inner query in the SQL text, so their
    # parameters come first.
    params: list = []
    outer_cols = [
        "opportunity_id",
        "visit_id",
        "visit_date",
        "username",
        "entity_name",
        "status",
        "form_name",
        "case_id",
        "weight",
        "registration_weight",
        "birth_weight",
        "skin_to_skin",
        "(dgroup IS NOT NULL) AS has_checklist",
    ]
    referred = config.get("referred_path")
    if referred:
        outer_cols.append("dgroup #>> %s::text[] AS referred")
        params.append(_path(referred))
    else:
        outer_cols.append("NULL::text AS referred")
    questions = config.get("danger_questions") or {}
    for i, (sign, paths) in enumerate(sorted(questions.items())):
        outer_cols.append(f"{_first_of(list(paths), params, base='dgroup')} AS q{i}")
    labels = config.get("danger_labels") or {}
    for i, (sign, path) in enumerate(sorted(labels.items())):
        outer_cols.append(f"dgroup #>> %s::text[] AS l{i}")
        params.append(_path(path))

    where, wparams = [], []
    excluded = config.get("exclude_statuses")
    if excluded:
        where.append("NOT (COALESCE(status, '') = ANY(%s))")
        wparams.append(list(excluded))
    if username:
        where.append("username = %s")
        wparams.append(username)
    if case_id:
        where.append("case_id = %s")
        wparams.append(case_id)
    if as_of:
        where.append("visit_date <= %s::date")
        wparams.append(str(as_of)[:10])
    sql = (
        f"SELECT {', '.join(outer_cols)} FROM ({inner}) AS cv"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY opportunity_id, case_id, visit_date"
    )
    return sql, params + inner_params + wparams


def shape_row(raw: dict, config: dict) -> dict:
    """A loader row as ``case_coaching.visit_from_row`` reads it."""
    questions = sorted((config.get("danger_questions") or {}).keys())
    labels = sorted((config.get("danger_labels") or {}).keys())
    out = {k: raw.get(k) for k in raw if not (k[:1] in "ql" and k[1:].isdigit())}
    out["danger_questions"] = {sign: raw.get(f"q{i}") for i, sign in enumerate(questions)}
    out["danger_labels"] = {sign: raw.get(f"l{i}") for i, sign in enumerate(labels)}
    return out


def _execute(opps, sql: str, params: list) -> list[dict]:
    from connect_labs.explorer import engine

    cte, cte_params = engine.visits_cte(opps)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL transaction_read_only = on")
            cursor.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
            cursor.execute(f"WITH {cte}\n{sql}", cte_params + params)
            columns = [d[0] for d in cursor.description or []]
            raw = [dict(zip(columns, r)) for r in cursor.fetchall()]
        # Nothing to keep; rolling back undoes the SET LOCALs inside an outer transaction.
        transaction.set_rollback(True)
    return raw


def _resolve(caller, opportunity_ids):
    from connect_labs.explorer import scope

    try:
        return scope.resolve(caller, opportunity_ids)
    except scope.ScopeError as e:
        raise CaseDataError(e.code, e.message) from e


def load_rows(
    caller, opportunity_ids, config: dict, *, case_id: str | None = None, username: str | None = None
) -> list[dict]:
    """The visit rows of one case, one worker's cases, or every case in
    ``opportunity_ids``, read as ``caller`` (``labs.access.scopes.Caller``): every
    opportunity must be one the caller holds. Audited."""
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    opps = _resolve(caller, opportunity_ids)
    sql, params = build_sql(config, case_id=case_id, username=username)
    try:
        raw = _execute(opps, sql, params)
    except Exception as e:  # noqa: BLE001 -- reported as a stable code
        logger.warning("case visits read failed for opps %s", [o.id for o in opps], exc_info=True)
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=opps[0].id,
            outcome=Outcome.FAILURE,
            metadata={"opportunity_ids": [o.id for o in opps], "error": type(e).__name__},
            user=caller.user,
        )
        raise CaseDataError("read_failed", "Could not read the visits for these cases. Try again shortly.") from e
    for o in opps:
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=o.id,
            record_count=sum(1 for r in raw if r.get("opportunity_id") == o.id),
            metadata={"case": bool(case_id), "worker": bool(username)},
            user=caller.user,
        )
    return [shape_row(r, config) for r in raw]


def load_rows_for(opportunity_ids, config: dict, *, as_of: str | None = None) -> list[dict]:
    """Every case's visit rows in ``opportunity_ids`` up to ``as_of``, with NO access
    check: for a saved run's snapshot builder, which runs over visits its workflow
    already reads (``snapshot_builders``). Never call it on a person's behalf."""
    from connect_labs.explorer.scope import Opportunity

    opps = [Opportunity(id=int(o), name="", llo="") for o in opportunity_ids]
    sql, params = build_sql(config, case_id=None, username=None, as_of=as_of)
    return [shape_row(r, config) for r in _execute(opps, sql, params)]


def latest_from_rows(rows: list[dict]) -> dict[int, dt.date]:
    out: dict[int, dt.date] = {}
    for r in rows:
        d = r.get("visit_date")
        d = d if isinstance(d, dt.date) else (dt.date.fromisoformat(str(d)[:10]) if d else None)
        if d is not None:
            o = int(r["opportunity_id"])
            out[o] = max(d, out.get(o, d))
    return out


def latest_visit_dates(caller, opportunity_ids) -> dict[int, dt.date]:
    """The latest visit date in each opportunity's cached data (the finder's "now")."""
    opps = _resolve(caller, opportunity_ids)
    raw = _execute(opps, "SELECT opportunity_id, MAX(visit_date) AS d FROM visits GROUP BY opportunity_id", [])
    return {int(r["opportunity_id"]): r["d"] for r in raw if r["d"] is not None}


def load_cases(caller, opportunity_ids, config: dict, **kw) -> list[Any]:
    from connect_labs.workflow import case_coaching

    return case_coaching.cases_from_rows(load_rows(caller, opportunity_ids, config, **kw), config)
