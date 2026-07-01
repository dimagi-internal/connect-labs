"""View-layer helpers for the microplans app.

Small, reusable utilities pulled out of ``views.py`` (URL builders, the queued-task
envelope, opportunity filtering, the new-plan map seed, ADM1/State lookup, and the
Celery mutation enqueue). These are leaf functions: they don't import ``views``, so
``views.py`` imports them — keeping the view module focused on request handling.
"""

import json
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


def _float_or_none(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _sd_urls(opp_id=123):
    """Service-delivery layer endpoints for the unified page.

    opp_id is a routing placeholder (same trick as the area-definition URLs): the
    POST body's ``opp_ids`` drive the actual fetch and are validated against the
    user's accessible opportunities, so the URL's opp_id is irrelevant.
    """
    from django.urls import reverse

    return {
        "preview_service_delivery_url": reverse("microplans:preview_service_delivery", args=[opp_id]),
        "service_delivery_pipelines_url": reverse("microplans:service_delivery_pipelines", args=[opp_id]),
        "derive_boundary_url": reverse("microplans:derive_boundary", args=[opp_id]),
    }


# Name fragments that mark a test / QA / throwaway opportunity. Matched
# case-insensitively as substrings, so "[TO DELETE] Foo", "DELETE-ME 3",
# "Bar [TEST]", "QA demo" are all excluded from the delivery-points picker.
_JUNK_OPP_MARKERS = ("to delete", "delete-me", "deleteme", "[test", "[demo", "[qa", "test opp", "dummy")


def _filter_demo_junk_opps(opps: list) -> list:
    """Drop obvious test/QA/throwaway opportunities from a picker list.

    The service-delivery picker should show real delivery footprints, not the
    test junk that accumulates in a shared account. Conservative substring match
    on the opportunity name; anything unnamed is kept (we don't guess)."""
    out = []
    for o in opps:
        name = (o.get("name") or "").lower()
        if name and any(m in name for m in _JUNK_OPP_MARKERS):
            continue
        out.append(o)
    return out


def _program_map_seed(plans) -> dict | None:
    """Where to open the new-plan map so the Boundaries layer actually loads.

    The new-plan map otherwise opens zoomed out to a whole country with nothing
    drawn — so no boundary is clickable AND the country never auto-detects (the
    global Overture source needs an iso, which is itself inferred from loaded
    boundaries: a cold-start chicken-and-egg). Seeding the map over the program's
    existing footprint breaks it: boundaries load there, the country detects, and
    the by-name search starts working.

    Scans the program's plans newest-first for an admin-area country (carried on
    ``input_areas``) and a centroid (mean of work-area centroids). Returns
    ``{iso, lng, lat, zoom}`` or ``None`` when the program has no usable footprint
    yet (a brand-new program — the map keeps its default and the user pans)."""
    for p in sorted(plans, key=lambda x: x.data.get("created_at", ""), reverse=True):
        data = p.data
        iso = ""
        for area in data.get("input_areas") or []:
            if isinstance(area, dict) and area.get("country"):
                iso = str(area["country"])
                break
        pts = [w.get("centroid") for w in (data.get("work_areas") or []) if w.get("centroid")]
        if pts:
            lng = sum(pt[0] for pt in pts) / len(pts)
            lat = sum(pt[1] for pt in pts) / len(pts)
            return {"iso": iso, "lng": round(lng, 5), "lat": round(lat, 5), "zoom": 10}
        if iso:
            return {"iso": iso, "lng": None, "lat": None, "zoom": None}
    return None


def _queued(task):
    """202 envelope for an enqueued generation task: id + where to poll it."""
    from django.urls import reverse

    return JsonResponse(
        {
            "status": "queued",
            "task_id": task.id,
            "poll_url": reverse("microplans:preview_status", args=[task.id]),
        },
        status=202,
    )


def _adm1_state_for(input_areas, hulls) -> str:
    """Best-effort ADM1 (State) name for a plan whose creator picked an LGA-level
    boundary but supplied no State.

    Connect's WorkAreaCSVImporter REQUIRES a non-empty State, but the admin-boundary
    picker's search path resolves an ADM2 (LGA) area that carries no parent State —
    so a plan created by clicking "Kano Municipal" lands with state="" and its export
    is rejected ("Connect needs State"). Here we find the ADM1 admin boundary whose
    polygon contains the plan geometry's centroid and use its name. Returns "" if
    nothing matches (caller leaves State empty — same warn-and-block as before, no
    regression). NIGERIA/labs note: admin_level 1 == State in the GeoPoDe layer.
    """
    from django.contrib.gis.geos import GEOSGeometry

    try:
        from connect_labs.labs.admin_boundaries.models import AdminBoundary
    except Exception:  # noqa: BLE001
        return ""

    candidates = list(input_areas or [])
    if isinstance(hulls, dict):
        candidates += hulls.get("features") or []
    pt = None
    for c in candidates:
        g = c.get("geometry") if isinstance(c, dict) and "geometry" in c else c
        if not g:
            continue
        try:
            pt = GEOSGeometry(json.dumps(g), srid=4326).centroid
            break
        except Exception:  # noqa: BLE001
            continue
    if pt is None:
        return ""
    try:
        adm1 = AdminBoundary.objects.filter(admin_level=1, geometry__contains=pt).first()
    except Exception:  # noqa: BLE001
        logger.exception("microplans ADM1 State derivation failed")
        return ""
    return (adm1.name if adm1 else "")[:255]


def _plan_scoped_urls(program_id, plan_id) -> dict:
    """The per-plan endpoint URLs the review page binds to. Returned on create so
    the client can adopt them in place (create-in-place) without a page reload."""
    from django.urls import reverse

    return {
        "review": reverse("microplans:program_review", args=[program_id, plan_id]),
        "plan": reverse("microplans:program_plan", args=[program_id, plan_id]),
        "regenerate": reverse("microplans:program_plan_regenerate", args=[program_id, plan_id]),
        "footprints": reverse("microplans:program_plan_footprints", args=[program_id, plan_id]),
        "regroup": reverse("microplans:program_plan_regroup", args=[program_id, plan_id]),
        "reassign": reverse("microplans:program_plan_reassign", args=[program_id, plan_id]),
        "csv": reverse("microplans:program_plan_csv", args=[program_id, plan_id]),
        "edit": reverse("microplans:program_plan_edit", args=[program_id, plan_id]),
    }


def _enqueue_plan_mutation(request, op, program_id, plan_id, params):
    """Offload a heavy plan mutation (regroup/reassign/regenerate) to Celery and
    return 202 + a pollable task id. The client polls ``microplans:preview_status``
    and reads the task's result (``plan_to_json`` on success, or a
    ``{status: conflict|error}`` envelope). The worker writes via the LabsRecord
    API, so it needs the caller's OAuth token (same pattern as bulk-create)."""
    from django.urls import reverse

    from connect_labs.microplans.tasks import apply_plan_mutation_task

    access_token = (request.session.get("labs_oauth") or {}).get("access_token")
    if not access_token:
        return JsonResponse(
            {"status": "error", "detail": "Not authenticated with Connect — sign in again."}, status=401
        )
    task = apply_plan_mutation_task.delay(
        op, int(program_id), int(plan_id), params, request.user.get_username(), access_token
    )
    return JsonResponse(
        {"status": "queued", "task_id": task.id, "poll_url": reverse("microplans:preview_status", args=[task.id])},
        status=202,
    )
