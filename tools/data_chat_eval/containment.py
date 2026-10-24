"""Containment: labs' data_* tools called DIRECTLY as ace (its PAT over MCP, no chat).

The Data page's SQL runs as ``labs_data_reader`` under row-level security on the visit and
pipeline caches (``labsdata/engine.py``, ``labs/migrations/0039``-``0040``), behind the
validator and the scope checks. A ``containment:`` block in a suite probes all of it from
outside, the way a caller (or an agent the person talks into it) would:

* **correctness under RLS** -- what the reader role counts matches what the app role
  reports: each opportunity's ``count(*)`` against ``data_describe``'s ``cached_visits``;
  one program = the sum of its opportunities = the workflow selection = a data space on the
  program; ``visits`` holds exactly the selected opportunities; a pipeline relation's rows
  per opportunity match what ``data_describe`` says it stores.
* **containment** -- the caches by their own names (bare, schema-qualified, quoted), the
  grants table, Django's tables, the catalogs, functions that run a string as SQL or
  reach the server, role changes, several statements, writes, a pipeline relation without
  its workflow or from another workflow, ``space_id`` beside an outside opportunity, and
  opportunities and programs ace does not hold: each must be refused (or read nothing).
* **error quality** -- every refusal is a typed error (``error``/``reason`` from a known
  set) with a readable message that leaks nothing the caller did not write: no raw
  Postgres "permission denied", no table or policy names it never named.

Each probe is a check in the report (gate or advisory) with what came back; every raw
response is written to ``<run>/<id>/probes.json``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .labs_mcp import LabsMCP
from .suite import LABS

ROUTE_RLS = (
    "Data page SQL containment: connect_labs/labsdata/engine.py (role + RLS grants, _classify), "
    "labs/migrations/0039_labs_data_reader_role.py, labsdata/validator.py + sql_validator.py"
)
ROUTE_SCOPE = "Data page scope: connect_labs/labsdata/scope.py, pipelines.py, spaces.py; mcp/tools/labsdata.py"
ROUTE_ERRORS = "Data page error text: labsdata/engine.py ERROR_GUIDE + _classify; mcp/tools/labsdata.py _query_error"

#: The typed outcomes a refusal may carry (``engine.ERROR_GUIDE``, the validator's own
#: codes, ``scope.ScopeError`` reasons, ``spaces``).
KNOWN_CODES = {
    "invalid_sql",
    "parse_error",
    "table_not_allowed",
    "schema_not_allowed",
    "dangerous_function",
    "function_not_allowed",
    "forbidden_statement",
    "multiple_statements",
    "not_select",
    "read_only",
    "timeout",
    "no_opportunities",
    "not_held",
    "outside_space",
    "bad_scope",
    "bad_opportunity_ids",
    "system_catalog_not_allowed",
    "cast_type_not_allowed",
}

#: Tools an error may send the caller to that labs does not have (Scout's names): a message
#: pointing at one sends an agent looking for something that is not there.
_NO_SUCH_TOOLS = ("describe_table", "list_tables", "list_schemas", "get_schema")

#: Text in an error that is Postgres (or labs' plumbing) talking, not a message for a person.
_LEAKS = re.compile(
    r"permission denied|row-level security|\bpolic(?:y|ies)\b|labs_data_reader|"
    r"\bLINE \d|\bERROR:|HINT:|psycopg|Traceback|savepoint|search_path|labs_explorer_no_tables",
    re.I,
)
#: Internal relation names: a message may name one only when the caller's SQL did.
_INTERNAL = (
    "labs_raw_visit_cache",
    "labs_computed_visit_cache",
    "labs_computed_entity_cache",
    "labs_computed_flw_cache",
    "labsdata_scope_grant",
)

#: SQL that must never read anything outside the selection. ``expect``: the error codes
#: that are a correct refusal. ``internal``: reaching Postgres at all would be a finding.
REFUSED_SQL: list[tuple[str, str, set[str]]] = [
    ("cache, schema-qualified", "select count(*) from public.labs_raw_visit_cache", {"schema_not_allowed"}),
    ("cache, quoted", 'select count(*) from "public"."labs_raw_visit_cache"', {"schema_not_allowed"}),
    ("cache, bare", "select count(*) from labs_raw_visit_cache", {"table_not_allowed"}),
    ("computed cache, bare", "select count(*) from labs_computed_visit_cache", {"table_not_allowed"}),
    ("entity cache, qualified", "select count(*) from public.labs_computed_entity_cache", {"schema_not_allowed"}),
    ("grants table", "select * from labsdata_scope_grant", {"table_not_allowed"}),
    ("grants table, qualified", "select * from public.labsdata_scope_grant", {"schema_not_allowed"}),
    ("django users, qualified", "select count(*) from public.users_user", {"schema_not_allowed"}),
    ("django users, bare", "select count(*) from users_user", {"table_not_allowed"}),
    ("django sessions", "select count(*) from django_session", {"table_not_allowed"}),
    ("catalog", "select * from pg_catalog.pg_tables limit 1", {"schema_not_allowed"}),
    ("information_schema", "select * from information_schema.tables limit 1", {"schema_not_allowed"}),
    ("pg_class bare", "select relname from pg_class limit 1", {"table_not_allowed", "system_catalog_not_allowed"}),
    ("users in a subquery", "select (select count(*) from users_user) from visits limit 1", {"table_not_allowed"}),
    ("users in a CTE", "with u as (select * from users_user) select count(*) from u", {"table_not_allowed"}),
    ("users in a UNION", "select visit_id from visits union select username from users_user", {"table_not_allowed"}),
    ("set_config", "select set_config('role', 'postgres', false)", {"dangerous_function", "function_not_allowed"}),
    ("current_setting", "select current_setting('role')", {"dangerous_function", "function_not_allowed"}),
    ("current_user", "select current_user", {"dangerous_function", "function_not_allowed"}),
    (
        "query_to_xml (runs a string as SQL)",
        "select query_to_xml('select count(*) from public.labs_raw_visit_cache', false, false, '')",
        {"dangerous_function", "function_not_allowed"},
    ),
    (
        "table_to_xml",
        "select table_to_xml('public.users_user'::regclass, false, false, '')",
        {"dangerous_function", "function_not_allowed"},
    ),
    (
        "ts_stat (runs a string as SQL)",
        "select * from ts_stat('select to_tsvector(username) from public.users_user')",
        {"dangerous_function", "function_not_allowed", "table_not_allowed"},
    ),
    (
        "regclass cast",
        "select 'public.users_user'::regclass::oid",
        {"dangerous_function", "function_not_allowed", "invalid_sql", "cast_type_not_allowed"},
    ),
    ("pg_read_file", "select pg_read_file('/etc/passwd')", {"dangerous_function", "function_not_allowed"}),
    (
        "dblink",
        "select * from dblink('dbname=postgres', 'select 1') as t(x int)",
        {"dangerous_function", "function_not_allowed", "table_not_allowed"},
    ),
    ("pg_sleep", "select pg_sleep(30)", {"dangerous_function", "function_not_allowed"}),
    ("RESET ROLE", "reset role", {"forbidden_statement", "not_select", "parse_error"}),
    ("SET ROLE", "set role postgres", {"forbidden_statement", "not_select", "parse_error"}),
    ("SET search_path", "set search_path = public", {"forbidden_statement", "not_select", "parse_error"}),
    ("two statements", "select 1; select 2", {"multiple_statements"}),
    ("select then RESET ROLE", "select 1; reset role", {"multiple_statements", "forbidden_statement"}),
    ("DELETE", "delete from visits", {"forbidden_statement", "not_select"}),
    (
        "DELETE in a CTE",
        "with d as (delete from visits returning 1) select count(*) from d",
        {"forbidden_statement", "not_select"},
    ),
    ("COPY", "copy (select 1) to program 'id'", {"forbidden_statement", "not_select", "parse_error"}),
    (
        "FOR UPDATE",
        "select * from visits for update",
        {"forbidden_statement", "not_select", "invalid_sql", "read_only"},
    ),
]


@dataclass
class ContainmentScenario:
    id: str
    title: str
    program_id: int
    workflow: dict
    other_workflow: dict | None
    not_held_opportunity: int
    not_held_program: int
    probe_opportunity: int
    outside_held_opportunity: int | None = None
    space: bool = True
    covers: str = ""
    truth: dict = field(default_factory=dict)


def load(data: dict) -> list[ContainmentScenario]:
    out = []
    for raw in data.get("containment") or []:
        out.append(
            ContainmentScenario(
                id=raw["id"],
                title=raw.get("title", raw["id"]),
                program_id=int(raw["program_id"]),
                workflow=dict(raw["workflow"]),
                other_workflow=dict(raw["other_workflow"]) if raw.get("other_workflow") else None,
                not_held_opportunity=int(raw["not_held_opportunity"]),
                not_held_program=int(raw["not_held_program"]),
                probe_opportunity=int(raw["probe_opportunity"]),
                outside_held_opportunity=(
                    int(raw["outside_held_opportunity"]) if raw.get("outside_held_opportunity") else None
                ),
                space=bool(raw.get("space", True)),
                covers=raw.get("covers", ""),
            )
        )
    return out


# --------------------------------------------------------------------------- the calls


@dataclass
class Reply:
    """One tool call's answer: ``ok`` with its decoded JSON, or a refusal."""

    ok: bool
    data: dict | list | None
    text: str
    code: str = ""
    message: str = ""


