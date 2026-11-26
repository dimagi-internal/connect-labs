"""Connect Web's service layer: the one door every page (and the form engine) goes through.

Labs' backend acts as the pool worker's phone. A labs user checks a pool worker out
(`check_out`), and from then on every call is made as that worker: `ensure_signed_in` signs in
through PersonalID and keeps the access token server-side; the Connect calls
(`list_opportunities`, `opportunity_detail`, `start_learning`, `claim`, `confirm_payments`) carry it;
`hq_token` trades it for a CommCare HQ token per domain. The browser never sees any of them.

**The sign-in is the worker's, not the checkout's.** The PersonalID password, access token and HQ
tokens live (encrypted) on `PoolWorker`, so they survive a release: the next checkout signs in with
the saved password, and a full recovery — which rotates the password and spends a backup-code
attempt — runs only when PersonalID refuses that password.

Sign-in, in order (cheapest first, never more than needed):

1. a cached PersonalID access token that has more than `TOKEN_SKEW` left;
2. the password grant with the PersonalID password saved on the worker;
3. only if that fails with ``invalid_grant`` (or there is no password yet): full recovery —
   ``start_configuration`` → ``complete_recovery`` (rotates the password) → password grant.

Only one request signs a worker in at a time (`_sign_in_lock`, a short cache lock), and **no
database transaction is held across a network call**: what a sign-in learns is written in short
updates as it goes. A wrong backup code is never retried: the pool worker is blocked from sign-in
(`PoolWorker.sign_in_blocked_reason`) until an admin saves a new code, because connect-id locks
the account after a few wrong attempts.

Reads of the worker's Connect data go through `cache.py` (see there for keys, TTLs and what each
action invalidates).
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid as uuid_lib
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, TypeVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import cache as lcache
from . import hq, personalid
from .connect_api import ConnectClient
from .errors import (
    FAILED_USER_CREATE,
    INCORRECT_BACKUP_CODE,
    LEARNING_INCOMPLETE,
    SIGN_IN_BUSY,
    ConnectAPIError,
    ConnectWebError,
    GuardRefused,
    HQError,
    HQUserNotCreated,
    IncorrectBackupCode,
    PersonalIDError,
    WorkerUnavailable,
)
from .models import FormSession, PoolWorker, WorkerSession

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: A cached token is used only while it has more than this left.
TOKEN_SKEW = timedelta(seconds=60)
#: When a token endpoint omits expires_in.
DEFAULT_TOKEN_SECONDS = 600
#: How often a session's last-used time is written (it decides idle takeover).
TOUCH_EVERY = timedelta(minutes=1)

#: The sign-in lock outlives one sign-in's worst case (three calls, each within http's budget).
SIGN_IN_LOCK_SECONDS = 60
#: How long a request waits for another's sign-in of the same worker before giving up.
SIGN_IN_WAIT_SECONDS = 30
_SIGN_IN_POLL = 0.2

APP_KINDS = ("learn", "deliver")

_SIGN_IN_FIELDS = ("personalid_password", "access_token", "access_token_expires_at", "hq_tokens")

#: Concurrent Connect reads (learn and delivery progress). Threads only make HTTP calls.
_FETCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="connect-web-fetch")


# ---------------------------------------------------------------------------
# Checking a pool worker out
# ---------------------------------------------------------------------------


def current_session(user) -> WorkerSession | None:
    """The user's open session, if any."""
    if not getattr(user, "is_authenticated", False):
        return None
    return WorkerSession.objects.filter(user=user, ended_at__isnull=True).select_related("pool_worker").first()


def _idle_takeover_after() -> timedelta:
    return timedelta(minutes=settings.CONNECT_WEB_IDLE_TAKEOVER_MINUTES)


def can_take_over(session: WorkerSession) -> bool:
    """Someone else's session may be taken over once it has been idle this long."""
    return timezone.now() - session.last_used_at >= _idle_takeover_after()


