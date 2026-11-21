"""`ExportFacade`: a `ConnectWorkerSource` over Connect's per-opportunity export API.

Connect's export API was built for bulk per-opportunity reports, not for one
person's history. Every gap it has is worked around HERE and only here, each at the
point it bites, with the ask that would remove it (docs/connect-asks.md, by number):

* No cross-opportunity worker export (ask #1). `worker_opportunities` reads
  `user_data` for every opportunity the identity can see and keeps the worker's rows.
* No `?username=` filter except on `completed_module` (ask #1). Every other kind is
  pulled whole per opportunity and filtered in labs. Pulls are memoised per facade
  instance, so syncing many workers on one opportunity downloads each export once.
* No `?modified_since=` (ask #2). `since` is emulated by filtering a full pull on the
  row's newest timestamp; the sync turns successive full pulls into events by diffing
  snapshots.
* No ids on `completed_works`, `payment`, `assessment`, `completed_module` or
  `user_data` (ask #6). Rows get stable synthetic keys from fields that never change
  over the row's life (`shapes.synthetic_key`). A payment Connect deletes simply
  stops appearing: the sync notices the key is gone.
* `user_visits` carries ~25 KB of `form_json` per row and cannot exclude it (ask #2).
  Visits are read from labs' own caches first (the analysis raw-visit cache, then
  Pulse's tail); only when asked (`visit_fetch=True`) is the export streamed, and each
  page is projected to the thin shape before the next is fetched.
* `assigned_tasks` lacks the task's mode (ask #3): it is joined from `task_types`.
* No change history (ask #4): `events()` returns nothing, with coverage saying so.
* Connect records no notifications it sends (ask #5): `notifications()` likewise.
* `opp_org_program_list` omits supervising-org and funder opportunities (ask #7):
  `accessible_opportunities(probe_ids=...)` asks `opportunity_access` about others.
* No learn-module list endpoint (ask #8): module names come from the single-
  opportunity export's `learn_app.learn_modules`.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from connect_labs.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

from . import shapes
from .base import (
    ACCESSIBLE_OPPORTUNITIES,
    ASSIGNED_TASKS,
    COMPLETED_MODULE,
    ENTITY_KINDS,
    TASK_TYPES,
    USER_DATA,
    VISITS_THIN,
    AccessibleOpportunities,
    Capabilities,
    ConnectWorkerSource,
    Coverage,
    OpportunityRef,
    Result,
)

logger = logging.getLogger(__name__)

SOURCE = "connect-export"
# Connect's cap on one opportunity_access request (data_export/const.py).
ACCESS_CHECK_BATCH = 500

_ENDPOINTS = {
    USER_DATA: "user_data",
    COMPLETED_MODULE: "completed_module",
    "assessment": "assessment",
    "completed_works": "completed_works",
    "payment": "payment",
    ASSIGNED_TASKS: "assigned_tasks",
    TASK_TYPES: "task_types",
    "audit_report_entries": "audit_report_entries",
    "work_areas": "work_areas",
}

# Kinds whose rows carry no Connect id, so their keys are synthetic (ask #6).
_SYNTHETIC_KEYS = {"user_data", "completed_module", "assessment", "completed_works", "payment"}

NO_EVENTS_NOTE = (
    "Connect exports no history of changes (suspensions, task status, review outcomes, payment deletions), "
    "so labs writes these events itself by comparing successive pulls; a change undone between two pulls is not seen."
)
NO_NOTIFICATIONS_NOTE = (
    "Connect does not record the messages it sends a worker (invite SMS, pushes, payment and task notices, "
    "inactivity nudges), so they are not on this profile."
)


class ExportFacade(ConnectWorkerSource):
    name = "export-facade"
    # Server-side filtering the export API really offers: completed_module takes ?username=.
    capabilities = Capabilities(username_filter=frozenset({COMPLETED_MODULE}))

    def __init__(self, client: ExportAPIClient, *, visit_fetch: bool = False):
        self.client = client
        self.visit_fetch = visit_fetch
        self._pulls: dict[tuple, list[dict]] = {}
        self._details: dict[int, dict | None] = {}

    # ------------------------------------------------------------------ plumbing

    def _get_json(self, path: str) -> dict:
        response = self.client.http_client.get(f"{self.client.base_url}{path}")
        response.raise_for_status()
        return response.json()

    def _post_json(self, path: str, body: dict) -> dict:
        response = self.client.http_client.post(f"{self.client.base_url}{path}", json=body)
        response.raise_for_status()
        return response.json()

    def _pull(self, opp_id: int, kind: str, params: dict | None = None) -> list[dict]:
        """One export, whole, once per facade (ask #1/#2: no per-user filter, no since)."""
        memo_key = (opp_id, kind, tuple(sorted((params or {}).items())))
        if memo_key not in self._pulls:
            self._pulls[memo_key] = self.client.fetch_all(
                f"/export/opportunity/{opp_id}/{_ENDPOINTS[kind]}/", params=params
            )
        return self._pulls[memo_key]

    def opportunity_detail(self, opp_id: int) -> dict | None:
        """The single-opportunity export (name, org, dates, learn modules), or None if unreadable."""
        if opp_id not in self._details:
            try:
                self._details[opp_id] = self._get_json(f"/export/opportunity/{opp_id}/")
            except Exception as exc:  # noqa: BLE001 -- unreadable is an answer here, reported by callers
                logger.info("[workers] opportunity %s detail unreadable: %s", opp_id, exc)
                self._details[opp_id] = None
        return self._details[opp_id]

    # ------------------------------------------------------------------ opportunities

    def accessible_opportunities(self, *, probe_ids=None) -> AccessibleOpportunities:
        payload = self._get_json("/export/opp_org_program_list/")
        programs = {p["id"]: p for p in payload.get("programs") or [] if p.get("id") is not None}
        orgs = {o["slug"]: o for o in payload.get("organizations") or [] if o.get("slug")}
        refs: list[OpportunityRef] = []
        for row in payload.get("opportunities") or []:
            if row.get("id") is None:
                continue
            refs.append(
                OpportunityRef(
                    id=int(row["id"]),
                    name=row.get("name") or "",
                    program_id=row.get("program"),
                    organization_slug=row.get("organization") or "",
                    is_active=bool(row.get("is_active")),
                    end_date=row.get("end_date"),
                    is_test=bool(row.get("is_test")),
                    visit_count=row.get("visit_count"),
                    via="listed",
                )
            )
        listed = {r.id for r in refs}
        notes = [
            "Connect lists only the sync identity's own organisations' and programmes' opportunities; "
            "supervising-organisation and funder opportunities are found only by asking about them one id at a time."
        ]
        # ask #7: opp_org_program_list ignores supervising/funder access, so probe ids labs already knows of.
        to_probe = sorted({int(i) for i in (probe_ids or ())} - listed)
        probe_failed = False
        for start in range(0, len(to_probe), ACCESS_CHECK_BATCH):
            batch = to_probe[start : start + ACCESS_CHECK_BATCH]
            try:
                answer = self._post_json("/export/opportunity_access/", {"opportunity_ids": batch})
            except Exception as exc:  # noqa: BLE001 -- the listing still stands without the probe
                logger.warning("[workers] opportunity_access probe failed: %s", exc)
                probe_failed = True
                continue
            for opp_id, granted in (answer.get("opportunities") or {}).items():
                if not granted:
                    continue
                detail = self.opportunity_detail(int(opp_id)) or {}
                refs.append(
                    OpportunityRef(
                        id=int(opp_id),
                        name=detail.get("name") or "",
                        # The single-opportunity export carries no programme (ask #7).
                        program_id=None,
                        organization_slug=detail.get("organization") or "",
                        is_active=bool(detail.get("is_active")),
                        end_date=detail.get("end_date"),
                        via="probed",
                    )
                )
        if probe_failed:
            notes.append("Asking Connect about further opportunities failed; only the listed ones are covered.")
        return AccessibleOpportunities(
            opportunities=refs,
            coverage=Coverage(
                complete=False,
                exact=True,
                method=f"opp_org_program_list ({len(listed)}) + opportunity_access probe of {len(to_probe)} ids",
                source=SOURCE,
                notes=tuple(notes),
                as_of=timezone.now(),
            ),
            programs=programs,
            organizations=orgs,
        )

    # ------------------------------------------------------------------ workers

    def opportunity_workers(self, opp_id: int) -> Result:
        return self.entities(opp_id, USER_DATA)

    def worker_opportunities(self, username: str, *, opportunity_ids=None) -> Result:
        # ask #1: no per-worker export, so read user_data for every opportunity and keep this worker's rows.
        if opportunity_ids is None:
            opportunity_ids = [r.id for r in self.accessible_opportunities().opportunities]
        rows: list[dict] = []
        failed: list[int] = []
        for opp_id in opportunity_ids:
            try:
                rows.extend(self.entities(opp_id, USER_DATA, username=username).rows)
            except ExportAPIError as exc:
                logger.info("[workers] user_data unreadable for opp %s: %s", opp_id, exc)
                failed.append(opp_id)
        notes = [f"Searched {len(opportunity_ids)} opportunities one by one (Connect has no per-worker export)."]
        if failed:
            notes.append(f"Could not read {len(failed)} of them: {', '.join(map(str, failed[:20]))}.")
        return Result(
            rows,
            Coverage(
                complete=not failed,
                exact=True,
                method="user_data per opportunity, filtered in labs",
                source=SOURCE,
                notes=tuple(notes),
                as_of=timezone.now(),
            ),
        )

    # ------------------------------------------------------------------ entities

    def entities(self, opp_id: int, kind: str, *, username=None, since=None) -> Result:
        if kind not in ENTITY_KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        if kind == VISITS_THIN:
            return self._visits_thin(opp_id, username=username, since=since)

        notes: list[str] = []
        if kind == COMPLETED_MODULE and username:
            # The one export that filters by worker on the server.
            raw = self._pull(opp_id, kind, {"username": username})
        else:
            raw = self._pull(opp_id, kind)
            if username:
                notes.append("Pulled for the whole opportunity and filtered in labs (no per-worker filter).")

        if kind == ASSIGNED_TASKS:
            types = {t.get("id"): t for t in self._pull(opp_id, TASK_TYPES)}
            rows = [shapes.assigned_task(r, opp_id, types) for r in raw]
        else:
            rows = [shapes.NORMALIZERS[kind](r, opp_id) for r in raw]

        if username:
            rows = [r for r in rows if r.get("username") == username]
        if since is not None:
            notes.append("Connect cannot answer 'changed since'; a full pull was filtered on the rows' own dates.")
            rows = [r for r in rows if (shapes.changed_at(kind, r) or since) >= since]
        if kind in _SYNTHETIC_KEYS:
            notes.append("Connect gives these rows no id; labs keys them on fields that never change.")
        if kind == "payment":
            notes.append("A payment Connect deletes disappears from the export without a trace; labs notices the gap.")
        return Result(
            rows,
            Coverage(
                complete=True,
                exact=True,
                method=f"{_ENDPOINTS[kind]} export, whole opportunity",
                source=SOURCE,
                notes=tuple(notes),
                as_of=timezone.now(),
            ),
        )

    def _visits_thin(self, opp_id: int, *, username=None, since=None) -> Result:
        """Visits without form_json, from labs' caches before Connect (ask #2: no exclude=form_json)."""
        now = timezone.now()
        pulse_days = _pulse_retention_days()
        # A recent `since` is exactly what Pulse's tail holds, fresh to the minute.
        if since is not None and pulse_days and since >= now - timedelta(days=pulse_days):
            rows = _pulse_visits(opp_id, username=username, since=since)
            if rows is not None:
                return Result(
                    rows,
                    Coverage(
                        complete=True,
                        exact=True,
                        method="Pulse visit tail",
                        source="labs-pulse",
                        notes=("Status changes to visits older than the window arrive with cache refreshes.",),
                        as_of=now,
                    ),
                )

        cached = _cached_visits(opp_id, username=username)
        if cached is not None:
            rows, filled_at = cached
            if since is not None:
                rows = [r for r in rows if (shapes.changed_at(VISITS_THIN, r) or since) >= since]
            return Result(
                rows,
                Coverage(
                    complete=True,
                    exact=True,
                    method="labs visit cache",
                    source="labs-visit-cache",
                    notes=("Statuses are as Connect reported them when the cache was filled.",),
                    as_of=filled_at,
                ),
            )

        if self.visit_fetch:
            return self._stream_visits(opp_id, username=username, since=since)

        rows = _pulse_visits(opp_id, username=username, since=since) or []
        return Result(
            rows,
            Coverage(
                complete=False,
                exact=True,
                method="Pulse visit tail" if rows else "none",
                source="labs-pulse",
                notes=(
                    f"Labs holds no full visit cache for this opportunity; only visits from the last {pulse_days} "
                    "days are shown. Opening a workflow on the opportunity fills the cache.",
                ),
                as_of=now,
            ),
        )

    def _stream_visits(self, opp_id: int, *, username=None, since=None) -> Result:
        rows: list[dict] = []
        for page in self.client.paginate(f"/export/opportunity/{opp_id}/user_visits/"):
            # Project at once: never hold a page of form_json longer than this loop.
            for raw in page:
                if username and raw.get("username") != username:
                    continue
                thin = shapes.visit_thin(raw, opp_id)
                if since is not None and (shapes.changed_at(VISITS_THIN, thin) or since) < since:
                    continue
                rows.append(thin)
        return Result(
            rows,
            Coverage(
                complete=True,
                exact=True,
                method="user_visits export, projected to thin rows as it streamed",
                source=SOURCE,
                notes=("Connect cannot leave out each visit's form, so the whole export was downloaded.",),
                as_of=timezone.now(),
            ),
        )

    # ------------------------------------------------------------------ what Connect does not have

    def events(self, opp_id, *, since, username=None) -> Result:
        return Result(
            [],
            Coverage(complete=False, exact=False, method="none", source=SOURCE, notes=(NO_EVENTS_NOTE,)),
        )

    def notifications(self, username, *, since) -> Result:
        return Result(
            [],
            Coverage(complete=False, exact=False, method="none", source=SOURCE, notes=(NO_NOTIFICATIONS_NOTE,)),
        )

    def learn_modules(self, opp_id: int) -> Result:
        # ask #8: no module list endpoint; the single-opportunity export carries learn_app.learn_modules.
        detail = self.opportunity_detail(opp_id)
        modules = ((detail or {}).get("learn_app") or {}).get("learn_modules") or []
        rows = [
            {
                "key": str(m.get("id") if m.get("id") is not None else m.get("slug")),
                "id": m.get("id"),
                "slug": m.get("slug") or "",
                "name": m.get("name") or "",
                "time_estimate": m.get("time_estimate"),
                "opportunity_id": opp_id,
            }
            for m in modules
        ]
        return Result(
            rows,
            Coverage(
                complete=detail is not None,
                exact=True,
                method="single-opportunity export (learn_app.learn_modules)",
                source=SOURCE,
                notes=() if detail is not None else ("The opportunity could not be read.",),
                as_of=timezone.now(),
            ),
        )


# ---------------------------------------------------------------------- labs' own visit caches


def _pulse_retention_days() -> int:
    from django.conf import settings

    return int(getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30) or 0)


