"""Auth event receivers → audit trail.

Connected in AuditTrailConfig.ready(). Login/logout fire inside a request
(the OAuth callback calls django.contrib.auth.login), so the middleware's
buffered context supplies IP/user-agent/request-id attribution.
"""
from celery.signals import before_task_publish, task_postrun, task_prerun
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import AuditContext, get_audit_context, reset_audit_context, set_audit_context
from connect_labs.audit_trail.models import Action, Outcome, Source

# Celery message headers carrying the identity of whoever enqueued the task.
# Protocol-2 custom headers surface on the worker as ``task.request.<key>``.
_ACTOR_HEADERS = ("labs_actor_id", "labs_actor_name", "labs_origin_request_id")


@receiver(user_logged_in, dispatch_uid="audit_trail_login")
def on_user_logged_in(sender, request, user, **kwargs):
    service.record(Action.LOGIN, resource_type="auth", user=user)
    # Product analytics ride the same signal (server-side: OAuth logins have
    # no client-side page event to hook).
    from connect_labs.utils import server_analytics
    from connect_labs.utils.dimagi_user import is_dimagi_user

    server_analytics.send_event("login", {"is_dimagi": is_dimagi_user(user)})


@receiver(user_logged_out, dispatch_uid="audit_trail_logout")
def on_user_logged_out(sender, request, user, **kwargs):
    service.record(Action.LOGOUT, resource_type="auth", user=user)


@receiver(user_login_failed, dispatch_uid="audit_trail_login_failed")
def on_user_login_failed(sender, credentials, request=None, **kwargs):
    username = ""
    if isinstance(credentials, dict):
        username = str(credentials.get("username") or "")[:150]
    service.record(
        Action.LOGIN_FAILED,
        resource_type="auth",
        outcome=Outcome.FAILURE,
        metadata={"attempted_username": username},
    )


@before_task_publish.connect
def on_before_task_publish(headers=None, **kwargs):
    """Stamp the enqueuing user onto the task message.

    A task inherits nothing from the request that queued it, so work done
    on behalf of a person used to land in the audit trail — and in Sentry —
    with no actor at all. Carrying the id in a message header closes that gap
    for every task, without each task growing a ``user_id`` argument.
    """
    try:
        if headers is None:
            return
        ctx = get_audit_context()
        if ctx is None:
            return
        service.resolve_context_user(ctx)
        if ctx.user_id is None:
            return
        # Id + username only. The email is deliberately not written to the
        # broker: it buys nothing the id doesn't, and Redis is not a place to
        # persist personal data.
        headers["labs_actor_id"] = ctx.user_id
        headers["labs_actor_name"] = ctx.username or ""
        headers["labs_origin_request_id"] = ctx.request_id or ""
    except Exception:  # pragma: no cover - never break enqueueing
        pass


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, **kwargs):
    """Open a Celery audit context, attributed to whoever enqueued the task.

    Falls back to an unattributed context for tasks published by beat or by a
    script (tasks that know better can still open a nested
    ``audit_context(user=...)``)."""
    try:
        ctx = AuditContext(source=Source.CELERY, request_id=f"celery:{task_id or ''}")
        request = getattr(task, "request", None)
        actor_id = getattr(request, "labs_actor_id", None)
        if actor_id is not None:
            ctx.user_id = actor_id
            ctx.username = getattr(request, "labs_actor_name", "") or ""
        if task is not None:
            ctx.path = getattr(task, "name", "")
        task.request.audit_trail_token = set_audit_context(ctx)
    except Exception:  # pragma: no cover - never break task startup
        pass


@task_postrun.connect
def on_task_postrun(sender=None, task=None, **kwargs):
    try:
        token = getattr(getattr(task, "request", None), "audit_trail_token", None)
        if token is not None:
            reset_audit_context(token)
    except Exception:  # pragma: no cover
        pass
