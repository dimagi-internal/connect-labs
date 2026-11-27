"""Audit reviews: everything that changes the stored state, and the recompute that follows it.

The views call four things:

- ``open_source(dao, definition)``  the AuditSource for an audit workflow (made on first use).
- ``sync(source, dao)``             freeze any newly completed source runs; import the decisions the
                                    old page saved in its run's state, once. Runs as the viewer (their
                                    token reads the source runs).
- ``record_decisions`` / ``record_event``  validated against the current world, then recompute.
- ``world(source)``                 the page's answer.

Every write recomputes: the rules replay every stored run with every stored decision
(``engine.replay``), and the result replaces ``Review`` rows and the ``AuditWorld`` payload.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from connect_labs.audit import setups as audit_setups
from connect_labs.audit_reviews import engine
from connect_labs.audit_reviews.models import (
    Attachment,
    AuditSource,
    AuditWorld,
    Decision,
    Review,
    SettingsVersion,
    SourceRun,
    TeamEvent,
)

logger = logging.getLogger(__name__)

#: Paths under a KC Pulse run's snapshot state that the rules read.
SOURCE_KEYS = [
    "snapshot.meta",
    "snapshot.series.WINDOW.programme",
    "snapshot.series.WINDOW.byLLO",
    "snapshot.series.WINDOW.byOpp",
    "snapshot.series.WINDOW.byFLW",
    "snapshot.series.WINDOW.measures",
]

CLOSE_STATUSES = {"closed_improved", "closed_suspended", "closed_other"}
TEAM_EVENT_TYPES = {"action", "comment", "pin", "unpin", "owner"}
#: What can be pinned to the top of a review's timeline.
PINNABLE = {"action", "comment"}


# ---------------------------------------------------------------------------
# Owners: who may own a review is one rule, chosen by AUDIT_REVIEWS_OWNER_POOL
# ---------------------------------------------------------------------------


def _pool_dimagi_com(source):
    """People who sign in to Labs with an @dimagi.com address (and have done so at least once)."""
    return get_user_model().objects.filter(is_active=True, email__iendswith="@dimagi.com")


#: The rules there are. To open ownership to more people, add a rule here (e.g. everyone who may
#: read the source's opportunities) and set AUDIT_REVIEWS_OWNER_POOL to its name; nothing else changes.
OWNER_POOLS = {"dimagi.com": _pool_dimagi_com}


def owner_pool(source):
    """The people who may own this source's reviews (a queryset of users)."""
    name = getattr(django_settings, "AUDIT_REVIEWS_OWNER_POOL", "dimagi.com")
    rule = OWNER_POOLS.get(name)
    if rule is None:
        logger.error("audit_reviews: unknown AUDIT_REVIEWS_OWNER_POOL %r; using dimagi.com", name)
        rule = _pool_dimagi_com
    return rule(source)


def owner_name(user) -> str:
    return (
        (getattr(user, "name", "") or "").strip()
        or (getattr(user, "email", "") or "").split("@")[0]
        or getattr(user, "username", "")
        or "Staff"
    )[:200]


def audit_setup_choices() -> list[dict]:
    """[{id, name, summary, ...}] of the saved image audit setups, for a metric's Settings."""
    from connect_labs.audit.setup_models import AuditSetup

    return [audit_setups.as_dict(s) for s in AuditSetup.objects.filter(archived=False)]


def owner_candidates(source) -> list[dict]:
    """[{id, name}] for the owner picker, by name."""
    people = [{"id": u.pk, "name": owner_name(u)} for u in owner_pool(source)[:1000]]
    return sorted(people, key=lambda p: p["name"].lower())


class Refused(Exception):
    """A request the rules do not allow; the message is shown to the person."""


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def to_ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1000)


def from_ms(ms) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def _config(definition) -> dict:
    data = getattr(definition, "data", None) or {}
    return dict(data.get("config") or getattr(definition, "config", None) or {})


def open_source(definition, *, opportunity_id=None, program_id=None) -> AuditSource:
    """The AuditSource for an audit workflow, kept in step with its definition's config.

    The config is where the workflow is set up (no deploy needed to change it):
    ``audit_source_workflow_id``, ``audit_go_live_run_id``, ``audit_mode``, ``audit_setup``
    (pool, parents, supporting, known, registry) and ``audit_program_name``; and, when the report
    lives in another scope than the audit workflow, ``audit_source_program_id`` (see ``source_scope``).
    """
    cfg = _config(definition)
    source_wf = cfg.get("audit_source_workflow_id")
    if not source_wf:
        raise Refused("This workflow does not name the report it follows (config.audit_source_workflow_id).")
    defaults = {
        "source_workflow_id": int(source_wf),
        "opportunity_id": opportunity_id,
        "program_id": program_id,
    }
    source, _ = AuditSource.objects.get_or_create(workflow_id=int(definition.id), defaults=defaults)
    wanted = {
        "source_workflow_id": int(source_wf),
        "go_live_run_id": cfg.get("audit_go_live_run_id") or None,
        "mode": cfg.get("audit_mode") or AuditSource.MODE_LIVE,
        "program_name": cfg.get("audit_program_name") or source.program_name or "",
        "legacy_run_id": cfg.get("audit_decisions_run_id") or None,
    }
    if cfg.get("audit_setup") is not None:
        wanted["setup"] = cfg["audit_setup"]
    if opportunity_id and not source.opportunity_id:
        wanted["opportunity_id"] = opportunity_id
    if program_id and not source.program_id:
        wanted["program_id"] = program_id
    changed = [k for k, v in wanted.items() if getattr(source, k) != v]
    if "go_live_run_id" in changed:  # a different go-live run: its week is worked out afresh
        wanted["go_live_week"] = ""
        changed.append("go_live_week")
    if changed:
        for k in changed:
            setattr(source, k, wanted[k])
        source.save(update_fields=changed + ["updated_at"])
    return source


# ---------------------------------------------------------------------------
# Sync: source runs and the one-time import
# ---------------------------------------------------------------------------


def _week_ok(period_start, period_end) -> bool:
    """A weekly run: its period is 7 days (end - start = 6). The end date must be readable (the rules
    date each run by it); an unreadable start date passes."""
    try:
        b = date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return False
    try:
        a = date.fromisoformat(str(period_start)[:10])
    except ValueError:
        return True
    return (b - a).days == 6


def read_run_state(run) -> dict:
    """The SOURCE_KEYS of a completed run's snapshot, flat (``{"snapshot.meta": ...}``), as the
    run-history API projects them: from the run's summary when it covers them, else the snapshot."""
    from connect_labs.workflow.run_snapshot_store import snapshot_state
    from connect_labs.workflow.snapshot_runtime import project_state

    return project_state(snapshot_state(run, SOURCE_KEYS), SOURCE_KEYS)