def check_out(user, pool_worker: PoolWorker, *, take_over: bool = False) -> WorkerSession:
    """Start acting as `pool_worker`. Ends the user's other open session, if any.

    Refuses a worker someone else has checked out unless `take_over` and their session is idle
    (`can_take_over`). Checking out the worker you already hold returns your session.
    """
    if not pool_worker.active:
        raise WorkerUnavailable(f"{pool_worker.display_name} is not available (inactive).")
    with transaction.atomic():
        PoolWorker.objects.select_for_update().get(pk=pool_worker.pk)
        held = pool_worker.sessions.filter(ended_at__isnull=True).select_related("user").first()
        if held and held.user_id == user.pk:
            return held
        if held:
            if not (take_over and can_take_over(held)):
                raise WorkerUnavailable(f"{pool_worker.display_name} is checked out by {display_user(held.user)}.")
            _end(held)
        for mine in WorkerSession.objects.select_for_update().filter(user=user, ended_at__isnull=True):
            _end(mine)
        try:
            with transaction.atomic():
                return WorkerSession.objects.create(user=user, pool_worker=pool_worker)
        except IntegrityError:
            raise WorkerUnavailable(f"{pool_worker.display_name} was just checked out by someone else.") from None


def release(session: WorkerSession) -> None:
    """End the session. The worker's sign-in stays on the worker for the next checkout."""
    _end(session)


def _end(session: WorkerSession) -> None:
    session.ended_at = timezone.now()
    session.save(update_fields=["ended_at"])
    FormSession.objects.filter(worker_session=session).delete()


def display_user(user) -> str:
    return (getattr(user, "name", "") or user.email or user.username) if user else ""


def touch(session: WorkerSession) -> None:
    """Record that the session is in use (at most once a minute): it decides idle takeover."""
    now = timezone.now()
    if now - session.last_used_at > TOUCH_EVERY:
        session.last_used_at = now
        WorkerSession.objects.filter(pk=session.pk).update(last_used_at=now)


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


def _fresh(expires_at) -> bool:
    return bool(expires_at) and expires_at - TOKEN_SKEW > timezone.now()


def _expiry(expires_in: int):
    return timezone.now() + timedelta(seconds=expires_in or DEFAULT_TOKEN_SECONDS)


def _has_fresh_token(worker: PoolWorker) -> bool:
    return bool(worker.access_token) and _fresh(worker.access_token_expires_at)


def _reload(worker: PoolWorker) -> None:
    """Re-read what another request may have changed: the sign-in, the username, a block."""
    worker.refresh_from_db(fields=[*_SIGN_IN_FIELDS, "personalid_username", "sign_in_blocked_reason"])


def _save(worker: PoolWorker, *fields: str) -> None:
    """Write `fields` of the worker now, in one short statement (no transaction left open)."""
    PoolWorker.objects.filter(pk=worker.pk).update(
        **{name: getattr(worker, name) for name in fields}, updated_at=timezone.now()
    )


@contextlib.contextmanager
def _sign_in_lock(worker_pk: int):
    """Single flight for one worker's sign-in, without a database transaction.

    Yields True (this request holds the lock), False (another request held it for the whole
    wait) or None (the cache is down: the caller falls back to a row lock).
    """
    key = f"{lcache.PREFIX}:signin:{worker_pk}"
    owner = uuid_lib.uuid4().hex
    deadline = time.monotonic() + SIGN_IN_WAIT_SECONDS
    while True:
        got = lcache.add(key, owner, SIGN_IN_LOCK_SECONDS)
        if got is not False or time.monotonic() >= deadline:
            break
        time.sleep(_SIGN_IN_POLL)
    try:
        yield got
    finally:
        if got:
            lcache.release(key, owner)


