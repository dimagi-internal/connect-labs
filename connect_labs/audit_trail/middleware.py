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
            user_agent=(request.headers.get("user-agent") or "")[:300],
            request_id=str(uuid.uuid4()),
            path=request.path[:300],
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
            service.flush_buffer(ctx, status_code=response.status_code)
        finally:
            reset_audit_context(token)
        return response
