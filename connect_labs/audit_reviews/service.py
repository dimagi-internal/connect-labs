"""Audit reviews: everything that changes the stored state, and the recompute that follows it.

The views call four things:

- ``open_source(dao, definition)``  the AuditSource for an audit workflow (made on first use).
- ``sync(source, dao)``             freeze any newly completed source runs; import the decisions the
                                    old page saved in its run's state, once; recompute if anything
                                    changed. Runs as the viewer (their token reads the source runs).
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

from django.db import transaction
from django.utils import timezone as dj_timezone

from connect_labs.audit_reviews import engine
from connect_labs.audit_reviews.models import AuditSource, AuditWorld, Decision, Review, SourceRun, TeamEvent

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
TEAM_EVENT_TYPES = {"action", "comment"}


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
    (pool, parents, supporting, known, registry) and ``audit_program_name``.
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
    }
    if cfg.get("audit_setup") is not None:
        wanted["setup"] = cfg["audit_setup"]
    if opportunity_id and not source.opportunity_id:
        wanted["opportunity_id"] = opportunity_id
    if program_id and not source.program_id:
        wanted["program_id"] = program_id
    changed = [k for k, v in wanted.items() if getattr(source, k) != v]
    if changed:
        for k in changed:
            setattr(source, k, wanted[k])
        source.save(update_fields=changed + ["updated_at"])
    return source


# ---------------------------------------------------------------------------
# Sync: source runs and the one-time import
# ---------------------------------------------------------------------------


def _week_ok(period_start, period_end) -> bool:
    """A weekly run: its period is 7 days (end - start = 6). Unknown dates pass."""
    try:
        a = date.fromisoformat(str(period_start)[:10])
        b = date.fromisoformat(str(period_end)[:10])
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
        SourceRun.objects.create(
            source=source,
            run_id=run.id,
            period_start=str(run.period_start or "")[:10],
            period_end=str(run.period_end or "")[:10],
            completed_at=str(run.completed_at or ""),
            data=data,
        )
        changed += 1
    return changed


def weekly_runs(source: AuditSource) -> list[SourceRun]:
    """The runs the rules read: one per week (the newest copy of a week wins), oldest first."""
    by_week: dict[str, SourceRun] = {}
    for sr in source.source_runs.all():
        if sr.data.get("unusable") or not _week_ok(sr.period_start, sr.period_end):
            continue
        week = str(sr.data.get("as_of") or sr.period_end)[:10]
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
                    "status": str(v.get("status") or ("closed_improved" if kind == "close" else "")),
                    "note": str(v.get("note") or ""),
                    "decided_by": str(v.get("by") or ""),
                    "decided_at": from_ms(v.get("at") or 0),
                },
            )
            count += made
        for review_key, event_id, v in events:
            payload = {k: x for k, x in v.items() if k not in ("by", "at", "type")}
            _, made = TeamEvent.objects.get_or_create(
                source=source,
                event_id=event_id,
                defaults={
                    "review_key": review_key,
                    "type": str(v.get("type") or "comment"),
                    "payload": payload,
                    "created_by": str(v.get("by") or ""),
                    "created_at": from_ms(v.get("at") or 0),
                },
            )
            count += made
        if isinstance(state.get("cfg"), dict) and not source.settings:
            source.settings = state["cfg"]
        source.imported_from_run_id = run_id
        source.save(update_fields=["settings", "imported_from_run_id", "updated_at"])
    return count


def sync(source: AuditSource, dao, definition=None) -> bool:
    """Bring the stored inputs up to date as the viewer; recompute if anything changed or the world is
    stale. Returns whether it recomputed."""
    added = ingest_runs(source, dao)
    cfg = _config(definition) if definition is not None else {}
    legacy_run = cfg.get("audit_decisions_run_id")
    imported = 0
    if legacy_run and source.imported_from_run_id is None:
        run = dao.get_run(int(legacy_run))
        if run is not None:
            imported = import_run_state(source, dict(run.state or {}), int(legacy_run))
            logger.info("audit_reviews: imported %s decisions/events from run %s", imported, legacy_run)
    source.last_synced_at = dj_timezone.now()
    source.save(update_fields=["last_synced_at"])
    if added or imported or _is_stale(source):
        recompute(source)
        return True
    return False


# ---------------------------------------------------------------------------
# Recompute
# ---------------------------------------------------------------------------


def _inputs(source: AuditSource):
    runs = weekly_runs(source)
    decisions = list(source.decisions.all())
    events = list(source.team_events.all())
    return runs, decisions, events


def _fingerprint(source, runs, decisions, events) -> str:
    h = hashlib.sha256()
    parts = [
        engine.ENGINE_VERSION,
        source.go_live_run_id,
        source.mode,
        source.program_name,
        source.setup,
        source.settings,
        [(r.run_id, r.completed_at) for r in runs],
        [(d.id, d.kind, d.run_id, d.entity, d.review_number, d.status, d.note, d.decided_by) for d in decisions],
        [(e.id, e.event_id) for e in events],
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


def decisions_for_engine(decisions: list[Decision]) -> dict:
    out = {"opens": {}, "closes": {}, "dismissed": {}}
    for d in decisions:
        entry = {"by": d.decided_by, "at": to_ms(d.decided_at)}
        if d.kind == Decision.OPEN:
            entry["num"] = d.review_number
            out["opens"].setdefault(str(d.run_id), {})[d.entity] = entry
        elif d.kind == Decision.CLOSE:
            entry["status"] = d.status or "closed_improved"
            if d.note:
                entry["note"] = d.note
            out["closes"].setdefault(str(d.run_id), {})[d.entity] = entry
        elif d.kind == Decision.DISMISS:
            out["dismissed"].setdefault(str(d.run_id), {})[d.entity] = entry
    return out


def _clean(x):
    """Drop None-valued keys: the payload is large and the page treats missing as null."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def recompute(source: AuditSource) -> AuditWorld:
    """Replay every stored run with every stored decision; replace the derived rows."""
    runs, decisions, events = _inputs(source)
    fingerprint = _fingerprint(source, runs, decisions, events)
    D = build_data(source, runs)
    CFG = engine.default_config(D, source.settings or None)
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
    }
    reviews_rows: list[Review] = []
    if runs:
        go_live = source.go_live_run_id or runs[-1].run_id
        W = engine.replay(D, CFG, decisions_for_engine(decisions), {"goLiveRunId": go_live})
        events_by_review: dict[str, list[TeamEvent]] = {}
        for e in events:
            events_by_review.setdefault(e.review_key, []).append(e)
        for rv in W["reviews"]:
            rv["key"] = f"{rv['ent']}@{rv['openedRunId']}"
            team = events_by_review.get(rv["key"], [])
            for e in team:
                rv["events"].append(
                    {
                        "t": to_ms(e.created_at),
                        "type": e.type,
                        "actor": e.created_by,
                        "team": True,
                        "payload": {**e.payload, "type": e.type, "by": e.created_by, "at": to_ms(e.created_at)},
                    }
                )
            rv["events"].sort(key=lambda ev: ev["t"] or 0)
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
                    last_action_type=str((last.payload or {}).get("action") or "") if last else "",
                )
            )
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
            }
        )
    payload = _clean(engine.as_json_safe(payload))
    now = dj_timezone.now()
    payload["computed_at"] = now.isoformat()
    with transaction.atomic():
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


