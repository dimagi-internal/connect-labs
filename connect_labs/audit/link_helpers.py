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
from connect_labs.utils.urls import build_absolute_url as _build_absolute_url_no_request

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


def _find_org_slug(org_data: dict | None, opportunity_id) -> str | None:
    """Return the org slug for opportunity_id within org_data, or None if
    org_data is empty/doesn't list that opportunity at all -- distinct from a
    "" slug, which means the opportunity WAS found but its slug is blank."""
    if not org_data:
        return None
    for opp in org_data.get("opportunities", []):
        if opp.get("id") == opportunity_id:
            return opp.get("organization", "")
    return None


def resolve_org_slug(access_token: str, opportunity_id, request=None) -> str:
    """Return the organization slug for an opportunity (needed by build_connect_visit_url).

    When a live request is available, reuse its session-cached org/opportunity list
    (get_org_data) first -- no extra API call. If that doesn't list the requested
    opportunity (e.g. access was granted after login), or there's no request at all
    (background/Celery contexts, e.g. AI review or duplicate detection), fall back to
    a live fetch_user_organization_data call -- the same /export/opp_org_program_list/
    the OAuth login flow already makes -- cached briefly (_ORG_DATA_CACHE_TTL) so a
    batch run resolving URLs for many sessions of the same user doesn't repeat this
    call once per session. Only a genuinely successful fetch is cached -- a transient
    failure (coerced to {}) is never cached, so it doesn't get "stuck" returning a
    blank org_slug for the rest of the TTL window instead of retrying.
    """
    if request is not None:
        slug = _find_org_slug(get_org_data(request), opportunity_id)
        if slug is not None:
            return slug

    cache_key = f"link_helpers:org_data:{hashlib.sha256(access_token.encode()).hexdigest()}"
    org_data = cache.get(cache_key)
    if org_data is None:
        org_data = fetch_user_organization_data(access_token)
        if org_data:
            cache.set(cache_key, org_data, _ORG_DATA_CACHE_TTL)
    return _find_org_slug(org_data, opportunity_id) or ""


def build_absolute_url(path: str, request=None) -> str:
    """Return an absolute URL for `path`, with or without a live request.

    request=None uses connect_labs.utils.urls.build_absolute_url -- the same
    shared Site-based fallback connect_labs/program/tasks.py and
    connect_labs/utils/sms.py use for their own request-less contexts.
    """
    if request is not None:
        return request.build_absolute_uri(path)
    return _build_absolute_url_no_request(path)


def resolve_urls_by_blob(
    *,
    data_access,
    access_token: str,
    opportunity_id,
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
        visit_images: {visit_id_str: [image_dict, ...]} (session.data["visit_images"] shape).
            Per-opportunity visit ids for get_visits_batch are derived directly
            from this (see grouping above) -- there's no separate visit_ids
            input to keep in sync, so it can't silently diverge from what's
            actually being resolved.
        request: Live HttpRequest if available; None in background contexts.

    Returns {} if visit_images is empty, or a partial mapping if resolving one
    opportunity's group fails while another's succeeds -- each group is
    isolated in its own try/except, so a failure never discards URLs a prior
    group in the same call already resolved (failures are logged, not raised).
    """
    if not visit_images:
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
        # The WHOLE group is inside one try/except -- not just get_visits_batch
        # -- so a failure resolving one opportunity's URLs (e.g. reverse()
        # raising on a malformed opp_for_group) can't wipe out urls_by_blob
        # entries a PRIOR group in this same call already resolved successfully.
        try:
            visit_ids_for_group = [int(vid) for vid in grouped_visit_images]
            visits = data_access.get_visits_batch(visit_ids_for_group, opp_for_group)

            xform_id_by_visit = {str(v["id"]): v.get("xform_id") for v in visits}
            link_id_by_visit = {str(v["id"]): (v.get("user_id"), v.get("user_visit_id")) for v in visits}

            hq_link_base = resolve_hq_link_base(access_token, opp_for_group)
            org_slug = resolve_org_slug(access_token, opp_for_group, request=request)

            for visit_id_str, images in grouped_visit_images.items():
                form_url = build_hq_form_url(hq_link_base, xform_id_by_visit.get(visit_id_str))
                user_id, user_visit_id = link_id_by_visit.get(visit_id_str, (None, None))
                connect_visit_url = build_connect_visit_url(
                    connect_url, org_slug, opp_for_group, user_id, user_visit_id
                )
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
        except Exception:
            logger.exception("[Audit] Failed to resolve URLs for opportunity %s", opp_for_group)
            continue
    return urls_by_blob
