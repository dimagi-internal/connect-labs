"""The public surface of `connect_labs.workers` for other labs apps (messaging, its canopy bridge).

Use these rather than reaching into the models or the sync:

* `get_or_sync_worker(username)` -- the ConnectWorker, created and queued for sync if new.
* `worker_brief_data(worker, program_id)` -- agent-safe, program-scoped profile data.
* `stage_for(worker, opportunity_id)`, `facets_for(worker, opportunity_id)`,
  `states_for(worker, program_id=None)` -- derived lifecycle state.
* `timeline(worker, ...)` -- events as dicts, normal visibility unless asked.
* `record_event(worker, kind, ...)` -- write a timeline line idempotently (e.g. message_in/out).
* `ocs_sessions_for(worker)` -- OCS coaching session refs (for the transcript import).
* `profile_for(user, worker)` -- the profile a staff member may see (the page's and worker_get's).
"""

from __future__ import annotations

import logging

from . import events as ev
from .access import view_for
from .events import record_event
from .models import ConnectWorker, WorkerState
from .ocs_link import ocs_sessions_for
from .profile import build_profile, event_dict, timeline_queryset, worker_brief_data

logger = logging.getLogger(__name__)

__all__ = [
    "get_or_sync_worker",
    "worker_brief_data",
    "stage_for",
    "facets_for",
    "states_for",
    "timeline",
    "record_event",
    "ocs_sessions_for",
    "profile_for",
    "EVENT_KINDS",
]

EVENT_KINDS = tuple(ev.KIND_LABELS)


def get_or_sync_worker(username: str, *, sync: str | bool = "async") -> ConnectWorker | None:
    """The ConnectWorker for a PersonalID username.

    A worker labs already holds is returned as is (the beat keeps it current). A new
    username is created at once and, with ``sync="async"`` (default), a discovery sync
    is queued; ``sync="inline"`` runs it now (one request per readable opportunity --
    not for a request path); ``sync=False`` returns None for an unknown username.
    """
    username = (username or "").strip()
    if not username:
        return None
    worker = ConnectWorker.objects.filter(username=username).first()
    if worker is not None:
        return worker
    if not sync:
        return None
    worker, _ = ConnectWorker.objects.get_or_create(username=username)
    if sync == "inline":
        from .sync import sync_worker

        sync_worker(username, discover=True)
        worker.refresh_from_db()
    else:
        from .tasks import sync_worker_task

        try:
            sync_worker_task.delay(username, discover=True)
        except Exception:  # noqa: BLE001 -- the record exists; the beat will fill it
            logger.warning("[workers] could not queue a sync for %s", username, exc_info=True)
    return worker


def states_for(worker: ConnectWorker, program_id: int | None = None) -> list[dict]:
    qs = WorkerState.objects.filter(membership__worker=worker).select_related("membership")
    if program_id is not None:
        qs = qs.filter(membership__program_id=program_id)
    return [
        {
            "opportunity_id": s.membership.opportunity_id,
            "program_id": s.membership.program_id,
            "stage": s.stage,
            "facets": s.facets,
            "computed_at": s.computed_at.isoformat(),
        }
        for s in qs.order_by("membership__opportunity_id")
    ]


def _state(worker, opportunity_id):
    return WorkerState.objects.filter(membership__worker=worker, membership__opportunity_id=opportunity_id).first()


def stage_for(worker: ConnectWorker, opportunity_id: int) -> str | None:
    state = _state(worker, opportunity_id)
    return state.stage if state else None


def facets_for(worker: ConnectWorker, opportunity_id: int) -> dict:
    state = _state(worker, opportunity_id)
    return dict(state.facets) if state else {}


def timeline(
    worker: ConnectWorker,
    *,
    program_id: int | None = None,
    opportunity_ids=None,
    kinds=None,
    since=None,
    limit: int = 100,
    include_restricted: bool = False,
) -> list[dict]:
    """Events, newest first. Scoped to ``opportunity_ids`` or else ``program_id``'s opportunities."""
    from .models import WorkerMembership

    if opportunity_ids is None:
        qs = WorkerMembership.objects.filter(worker=worker)
        if program_id is not None:
            qs = qs.filter(program_id=program_id)
        opportunity_ids = list(qs.values_list("opportunity_id", flat=True))
    programs = [program_id] if program_id is not None else []
    events = timeline_queryset(
        worker,
        opportunity_ids=opportunity_ids,
        program_ids=programs,
        restricted=include_restricted,
        kinds=kinds,
        since=since,
    )
    return [event_dict(e) for e in events[:limit]]


def profile_for(
    user, worker: ConnectWorker, *, request=None, access_token=None, on_page=False, **kwargs
) -> dict | None:
    """The profile ``user`` may see, or None if they may access none of the worker's opportunities."""
    view = view_for(user, worker, request=request, access_token=access_token, on_page=on_page)
    if not view.any:
        return None
    return build_profile(worker, view, **kwargs)