def ingest_runs(source: AuditSource, dao) -> int:
    """Freeze every completed source run not seen before. Returns how many runs were added or dropped."""
    seen = set(source.source_runs.values_list("run_id", flat=True))
    changed = 0
    listed = [run for run in dao.list_runs(definition_id=source.source_workflow_id) if run.is_completed]
    # Follow the report: a run it no longer has (deleted, or replaced by a history rebuild) is
    # dropped here too. An empty listing is not trusted to mean "every run is gone".
    if listed:
        gone = seen - {run.id for run in listed}
        if gone:
            source.source_runs.filter(run_id__in=gone).delete()
            changed += len(gone)
    for run in listed:
        if run.id in seen:
            continue
        state = read_run_state(run)
        data = engine.from_kc_run(
            {"id": run.id, "period_start": run.period_start, "period_end": run.period_end, "state": state}
        )
        if data is None:  # a run without the 28-day worker figures: never usable, but remember it
            data = {"unusable": True}
        data["as_of"] = ((state or {}).get("snapshot.meta") or {}).get("as_of")
        # get_or_create: a page load and the after-save task may ingest the same run at once.
        try:
            with transaction.atomic():
                _, made = SourceRun.objects.get_or_create(
                    source=source,
                    run_id=run.id,
                    defaults={
                        "period_start": str(run.period_start or "")[:10],
                        "period_end": str(run.period_end or "")[:10],
                        "completed_at": str(run.completed_at or ""),
                        "data": data,
                    },
                )
        except IntegrityError:
            made = False
        changed += made
    return changed


def week_of(sr: SourceRun) -> str:
    """A source run's week: its as-of date. A history rebuild replaces a run with a new id for the same
    week, so decisions, events and go-live are tied to the week, never to the run id."""
    return str((sr.data or {}).get("as_of") or sr.period_end)[:10]


def weekly_runs(source: AuditSource) -> list[SourceRun]:
    """The runs the rules read: one per week (the newest copy of a week wins), oldest first."""
    by_week: dict[str, SourceRun] = {}
    for sr in source.source_runs.all():
        if sr.data.get("unusable") or not _week_ok(sr.period_start, sr.period_end):
            continue
        week = week_of(sr)
        cur = by_week.get(week)
        if cur is None or sr.completed_at > cur.completed_at:
            by_week[week] = sr
    return [by_week[k] for k in sorted(by_week)]


def import_run_state(source: AuditSource, state: dict, run_id: int) -> int:
    """Bring in the decisions and events the browser-only page saved in its run's state.

    Keys (workflow 30017, render code v3): ``open|<run>|<entity>``, ``close|<run>|<entity>``,
    ``dismiss|<run>|<entity>``, ``ev|<entity>@<opened run>|<id>``, and ``cfg``. Review numbers are
    given as that page gave them: in the order reviews were opened (run, then time, then entity).
    """
    run_index = {sr.run_id: i for i, sr in enumerate(weekly_runs(source))}
    weeks = {sr.run_id: week_of(sr) for sr in source.source_runs.all()}
    opens, rest, events = [], [], []
    for key, value in (state or {}).items():
        if not isinstance(value, dict):
            continue
        parts = key.split("|")
        if parts[0] == "ev" and len(parts) >= 3:
            events.append((parts[1], parts[2], value))
        elif parts[0] in ("open", "close", "dismiss") and len(parts) >= 3:
            try:
                rid = int(parts[1])
            except ValueError:
                continue
            item = (parts[0], rid, "|".join(parts[2:]), value)
            (opens if parts[0] == "open" else rest).append(item)
    opens.sort(key=lambda o: (run_index.get(o[1], 10**9), o[3].get("at") or 0, o[2]))
    count = 0
    with transaction.atomic():
        for i, (kind, rid, ent, v) in enumerate(opens):
            _, made = Decision.objects.get_or_create(
                source=source,
                kind=kind,
                run_id=rid,
                entity=ent,
                defaults={
                    "review_number": 1001 + i,
                    "week": weeks.get(rid, ""),
                    "decided_by": str(v.get("by") or ""),
                    "decided_at": from_ms(v.get("at") or 0),
                },
            )
            count += made
        for kind, rid, ent, v in rest:
            _, made = Decision.objects.get_or_create(
                source=source,
                kind=kind,
                run_id=rid,
                entity=ent,
                defaults={
                    "week": weeks.get(rid, ""),
                    "status": str(v.get("status") or ("closed_improved" if kind == "close" else "")),
                    "note": str(v.get("note") or ""),
                    "decided_by": str(v.get("by") or ""),
                    "decided_at": from_ms(v.get("at") or 0),
                },
            )
            count += made
        numbers = {
            f"{d.entity}@{d.run_id}": d.review_number
            for d in Decision.objects.filter(source=source, kind=Decision.OPEN)
        }
        for review_key, event_id, v in events:
            payload = {k: x for k, x in v.items() if k not in ("by", "at", "type")}
            _, made = TeamEvent.objects.get_or_create(
                source=source,
                event_id=event_id,
                defaults={
                    "review_key": review_key,
                    "review_number": numbers.get(review_key),
                    "type": str(v.get("type") or "comment"),
                    "payload": payload,
                    "created_by": str(v.get("by") or ""),
                    "created_at": from_ms(v.get("at") or 0),
                },
            )
            count += made
        locked = AuditSource.objects.select_for_update().get(pk=source.pk)
        if isinstance(state.get("cfg"), dict) and not locked.settings and not locked.settings_versions.exists():
            locked.settings = state["cfg"]
        locked.imported_from_run_id = run_id
        locked.save(update_fields=["settings", "imported_from_run_id", "updated_at"])
        source.settings, source.imported_from_run_id = locked.settings, locked.imported_from_run_id
    return count


def source_scope(cfg: dict, dao) -> dict | None:
    """Where the followed report's runs are, when not where ``dao`` reads: ``audit_source_program_id``
    (a program-owned report, e.g. KC Pulse 30534 in program 46), else an ``audit_source_opportunity_id``
    other than the dao's own. None: read them with ``dao``.

    Labs lists runs by their exact scope, so a program-owned report's runs cannot be seen from the
    opportunity the audit workflow lives in."""
    pid = cfg.get("audit_source_program_id")
    if pid:
        pid = int(pid)
        if getattr(dao, "program_id", None) == pid and not getattr(dao, "opportunity_id", None):
            return None
        return {"program_id": pid}
    oid = cfg.get("audit_source_opportunity_id")
    if oid and int(oid) != getattr(dao, "opportunity_id", None):
        return {"opportunity_id": int(oid)}
    return None


def _source_dao(dao, scope: dict):
    """A data access in the report's own scope, as the same person (their token)."""
    from connect_labs.workflow.data_access import WorkflowDataAccess

    token = getattr(dao, "access_token", None)
    if not token:
        raise Refused("Cannot read the report's runs in its own scope: no sign-in to read them with.")
    return WorkflowDataAccess(access_token=token, **scope)


