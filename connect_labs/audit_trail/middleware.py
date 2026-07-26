"""Audit context middleware.

Opens a buffered audit context for every request so data-access choke points
(``LabsRecordAPIClient``, ``ExportAPIClient``) and auth signals can record
events without a request object, then flushes the buffer after the response.

Ordering: must sit after AuthenticationMiddleware (needs request.user).
Flushing happens in the response phase of ``__call__``, which runs OUTSIDE the
view's ATOMIC_REQUESTS transaction — audit rows survive request rollbacks and
carry the final status code.
"""
import uuid

from connect_labs.audit_trail import service
from connect_labs.audit_trail.context import AuditContext, reset_audit_context, set_audit_context
from connect_labs.audit_trail.models import Action, Outcome, Source


def _client_ip(request) -> str:
    """Best client IP available: first X-Forwarded-For hop (ALB) or REMOTE_ADDR."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]


class AuditTrailMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ctx = AuditContext(
            source=Source.WEB,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent") or "",
            request_id=str(uuid.uuid4()),
            path=request.path,
            query_string=service.redact_query_string(request.META.get("QUERY_STRING", "")),
            buffer=[],
            request=request,
        )
        token = set_audit_context(ctx)
        try:
            response = self.get_response(request)
        except Exception:
            # The exception escaped the view (500). Flush what we have so the
            # attempted access is still on record, then re-raise.
            service.flush_buffer(ctx, status_code=500)
            reset_audit_context(token)
            raise
        try:
            if response.status_code == 403:
                service.record(
                    Action.ACCESS_DENIED,
                    resource_type="http",
                    outcome=Outcome.FAILURE,
                    status_code=403,
                )
            if self._is_page_view(request, response):
                service.record(Action.PAGE_VIEW, resource_type="page", status_code=response.status_code)
            service.flush_buffer(ctx, status_code=response.status_code)
        finally:
            reset_audit_context(token)
        return response

    @staticmethod
    def _is_page_view(request, response) -> bool:
        """Authenticated HTML GET renders — the navigation record that makes a
        user's session reconstructable even for pages that touch no data.
        htmx partial refreshes are excluded (sub-page churn, not navigation)."""
        if request.method != "GET" or response.status_code != 200:
            return False
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if request.headers.get("HX-Request"):
            return False
        content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
        return content_type.startswith("text/html")