def call(mcp: LabsMCP, name: str, args: dict) -> Reply:
    result = mcp._rpc("tools/call", {"name": name, "arguments": args})
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    text = "\n".join(texts)
    structured = result.get("structuredContent")
    if result.get("isError"):
        code, message = _typed(text)
        return Reply(False, None, text, code, message)
    data = structured
    if data is None:
        try:
            data = json.loads(texts[0]) if texts else None
        except ValueError:
            data = None
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("type") == "text":
        data = json.loads(data[0]["text"])
    # data_validate answers a refusal as a result, not an error.
    if isinstance(data, dict) and data.get("valid") is False:
        return Reply(False, data, text, str(data.get("error") or ""), str(data.get("message") or ""))
    return Reply(True, data, text)


def _typed(text: str) -> tuple[str, str]:
    """An MCP error's code and message: labs puts a JSON object after the message."""
    code, message = "", text.strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            code = str(obj.get("error") or obj.get("reason") or "")
            message = str(obj.get("message") or text.split("\n", 1)[0]).strip()
            break
    return code, message


def leaks(message: str, sql: str = "") -> list[str]:
    """What in ``message`` is not for the caller: Postgres/plumbing text, or an internal
    relation name the caller's own SQL did not name."""
    found = [m.group(0) for m in _LEAKS.finditer(message or "")]
    mine = (sql or "").lower()
    found += [n for n in _INTERNAL if n in (message or "").lower() and n not in mine]
    return found


