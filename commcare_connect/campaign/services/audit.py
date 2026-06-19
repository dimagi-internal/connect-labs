"""Write-side audit logging for privileged admin actions.

Every privileged write in the tool (payments, KYC, user management, activities,
microplans) calls :func:`record` so the System Administration › Activity log shows
what actually happened. The client IP is masked to two octets to match the demo's
privacy posture (``102.89.x.x``).
"""

from __future__ import annotations

from commcare_connect.campaign.auth.decorators import current_campaign_user
from commcare_connect.campaign.models import AuditLog


def mask_ip(ip: str) -> str:
    """Mask the host portion of an address: 102.89.4.17 -> 102.89.x.x."""
    if not ip:
        return ""
    if ":" in ip and "." not in ip:  # IPv6 — keep the routing prefix only
        head = ip.split(":")[:2]
        return ":".join(head) + "::x"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    return ip


def client_ip(request) -> str:
    """Best-effort client IP behind the ECS load balancer (first XFF hop)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def record(request, action: str, module: str, campaign=None) -> AuditLog:
    """Persist one audit row attributed to the request's authenticated user."""
    cu = current_campaign_user(request)
    actor = (cu.name or cu.commcare_username) if cu else "system"
    return AuditLog.objects.create(
        campaign=campaign,
        user=actor,
        action=action,
        module=module,
        ip=mask_ip(client_ip(request)),
    )