def sync(source: AuditSource, dao, definition=None) -> bool:
    """Bring the stored inputs up to date as the viewer. Returns whether anything changed.

    It does not recompute: ``world`` does that when the inputs have changed, so a failure in the
    rules is reported as such rather than as a failure to read the source report."""
    cfg = _config(definition) if definition is not None else {}
    scope = source_scope(cfg, dao)
    source_dao = _source_dao(dao, scope) if scope else None
    try:
        # Held open while the runs are read: an externalized snapshot is fetched through it.
        added = ingest_runs(source, source_dao or dao)
    finally:
        if source_dao is not None:
            source_dao.close()
    legacy_run = cfg.get("audit_decisions_run_id")
    imported = 0
    if legacy_run and source.imported_from_run_id is None:
        run = dao.get_run(int(legacy_run))
        if run is not None:
            imported = import_run_state(source, dict(run.state or {}), int(legacy_run))
            logger.info("audit_reviews: imported %s decisions/events from run %s", imported, legacy_run)
    source.last_synced_at = dj_timezone.now()
    source.save(update_fields=["last_synced_at"])
    return bool(added or imported)


# ---------------------------------------------------------------------------
# Recompute
# ---------------------------------------------------------------------------


def _inputs(source: AuditSource):
    runs = weekly_runs(source)
    decisions = list(source.decisions.filter(revoked_at__isnull=True))
    events = list(source.team_events.all())
    return runs, decisions, events


#: The shape of the answer this module builds around the rules' output (team layer, Triage lists...).
#: Bump it whenever compute() adds or changes what it returns, so a deploy rebuilds every stored answer
#: instead of serving the old shape until an input happens to change. 2: pinned, owner (#3251); 3: set_aside;
#: 4: escalations shown on the parent's review (escalated_in), and Escalate in levelActions.
PAYLOAD_VERSION = 4