def count(reply: Reply) -> dict | None:
    """``{first column value: second}`` of a 2-column result, or ``{"*": n}`` for one value."""
    if not reply.ok or not isinstance(reply.data, dict):
        return None
    rows = reply.data.get("rows") or []
    if rows and len(rows[0]) == 1:
        return {"*": rows[0][0]}
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- the run


class _Steps:
    def __init__(self):
        self.results: list[dict] = []
        self.raw: list[dict] = []

    def add(self, step: int, name: str, passed: bool, detail: str, route: str, gate: bool = True) -> bool:
        self.results.append(
            {"turn": step, "name": name, "passed": bool(passed), "detail": detail[:600], "gate": gate, "route": route}
        )
        return bool(passed)

    def keep(self, label: str, args: dict, reply: Reply) -> Reply:
        self.raw.append(
            {
                "probe": label,
                "args": args,
                "ok": reply.ok,
                "code": reply.code,
                "message": reply.message,
                "text": reply.text[:2000],
            }
        )
        return reply


def run(sc: ContainmentScenario, mcp: LabsMCP, out_dir: Path, args) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = _Steps()
    entry = {
        "id": sc.id,
        "title": sc.title,
        "covers": sc.covers,
        "surface": "containment",
        "url": f"{LABS}/mcp/ (data_query / data_validate / data_describe as ace, no chat)",
        "truth": {},
        "checks": steps.results,
        "turns": [],
        "agent": "(direct MCP)",
    }
    try:
        _correctness(sc, mcp, steps, entry)
        _containment(sc, mcp, steps)
        if sc.space:
            _space(sc, mcp, steps, args)
    finally:
        (out_dir / "probes.json").write_text(json.dumps(steps.raw, indent=1, default=str))
    return entry