def _pulse_visits(opp_id: int, *, username=None, since=None) -> list[dict] | None:
    """Thin visits from Pulse's tail (PulseEvent keys a worker by username), or None if it holds none."""
    from connect_labs.pulse.models import PulseEvent

    qs = PulseEvent.objects.filter(opportunity_id=opp_id)
    if not qs.exists():
        return None
    if username:
        qs = qs.filter(worker_hash=username[:64])
    if since is not None:
        qs = qs.filter(sync_ts__gte=since)
    out = []
    for e in qs.order_by("sync_ts").values(
        "connect_visit_id", "worker_hash", "field_ts", "sync_ts", "status", "flagged", "flag_type", "review_status"
    ):
        out.append(
            {
                "key": str(e["connect_visit_id"]),
                "visit_id": str(e["connect_visit_id"]),
                "opportunity_id": opp_id,
                "username": e["worker_hash"],
                "visit_date": e["field_ts"].isoformat() if e["field_ts"] else None,
                "date_created": e["sync_ts"].isoformat() if e["sync_ts"] else None,
                "status": e["status"],
                "status_modified_date": None,
                "flagged": e["flagged"],
                "flag_type": e["flag_type"],
                "review_status": e["review_status"],
                "review_created_on": None,
                "deliver_unit": "",
                "deliver_unit_id": None,
                "completed_work_id": None,
                "entity_key": "",
            }
        )
    return out


def _cached_visits(opp_id: int, *, username=None) -> tuple[list[dict], object] | None:
    """Thin visits from the analysis raw-visit cache (the shared user_visits slot), or None if cold."""
    from django.db.models import Min

    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    qs = SQLCacheManager(opp_id).get_raw_visits_queryset()
    filled_at = qs.aggregate(at=Min("created_at"))["at"]
    if filled_at is None:
        return None
    if username:
        qs = qs.filter(username=username)
    rows = []
    for v in qs.values(
        "visit_id",
        "username",
        "visit_date",
        "date_created",
        "status",
        "status_modified_date",
        "flagged",
        "flag_reason",
        "review_status",
        "review_created_on",
        "deliver_unit",
        "deliver_unit_id",
        "completed_work_id",
        "entity_id",
    ):
        rows.append(shapes.visit_thin({**v, "id": v["visit_id"]}, opp_id))
    return rows, filled_at


__all__ = ["ExportFacade", "ACCESSIBLE_OPPORTUNITIES"]