def ensure_signed_in(session: WorkerSession) -> str:
    """A live PersonalID access token for the session's worker (cached, password grant, or recovery).

    Whatever a failed sign-in learned — a rotated password, the username, a pause after a rejected
    backup code — is saved before the error is raised. Labs runs with ``ATOMIC_REQUESTS``, so
    callers must be ``non_atomic_requests`` views (connect_web's are, see urls.py) or those writes
    roll back with the request.
    """
    if not session.is_open:
        raise WorkerUnavailable("This session has ended. Pick a worker again.")
    worker = session.pool_worker
    if _has_fresh_token(worker):
        touch(session)
        return worker.access_token

    # Fail on configuration before anything rotates the worker's password.
    personalid.client_id()
    with _sign_in_lock(worker.pk) as held:
        _reload(worker)
        if _has_fresh_token(worker):  # another request signed in while this one waited
            touch(session)
            return worker.access_token
        if not WorkerSession.objects.filter(pk=session.pk, ended_at__isnull=True).exists():
            raise WorkerUnavailable("This session has ended. Pick a worker again.")
        if held is False:
            raise PersonalIDError(error_code=SIGN_IN_BUSY)
        if held:
            failure = _sign_in(worker)
        else:
            # The cache is down: fall back to the worker's row lock for the whole sign-in. Slower
            # (a transaction stays open across the calls) but never two recoveries at once.
            with transaction.atomic():
                PoolWorker.objects.select_for_update().get(pk=worker.pk)
                _reload(worker)
                failure = None if _has_fresh_token(worker) else _sign_in(worker)
    if failure is not None:
        raise failure
    touch(session)
    return worker.access_token


def _sign_in(worker: PoolWorker) -> ConnectWebError | None:
    """Sign in and save the result on `worker`; on failure, save what was learned and return the error."""
    try:
        token = None
        if worker.personalid_password and worker.personalid_username:
            try:
                token = personalid.password_grant(worker.personalid_username, worker.personalid_password)
            except PersonalIDError as exc:
                if exc.error_code != "invalid_grant":
                    raise
                logger.info("connect_web: password grant refused for pool worker %s; recovering", worker.pk)
        if token is None:
            token = _recover(worker)
    except ConnectWebError as exc:
        if isinstance(exc, IncorrectBackupCode) or exc.error_code == "LOCKED_ACCOUNT":
            reason = "PersonalID rejected the backup code" if isinstance(exc, IncorrectBackupCode) else "Locked"
            worker.sign_in_blocked_reason = reason
            _save(worker, "sign_in_blocked_reason")
            logger.warning("connect_web: sign-in paused for pool worker %s (%s)", worker.pk, exc.error_code)
        return exc
    worker.access_token = token.access_token
    worker.access_token_expires_at = _expiry(token.expires_in)
    _save(worker, "access_token", "access_token_expires_at")
    return None


def _recover(worker: PoolWorker) -> personalid.AccessToken:
    if worker.sign_in_blocked_reason:
        raise WorkerUnavailable(
            f"Sign-in is paused for {worker.display_name}: {worker.sign_in_blocked_reason}. "
            "An admin must save a new backup code for them.",
            error_code=INCORRECT_BACKUP_CODE,
        )
    config = personalid.start_configuration(worker.phone_number, str(worker.device_id))
    creds = personalid.complete_recovery(config.token, worker.backup_code)
    if worker.personalid_username and worker.personalid_username != creds.username:
        logger.warning("connect_web: pool worker %s's PersonalID username changed at recovery", worker.pk)
    worker.personalid_username = creds.username
    worker.personalid_password = creds.password
    # Saved at once: the recovery rotated the password, so it must survive a failed grant below.
    _save(worker, "personalid_username", "personalid_password")
    return personalid.password_grant(creds.username, creds.password)


def _drop_access_token(session: WorkerSession, refused: str) -> None:
    """Forget the access token a server refused — unless another request already replaced it."""
    worker = session.pool_worker
    with transaction.atomic():
        locked = PoolWorker.objects.select_for_update().only("pk", "access_token").get(pk=worker.pk)
        if locked.access_token == refused:
            PoolWorker.objects.filter(pk=worker.pk).update(access_token="", access_token_expires_at=None)
    if worker.access_token == refused:
        worker.access_token = ""
        worker.access_token_expires_at = None


def _as_worker(session: WorkerSession, call: Callable[[ConnectClient], T]) -> T:
    """Run a Connect call as the worker; on a 401, drop the cached token, sign in again, retry once."""
    token = ensure_signed_in(session)
    try:
        return call(ConnectClient(token))
    except ConnectAPIError as exc:
        if exc.status != 401:
            raise
    _drop_access_token(session, token)
    return call(ConnectClient(ensure_signed_in(session)))