def _q(mcp, steps, label, sql, **selection) -> Reply:
    args = {"sql": sql, "max_rows": 1000, **selection}
    return steps.keep(label, args, call(mcp, "data_query", args))


def _correctness(sc, mcp, steps, entry) -> None:
    """(a) What the reader role counts is what the app role reports, at every selection."""
    by_opp_sql = "select opportunity_id, count(*) from visits group by 1 order by 1"
    described = steps.keep(
        "describe program",
        {"program_ids": [sc.program_id]},
        call(mcp, "data_describe", {"program_ids": [sc.program_id]}),
    )
    if not steps.add(1, "describe the program", described.ok, described.message or "ok", ROUTE_SCOPE):
        return
    app = {o["opportunity_id"]: o.get("cached_visits") for o in described.data.get("opportunities") or []}
    opps = sorted(app)
    entry["truth"] = {"opportunities": len(opps), "cached_visits": sum(v or 0 for v in app.values())}

    per_opp = count(_q(mcp, steps, "per opportunity (opportunity_ids)", by_opp_sql, opportunity_ids=opps)) or {}
    wrong = {o: (per_opp.get(o), app[o]) for o in opps if per_opp.get(o, 0) != (app[o] or 0)}
    steps.add(
        1,
        "each opportunity's count = what data_describe reports (app role)",
        per_opp and not wrong,
        f"reader role vs cached_visits, differing: {wrong}" if wrong else f"{len(opps)} opportunities agree",
        ROUTE_RLS,
    )
    one_each = {}
    for o in opps[:3]:
        r = count(_q(mcp, steps, f"opportunity {o} alone", "select count(*) from visits", opportunity_ids=[o]))
        one_each[o] = (r or {}).get("*")
    steps.add(
        1,
        "an opportunity read alone counts the same as in a set",
        all(one_each[o] == per_opp.get(o) for o in one_each),
        f"alone {one_each} vs in the set {dict((o, per_opp.get(o)) for o in one_each)}",
        ROUTE_RLS,
    )
    total = sum(per_opp.values()) if per_opp else None
    program = (count(_q(mcp, steps, "program", "select count(*) from visits", program_ids=[sc.program_id])) or {}).get(
        "*"
    )
    steps.add(
        1, "program count = sum of its opportunities", program == total, f"program {program}, sum {total}", ROUTE_RLS
    )
    held = (
        count(
            _q(
                mcp,
                steps,
                "program: which opportunities",
                "select distinct opportunity_id, 1 from visits",
                program_ids=[sc.program_id],
            )
        )
        or {}
    )
    steps.add(
        1,
        "visits holds exactly the selected opportunities",
        set(held) == set(opps),
        f"visits has {sorted(held)}; selected {opps}",
        ROUTE_RLS,
    )
    # The pipelines' stored output lives an hour; rebuild it first so the pipeline checks
    # below read real rows (and RLS on the pipeline caches is exercised).
    _rebuild(mcp, steps, sc.workflow)
    wf = _q(mcp, steps, "workflow selection", by_opp_sql, workflow=sc.workflow)
    wf_counts = count(wf) or {}
    wf_described = steps.keep(
        "describe workflow", {"workflow": sc.workflow}, call(mcp, "data_describe", {"workflow": sc.workflow})
    )
    wf_opps = (
        sorted(o["opportunity_id"] for o in (wf_described.data or {}).get("opportunities") or [])
        if wf_described.ok
        else []
    )
    steps.add(
        1,
        "workflow selection counts = the same opportunities read directly",
        wf.ok
        and wf_counts
        and all(wf_counts.get(o, 0) == per_opp.get(o, app.get(o)) for o in wf_opps)
        and set(wf_counts) <= set(wf_opps),
        f"workflow {sc.workflow['id']}: {wf.message or wf_counts}; its opportunities {wf_opps}",
        ROUTE_RLS,
    )
    # Pipeline relations: rows per opportunity as stored (data_describe) = rows the query reads.
    relations = {k: v for k, v in ((wf_described.data or {}).get("relations") or {}).items() if k != "visits"}
    entry["truth"]["pipeline_relations"] = sorted(relations)
    for name, rel in relations.items():
        stored = {int(k): v for k, v in (rel.get("rows_by_opportunity") or {}).items()}
        read = count(
            _q(
                mcp,
                steps,
                f"pipeline {name}",
                f"select opportunity_id, count(*) from {name} group by 1",
                workflow=sc.workflow,
            )
        )
        read = read or {}
        diff = {o: (read.get(o, 0), n) for o, n in stored.items() if read.get(o, 0) != n}
        outside = sorted(set(read) - set(wf_opps))
        steps.add(
            1,
            f"pipeline relation {name}: rows read = rows stored, only the workflow's opportunities",
            read is not None and not diff and not outside,
            (f"read vs stored differ: {diff}; " if diff else "")
            + (f"rows from outside the workflow: {outside}; " if outside else "")
            + f"{sum(read.values())} rows over {len(read)} opportunities",
            ROUTE_RLS,
        )
        empty = sorted(o for o, n in stored.items() if not n)
        if empty:
            why = rel.get("why_no_rows") or {}
            steps.add(
                1,
                f"pipeline relation {name}: every opportunity has current rows",
                False,
                f"no CURRENT rows for opportunities {empty} even after rebuilding: "
                + (", ".join(sorted({w.get("state", "?") for w in why.values()})) or "no reason given")
                + ("; RLS on the pipeline caches is NOT exercised by this run" if len(empty) == len(stored) else ""),
                ROUTE_SCOPE,
                gate=False,
            )
            steps.add(
                1,
                f"pipeline relation {name}: data_describe says why it has no rows",
                all(str(o) in why for o in empty) and bool(rel.get("note")),
                rel.get("note") or f"why_no_rows names {sorted(why)}; empty {empty}",
                ROUTE_SCOPE,
            )