def world(source: AuditSource) -> dict:
    w = AuditWorld.objects.filter(source=source).first()
    if w is None or _is_stale(source):
        w = recompute(source)
    return w.payload


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _latest(source: AuditSource) -> tuple[dict, dict]:
    """The current world, and its latest proposals by entity."""
    payload = world(source)
    if payload.get("preview"):
        raise Refused("No go-live run is set, so nothing can be confirmed yet.")
    if not payload.get("runs"):
        raise Refused("No runs of the report have been read yet.")
    return payload, {p["ent"]: p for p in payload.get("latestProposals") or []}


def record_decisions(source: AuditSource, items: list[dict], *, user, by: str) -> list[dict]:
    """Confirm, set aside, or close, on the latest run. All or nothing. Returns what was recorded."""
    if not isinstance(items, list) or not items:
        raise Refused("Nothing to record.")
    with transaction.atomic():
        source = AuditSource.objects.select_for_update().get(pk=source.pk)
        payload, props = _latest(source)
        latest_run = int(payload["runs"][payload["latestRi"]]["run_id"])
        open_reviews = {r["ent"]: r for r in payload.get("reviews") or [] if r.get("status") == "open"}
        next_num = max([d for d in source.decisions.values_list("review_number", flat=True) if d] or [1000]) + 1
        now = dj_timezone.now()
        done = []
        for item in items:
            kind = (item or {}).get("kind")
            ent = str((item or {}).get("entity") or "")
            p = props.get(ent)
            if kind == Decision.OPEN:
                if not p or p.get("cls") != "open" or not p.get("pending"):
                    raise Refused(f"{ent} has no review waiting to be opened this week.")
                d = Decision(kind=kind, review_number=next_num)
                next_num += 1
            elif kind == Decision.DISMISS:
                if not p or p.get("cls") not in ("open", "close") or not p.get("pending"):
                    raise Refused(f"{ent} has nothing waiting for a decision this week.")
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
                d = Decision(kind=kind, status=status)
            else:
                raise Refused(f"Unknown decision {kind}.")
            d.source = source
            d.run_id = latest_run
            d.entity = ent
            d.note = str(item.get("note") or "")[:5000]
            d.decided_by = by
            d.user = user if getattr(user, "pk", None) else None
            d.decided_at = now
            if Decision.objects.filter(source=source, kind=d.kind, run_id=d.run_id, entity=ent).exists():
                raise Refused(f"That decision on {ent} has already been recorded.")
            d.save()
            done.append({"kind": d.kind, "entity": ent, "review_number": d.review_number, "status": d.status})
    recompute(source)
    return done


def record_event(source: AuditSource, *, review_key: str, event_id: str, type: str, payload: dict, user, by: str):
    """An action or comment on a review. Saving the same event id twice keeps the first."""
    if type not in TEAM_EVENT_TYPES:
        raise Refused(f"Unknown event type {type}.")
    if not event_id or len(event_id) > 60:
        raise Refused("An event needs an id.")
    current = world(source)
    if review_key not in {r.get("key") for r in current.get("reviews") or []}:
        raise Refused("No such review.")
    clean = {k: v for k, v in (payload or {}).items() if k not in ("by", "at", "type")}
    if len(json.dumps(clean, default=str)) > 20_000:
        raise Refused("That is too long to save.")
    _, made = TeamEvent.objects.get_or_create(
        source=source,
        event_id=event_id,
        defaults={
            "review_key": review_key,
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