def _fingerprint(source, runs, decisions, events) -> str:
    h = hashlib.sha256()
    parts = [
        source.settings_versions.order_by("-number").values_list("number", flat=True).first(),
        engine.ENGINE_VERSION,
        PAYLOAD_VERSION,
        source.go_live_run_id,
        source.go_live_week,
        source.mode,
        source.program_name,
        source.setup,
        source.settings,
        [(r.run_id, r.completed_at, week_of(r)) for r in runs],
        [
            (
                d.id,
                d.kind,
                d.run_id,
                d.week,
                d.entity,
                d.review_number,
                d.closes_review,
                d.status,
                d.note,
                d.decided_by,
            )
            for d in decisions
        ],
        [(e.id, e.event_id, e.review_number) for e in events],
    ]
    h.update(json.dumps(parts, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _is_stale(source: AuditSource) -> bool:
    world = AuditWorld.objects.filter(source=source).first()
    if world is None:
        return True
    return world.fingerprint != _fingerprint(source, *_inputs(source))


def _metrics_from(measures) -> dict:
    out = {}
    for m in measures or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out[m["id"]] = {
            "title": m.get("title") or m.get("label") or m["id"],
            "unit": m.get("unit"),
            "direction": m.get("direction"),
            "bands": m.get("bands") or [],
            "min_den": 10 if m.get("min_denominator") is None else m.get("min_denominator"),
            "flw_applicable": m.get("flw_applicable"),
            "plain": m.get("plain"),
            "category": m.get("category"),
        }
    return out


def build_data(source: AuditSource, runs: list[SourceRun]) -> dict:
    """engine's ``D`` from the stored runs: the latest run's measures say what each metric is."""
    run_rows = []
    opps = {}
    for sr in runs:
        row = dict(sr.data)
        row["run_id"] = sr.run_id
        row["period_start"] = sr.period_start
        row["period_end"] = sr.period_end
        run_rows.append(row)
        for o in row.get("opps") or []:
            opps[o.get("id")] = {"id": o.get("id"), "name": f"Opportunity {o.get('id')}", "llo": o.get("llo")}
    setup = source.setup or {}
    return {
        "source": {
            "program_name": source.program_name or "Program",
            "opportunities": list(opps.values()),
        },
        "metrics": _metrics_from(run_rows[-1].get("measures") if run_rows else None),
        "pool": setup.get("pool") or {},
        "parents": setup.get("parents") or {},
        "supporting": setup.get("supporting") or [],
        "knownMetrics": setup.get("known"),
        "runs": run_rows,
    }


def decisions_for_engine(decisions: list[Decision], runs: list[SourceRun] | None = None, warnings=None) -> dict:
    """The stored decisions keyed as the rules read them: by the id of the run that holds their week
    today, so a decision survives the report rebuilding that week's run. A decision with no week
    (none recorded) keeps its run id; one whose week the report no longer has is set aside, with a
    warning."""
    run_of_week = {week_of(sr): sr.run_id for sr in (runs or [])}
    out = {"opens": {}, "closes": {}, "dismissed": {}}
    lost = 0
    for d in decisions:
        if d.week:
            rid = run_of_week.get(d.week)
            if rid is None:
                lost += 1
                continue
        else:
            rid = d.run_id
        entry = {"by": d.decided_by, "at": to_ms(d.decided_at)}
        if d.kind == Decision.OPEN:
            entry["num"] = d.review_number
            if d.metrics:
                entry["metrics"] = list(d.metrics)
            out["opens"].setdefault(str(rid), {})[d.entity] = entry
        elif d.kind == Decision.CLOSE:
            entry["status"] = d.status or "closed_improved"
            if d.note:
                entry["note"] = d.note
            if d.closes_review is not None:
                entry["review"] = d.closes_review
            out["closes"].setdefault(str(rid), {})[d.entity] = entry
        elif d.kind == Decision.DISMISS:
            out["dismissed"].setdefault(str(rid), {})[d.entity] = entry
    if lost and warnings is not None:
        warnings.append(
            f"{lost} saved decision{'s' if lost != 1 else ''} belong to a week KC Pulse no longer has a run for, "
            "so they are not shown. They are kept, and come back if that week's run does."
        )
    return out


def _go_live_run(source: AuditSource, runs: list[SourceRun]):
    """The run go-live points at today. Go-live is set as a run id; its week is remembered the first
    time that run is seen, so a rebuilt run (new id, same week) keeps the same go-live."""
    if not source.go_live_run_id:
        return None
    sr = source.source_runs.filter(run_id=source.go_live_run_id).first()
    if sr is not None and source.go_live_week != week_of(sr):
        source.go_live_week = week_of(sr)
        source.save(update_fields=["go_live_week", "updated_at"])
    if source.go_live_week:
        for r in runs:  # that week, or the first week after it the report still has
            if week_of(r) >= source.go_live_week:
                return r.run_id
        return "none"
    return source.go_live_run_id


def _clean(x):
    """Drop None-valued keys: the payload is large and the page treats missing as null."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def _replay_or_main_only(D, CFG, decisions, go_live, warnings):
    """Replay; if the rules still fail on something sanitize did not foresee, replay the main metrics
    alone, so reviews keep working and the page says what was left out."""
    try:
        return engine.replay(D, CFG, decisions, {"goLiveRunId": go_live}), CFG
    except Exception as e:  # noqa: BLE001 -- the fallback below says what happened, and logs it
        logger.exception("audit_reviews: replay failed; retrying with the main metrics only")
        mains = {k: v for k, v in CFG["metrics"].items() if v.get("role") == "main"}
        details = sorted(k for k in CFG["metrics"] if k not in mains)
        warnings.append(
            f"The detail metrics could not be processed ({type(e).__name__}), so only the main metrics are shown: "
            + ", ".join(details)
            + "."
        )
        main_cfg = {**CFG, "metrics": mains}
        main_data = {**D, "metrics": {k: D["metrics"][k] for k in mains}}
        return engine.replay(main_data, main_cfg, decisions, {"goLiveRunId": go_live}), main_cfg


def _add_escalations_in(reviews: list[dict]) -> None:
    """An Escalate action also shows on the review of the entity it went to (the parent), when that
    entity had a review open at the time: as an ``escalated_in`` event there, and the action names that
    review (``escalation.review``). Derived on every recompute; only the action itself is stored."""
    by_ent: dict[str, list[dict]] = {}
    for rv in reviews:
        by_ent.setdefault(rv["ent"], []).append(rv)
    for rv in reviews:
        for ev in list(rv["events"]):
            p = ev.get("payload") or {}
            esc = p.get("escalation")
            if ev.get("type") != "action" or p.get("action") != "escalate" or not isinstance(esc, dict):
                continue
            t = ev.get("t") or 0
            target = next(
                (
                    r
                    for r in by_ent.get(esc.get("to"), [])
                    if (r.get("openedAt") or 0) <= t and (not r.get("closedAt") or r["closedAt"] >= t)
                ),
                None,
            )
            if target is None:
                continue
            p["escalation"] = {**esc, "review": target["id"]}
            target["events"].append(
                {
                    "id": f"{ev['id']}:in",
                    "t": t,
                    "type": "escalated_in",
                    "actor": ev.get("actor"),
                    "team": True,
                    "payload": {
                        "type": "escalated_in",
                        "from_review": rv["id"],
                        "from": rv["ent"],
                        "from_name": rv.get("name") or rv["ent"],
                        "from_type": rv.get("type"),
                        "metrics": p.get("metrics") or [],
                        "note": p.get("note") or "",
                        "fields": p.get("fields") or {},
                        "attachments": p.get("attachments") or [],
                        "by": p.get("by"),
                        "at": p.get("at"),
                    },
                }
            )


def compute(source: AuditSource, settings: dict | None = None):
    """Replay every stored run with every stored decision, under ``settings`` (default: the saved
    ones). Saves nothing except a resolved go-live week. Returns (payload, review rows, fingerprint)."""
    runs, decisions, events = _inputs(source)
    fingerprint = _fingerprint(source, runs, decisions, events)
    D, warnings = engine.sanitize(build_data(source, runs))
    CFG = engine.default_config(D, (source.settings if settings is None else settings) or None)
    payload: dict = {
        "engine_version": engine.ENGINE_VERSION,
        "source": {
            "workflow_id": source.workflow_id,
            "source_workflow_id": source.source_workflow_id,
            "opportunity_id": source.opportunity_id,
            "program_id": source.program_id,
            "go_live_run_id": source.go_live_run_id,
            "mode": source.mode,
            "program_name": source.program_name,
        },
        "preview": not source.go_live_run_id,
        "warnings": warnings,
    }
    reviews_rows: list[Review] = []
    if runs:
        go_live = _go_live_run(source, runs) or runs[-1].run_id
        W, CFG = _replay_or_main_only(D, CFG, decisions_for_engine(decisions, runs, warnings), go_live, warnings)
        by_number: dict[int, list[TeamEvent]] = {}
        by_key: dict[str, list[TeamEvent]] = {}
        for e in events:
            if e.review_number is not None:
                by_number.setdefault(e.review_number, []).append(e)
            else:
                by_key.setdefault(e.review_key, []).append(e)
        for rv in W["reviews"]:
            rv["key"] = f"{rv['ent']}@{rv['openedRunId']}"
            team = by_number.get(rv["id"], []) + by_key.get(rv["key"], [])
            team.sort(key=lambda e: (e.created_at, e.id))
            for e in team:
                rv["events"].append(
                    {
                        "id": e.event_id,
                        "t": to_ms(e.created_at),
                        "type": e.type,
                        "actor": e.created_by,
                        "team": True,
                        "payload": {**e.payload, "type": e.type, "by": e.created_by, "at": to_ms(e.created_at)},
                    }
                )
            # Pins and the owner are the latest word, in the order they were said.
            pinned: list[str] = []
            owner = None
            for e in team:
                p = e.payload or {}
                if e.type == "pin" and p.get("target") not in pinned:
                    pinned.append(p.get("target"))
                elif e.type == "unpin" and p.get("target") in pinned:
                    pinned.remove(p.get("target"))
                elif e.type == "owner":
                    owner = {"user_id": p["user_id"], "name": p.get("name") or ""} if p.get("user_id") else None
            rv["pinned"] = pinned
            rv["owner"] = owner
            actions = [e for e in team if e.type == "action"]
            last = actions[-1] if actions else None
            reviews_rows.append(
                Review(
                    source=source,
                    number=rv["id"],
                    entity=rv["ent"],
                    entity_type=rv["type"],
                    name=str(rv.get("name") or "")[:300],
                    llo=str(rv.get("llo") or "")[:200],
                    status=rv["status"],
                    opened_run_id=int(rv["openedRunId"]),
                    opened_at=from_ms(rv["openedAt"]) if rv.get("openedAt") else None,
                    closed_at=from_ms(rv["closedAt"]) if rv.get("closedAt") else None,
                    metrics=rv["metrics"],
                    previous_number=rv.get("prevId"),
                    team_event_count=len(team),
                    last_action_at=last.created_at if last else None,
                    last_action_type=str((last.payload or {}).get("action") or "")[:40] if last else "",
                )
            )
        _add_escalations_in(W["reviews"])
        for rv in W["reviews"]:
            rv["events"].sort(key=lambda ev: ev["t"] or 0)
        latest = W["proposalsByRun"][W["latestRi"]] or []
        payload.update(
            {
                "D": {k: v for k, v in D.items() if k != "runs"},
                "CFG": CFG,
                "runs": [
                    {"run_id": r["run_id"], "period_start": r.get("period_start"), "period_end": r["period_end"]}
                    for r in D["runs"]
                ],
                "latestRi": W["latestRi"],
                "goLiveIdx": W["goLiveIdx"],
                "pf": W["pf"],
                "entityInfo": W["entityInfo"],
                "history": W["history"],
                "reviews": W["reviews"],
                "latestProposals": latest,
                "set_aside": set_aside_this_week(decisions, runs),
            }
        )
    payload["settings"] = source.settings if settings is None else settings
    payload["settings_history"] = settings_history(source)
    payload = _clean(engine.as_json_safe(payload))
    return payload, reviews_rows, fingerprint


def recompute(source: AuditSource) -> AuditWorld:
    """Replay every stored run with every stored decision; replace the derived rows."""
    payload, reviews_rows, fingerprint = compute(source)
    now = dj_timezone.now()
    payload["computed_at"] = now.isoformat()
    with transaction.atomic():
        # One writer at a time: a page load and the after-save task may recompute together.
        AuditSource.objects.select_for_update().filter(pk=source.pk).first()
        Review.objects.filter(source=source).delete()
        Review.objects.bulk_create(reviews_rows)
        world, _ = AuditWorld.objects.update_or_create(
            source=source,
            defaults={
                "engine_version": engine.ENGINE_VERSION,
                "fingerprint": fingerprint,
                "payload": payload,
                "computed_at": now,
            },
        )
    return world


def world(source: AuditSource, *, auto_apply: bool = True) -> dict:
    source.refresh_from_db()  # settings or go-live may have been saved through another object
    w = AuditWorld.objects.filter(source=source).first()
    if w is None or _is_stale(source):
        w = recompute(source)
    if auto_apply and (source.settings or {}).get("autoApply") and _auto_apply(source, w.payload):
        w = AuditWorld.objects.get(source=source)
    return w.payload


AUTOMATIC = "Automatic"


def _auto_apply(source: AuditSource, payload: dict) -> bool:
    """With "Apply automatically" on, the latest run's open and ready-to-close proposals are confirmed
    without Triage, recorded as decisions by "Automatic" like any other. Returns whether it did any."""
    if payload.get("preview") or not payload.get("runs"):
        return False
    if source.legacy_run_id and source.imported_from_run_id is None:
        return False
    items = [
        (
            {"kind": "open", "entity": p["ent"]}
            if p.get("cls") == "open"
            else {"kind": "close", "entity": p["ent"], "status": "closed_improved"}
        )
        for p in payload.get("latestProposals") or []
        if p.get("pending") and p.get("cls") in ("open", "close")
    ]
    if not items:
        return False
    try:
        record_decisions(source, items, user=None, by=AUTOMATIC)
    except Refused:
        logger.warning("audit_reviews: could not apply the proposals automatically", exc_info=True)
        return False
    return True


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _require_imported(source: AuditSource) -> None:
    """No new decision or event until the old page's saved ones are in: their review numbers come first."""
    if source.legacy_run_id and source.imported_from_run_id is None:
        raise Refused(
            "The decisions saved by the previous version of this page have not been read yet. Reload the page."
        )


def _latest(source: AuditSource) -> tuple[dict, dict]:
    """The current world, and its latest proposals by entity."""
    _require_imported(source)
    payload = world(source, auto_apply=False)  # deciding never triggers more automatic decisions
    if payload.get("preview"):
        raise Refused("No go-live run is set, so nothing can be confirmed yet.")
    if not payload.get("runs"):
        raise Refused("No runs of the report have been read yet.")
    return payload, {p["ent"]: p for p in payload.get("latestProposals") or []}


def record_decisions(
    source: AuditSource, items: list[dict], *, user, by: str, run_id: int | None = None
) -> list[dict]:
    """Confirm, set aside, or close, on the latest run. All or nothing. Returns what was recorded.

    ``run_id`` is the run the person was looking at: if a newer run has arrived since, nothing is recorded.
    """
    if not isinstance(items, list) or not items:
        raise Refused("Nothing to record.")
    ents = [str((item or {}).get("entity") or "") for item in items]
    if len(set(ents)) != len(ents):
        raise Refused("Each review or proposal can be decided only once at a time.")
    with transaction.atomic():
        source = AuditSource.objects.select_for_update().get(pk=source.pk)
        payload, props = _latest(source)
        latest_run = int(payload["runs"][payload["latestRi"]]["run_id"])
        latest_sr = source.source_runs.filter(run_id=latest_run).first()
        latest_week = week_of(latest_sr) if latest_sr else ""
        if run_id is not None and int(run_id) != latest_run:
            raise Refused(
                "A newer KC Pulse run has arrived since this page was opened. Reload the page and decide again."
            )
        open_reviews = {r["ent"]: r for r in payload.get("reviews") or [] if r.get("status") == "open"}
        next_num = max([d for d in source.decisions.values_list("review_number", flat=True) if d] or [1000]) + 1
        now = dj_timezone.now()
        done = []
        for item in items:
            kind = (item or {}).get("kind")
            ent = str((item or {}).get("entity") or "")
            p = props.get(ent)
            if kind == UNDO_DISMISS:
                done.append(_undo_dismiss(source, ent, latest_run, latest_week, by=by, note=item.get("note"), now=now))
                continue
            if kind == Decision.OPEN:
                if not p or p.get("cls") != "open" or not p.get("pending"):
                    raise Refused(f"{ent} has no review waiting to be opened this week.")
                d = Decision(kind=kind, review_number=next_num, metrics=list(p.get("metrics") or []))
                next_num += 1
            elif kind == Decision.DISMISS:
                if not p or p.get("cls") not in ("open", "close") or not p.get("pending"):
                    raise Refused(f"{ent} has nothing waiting for a decision this week.")
                if p.get("cls") == "open" and not str(item.get("note") or "").strip():
                    raise Refused("Say why a proposed new review is not being opened this week.")
                d = Decision(kind=kind)
            elif kind == Decision.CLOSE:
                status = item.get("status") or "closed_improved"
                if status not in CLOSE_STATUSES:
                    raise Refused(f"Unknown status {status}.")
                if ent not in open_reviews:
                    raise Refused(f"{ent} has no open review.")
                if status == "closed_improved" and not (p and p.get("cls") == "close"):
                    raise Refused("Closed – improved needs every metric under review green for long enough.")
                if status == "closed_other" and not str(item.get("note") or "").strip():
                    raise Refused("Closed – other needs a note.")
                d = Decision(kind=kind, status=status, closes_review=open_reviews[ent].get("id"))
            else:
                raise Refused(f"Unknown decision {kind}.")
            d.source = source
            d.run_id = latest_run
            d.week = latest_week
            d.entity = ent
            d.note = str(item.get("note") or "")[:5000]
            d.decided_by = by
            d.user = user if getattr(user, "pk", None) else None
            d.decided_at = now
            same = Decision.objects.filter(source=source, kind=d.kind, entity=ent, revoked_at__isnull=True)
            if same.filter(run_id=d.run_id).exists() or (d.week and same.filter(week=d.week).exists()):
                raise Refused(f"That decision on {ent} has already been recorded.")
            d.save()
            done.append({"kind": d.kind, "entity": ent, "review_number": d.review_number, "status": d.status})
    recompute(source)
    return done


#: Put a "Not this week" back in Triage, so the proposal can be confirmed after all.
UNDO_DISMISS = "undo_dismiss"


def _undo_dismiss(source, ent, latest_run, latest_week, *, by, note, now) -> dict:
    """Undo this week's "Not this week" on ``ent``: kept for the record, no longer counted."""
    q = Decision.objects.filter(source=source, kind=Decision.DISMISS, entity=ent, revoked_at__isnull=True)
    d = (q.filter(week=latest_week).first() if latest_week else None) or q.filter(run_id=latest_run).first()
    if d is None:
        raise Refused(f"{ent} was not set aside this week.")
    d.revoked_at = now
    d.revoked_by = by[:200]
    d.revoke_note = str(note or "")[:5000]
    d.save(update_fields=["revoked_at", "revoked_by", "revoke_note"])
    return {"kind": UNDO_DISMISS, "entity": ent}


def set_aside_this_week(decisions: list[Decision], runs: list[SourceRun]) -> list[dict]:
    """This week's "Not this week" decisions, for Triage's list of what was set aside and why."""
    if not runs:
        return []
    last = runs[-1]
    week, rid = week_of(last), last.run_id
    out = []
    for d in decisions:
        if d.kind != Decision.DISMISS or d.revoked_at is not None:
            continue
        if (d.week and d.week == week) or (not d.week and d.run_id == rid):
            out.append({"entity": d.entity, "note": d.note, "by": d.decided_by, "at": to_ms(d.decided_at)})
    return out


def record_event(source: AuditSource, *, review_key: str, event_id: str, type: str, payload: dict, user, by: str):
    """An action or comment on a review. Saving the same event id twice keeps the first."""
    if type not in TEAM_EVENT_TYPES:
        raise Refused(f"Unknown event type {type}.")
    if not event_id or len(event_id) > 60:
        raise Refused("An event needs an id.")
    current = world(source)
    review = next((r for r in current.get("reviews") or [] if r.get("key") == review_key), None)
    if review is None:
        raise Refused("No such review.")
    _require_imported(source)
    clean = {k: v for k, v in (payload or {}).items() if k not in ("by", "at", "type")}
    for key in ("action", "option"):
        if clean.get(key) is not None and (not isinstance(clean[key], str) or len(clean[key]) > 40):
            raise Refused(f"Unknown {key}.")
    if type == "action":
        clean = _clean_action(source, current, review, clean)
    else:
        clean.pop("attachments", None)
    if len(json.dumps(clean, default=str)) > 20_000:
        raise Refused("That is too long to save.")
    if type in ("pin", "unpin"):
        target = clean.get("target")
        pinned = (
            TeamEvent.objects.filter(source=source, event_id=target).first()
            if isinstance(target, str) and target
            else None
        )
        if (
            pinned is None
            or pinned.type not in PINNABLE
            or not (pinned.review_number == review.get("id") or pinned.review_key == review_key)
        ):
            raise Refused("Only a comment or an action on this review can be pinned.")
        clean = {"target": target}
    elif type == "owner":
        uid = clean.get("user_id")
        if uid is None:
            clean = {"user_id": None, "name": ""}
        else:
            if isinstance(uid, bool) or not isinstance(uid, int):
                raise Refused("Choose the owner from the list.")
            person = owner_pool(source).filter(pk=uid).first()
            if person is None:
                raise Refused("That person cannot own audit reviews.")
            clean = {"user_id": person.pk, "name": owner_name(person)}
    _, made = TeamEvent.objects.get_or_create(
        source=source,
        event_id=event_id,
        defaults={
            "review_key": review_key,
            "review_number": review.get("id"),
            "type": type,
            "payload": clean,
            "created_by": by,
            "user": user if getattr(user, "pk", None) else None,
            "created_at": dj_timezone.now(),
        },
    )
    if made:
        recompute(source)
    return made


def _clean_action(source: AuditSource, current: dict, review: dict, clean: dict) -> dict:
    """An action's attachments, named by id, become {id, name, size, type} of this workflow's own
    uploads; an Escalate gets where it went (the review's parent entity) from the server, never the page."""
    atts = clean.get("attachments")
    if atts is not None:
        if not isinstance(atts, list) or len(atts) > MAX_ATTACHMENTS_PER_ACTION:
            raise Refused(f"An action can carry at most {MAX_ATTACHMENTS_PER_ACTION} documents.")
        ids = []
        for a in atts:
            aid = a.get("id") if isinstance(a, dict) else a
            if isinstance(aid, bool) or not isinstance(aid, int):
                raise Refused("Attach documents by uploading them.")
            ids.append(aid)
        ids = list(dict.fromkeys(ids))
        found = {a.pk: a for a in Attachment.objects.filter(source=source, pk__in=ids)}
        if len(found) != len(ids):
            raise Refused("An attached document was not found; upload it again.")
        clean["attachments"] = [attachment_info(found[i]) for i in ids]
    if clean.get("action") == "escalate":
        info = current.get("entityInfo") or {}
        parent = (info.get(review.get("ent")) or {}).get("parent")
        if review.get("type") == "program" or not parent:
            raise Refused("There is nothing above the program to escalate to.")
        to = info.get(parent) or {}
        clean["escalation"] = {
            "to": parent,
            "to_name": to.get("name") or ("Program" if parent == "program" else parent.split(":", 1)[-1]),
            "to_type": to.get("type") or ("program" if parent == "program" else "llo"),
        }
    else:
        clean.pop("escalation", None)
    return clean


# ---------------------------------------------------------------------------
# Attachments: documents uploaded for an action (e.g. the record of an escalation)
# ---------------------------------------------------------------------------

#: The most the page's sandbox bridge carries in one request (components/workflow/sandbox/protocol.ts
#: LIMITS.fetchBodyBytes), so the page can send a file as it is.
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_ATTACHMENTS_PER_ACTION = 5
#: Documents people keep as a record. Anything a browser would run (HTML, SVG, scripts) is refused,
#: and every file is served as a download, never shown in the page.
ATTACHMENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "csv": "text/csv",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "eml": "message/rfc822",
    "msg": "application/vnd.ms-outlook",
}
_SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def attachment_info(a: Attachment) -> dict:
    return {"id": a.pk, "name": a.filename, "size": a.size_bytes, "type": a.content_type}


def upload_attachment(source: AuditSource, raw: bytes, filename: str, *, user, by: str) -> dict:
    """Store one document for this workflow's reviews; {id, name, size, type} for an action to name."""
    _require_imported(source)
    base = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in base if ch >= " " and ch != "\x7f").strip()[:200]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not name or ext not in ATTACHMENT_TYPES:
        raise Refused("Attach a PDF, Word, Excel, PowerPoint, text, CSV, image (PNG or JPG) or email file.")
    if not raw:
        raise Refused("That file is empty.")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise Refused(
            f"That file is over {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB. Add a link to it in the note instead."
        )
    digest = hashlib.sha256(raw).hexdigest()
    safe = "".join(ch if ch in _SAFE_NAME else "_" for ch in name)
    key = f"audit_reviews/{source.workflow_id}/{digest[:16]}-{safe}"
    try:
        stored = default_storage.save(key, ContentFile(raw))
    except Exception as e:
        logger.exception("audit_reviews: the file store refused %s", key)
        raise Refused(
            "The file store refused this upload, so nothing was saved. Add a link to the document instead."
        ) from e
    a = Attachment.objects.create(
        source=source,
        storage_key=stored,
        filename=name,
        content_type=ATTACHMENT_TYPES[ext],
        size_bytes=len(raw),
        sha256=digest,
        uploaded_by=by,
        user=user if getattr(user, "pk", None) else None,
        uploaded_at=dj_timezone.now(),
    )
    return attachment_info(a)