def _rebuild(mcp, steps, workflow, *, calls: int = 20) -> None:
    """data_describe(workflow, load_missing) until no pipeline output is pending (a few
    opportunities per call, as labs paces it)."""
    pending, rebuilt, failed = None, [], []
    for _ in range(calls):
        reply = call(mcp, "data_describe", {"workflow": workflow, "load_missing": True})
        done = (reply.data or {}).get("pipelines_rebuilt") or {}
        rebuilt += done.get("rebuilt") or []
        failed += done.get("failed") or []
        pending = done.get("pending") or []
        if not reply.ok or not pending:
            break
    steps.keep(f"rebuild workflow {workflow['id']}", {"workflow": workflow}, reply)
    steps.add(
        1,
        f"workflow {workflow['id']}: load_missing rebuilds its pipelines' stored output",
        reply.ok and not pending and not failed,
        (
            f"rebuilt {sorted(set(rebuilt))}; pending {pending}; failed {failed}"
            if (rebuilt or failed or pending)
            else "nothing to rebuild (current)"
        ),
        ROUTE_SCOPE,
    )


def _refused(steps, step, label, reply: Reply, expect: set[str], sql: str = "", route=ROUTE_RLS) -> None:
    """A refusal as it should be: refused, typed, readable, and leaking nothing."""
    if reply.ok:
        rows = (reply.data or {}).get("rows") if isinstance(reply.data, dict) else None
        steps.add(step, f"refused: {label}", False, f"NOT refused -- it ran and returned {str(rows)[:200]}", route)
        return
    typed = reply.code in KNOWN_CODES
    steps.add(
        step,
        f"refused: {label}",
        True,
        f"{reply.code or '(no code)'}: {reply.message[:200]}",
        route,
    )
    if not (typed and (not expect or reply.code in expect)):
        steps.add(
            step,
            f"typed error: {label}",
            False,
            f"code {reply.code or '(none)'} (expected one of {sorted(expect)}): {reply.message[:200]}",
            ROUTE_ERRORS,
        )
    message = reply.message or reply.text
    leaked = leaks(message, sql)
    readable = 0 < len(reply.message) <= 400
    if leaked or not readable:
        steps.add(
            step,
            f"readable error: {label}",
            False,
            (f"leaks {leaked}; " if leaked else "") + f"message: {message[:300]}",
            ROUTE_ERRORS,
        )
    missing = [t for t in _NO_SUCH_TOOLS if t in message]
    if missing:
        steps.add(
            step,
            f"error points at tools labs has: {label}",
            False,
            f"sends the caller to {missing}, which labs does not have (data_describe is the schema tool): "
            f"{message[:200]}",
            ROUTE_ERRORS,
        )


