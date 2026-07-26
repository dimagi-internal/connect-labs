"""Auth event receivers → audit trail.

Connected in AuditTrailConfig.ready(). Login/logout fire inside a request
(the OAuth callback calls django.contrib.auth.login), so the middleware's
buffered context supplies IP/user-agent/request-id attribution.
"""
from celery.signals import task_postrun, task_prerun
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import AuditContext, reset_audit_context, set_audit_context
from connect_labs.audit_trail.models import Action, Outcome, Source


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


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, **kwargs):
    """Open a Celery audit context so choke-point events inside any task are
    at least attributed to the task (tasks that know the acting user can open
    a nested audit_context(user=...) for full attribution)."""
    try:
        ctx = AuditContext(source=Source.CELERY, request_id=f"celery:{task_id or ''}")
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