def open_attachment(source: AuditSource, attachment_id: int):
    """(attachment, open file) for one of this workflow's documents, or None."""
    a = Attachment.objects.filter(source=source, pk=attachment_id).first()
    if a is None:
        return None
    return a, default_storage.open(a.storage_key, "rb")


# ---------------------------------------------------------------------------
# Settings: validate, preview, save, undo
# ---------------------------------------------------------------------------

ROLES = {"main", "detail", "off"}
#: The AI checkers that exist in Labs today (connect_labs/labs/ai_review_agents); "none" = people review the photos.
CHANGE_TYPES = set(engine.NEGATIVE_DEFAULT) | {"improved_to_yellow", "improved_to_green", "stayed_green", "too_few"}
_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _ids(x, allowed, what) -> list:
    if not isinstance(x, list) or any(not isinstance(i, str) or i not in allowed for i in x):
        raise Refused(f"{what} must be a list of: {', '.join(sorted(allowed))}.")
    return list(dict.fromkeys(x))


def validate_settings(source: AuditSource, settings: dict) -> dict:
    """The settings as they will be stored, or Refused naming what is wrong.

    Shape (everything optional; what is missing takes the default):
      metrics       {<id>: {role, levels, initiateOn, persistence, parent, options}}
      levelActions  {<level>: [<action>]}
      actionKeys    (written on save) the actions there were, so one added later is on by default
      closeAfter    1-8 consecutive green runs before "ready to close"
      autoApply     apply open/close proposals without Triage
      options       {<key>: {label, action, levels, task, custom}}  what staff can record under an action
      knownMetrics  metrics an admin has confirmed (a new one arrives as a detail until confirmed)
      chatbotTasks  [{id, name, kind, bot, botName, levels, askQuestions}]  Open Chat Studio tasks
    """
    if not isinstance(settings, dict):
        raise Refused("Settings must be an object.")
    current = world(source)
    known_ids = set((current.get("CFG") or {}).get("metrics") or {}) | set(
        (current.get("D") or {}).get("metrics") or {}
    )
    levels = set(engine.LEVELS)
    out: dict = {}
    metrics = settings.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise Refused("metrics must be an object.")
    clean_metrics = {}
    for mid, m in metrics.items():
        if not isinstance(mid, str):
            raise Refused("Metric ids must be text.")
        if mid not in known_ids:
            raise Refused(f"Unknown metric {mid}.")
        if not isinstance(m, dict):
            raise Refused(f"{mid}: settings must be an object.")
        cm = {}
        if "role" in m:
            if not isinstance(m["role"], str) or m["role"] not in ROLES:
                raise Refused(f"{mid}: role must be main, detail or off.")
            cm["role"] = m["role"]
        if "levels" in m:
            cm["levels"] = _ids(m["levels"], levels, f"{mid}: levels")
        if "initiateOn" in m:
            cm["initiateOn"] = _ids(m["initiateOn"], CHANGE_TYPES, f"{mid}: what opens a review")
        if "persistence" in m:
            if not isinstance(m["persistence"], int) or not 1 <= m["persistence"] <= 8:
                raise Refused(f"{mid}: runs in a row must be 1-8.")
            cm["persistence"] = m["persistence"]
        if "parent" in m:
            if m["parent"] is not None and (not isinstance(m["parent"], str) or m["parent"] not in known_ids):
                raise Refused(f"{mid}: unknown parent metric {m['parent']}.")
            cm["parent"] = m["parent"]
        if "options" in m:
            if not isinstance(m["options"], list) or any(not isinstance(o, str) for o in m["options"]):
                raise Refused(f"{mid}: options must be a list of option keys.")
            cm["options"] = list(dict.fromkeys(m["options"]))
        # The image audit is a saved setup in Labs' audit app (connect_labs/audit/setups.py), named by id;
        # the photo / form / checker fields a metric once carried itself ("audit") are no longer kept.
        if "auditSetup" in m:
            sid = m["auditSetup"]
            if sid is not None:
                if isinstance(sid, bool) or not isinstance(sid, int):
                    raise Refused(f"{mid}: the image audit setup must be chosen from the list.")
                if audit_setups.get(sid) is None:
                    raise Refused(f"{mid}: image audit setup {sid} does not exist or was archived.")
            cm["auditSetup"] = sid
        # Offering an AI image audit needs a setup to make it from: without one, the option is not kept.
        if "image_audit" in (cm.get("options") or []) and not cm.get("auditSetup"):
            cm["options"] = [o for o in cm["options"] if o != "image_audit"]
        clean_metrics[mid] = cm
    out["metrics"] = clean_metrics
    la = settings.get("levelActions") or {}
    if not isinstance(la, dict):
        raise Refused("levelActions must be an object.")
    out["levelActions"] = {
        lv: _ids(acts, set(engine.ACTION_KEYS), f"Actions for {lv}") for lv, acts in la.items() if lv in levels
    }
    for lv, acts in out["levelActions"].items():
        if "escalate" in acts and lv not in engine.ESCALATE_LEVELS:
            raise Refused("There is nothing above the program to escalate to, so it cannot offer Escalate.")
    # The actions these settings were saved knowing: a level saved without Escalate now means "off".
    out["actionKeys"] = list(engine.ACTION_KEYS)
    if "closeAfter" in settings:
        ca = settings["closeAfter"]
        if not isinstance(ca, int) or not 1 <= ca <= 8:
            raise Refused("Ready to close needs 1-8 green runs in a row.")
        out["closeAfter"] = ca
    out["autoApply"] = bool(settings.get("autoApply"))
    options = settings.get("options") or {}
    if not isinstance(options, dict):
        raise Refused("options must be an object.")
    clean_options = {}
    for key, o in options.items():
        if not isinstance(key, str) or not key or len(key) > 40 or set(key) - _KEY_CHARS:
            raise Refused(f"Option key {key!r} must be lower-case letters, digits and _.")
        if not isinstance(o, dict) or not isinstance(o.get("label"), str) or not o["label"].strip():
            raise Refused(f"Option {key} needs a label.")
        if o.get("action") not in engine.ACTION_KEYS:
            raise Refused(f"Option {key}: unknown action.")
        clean_options[key] = {
            "label": o["label"].strip()[:60],
            "action": o["action"],
            "levels": _ids(o.get("levels") or [], levels, f"Option {key}: levels"),
            "task": bool(o.get("task")),
            "custom": bool(o.get("custom")),
        }
    out["options"] = clean_options
    out["chatbotTasks"] = _clean_chatbot_tasks(settings.get("chatbotTasks"), levels)
    if settings.get("knownMetrics") is not None:
        km = settings["knownMetrics"]
        if not isinstance(km, list) or any(not isinstance(i, str) or i not in known_ids for i in km):
            raise Refused("knownMetrics must list known metrics.")
        out["knownMetrics"] = list(dict.fromkeys(km))
    for mid, cm in clean_metrics.items():
        parent = cm.get("parent")
        if parent is not None and (clean_metrics.get(parent) or {}).get("role", None) == "off":
            raise Refused(f"{mid}: its parent metric is turned off.")
    if len(json.dumps(out)) > 100_000:
        raise Refused("Those settings are too large to save.")
    return out