def _containment(sc, mcp, steps) -> None:
    """(b) and (c): everything outside the selection is refused, typed and readable."""
    opp = [sc.probe_opportunity]
    for label, sql, expect in REFUSED_SQL:
        _refused(steps, 2, label, _q(mcp, steps, label, sql, opportunity_ids=opp), expect, sql)
        v = steps.keep(f"validate: {label}", {"sql": sql}, call(mcp, "data_validate", {"sql": sql}))
        _refused(steps, 2, f"data_validate {label}", v, expect, sql)
    # A CTE named like a cache reads the CTE, never the table.
    r = count(
        _q(
            mcp,
            steps,
            "CTE shadowing a cache",
            "with labs_raw_visit_cache as (select 7 as n) select n from labs_raw_visit_cache",
            opportunity_ids=opp,
        )
    )
    steps.add(2, "a CTE named like a cache reads only the CTE", r == {"*": 7}, f"got {r}", ROUTE_RLS)
    # Opportunities and programs ace does not hold.
    _refused(
        steps,
        3,
        "an opportunity ace does not hold",
        _q(mcp, steps, "not held opp", "select count(*) from visits", opportunity_ids=[sc.not_held_opportunity]),
        {"not_held"},
        route=ROUTE_SCOPE,
    )
    _refused(
        steps,
        3,
        "a held opportunity beside one ace does not hold",
        _q(
            mcp,
            steps,
            "held + not held",
            "select count(*) from visits",
            opportunity_ids=[sc.probe_opportunity, sc.not_held_opportunity],
        ),
        {"not_held"},
        route=ROUTE_SCOPE,
    )
    _refused(
        steps,
        3,
        "a program ace does not hold",
        _q(mcp, steps, "not held program", "select count(*) from visits", program_ids=[sc.not_held_program]),
        {"not_held"},
        route=ROUTE_SCOPE,
    )
    d = steps.keep(
        "describe not held",
        {"opportunity_ids": [sc.not_held_opportunity]},
        call(mcp, "data_describe", {"opportunity_ids": [sc.not_held_opportunity]}),
    )
    _refused(steps, 3, "data_describe of an opportunity ace does not hold", d, {"not_held"}, route=ROUTE_SCOPE)
    # SQL naming an outside opportunity reads nothing.
    r = count(
        _q(
            mcp,
            steps,
            "where outside opp",
            f"select count(*) from visits where opportunity_id = {sc.not_held_opportunity}",
            opportunity_ids=opp,
        )
    )
    steps.add(3, "SQL naming an outside opportunity reads nothing", r == {"*": 0}, f"got {r}", ROUTE_RLS)
    # Pipeline relations exist only with their workflow.
    described = call(mcp, "data_describe", {"workflow": sc.workflow})
    mine = sorted(k for k in ((described.data or {}).get("relations") or {}) if k != "visits") if described.ok else []
    for name in mine[:2]:
        sql = f"select count(*) from {name}"
        _refused(
            steps,
            4,
            f"pipeline relation {name} without its workflow",
            _q(mcp, steps, f"{name} no workflow", sql, opportunity_ids=opp),
            {"table_not_allowed"},
            sql,
            ROUTE_SCOPE,
        )
        _refused(
            steps,
            4,
            f"pipeline relation {name} over a program selection",
            _q(mcp, steps, f"{name} program", sql, program_ids=[sc.program_id]),
            {"table_not_allowed"},
            sql,
            ROUTE_SCOPE,
        )
    if sc.other_workflow:
        _rebuild(mcp, steps, sc.other_workflow)
        other = call(mcp, "data_describe", {"workflow": sc.other_workflow})
        theirs = sorted(k for k in ((other.data or {}).get("relations") or {}) if k != "visits") if other.ok else []
        foreign = [n for n in theirs if n not in mine]
        # Same names, different pipelines: each workflow's relation reads its OWN pipeline's
        # rows (RLS grants are per opportunity and pipeline), never the other's.
        mine_rel = (described.data or {}).get("relations") or {}
        their_rel = (other.data or {}).get("relations") or {}
        for name in sorted(set(mine) & set(theirs)):
            a, b = mine_rel[name], their_rel[name]
            if (a.get("pipeline") or {}).get("id") == (b.get("pipeline") or {}).get("id"):
                continue
            for label, wf, rel in (("this", sc.workflow, a), ("the other", sc.other_workflow, b)):
                stored = {int(k): v for k, v in (rel.get("rows_by_opportunity") or {}).items()}
                read = (
                    count(
                        _q(
                            mcp,
                            steps,
                            f"{label} {name}",
                            f"select opportunity_id, count(*) from {name} group by 1",
                            workflow=wf,
                        )
                    )
                    or {}
                )
                diff = {o: (read.get(o, 0), n) for o, n in stored.items() if read.get(o, 0) != n}
                steps.add(
                    4,
                    f"{label} workflow's {name} reads its own pipeline ({(rel.get('pipeline') or {}).get('id')}) only",
                    not diff and sum(stored.values()) > 0,
                    (f"read vs stored differ: {diff}" if diff else f"{sum(read.values())} rows, as stored")
                    + ("" if sum(stored.values()) else "; nothing stored, so RLS is not exercised"),
                    ROUTE_RLS,
                )
        steps.add(
            4,
            "the other workflow has a relation of its own to probe",
            bool(foreign),
            f"mine {mine}, theirs {theirs}",
            ROUTE_SCOPE,
            gate=False,
        )
        for name in foreign[:2]:
            sql = f"select count(*) from {name}"
            _refused(
                steps,
                4,
                f"another workflow's relation {name}",
                _q(mcp, steps, f"foreign {name}", sql, workflow=sc.workflow),
                {"table_not_allowed"},
                sql,
                ROUTE_SCOPE,
            )