def _fetch_together(session: WorkerSession, jobs: dict[str, tuple[int, Callable[[ConnectClient], Any]]], bypass):
    """Several Connect reads at once, through the cache: ``{name: (ttl, call)}`` → ``{name: value}``.

    What the memo or cache holds is used (unless `bypass`); the rest is fetched concurrently on
    the shared HTTP client, with `_as_worker`'s one retry after a 401.
    """
    values = {}
    missing = {}
    for name, (ttl, call) in jobs.items():
        hit = lcache.lookup(session, name, bypass=bypass)
        if hit is None:
            missing[name] = (ttl, call)
        else:
            values[name] = hit
    if not missing:
        return values

    def run(token: str, names: list[str]) -> dict[str, Any]:
        client = ConnectClient(token)
        if len(names) == 1:
            try:
                return {names[0]: missing[names[0]][1](client)}
            except ConnectWebError as exc:
                return {names[0]: exc}
        futures = {name: _FETCH_POOL.submit(missing[name][1], client) for name in names}
        out = {}
        for name, future in futures.items():
            try:
                out[name] = future.result()
            except ConnectWebError as exc:
                out[name] = exc
        return out

    token = ensure_signed_in(session)
    results = run(token, list(missing))
    refused = [n for n, r in results.items() if isinstance(r, ConnectAPIError) and r.status == 401]
    if refused:
        _drop_access_token(session, token)
        results.update(run(ensure_signed_in(session), refused))
    for name in missing:  # the first failure, in a stable order
        if isinstance(results[name], Exception):
            raise results[name]
    for name, (ttl, _call) in missing.items():
        lcache.remember(session, name, results[name], ttl)
        values[name] = results[name]
    return values


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


def _opportunities(session: WorkerSession, *, bypass: bool = False) -> list[dict]:
    """The worker's Connect list (cached `cache.OPPS_TTL`)."""
    return lcache.cached(
        session, "opps", lcache.OPPS_TTL, lambda: _as_worker(session, lambda c: c.list_opportunities()), bypass=bypass
    )


def list_opportunities(session: WorkerSession) -> list[dict]:
    """The worker's opportunities, each as ``{"opp": <Connect's item>, **summary_of(opp)}``."""
    return [{"opp": opp, **summary_of(opp)} for opp in _opportunities(session)]


def summary_of(opp: dict) -> dict:
    """Card status and progress from a list item alone: Learn, Claimed (nothing delivered yet) or Deliver."""
    claim = opp.get("claim")
    if claim:
        done = int(opp.get("deliver_progress") or 0)
        total = int(claim.get("max_payments") or 0)
        stage = "deliver" if done else "claimed"
        label = "Deliver" if done else "Claimed"
        unit = "visits"
    else:
        progress = opp.get("learn_progress") or {}
        done = int(progress.get("completed_modules") or 0)
        total = int(progress.get("total_modules") or 0)
        stage, label, unit = "learn", "Learn", "modules"
    pct = min(100, round(100 * done / total)) if total > 0 else 0
    return {
        "stage": stage,
        "stage_label": label,
        "progress_done": done,
        "progress_total": total if total > 0 else None,
        "progress_pct": pct,
        "progress_unit": unit,
        "suspended": bool(opp.get("is_user_suspended")),
    }


def _find(opps: Iterable[dict], opportunity_id: str) -> dict:
    for opp in opps:
        if str(opp.get("opportunity_id")) == str(opportunity_id) or str(opp.get("id")) == str(opportunity_id):
            return opp
    raise ConnectWebError("This worker doesn't have access to that opportunity.", error_code="NO_OPPORTUNITY_ACCESS")


def get_opportunity(session: WorkerSession, opportunity_id: str, *, bypass: bool = False) -> dict:
    """The worker's own list item for `opportunity_id` (its uuid or numeric id); refuses anything else.

    A miss in a cached list is checked once against a fresh one (a new invite shows at once).
    """
    try:
        return _find(_opportunities(session, bypass=bypass), opportunity_id)
    except ConnectWebError:
        if lcache.fetched(session, "opps"):
            raise
    return _find(_opportunities(session, bypass=True), opportunity_id)