#: A chatbot task's kind, and the levels it may be offered on. An investigation talks to ONE FLW about
#: everything on their review; a survey goes to every FLW under an LLO, opportunity or the program.
CHATBOT_KINDS = {"investigation": {"flw"}, "survey": {"llo", "opportunity", "program"}}
MAX_CHATBOT_TASKS = 20


def _clean_chatbot_tasks(tasks, levels: set) -> list[dict]:
    """Settings' chatbot tasks: which Open Chat Studio bot each uses and where it is offered.

    Labs sends each conversation FACTS (the review's metrics, or a survey's questions) as a coaching
    briefing in the session state; how the bot words anything lives in the bot, in OCS. So a task holds
    no prompt: only its name, kind, bot and levels, and for a survey whether staff type the questions
    when sending (a one-off survey) or the bot already has them (a standing survey).
    """
    if tasks is None:
        return []
    if not isinstance(tasks, list) or len(tasks) > MAX_CHATBOT_TASKS:
        raise Refused(f"Chatbot tasks must be a list of at most {MAX_CHATBOT_TASKS}.")
    out, seen = [], set()
    for t in tasks:
        if not isinstance(t, dict):
            raise Refused("Each chatbot task must be an object.")
        tid = t.get("id")
        if not isinstance(tid, str) or not tid or len(tid) > 30 or set(tid) - _KEY_CHARS:
            raise Refused("A chatbot task's id must be lower-case letters, digits and _ (at most 30).")
        if tid in seen:
            raise Refused(f"Chatbot task {tid} is listed twice.")
        seen.add(tid)
        name = t.get("name")
        if not isinstance(name, str) or not name.strip():
            raise Refused(f"Chatbot task {tid} needs a name.")
        kind = t.get("kind")
        if kind not in CHATBOT_KINDS:
            raise Refused(f"Chatbot task {name.strip()}: the kind must be investigation or survey.")
        bot = t.get("bot")
        if not isinstance(bot, str) or not bot.strip() or len(bot) > 100:
            raise Refused(f"Chatbot task {name.strip()}: choose its Open Chat Studio bot.")
        task_levels = _ids(t.get("levels") or [], levels, f"Chatbot task {name.strip()}: levels")
        stray = [lv for lv in task_levels if lv not in CHATBOT_KINDS[kind]]
        if stray or not task_levels:
            allowed = ", ".join(sorted(CHATBOT_KINDS[kind]))
            raise Refused(f"Chatbot task {name.strip()}: a {kind} can be offered on {allowed} reviews only.")
        clean = {
            "id": tid,
            "name": name.strip()[:60],
            "kind": kind,
            "bot": bot.strip(),
            "botName": str(t.get("botName") or "").strip()[:120],
            "levels": task_levels,
        }
        if kind == "survey":
            clean["askQuestions"] = bool(t.get("askQuestions", True))
        out.append(clean)
    return out


