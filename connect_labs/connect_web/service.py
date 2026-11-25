"""Connect Web's service layer: the one door every page (and, later, the form engine) goes through.

Labs' backend acts as the pool worker's phone. A labs user checks a pool worker out
(`check_out`), and from then on every call is made as that worker: `ensure_signed_in` signs in
through PersonalID's recovery flow and caches the access token server-side; the Connect calls
(`list_opportunities`, `opportunity_detail`, `start_learning`, `claim`, `confirm_payments`) carry it;
`hq_token` trades it for a CommCare HQ token per domain. The browser never sees any of them.

**The seam for the form engine** is `form_engine_context(session, opportunity, app_kind)`: the HQ
server, domain, app id, install URL, HQ username and a live HQ token for the learn or deliver app.

Sign-in, in order (cheapest first, never more than needed):

1. a cached PersonalID access token that has more than `TOKEN_SKEW` left;
2. the password grant with the PersonalID password saved on the session;
3. only if that fails with ``invalid_grant`` (or there is no password yet): full recovery —
   ``start_configuration`` → ``complete_recovery`` (rotates the password) → password grant.

A wrong backup code is never retried: the pool worker is blocked from sign-in
(`PoolWorker.sign_in_blocked_reason`) until an admin saves a new code, because connect-id locks
the account after a few wrong attempts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any, TypeVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import hq, personalid
from .connect_api import ConnectClient
from .errors import (
    INCORRECT_BACKUP_CODE,
    LEARNING_INCOMPLETE,
    ConnectAPIError,
    ConnectWebError,
    GuardRefused,
    HQError,
    IncorrectBackupCode,
    PersonalIDError,
    WorkerUnavailable,
)
from .models import PoolWorker, WorkerSession

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: A cached token is used only while it has more than this left.
TOKEN_SKEW = timedelta(seconds=60)
#: When a token endpoint omits expires_in.
DEFAULT_TOKEN_SECONDS = 600

APP_KINDS = ("learn", "deliver")


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
    """End the session and drop every secret it held."""
    _end(session)


def _end(session: WorkerSession) -> None:
    session.ended_at = timezone.now()
    session.personalid_password = ""
    session.clear_tokens()
    session.save(
        update_fields=["ended_at", "personalid_password", "access_token", "access_token_expires_at", "hq_tokens"]
    )


def display_user(user) -> str:
    return (getattr(user, "name", "") or user.email or user.username) if user else ""


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


def _fresh(expires_at) -> bool:
    return bool(expires_at) and expires_at - TOKEN_SKEW > timezone.now()


def _expiry(expires_in: int):
    return timezone.now() + timedelta(seconds=expires_in or DEFAULT_TOKEN_SECONDS)


def ensure_signed_in(session: WorkerSession) -> str:
    """A live PersonalID access token for the session's worker (cached, password grant, or recovery).

    Locks the pool worker, then the session (the order `check_out` uses), so two requests never
    recover at once. Whatever a failed sign-in learned — a rotated password, the username, a
    pause after a rejected backup code — is saved inside the block and the error raised only
    after it commits. Labs runs with ``ATOMIC_REQUESTS``, so callers must be
    ``non_atomic_requests`` views (connect_web's are, see urls.py) or those writes roll back with
    the request.
    """
    if not session.is_open:
        raise WorkerUnavailable("This session has ended. Pick a worker again.")
    if session.access_token and _fresh(session.access_token_expires_at):
        _touch(session)
        return session.access_token

    # Fail on configuration before anything rotates the worker's password.
    personalid.client_id()
    failure: ConnectWebError | None = None
    with transaction.atomic():
        PoolWorker.objects.select_for_update().get(pk=session.pool_worker_id)
        locked = WorkerSession.objects.select_for_update().select_related("pool_worker").get(pk=session.pk)
        if not locked.is_open:
            raise WorkerUnavailable("This session has ended. Pick a worker again.")
        if not (locked.access_token and _fresh(locked.access_token_expires_at)):
            failure = _sign_in(locked)
    _copy_auth(locked, session)
    if failure is not None:
        raise failure
    return session.access_token


def _sign_in(locked: WorkerSession) -> ConnectWebError | None:
    """Sign in and save the result on `locked`; on failure, save what was learned and return the error."""
    worker = locked.pool_worker
    try:
        token = None
        if locked.personalid_password and worker.personalid_username:
            try:
                token = personalid.password_grant(worker.personalid_username, locked.personalid_password)
            except PersonalIDError as exc:
                if exc.error_code != "invalid_grant":
                    raise
                logger.info("connect_web: password grant refused for pool worker %s; recovering", worker.pk)
        if token is None:
            token = _recover(locked)
    except ConnectWebError as exc:
        if isinstance(exc, IncorrectBackupCode) or exc.error_code == "LOCKED_ACCOUNT":
            reason = "PersonalID rejected the backup code" if isinstance(exc, IncorrectBackupCode) else "Locked"
            worker.sign_in_blocked_reason = reason
            worker.save(update_fields=["sign_in_blocked_reason", "updated_at"])
            logger.warning("connect_web: sign-in paused for pool worker %s (%s)", worker.pk, exc.error_code)
        # Keep a password a recovery just rotated, even though the grant after it failed.
        locked.save(update_fields=["personalid_password"])
        return exc
    locked.access_token = token.access_token
    locked.access_token_expires_at = _expiry(token.expires_in)
    locked.last_used_at = timezone.now()
    locked.save(update_fields=["personalid_password", "access_token", "access_token_expires_at", "last_used_at"])
    return None


def _recover(locked: WorkerSession) -> personalid.AccessToken:
    worker = locked.pool_worker
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
    if worker.personalid_username != creds.username:
        worker.personalid_username = creds.username
        worker.save(update_fields=["personalid_username", "updated_at"])
    locked.personalid_password = creds.password
    return personalid.password_grant(creds.username, creds.password)


def _copy_auth(source: WorkerSession, target: WorkerSession) -> None:
    for name in ("personalid_password", "access_token", "access_token_expires_at", "hq_tokens", "last_used_at"):
        setattr(target, name, getattr(source, name))
    target.pool_worker = source.pool_worker


def _touch(session: WorkerSession) -> None:
    now = timezone.now()
    if now - session.last_used_at > timedelta(minutes=1):
        session.last_used_at = now
        WorkerSession.objects.filter(pk=session.pk).update(last_used_at=now)


def _drop_access_token(session: WorkerSession) -> None:
    session.access_token = ""
    session.access_token_expires_at = None
    WorkerSession.objects.filter(pk=session.pk).update(access_token="", access_token_expires_at=None)


def _as_worker(session: WorkerSession, call: Callable[[ConnectClient], T]) -> T:
    """Run a Connect call as the worker; on a 401, drop the cached token, sign in again, retry once."""
    try:
        return call(ConnectClient(ensure_signed_in(session)))
    except ConnectAPIError as exc:
        if exc.status != 401:
            raise
    _drop_access_token(session)
    return call(ConnectClient(ensure_signed_in(session)))


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


def list_opportunities(session: WorkerSession) -> list[dict]:
    """The worker's opportunities, each as ``{"opp": <Connect's item>, **summary_of(opp)}``."""
    opps = _as_worker(session, lambda c: c.list_opportunities())
    return [{"opp": opp, **summary_of(opp)} for opp in opps]


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


def get_opportunity(session: WorkerSession, opportunity_id: str) -> dict:
    return _find(_as_worker(session, lambda c: c.list_opportunities()), opportunity_id)


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


def opportunity_detail(session: WorkerSession, opportunity_id: str) -> dict:
    """The list item merged with learn progress, and delivery progress once claimed."""

    def load(client: ConnectClient) -> dict:
        opp = _find(client.list_opportunities(), opportunity_id)
        uuid = opp["opportunity_id"]
        learn = learn_status(opp, client.learn_progress(uuid))
        delivery = client.delivery_progress(uuid) if opp.get("claim") else None
        return {"opp": opp, "learn": learn, "delivery": delivery}

    data = _as_worker(session, load)
    opp, learn, delivery = data["opp"], data["learn"], data["delivery"]
    return {
        "opp": opp,
        **summary_of(opp),
        "claimed": bool(opp.get("claim")),
        "learn": learn,
        "can_claim": learn["complete"] and not opp.get("claim"),
        "delivery": delivery,
        "deliveries": (delivery or {}).get("deliveries") or [],
        "payments": _payments(delivery or {}),
    }


def start_learning(session: WorkerSession, opportunity_id: str) -> None:
    """Accept the invite and create the worker's HQ user in the learn domain (Connect's start_learn_app)."""

    def go(client: ConnectClient) -> None:
        opp = _find(client.list_opportunities(), opportunity_id)
        client.start_learn_app(opp["opportunity_id"])

    _as_worker(session, go)


def claim(session: WorkerSession, opportunity_id: str) -> bool:
    """Claim, only once learning is complete (Connect itself does not check). True if newly claimed."""

    def go(client: ConnectClient) -> bool:
        opp = _find(client.list_opportunities(), opportunity_id)
        if opp.get("claim"):
            return False
        status = learn_status(opp, client.learn_progress(opp["opportunity_id"]))
        if not status["complete"]:
            raise GuardRefused(error_code=LEARNING_INCOMPLETE)
        return client.claim(opp["opportunity_id"])

    return _as_worker(session, go)


def confirm_payments(session: WorkerSession, decisions: Iterable[tuple[str | int, bool]]) -> None:
    """Confirm (True) or dispute (False) payments by payment uuid or id."""
    decisions = list(decisions)
    _as_worker(session, lambda c: c.confirm_payments(decisions))


def confirm_payment(session: WorkerSession, payment_id: str | int, confirmed: bool) -> None:
    confirm_payments(session, [(payment_id, confirmed)])


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
    cached = session.get_hq_tokens().get(key) or {}
    expires_at = parse_datetime(cached.get("expires_at") or "")
    if cached.get("token") and _fresh(expires_at):
        return cached["token"]

    def fetch():
        access = ensure_signed_in(session)
        return hq.request_token(base, session.pool_worker.personalid_username, domain, access)

    try:
        token = fetch()
    except HQError as exc:
        # HQ checks the PersonalID token with connect-id: a stale one reads as a refused grant.
        if exc.status not in (400, 401) or exc.error_code not in ("invalid_grant", ""):
            raise
        _drop_access_token(session)
        token = fetch()
    session.set_hq_token(key, token.access_token, _expiry(token.expires_in))
    # Only onto a session that is still open: a release mid-request must not get a token back.
    WorkerSession.objects.filter(pk=session.pk, ended_at__isnull=True).update(hq_tokens=session.hq_tokens)
    return token.access_token


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