def learn_status(opp: dict, progress: dict) -> dict:
    """Modules with their done state, assessments, and whether learning is complete.

    Complete mirrors the Android app: every learn module completed AND an assessment passed.
    A completed module's ``module`` is the learn module's ``id``.
    """
    modules = (opp.get("learn_app") or {}).get("learn_modules") or []
    done_keys = {str(c.get("module")) for c in (progress.get("completed_modules") or [])}
    rows = [
        {
            "id": m.get("id"),
            "slug": m.get("slug"),
            "name": m.get("name") or m.get("slug") or "Module",
            "description": m.get("description") or "",
            "time_estimate": m.get("time_estimate"),
            "done": str(m.get("id")) in done_keys or str(m.get("slug")) in done_keys,
        }
        for m in modules
    ]
    assessments = sorted(progress.get("assessments") or [], key=lambda a: str(a.get("date") or ""), reverse=True)
    passed = any(bool(a.get("passed")) for a in assessments)
    modules_done = sum(1 for r in rows if r["done"])
    modules_complete = all(r["done"] for r in rows)
    return {
        "modules": rows,
        "modules_done": modules_done,
        "modules_total": len(rows),
        "modules_complete": modules_complete,
        "assessments": assessments,
        "latest_assessment": assessments[0] if assessments else None,
        "passed": passed,
        "complete": modules_complete and passed,
    }


def _payments(delivery: dict) -> list[dict]:
    rows = []
    for p in delivery.get("payments") or []:
        uuid = p.get("payment_id") or p.get("payment_uuid")
        confirmed_on = p.get("confirmation_date") or p.get("date_confirmed")
        confirmed = bool(p.get("confirmed"))
        rows.append(
            {
                "id": p.get("id"),
                "uuid": uuid,
                "confirm_id": uuid or p.get("id"),
                "amount": p.get("amount"),
                "date_paid": p.get("date_paid"),
                "confirmed": confirmed,
                "confirmation_date": confirmed_on,
                "state": "confirmed" if confirmed else ("disputed" if confirmed_on else "unconfirmed"),
            }
        )
    return rows


def _progress_jobs(uuid: str, *, learn: bool, delivery: bool) -> dict:
    jobs = {}
    if learn:
        jobs[lcache.learn_name(uuid)] = (lcache.LEARN_TTL, lambda c: c.learn_progress(uuid))
    if delivery:
        jobs[lcache.delivery_name(uuid)] = (lcache.DELIVERY_TTL, lambda c: c.delivery_progress(uuid))
    return jobs


def opportunity_detail(session: WorkerSession, opportunity_id: str) -> dict:
    """The list item merged with learn progress, and delivery progress once claimed.

    The two progress reads run concurrently. While a submitted form is awaited
    (`cache.awaiting`), nothing is read from the cache.
    """
    opp = get_opportunity(session, opportunity_id)
    uuid = opp["opportunity_id"]
    fresh = lcache.awaiting(session, uuid)
    if fresh:
        opp = get_opportunity(session, opportunity_id, bypass=True)
    claimed = bool(opp.get("claim"))
    values = _fetch_together(session, _progress_jobs(uuid, learn=True, delivery=claimed), bypass=fresh)
    learn = learn_status(opp, values[lcache.learn_name(uuid)])
    delivery = values.get(lcache.delivery_name(uuid)) if claimed else None
    return {
        "opp": opp,
        **summary_of(opp),
        "claimed": claimed,
        "learn": learn,
        "can_claim": learn["complete"] and not claimed,
        "delivery": delivery,
        "deliveries": (delivery or {}).get("deliveries") or [],
        "payments": _payments(delivery or {}),
    }