def _summary(payload: dict) -> dict:
    """What a person checks before saving: open reviews per level, and what Triage would hold."""
    open_by_level: dict = {}
    for r in payload.get("reviews") or []:
        if r.get("status") == "open":
            open_by_level[r.get("type")] = open_by_level.get(r.get("type"), 0) + 1
    pending = [p for p in payload.get("latestProposals") or [] if p.get("pending")]
    mains = sorted(k for k, v in ((payload.get("CFG") or {}).get("metrics") or {}).items() if v.get("role") == "main")
    return {
        "open_reviews": sum(open_by_level.values()),
        "open_by_level": open_by_level,
        "triage_open_new": sum(1 for p in pending if p.get("cls") == "open"),
        "triage_ready_to_close": sum(1 for p in pending if p.get("cls") == "close"),
        "main_metrics": mains,
    }


def preview_settings(source: AuditSource, settings: dict) -> dict:
    """How the reviews would look under ``settings``, beside how they look now. Saves nothing."""
    clean = validate_settings(source, settings)
    after, _, _ = compute(source, clean)
    return {"before": _summary(world(source)), "after": _summary(after), "warnings": after.get("warnings") or []}


def settings_history(source: AuditSource, limit: int = 20) -> list[dict]:
    return [
        {"number": v.number, "note": v.note, "saved_by": v.saved_by, "saved_at": v.saved_at.isoformat()}
        for v in source.settings_versions.order_by("-number")[:limit]
    ]