def _space(sc, mcp, steps, args) -> None:
    """A data space on the program: counts as the program does, and holds its caller to it."""
    from playwright.sync_api import sync_playwright

    from .driver import SESSION_FILE
    from .spaces import API, _post

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not getattr(args, "headed", False))
        ctx = browser.new_context(storage_state=str(SESSION_FILE))
        page = ctx.new_page()
        space_id = None
        try:
            page.goto(f"{LABS}/labs/data/", wait_until="load", timeout=120_000)
            made = _post(
                page,
                f"{API}/spaces/",
                {
                    "name": f"eval {sc.id} {time.strftime('%Y%m%d-%H%M%S')}",
                    "description": "data_chat_eval containment (synthetic)",
                    "program_ids": [sc.program_id],
                },
            )
            space_id = ((made.get("body") or {}).get("space") or {}).get("id")
            if not steps.add(
                5,
                "create a space on the program",
                space_id,
                f"HTTP {made['status']}: {str(made['body'])[:200]}",
                ROUTE_SCOPE,
            ):
                return
            in_space = (count(_q(mcp, steps, "space", "select count(*) from visits", space_id=space_id)) or {}).get(
                "*"
            )
            program = (
                count(
                    _q(
                        mcp,
                        steps,
                        "program (for the space)",
                        "select count(*) from visits",
                        program_ids=[sc.program_id],
                    )
                )
                or {}
            ).get("*")
            steps.add(
                5,
                "space count = program count",
                in_space is not None and in_space == program,
                f"space {in_space}, program {program}",
                ROUTE_RLS,
            )
            _refused(
                steps,
                5,
                "space_id with an opportunity outside it",
                _q(
                    mcp,
                    steps,
                    "space + outside opp",
                    "select count(*) from visits",
                    space_id=space_id,
                    opportunity_ids=[sc.not_held_opportunity],
                ),
                {"outside_space"},
                route=ROUTE_SCOPE,
            )
            if sc.outside_held_opportunity:
                _refused(
                    steps,
                    5,
                    "space_id with an opportunity ace holds but the space does not",
                    _q(
                        mcp,
                        steps,
                        "space + held outside opp",
                        "select count(*) from visits",
                        space_id=space_id,
                        opportunity_ids=[sc.outside_held_opportunity],
                    ),
                    {"outside_space"},
                    route=ROUTE_SCOPE,
                )
            _refused(
                steps,
                5,
                "space_id with another program",
                _q(
                    mcp,
                    steps,
                    "space + other program",
                    "select count(*) from visits",
                    space_id=space_id,
                    program_ids=[sc.not_held_program],
                ),
                {"outside_space"},
                route=ROUTE_SCOPE,
            )
            _refused(
                steps,
                5,
                "space_id with a workflow",
                _q(
                    mcp,
                    steps,
                    "space + workflow",
                    "select count(*) from visits",
                    space_id=space_id,
                    workflow=sc.workflow,
                ),
                {"outside_space"},
                route=ROUTE_SCOPE,
            )
            r = count(
                _q(
                    mcp,
                    steps,
                    "space, SQL naming outside",
                    f"select count(*) from visits where opportunity_id = {sc.not_held_opportunity}",
                    space_id=space_id,
                )
            )
            steps.add(
                5, "in a space, SQL naming an outside opportunity reads nothing", r == {"*": 0}, f"got {r}", ROUTE_RLS
            )
            v = steps.keep(
                "space validate users",
                {},
                call(mcp, "data_validate", {"sql": "select * from users_user", "space_id": space_id}),
            )
            _refused(
                steps,
                5,
                "data_validate in a space naming users_user",
                v,
                {"table_not_allowed"},
                "select * from users_user",
            )
        finally:
            if space_id:
                _post(page, f"{API}/spaces/{space_id}/archive/", {})
            browser.close()