def progress(session: WorkerSession, opportunity_id: str, kind: str) -> dict:
    """One kind's progress, read fresh from Connect (the post-submit poll): one Connect call.

    ``{"opp", "claimed", "learn" | None, "delivery" | None}``. The list item comes from the cache;
    deliver progress needs a claim.
    """
    if kind not in APP_KINDS:
        raise ValueError(f"kind must be one of {APP_KINDS}")
    opp = get_opportunity(session, opportunity_id)
    uuid = opp["opportunity_id"]
    claimed = bool(opp.get("claim"))
    jobs = _progress_jobs(uuid, learn=kind == "learn", delivery=kind == "deliver" and claimed)
    values = _fetch_together(session, jobs, bypass=True) if jobs else {}
    learn_raw = values.get(lcache.learn_name(uuid))
    return {
        "opp": opp,
        "claimed": claimed,
        "learn": learn_status(opp, learn_raw) if learn_raw is not None else None,
        "delivery": values.get(lcache.delivery_name(uuid)),
    }


def start_learning(session: WorkerSession, opportunity_id: str) -> None:
    """Accept the invite and create the worker's HQ user in the learn domain (Connect's start_learn_app)."""
    opp = get_opportunity(session, opportunity_id)
    uuid = opp["opportunity_id"]
    try:
        _as_worker(session, lambda c: c.start_learn_app(uuid))
    except ConnectAPIError as exc:
        if exc.error_code == FAILED_USER_CREATE:
            raise HQUserNotCreated(status=exc.status, domain=_domain(opp, "learn"), action="learn") from None
        raise
    finally:
        lcache.forget(session, "opps", lcache.learn_name(uuid))


def claim(session: WorkerSession, opportunity_id: str) -> bool:
    """Claim, only once learning is complete (Connect itself does not check). True if newly claimed."""
    opp = get_opportunity(session, opportunity_id)
    if opp.get("claim"):
        return False
    uuid = opp["opportunity_id"]
    # The guard reads learn progress fresh: a stale "incomplete" must not refuse a worker who just finished.
    values = _fetch_together(session, _progress_jobs(uuid, learn=True, delivery=False), bypass=True)
    if not learn_status(opp, values[lcache.learn_name(uuid)])["complete"]:
        raise GuardRefused(error_code=LEARNING_INCOMPLETE)
    try:
        return _as_worker(session, lambda c: c.claim(uuid))
    except ConnectAPIError as exc:
        if exc.error_code == FAILED_USER_CREATE:
            raise HQUserNotCreated(status=exc.status, domain=_domain(opp, "deliver"), action="claim") from None
        raise
    finally:
        lcache.forget(
            session,
            "opps",
            lcache.learn_name(uuid),
            lcache.delivery_name(uuid),
            *(lcache.target_name(oid, kind) for oid in _ids(opp) for kind in APP_KINDS),
        )


def _domain(opp: dict, app_kind: str) -> str:
    return (opp.get(f"{app_kind}_app") or {}).get("cc_domain") or ""


def _ids(opp: dict) -> list[str]:
    """The ids an opportunity may be addressed by (its uuid and numeric id)."""
    return [str(v) for v in (opp.get("opportunity_id"), opp.get("id")) if v not in (None, "")]


def confirm_payments(
    session: WorkerSession, decisions: Iterable[tuple[str | int, bool]], *, opportunity_id: str | None = None
) -> None:
    """Confirm (True) or dispute (False) payments by payment uuid or id."""
    decisions = list(decisions)
    try:
        _as_worker(session, lambda c: c.confirm_payments(decisions))
    finally:
        _forget_after_payment(session, opportunity_id)


def _forget_after_payment(session: WorkerSession, opportunity_id: str | None) -> None:
    opps = lcache.lookup(session, "opps")
    if opportunity_id is not None and opps is not None:
        try:
            uuid = _find(opps, opportunity_id)["opportunity_id"]
        except ConnectWebError:
            pass
        else:
            lcache.forget(session, "opps", lcache.delivery_name(uuid))
            return
    lcache.bump(session)


def confirm_payment(
    session: WorkerSession, payment_id: str | int, confirmed: bool, *, opportunity_id: str | None = None
) -> None:
    confirm_payments(session, [(payment_id, confirmed)], opportunity_id=opportunity_id)


