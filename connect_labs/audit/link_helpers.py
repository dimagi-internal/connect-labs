"""
Shared URL-builder helpers for linking an audit assessment back to its source:
the CommCareHQ form submission and the visit in Connect.

Factored out of ExperimentBulkAssessmentExportCSVView._resolve_hq_link_base and
_resolve_visit_cluster_group (connect_labs/audit/views.py) so the classifier-fail
training-data export (connect_labs/audit/classifier_fail_sync.py) can build the
same links without duplicating the logic.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata
from connect_labs.labs.context import get_org_data
from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

logger = logging.getLogger(__name__)

# Short TTL: this only exists to de-dupe the N-sessions-in-one-batch-run case
# (AI review / duplicate detection resolving URLs once per session, all for
# the same user/token) -- not meant to serve stale org data for long.
_ORG_DATA_CACHE_TTL = 300


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
    OAuth login flow already makes -- cached briefly (_ORG_DATA_CACHE_TTL) so a
    batch run resolving URLs for many sessions of the same user doesn't repeat
    this call once per session.
    """
    org_data = get_org_data(request) if request is not None else None
    if not org_data:
        cache_key = f"link_helpers:org_data:{hashlib.sha256(access_token.encode()).hexdigest()}"
        org_data = cache.get(cache_key)
        if org_data is None:
            org_data = fetch_user_organization_data(access_token) or {}
            cache.set(cache_key, org_data, _ORG_DATA_CACHE_TTL)
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
    one HQ-metadata fetch, one org-slug lookup PER DISTINCT OPPORTUNITY represented --
    regardless of how many images are in visit_images. Works identically with or
    without a live `request` (see resolve_org_slug / build_absolute_url), so the same
    function serves both the human save/complete path and the request-less
    AI-review/duplicate-detection paths.

    Groups images by their OWN `opportunity_id` when present (falling back to the
    `opportunity_id` param otherwise) rather than assuming every image in
    `visit_images` belongs to a single opportunity -- a multi-opp session (e.g.
    muac_picture_audit, weekly_dual_track_audit) can flag images sourced from a
    different opportunity than the session's own. Callers don't need to pre-group
    or make one call per opportunity themselves; this handles the common
    single-opportunity case (no image carries its own "opportunity_id") with
    exactly the one get_visits_batch/HQ/org-slug round trip it always did.

    Args:
        data_access: AuditDataAccess instance (needs get_visits_batch).
        access_token: OAuth token for the HQ-metadata and org-slug lookups.
        opportunity_id: Default/primary opportunity for images that don't carry
            their own "opportunity_id" key.
        visit_ids: Visit ids scoping the session -- only used to short-circuit
            when the session has none; per-opportunity visit ids are derived
            from visit_images itself (see grouping above).
        visit_images: {visit_id_str: [image_dict, ...]} (session.data["visit_images"] shape).
        request: Live HttpRequest if available; None in background contexts.

    Returns {} if visit_ids is empty, or a partial mapping if one opportunity's
    visit-batch fetch fails while another's succeeds (failures are logged, not raised).
    """
    if not visit_ids:
        return {}

    # {opportunity_id: {visit_id_str: [image_dict, ...]}}
    visit_images_by_opp: dict = {}
    for visit_id_str, images in visit_images.items():
        for image in images:
            opp_for_image = image.get("opportunity_id") or opportunity_id
            visit_images_by_opp.setdefault(opp_for_image, {}).setdefault(visit_id_str, []).append(image)

    connect_url = getattr(settings, "CONNECT_PRODUCTION_URL", "https://connect.dimagi.com").rstrip("/")

    urls_by_blob: dict[str, dict] = {}
    for opp_for_group, grouped_visit_images in visit_images_by_opp.items():
        visit_ids_for_group = [int(vid) for vid in grouped_visit_images]
        try:
            visits = data_access.get_visits_batch(visit_ids_for_group, opp_for_group)
        except Exception:
            logger.exception("[Audit] Failed to fetch visit batch for opportunity %s", opp_for_group)
            continue

        xform_id_by_visit = {str(v["id"]): v.get("xform_id") for v in visits}
        link_id_by_visit = {str(v["id"]): (v.get("user_id"), v.get("user_visit_id")) for v in visits}

        hq_link_base = resolve_hq_link_base(access_token, opp_for_group)
        org_slug = resolve_org_slug(access_token, opp_for_group, request=request)

        for visit_id_str, images in grouped_visit_images.items():
            form_url = build_hq_form_url(hq_link_base, xform_id_by_visit.get(visit_id_str))
            user_id, user_visit_id = link_id_by_visit.get(visit_id_str, (None, None))
            connect_visit_url = build_connect_visit_url(connect_url, org_slug, opp_for_group, user_id, user_visit_id)
            for image in images:
                blob_id = image.get("blob_id")
                if not blob_id:
                    continue
                image_path = reverse(
                    "audit:audit_image_connect", kwargs={"opp_id": opp_for_group, "blob_id": blob_id}
                )
                urls_by_blob[blob_id] = {
                    "image_url": build_absolute_url(image_path, request=request),
                    "form_url": form_url,
                    "connect_url": connect_visit_url,
                }
    return urls_by_blob
