"""Who may see what of a Connect Worker.

A person may open a worker's profile only if they may access at least one of the
opportunities the worker is on, and then they see only those opportunities'
memberships, events, coverage and program-scoped facts and notes. Global facts and
notes (no program) go with any access.

"May access" is Connect's own answer, asked as that person: what labs already
knows they hold (`labs.access.scopes.holdings`, from their opp_org_program_list),
and for the worker's other opportunities, Connect's `opportunity_access` check with
their own token, which also answers yes for supervising-organisation and funder
access that the listing leaves out (docs/connect-asks.md #7). Only a yes from
Connect grants; anything else denies.

Restricted content (suspension reasons, restricted events) is for staff with access
to that opportunity, on the page. It never goes into an MCP result or an agent's brief.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from django.conf import settings
from django.core.cache import cache

from connect_labs.labs.access.scopes import Caller, ScopesUnavailable, holdings

logger = logging.getLogger(__name__)

ACCESS_CACHE_SECONDS = 300
PROBE_BATCH = 500


@dataclass(frozen=True)
class WorkerView:
    """What one person may see of one worker."""

    opportunity_ids: frozenset[int]
    program_ids: frozenset[int]
    # On the page only (a person reading); never set for an MCP or agent read.
    restricted: bool = False
    private_notes: bool = False
    unknowable: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def any(self) -> bool:
        return bool(self.opportunity_ids)


def _token(user, request=None) -> str | None:
    from connect_labs.labs.connect_tokens import get_valid_access_token

    try:
        return get_valid_access_token(user)
    except Exception:  # noqa: BLE001 -- no stored token: fall back to the session's
        pass
    if request is not None and hasattr(request, "session"):
        return ((request.session.get("labs_oauth") or {}) or {}).get("access_token")
    return None


def _probe(token: str, ids: list[int]) -> set[int]:
    """Connect's own yes, per id, for this token (opportunity_access)."""
    granted: set[int] = set()
    base = settings.CONNECT_PRODUCTION_URL.rstrip("/")
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json; version=2.0"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        for start in range(0, len(ids), PROBE_BATCH):
            response = client.post(
                f"{base}/export/opportunity_access/", json={"opportunity_ids": ids[start : start + PROBE_BATCH]}
            )
            response.raise_for_status()
            for opp_id, ok in (response.json().get("opportunities") or {}).items():
                if ok:
                    granted.add(int(opp_id))
    return granted


def accessible_opportunity_ids(user, candidate_ids, *, request=None, access_token=None) -> tuple[set[int], bool]:
    """Which of ``candidate_ids`` this person may access, and whether that could be established at all."""
    candidates = {int(i) for i in candidate_ids if i is not None}
    if not candidates or user is None or not getattr(user, "is_authenticated", False):
        return set(), True
    cache_key = "workers:access:{}:{}".format(user.pk, ",".join(map(str, sorted(candidates))))
    hit = cache.get(cache_key)
    if hit is not None:
        return set(hit), True

    known = True
    try:
        held = set(holdings(Caller(user=user, request=request, access_token=access_token)).opportunity_ids)
    except ScopesUnavailable:
        held, known = set(), False
    allowed = candidates & held
    rest = sorted(candidates - allowed)
    answered = not rest
    if rest:
        token = access_token or _token(user, request)
        if token:
            try:
                allowed |= _probe(token, rest)
                known = answered = True  # Connect answered for every remaining id
            except Exception as exc:  # noqa: BLE001 -- an unreachable authority grants nothing
                logger.info("[workers] opportunity_access probe failed for %s: %s", user.pk, exc)
    # A denial nobody confirmed is not cached: the next read asks again.
    if known and answered:
        cache.set(cache_key, sorted(allowed), ACCESS_CACHE_SECONDS)
    return allowed, known


def view_for(user, worker, *, request=None, access_token=None, on_page: bool = False) -> WorkerView:
    """What ``user`` may see of ``worker``. ``on_page``: a person reading the page (restricted allowed)."""
    from .models import WorkerMembership

    memberships = list(WorkerMembership.objects.filter(worker=worker).values_list("opportunity_id", "program_id"))
    allowed, known = accessible_opportunity_ids(
        user, [o for o, _ in memberships], request=request, access_token=access_token
    )
    programs = {p for o, p in memberships if o in allowed and p is not None}
    notes = () if known else ("Could not ask Connect what you may access; showing nothing rather than guessing.",)
    return WorkerView(
        opportunity_ids=frozenset(allowed),
        program_ids=frozenset(programs),
        restricted=on_page and bool(allowed),
        private_notes=on_page and bool(allowed),
        unknowable=not known,
        notes=notes,
    )