def form_submitted(session: WorkerSession, opportunity_uuid: str, app_kind: str) -> None:
    """A form reached HQ: Connect will count it soon. Drop what it changes and await it."""
    progress_name = lcache.learn_name if app_kind == "learn" else lcache.delivery_name
    lcache.forget(session, "opps", progress_name(opportunity_uuid))
    lcache.set_awaiting(session, opportunity_uuid)


# ---------------------------------------------------------------------------
# CommCare HQ, and the form engine's seam
# ---------------------------------------------------------------------------


def _hq_key(hq_base: str, domain: str) -> str:
    return f"{urlsplit(hq_base).netloc}/{domain}"


def hq_token(session: WorkerSession, domain: str, hq_base: str | None = None) -> str:
    """A live CommCare HQ token for the worker in `domain`, cached per (HQ server, domain) until expiry.

    `hq_base` is always checked against ``CONNECT_WEB_HQ_HOSTS``: the worker's PersonalID token is
    the password of this request.
    """
    if not session.is_open:
        raise WorkerUnavailable("This session has ended. Pick a worker again.")
    base = hq.hq_base_for(hq_base or None)
    key = _hq_key(base, domain)
    worker = session.pool_worker
    cached = worker.get_hq_tokens().get(key) or {}
    expires_at = parse_datetime(cached.get("expires_at") or "")
    if cached.get("token") and _fresh(expires_at):
        return cached["token"]

    access = ensure_signed_in(session)
    try:
        token = hq.request_token(base, worker.personalid_username, domain, access)
    except HQError as exc:
        # HQ checks the PersonalID token with connect-id: a stale one reads as a refused grant.
        if exc.status not in (400, 401) or exc.error_code not in ("invalid_grant", ""):
            raise
        _drop_access_token(session, access)
        token = hq.request_token(base, worker.personalid_username, domain, ensure_signed_in(session))
    _store_hq_token(worker, key, token.access_token, _expiry(token.expires_in))
    return token.access_token


def _store_hq_token(worker: PoolWorker, key: str, token: str, expires_at) -> None:
    """Merge one domain's token into the worker's tokens under the row lock, re-reading first.

    The HTTP call that got the token is already done: the lock is held only for this read-modify-write,
    so two domains' tokens fetched at once both survive.
    """
    with transaction.atomic():
        locked = PoolWorker.objects.select_for_update().only("pk", "hq_tokens").get(pk=worker.pk)
        locked.set_hq_token(key, token, expires_at)
        PoolWorker.objects.filter(pk=worker.pk).update(hq_tokens=locked.hq_tokens)
    worker.hq_tokens = locked.hq_tokens


def form_engine_context(
    session: WorkerSession, opportunity: dict | str, app_kind: str, *, with_token: bool = True
) -> dict[str, Any]:
    """Everything the form engine needs to run the worker's learn or deliver app on HQ.

    ``{hq_base, domain, app_id, install_url, hq_username, hq_token}``. `opportunity` is a Connect
    list item or its ``opportunity_id``. A deliver app needs a claim first (Connect creates the
    deliver-domain HQ user at claim). `with_token=False` leaves ``hq_token`` None (no HQ call).
    """
    if app_kind not in APP_KINDS:
        raise ValueError(f"app_kind must be one of {APP_KINDS}")
    opp = opportunity if isinstance(opportunity, dict) else get_opportunity(session, opportunity)
    app = opp.get(f"{app_kind}_app") or {}
    domain, app_id = app.get("cc_domain"), app.get("cc_app_id")
    if not domain or not app_id:
        raise ConnectWebError(f"This opportunity has no {app_kind} app.")
    if app_kind == "deliver" and not opp.get("claim"):
        raise GuardRefused("Claim the opportunity before delivering.", error_code="NOT_CLAIMED")
    install_url = app.get("install_url") or ""
    hq_base = hq.hq_base_for(install_url or None)
    if with_token:
        ensure_signed_in(session)
    username = session.pool_worker.personalid_username
    return {
        "hq_base": hq_base,
        "domain": domain,
        "app_id": app_id,
        "install_url": install_url,
        "hq_username": hq.hq_username(username, domain) if username else None,
        "hq_token": hq_token(session, domain, hq_base) if with_token else None,
    }