def _version(v) -> int | None:
    """The settings version a person edited, as sent by the page; None when not sent."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).strip().isdigit():
        raise Refused("The settings version must be a whole number.")
    return int(v)


def _save_version(source: AuditSource, settings: dict, *, user, by: str, note: str) -> SettingsVersion:
    last = source.settings_versions.order_by("-number").first()
    if last is None and source.settings:
        # The settings in use before the first save (e.g. imported from the old page) become version 1.
        last = SettingsVersion.objects.create(
            source=source,
            number=1,
            settings=source.settings,
            note="Settings before the first change",
            saved_by="",
            saved_at=dj_timezone.now(),
        )
    v = SettingsVersion.objects.create(
        source=source,
        number=(last.number if last else 0) + 1,
        settings=settings,
        note=(note or "")[:300],
        saved_by=by,
        user=user if getattr(user, "pk", None) else None,
        saved_at=dj_timezone.now(),
    )
    source.settings = settings
    source.save(update_fields=["settings", "updated_at"])
    return v


def save_settings(
    source: AuditSource, settings: dict, *, user, by: str, note: str = "", expected_version=None
) -> dict:
    """Validate, save as a new version, recompute every week. ``expected_version`` (the version the
    person edited) refuses the save if someone else saved in between."""
    clean = validate_settings(source, settings)
    with transaction.atomic():
        source = AuditSource.objects.select_for_update().get(pk=source.pk)
        current = source.settings_versions.order_by("-number").values_list("number", flat=True).first() or 0
        expected = _version(expected_version)
        if expected is not None and expected != current:
            raise Refused("Someone else saved the settings since you opened them. Reload and make your change again.")
        v = _save_version(source, clean, user=user, by=by, note=note)
    recompute(source)
    return {"number": v.number}


def undo_settings(source: AuditSource, *, user, by: str, expected_version=None) -> dict:
    """Put the previous version's settings back, as a new version."""
    with transaction.atomic():
        source = AuditSource.objects.select_for_update().get(pk=source.pk)
        versions = list(source.settings_versions.order_by("-number")[:2])
        if len(versions) < 2:
            raise Refused("There is no earlier version to go back to.")
        expected = _version(expected_version)
        if expected is not None and expected != versions[0].number:
            raise Refused("Someone else saved the settings since you opened them. Reload and try again.")
        v = _save_version(source, versions[1].settings, user=user, by=by, note=f"Undid version {versions[0].number}")
    recompute(source)
    return {"number": v.number}
