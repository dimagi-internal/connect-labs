"""
Shared URL-builder helpers for linking an audit assessment back to its source:
the CommCareHQ form submission and the visit in Connect.

Factored out of ExperimentBulkAssessmentExportCSVView._resolve_hq_link_base and
_resolve_visit_cluster_group (connect_labs/audit/views.py) so the classifier-fail
training-data export (connect_labs/audit/classifier_fail_sync.py) can build the
same links without duplicating the logic.
"""

import logging

from django.conf import settings
from django.urls import reverse

from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata
from connect_labs.labs.context import get_org_data
from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

logger = logging.getLogger(__name__)


def resolve_hq_link_base(access_token: str, opportunity_id: int) -> str | None:
    """Return the CommCareHQ form_data base URL for an opportunity, or None if unresolvable.

    Callers append ``/{xform_id}/`` per form -- kept as a base (rather than
    taking xform_id here) so a caller resolving many forms for one opportunity
    only pays the opportunity-metadata fetch once.
    """
    try:
        metadata = fetch_opportunity_metadata(access_token, opportunity_id)
    except Exception:
        logger.exception("[Audit] Failed to resolve CommCareHQ domain for opportunity %s", opportunity_id)
        return None

    domain = metadata.get("cc_domain")
    if not domain:
        return None
    deliver_app = (metadata.get("raw") or {}).get("deliver_app") or {}
    hq_server_url = (deliver_app.get("hq_server") or {}).get("url") or "https://www.commcarehq.org"
    return f"{hq_server_url.rstrip('/')}/a/{domain}/reports/form_data"


def build_hq_form_url(hq_link_base: str | None, xform_id: str | None) -> str:
    """Combine a resolved base (see resolve_hq_link_base) with one form's xform_id."""
    return f"{hq_link_base}/{xform_id}/" if hq_link_base and xform_id else ""


def build_connect_visit_url(connect_url: str, org_slug: str, opportunity_id, user_id, user_visit_id) -> str:
    """Return the "view in Connect" visit URL, or "" if any required piece is missing."""
    if not (org_slug and user_id and user_visit_id):
        return ""
    return (
        f"{connect_url.rstrip('/')}/a/{org_slug}/opportunity/{opportunity_id}/user_visits/"
        f"?user={user_id}&visit_id={user_visit_id}"
    )


def resolve_org_slug(access_token: str, opportunity_id, request=None) -> str:
    """Return the organization slug for an opportunity (needed by build_connect_visit_url).

    When a live request is available, reuse its session-cached org/opportunity list
    (get_org_data) -- no extra API call. Otherwise (background/Celery contexts, e.g.
    AI review or duplicate detection, which have no request) fall back to a live
    fetch_user_organization_data call -- the same /export/opp_org_program_list/ the
    OAuth login flow already makes, just uncached here.
    """
    org_data = get_org_data(request) if request is not None else None
    if not org_data:
        org_data = fetch_user_organization_data(access_token) or {}
    for opp in org_data.get("opportunities", []):
        if opp.get("id") == opportunity_id:
            return opp.get("organization", "")
    return ""


def build_absolute_url(path: str, request=None) -> str:
    """Return an absolute URL for `path`, with or without a live request.

    Mirrors connect_labs/program/tasks.py's _build_absolute_uri fallback (the
    django.contrib.sites-based pattern already used for Celery-task emails) for the
    request-less case.
    """
    if request is not None:
        return request.build_absolute_uri(path)
    try:
        from django.contrib.sites.models import Site

        domain = Site.objects.get_current().domain
    except Exception:
        domain = "localhost"
    return f"https://{domain}{path}"


def resolve_urls_by_blob(
    *,
    data_access,
    access_token: str,
    opportunity_id,
    visit_ids: list,
    visit_images: dict,
    request=None,
) -> dict[str, dict]:
    """Build {blob_id: {"image_url", "form_url", "connect_url"}} for a batch of images.

    Consolidates the batching approach classifier_fail_sync.py and
    views.py::_resolve_visit_cluster_group each used inline: one get_visits_batch call,
    one HQ-metadata fetch, one org-slug lookup -- regardless of how many images are in
    visit_images. Works identically with or without a live `request` (see
    resolve_org_slug / build_absolute_url), so the same function serves both the
    human save/complete path and the request-less AI-review/duplicate-detection paths.

    Args:
        data_access: AuditDataAccess instance (needs get_visits_batch).
        access_token: OAuth token for the HQ-metadata and org-slug lookups.
        opportunity_id: Opportunity these visits/images belong to.
        visit_ids: Visit ids to batch-resolve xform_id/user_id/user_visit_id for.
        visit_images: {visit_id_str: [image_dict, ...]} (session.data["visit_images"] shape).
        request: Live HttpRequest if available; None in background contexts.

    Returns {} if visit_ids is empty or the visit batch fetch fails.
    """
    if not visit_ids:
        return {}

    try:
        visits = data_access.get_visits_batch(visit_ids, opportunity_id)
    except Exception:
        logger.exception("[Audit] Failed to fetch visit batch for opportunity %s", opportunity_id)
        return {}

    xform_id_by_visit = {str(v["id"]): v.get("xform_id") for v in visits}
    link_id_by_visit = {str(v["id"]): (v.get("user_id"), v.get("user_visit_id")) for v in visits}

    hq_link_base = resolve_hq_link_base(access_token, opportunity_id)
    org_slug = resolve_org_slug(access_token, opportunity_id, request=request)
    connect_url = getattr(settings, "CONNECT_PRODUCTION_URL", "https://connect.dimagi.com").rstrip("/")

    urls_by_blob: dict[str, dict] = {}
    for visit_id_str, images in visit_images.items():
        form_url = build_hq_form_url(hq_link_base, xform_id_by_visit.get(visit_id_str))
        user_id, user_visit_id = link_id_by_visit.get(visit_id_str, (None, None))
        connect_visit_url = build_connect_visit_url(connect_url, org_slug, opportunity_id, user_id, user_visit_id)
        for image in images:
            blob_id = image.get("blob_id")
            if not blob_id:
                continue
            image_path = reverse("audit:audit_image_connect", kwargs={"opp_id": opportunity_id, "blob_id": blob_id})
            urls_by_blob[blob_id] = {
                "image_url": build_absolute_url(image_path, request=request),
                "form_url": form_url,
                "connect_url": connect_visit_url,
            }
    return urls_by_blob
