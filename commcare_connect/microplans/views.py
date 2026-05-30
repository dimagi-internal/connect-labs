"""Views for the Rooftop Surveys setup flow (Stage A: area → frame → push)."""

import csv
import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

logger = logging.getLogger(__name__)


class _LabsContextSyncMixin:
    """Sync the labs context picker pill to the program_id / opp_id in this view's
    URL kwargs. Without it, the picker stays on "Select Context" on every
    microplans page because the labs middleware reads context from query params
    only — but these routes use path params (e.g. /microplans/program/135/).
    Result: users had to manually pick the program in the context picker even
    though the URL already says which program they're on. Now the picker just
    reflects the page on first load.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            from commcare_connect.labs.context import save_context_to_session, validate_context_access

            ctx = {}
            if "program_id" in kwargs:
                ctx["program_id"] = int(kwargs["program_id"])
            elif "opp_id" in kwargs:
                ctx["opportunity_id"] = int(kwargs["opp_id"])
            if ctx:
                validated = validate_context_access(request, ctx) or {}
                # If the cached OAuth org_data didn't include this program/opp,
                # validate gives us the id but no display object — synthesize a
                # minimal one so the picker pill at least shows the URL's program
                # rather than leaving the previous selection stuck on screen.
                if "program_id" in ctx and "program" not in validated:
                    validated["program_id"] = ctx["program_id"]
                    validated["program"] = {"id": ctx["program_id"], "name": f"Program #{ctx['program_id']}"}
                if "opportunity_id" in ctx and "opportunity" not in validated:
                    validated["opportunity_id"] = ctx["opportunity_id"]
                    validated["opportunity"] = {
                        "id": ctx["opportunity_id"],
                        "name": f"Opportunity #{ctx['opportunity_id']}",
                    }
                request.labs_context = validated
                save_context_to_session(request, ctx)
        return super().dispatch(request, *args, **kwargs)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SetupView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Area picker → frame config → preview → push-to-Connect.

    Stage A entry point. Renders a Mapbox GL JS map (matching Connect's
    microplanning display tooling) with draw controls for the intervention
    and optional comparison polygons.
    """

    template_name = "microplans/setup.html"

    def get_context_data(self, **kwargs):
        from commcare_connect.labs.context import get_org_data

        context = super().get_context_data(**kwargs)
        opp_id = kwargs.get("opp_id")
        context["opp_id"] = opp_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        if not settings.MAPBOX_TOKEN:
            context["error"] = "MAPBOX_TOKEN is not configured; the map cannot load."

        # Opportunities the user can overlay service-delivery data for; the
        # current opp floats to the top so it's the default selection.
        opps = []
        try:
            for o in get_org_data(self.request).get("opportunities", []):
                if o.get("id") is not None:
                    opps.append({"id": int(o["id"]), "name": o.get("name") or f"Opportunity #{o['id']}"})
        except Exception:  # noqa: BLE001 — context is best-effort, never break setup
            logger.exception("microplans setup: failed to load opportunity list")
        opps.sort(key=lambda o: (int(o["id"]) != int(opp_id) if opp_id else False, o["name"].lower()))
        context["sd_opps"] = opps
        return context


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ReviewView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """LLO review/edit page for a materialised plan.

    Renders the work areas on a map + an editable list (exclude, resize, regroup,
    reassign, bulk-exclude) backed by the plan edit endpoints. Planning-phase only.
    """

    template_name = "microplans/review.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        opp_id, plan_id = kwargs.get("opp_id"), kwargs.get("plan_id")
        context["opp_id"] = opp_id
        context["plan_id"] = plan_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        context["plan_url"] = reverse("microplans:plan", args=[opp_id, plan_id])
        context["edit_url"] = reverse("microplans:plan_edit", args=[opp_id, plan_id])
        context["csv_url"] = reverse("microplans:plan_csv", args=[opp_id, plan_id])
        context["compare_url"] = reverse("microplans:compare", args=[opp_id]) + f"?plans={plan_id}"
        return context


class PreviewFrameView(LoginRequiredMixin, View):
    """Fetch building footprints for the drawn area(s) and run the sampling preview.

    Synchronous: the first fetch for an area hits Overture S3 (~tens of seconds);
    subsequent runs are served from cache. Returns the sampled pins + cluster
    hulls as GeoJSON plus per-arm stats for the map to render.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.sampling.frame import FrameConfig, generate_frame

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config = FrameConfig.from_payload(payload.get("config", {}))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            result = generate_frame(areas, config)
        except ValueError as e:
            # Expected, actionable user errors (e.g. area too large) — safe to surface.
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            # Unexpected — log server-side, return a generic message (no internal leak).
            logger.exception("rooftop preview_frame failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Frame generation failed. Check server logs."},
                status=502,
            )

        return JsonResponse(
            {
                "status": "ok",
                "pins": result.pins_geojson,
                "hulls": result.hulls_geojson,
                "stats": result.stats,
            }
        )


class PreviewCoverageView(LoginRequiredMixin, View):
    """Coverage-mode preview: balanced/grid clusters → cluster polygons.

    Same footprint fetch as sampling, but instead of PPS-sampling pins it returns
    the cluster hulls (each = one WorkArea covering every household within it).
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config = CoverageConfig.from_payload(payload.get("config", {}))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            result = generate_coverage_frame(areas, config)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans preview_coverage failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Coverage generation failed. Check server logs."},
                status=502,
            )

        return JsonResponse({"status": "ok", "areas": result.areas_geojson, "stats": result.stats})


class PreviewFootprintsView(LoginRequiredMixin, View):
    """Return building footprints (as point features) inside the drawn area(s).

    Used by the setup-page "Show building footprints" toggle so the user can
    sanity-check what's in their area before generating cells. Reuses the same
    PG-cached `fetch_buildings` path; cheap on a warm cache.
    """

    def post(self, request, opp_id):
        from shapely.ops import unary_union

        from commcare_connect.microplans.core.area_input import resolve_area
        from commcare_connect.microplans.core.footprints import fetch_buildings

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            geom = unary_union([resolve_area(a) for a in areas])
            df = fetch_buildings(geom, min_confidence=None)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans preview_footprints failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": "Footprints fetch failed."}, status=502)

        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                "properties": {},
            }
            for _, row in df.iterrows()
        ]
        return JsonResponse(
            {"status": "ok", "footprints": {"type": "FeatureCollection", "features": features}, "count": len(features)}
        )


class SaveFrameView(LoginRequiredMixin, View):
    """Persist a previewed frame (area + pins) as LabsRecords for this opp.

    The client posts the already-generated pins/hulls/stats from the preview so
    we don't recompute. Returns the new record ids.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            # Coverage stores its cluster polygons in `hulls` (pins stays empty).
            if mode == "coverage":
                pins = empty_fc
                hulls = payload.get("coverage_areas") or payload.get("hulls", empty_fc)
            else:
                pins = payload["pins"]
                hulls = payload.get("hulls", empty_fc)
            stats = payload.get("stats", [])
            config = payload.get("config", {})
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            area_record = da.save_area(areas=areas, config=config, name=payload.get("name", ""), mode=mode)
            frame_record = da.save_frame(area_record_id=area_record.id, pins=pins, hulls=hulls, stats=stats, mode=mode)
        except Exception:  # noqa: BLE001
            logger.exception("microplans save_frame failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Saving the frame failed. Check server logs."},
                status=502,
            )

        return JsonResponse({"status": "ok", "area_record_id": area_record.id, "frame_record_id": frame_record.id})


class CountriesView(LoginRequiredMixin, View):
    """List ISO countries for the area picker, flagging those with bespoke data.

    `bespoke` = countries that have curated `labs.admin_boundaries` polygons
    loaded (so the resolver will prefer them over Overture). The UI can badge
    these as higher-quality.
    """

    def get(self, request):
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary
        from commcare_connect.microplans.core import iso

        # .order_by() clears AdminBoundary's Meta.ordering — without it Django adds
        # the ordering columns to the SELECT, so .distinct() dedupes whole rows
        # (12k) instead of iso_code (returning one entry per country).
        bespoke = sorted(AdminBoundary.objects.order_by().values_list("iso_code", flat=True).distinct())
        return JsonResponse({"status": "ok", "countries": iso.all_countries(), "bespoke": bespoke})


class AdminAreasView(LoginRequiredMixin, View):
    """List admin areas for a country/level via the boundary resolver.

    POST body: {country, level, q?, parent?, source?}. `parent` is an AdminArea
    (as returned by a previous call) used to narrow children; `source` lets the
    user pick a specific boundary source (falls back to the default if it can't
    serve the level). Response reports the source used + the pickable sources so
    the UI can offer a source dropdown.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.admin_boundaries import SOURCE_LABELS, AdminArea, get_resolver

        try:
            payload = json.loads(request.body)
            country = payload["country"]
            level = int(payload["level"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        parent = AdminArea.from_json(payload["parent"]) if isinstance(payload.get("parent"), dict) else None
        prefer = payload.get("source") or None
        resolver = get_resolver()
        try:
            areas = resolver.list_areas(
                country,
                level,
                name_contains=(payload.get("q") or None),
                parent=parent,
                source=prefer,
                limit=int(payload.get("limit", 500)),
            )
            used = resolver.source_for(country, level, prefer=prefer).name
            available = resolver.sources_for(country, level)
        except Exception:  # noqa: BLE001
            logger.exception("microplans admin areas lookup failed (country=%s level=%s)", country, level)
            return JsonResponse(
                {"status": "error", "detail": "Boundary lookup failed. Check server logs."}, status=502
            )

        return JsonResponse(
            {
                "status": "ok",
                "source": used,
                "available_sources": available,
                "source_labels": {n: SOURCE_LABELS.get(n, n) for n in available},
                "areas": [a.to_json() for a in areas],
            }
        )


class AdminAreaGeometryView(LoginRequiredMixin, View):
    """Resolve one chosen admin area to its GeoJSON geometry (the sampling boundary)."""

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.admin_boundaries import AdminArea, get_resolver

        try:
            area = AdminArea.from_json(json.loads(request.body)["area"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            geom = get_resolver().geometry(area)
        except Exception:  # noqa: BLE001
            logger.exception("microplans admin area geometry failed (%s/%s)", area.country, area.name)
            return JsonResponse({"status": "error", "detail": "Boundary geometry lookup failed."}, status=502)

        if not geom:
            return JsonResponse({"status": "error", "detail": "Area not found."}, status=404)
        return JsonResponse({"status": "ok", "name": area.name, "geometry": geom})


class DownloadWorkAreaCSVView(LoginRequiredMixin, View):
    """Render the previewed frame as a Connect microplanning work-area import CSV.

    Lets a frame be pushed to Connect *today* via the existing org-admin web
    importer (no prod write API needed). Sampling: each pin → one tiny WorkArea.
    Coverage: each cluster polygon → one WorkArea (visit every household).
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.workarea import build_coverage_work_areas, build_work_areas, to_csv_rows

        try:
            payload = json.loads(request.body)
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            geojson = payload["coverage_areas"] if mode == "coverage" else payload["pins"]
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        builder = build_coverage_work_areas if mode == "coverage" else build_work_areas
        rows = to_csv_rows(
            builder(
                geojson,
                ward_for_arm=payload.get("ward_for_arm"),
                lga=payload.get("lga", ""),
                state=payload.get("state", ""),
            )
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="rooftop_work_areas_opp{opp_id}.csv"'
        if rows:
            writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return response


# --- Planning-phase plan review/edit (the LLO validation layer; pre-upload) ---


def _plan_json(plan):
    """Serialize a plan for the review UI: work areas + headline summary."""
    from commcare_connect.microplans.core import plan as plan_lib

    return {
        "status": "ok",
        "plan_id": plan.id,
        "mode": plan.mode,
        "work_areas": plan.work_areas,
        "summary": plan_lib.summarize(plan.work_areas),
        "kpis": plan_lib.plan_kpis(plan.work_areas),
    }


class MaterializePlanView(LoginRequiredMixin, View):
    """Create an editable plan from a saved frame (one work area per cluster/pin)."""

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        try:
            payload = json.loads(request.body)
            frame_record_id = int(payload["frame_record_id"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        from commcare_connect.microplans.core.models import RooftopFrameRecord

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            frame = da.labs_api.get_record_by_id(frame_record_id, model_class=RooftopFrameRecord)
            plan = da.materialize_plan(frame, name=payload.get("name", ""))
        except Exception:  # noqa: BLE001
            logger.exception("microplans materialize_plan failed (opp=%s frame=%s)", opp_id, frame_record_id)
            return JsonResponse(
                {"status": "error", "detail": "Could not build the plan. Check server logs."}, status=502
            )
        return JsonResponse(_plan_json(plan))


class PlanView(LoginRequiredMixin, View):
    """Load a plan (work areas + summary) for review."""

    def get(self, request, opp_id, plan_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            logger.exception("microplans get_plan failed (opp=%s plan=%s)", opp_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        return JsonResponse(_plan_json(plan))


class PlanEditView(LoginRequiredMixin, View):
    """Apply one LLO edit (exclude/unexclude/resize/regroup/reassign) to a work area.

    Supports a single `wa_id` or a list `wa_ids` (bulk, e.g. lasso-exclude). The
    action + its params are recorded as a phase=planning audit event.
    """

    def post(self, request, opp_id, plan_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess
        from commcare_connect.microplans.core.plan import ACTIONS

        try:
            payload = json.loads(request.body)
            action = payload["action"]
            wa_ids = payload.get("wa_ids") or ([payload["wa_id"]] if payload.get("wa_id") else [])
            if action not in ACTIONS:
                raise ValueError(f"unknown action {action}")
            if not wa_ids:
                raise ValueError("no work area specified")
            if len(wa_ids) > 5000:  # bound the batch (a plan never has this many areas)
                raise ValueError("too many work areas in one request")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        params = {k: v for k, v in payload.items() if k not in ("action", "wa_id", "wa_ids")}
        actor = request.user.get_username()
        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            plan = da.apply_plan_edits(int(plan_id), [str(w) for w in wa_ids], action, params, actor)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans plan edit failed (opp=%s plan=%s)", opp_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Edit failed. Check server logs."}, status=502)
        return JsonResponse(_plan_json(plan))


class PlanCSVView(LoginRequiredMixin, View):
    """Export the edited plan as a Connect work-area import CSV (skips EXCLUDED)."""

    def post(self, request, opp_id, plan_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import RooftopDataAccess
        from commcare_connect.microplans.core.workarea import to_csv_rows

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            payload = {}
        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)

        rows = to_csv_rows(
            plan_lib.to_workarea_payloads(plan.work_areas, lga=payload.get("lga", ""), state=payload.get("state", ""))
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="microplan_work_areas_opp{opp_id}.csv"'
        if rows:
            writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return response


class PlanListView(LoginRequiredMixin, View):
    """List this opportunity's plans (for the comparison picker)."""

    def get(self, request, opp_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            plans = da.list_plans()
        except Exception:  # noqa: BLE001
            logger.exception("microplans list_plans failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": "Could not list plans."}, status=502)
        return JsonResponse(
            {
                "status": "ok",
                "plans": [
                    {
                        "plan_id": p.id,
                        "name": p.name or f"Plan {p.id}",
                        "mode": p.mode,
                        "created_at": p.created_at,
                        "work_areas": len(p.work_areas),
                    }
                    for p in plans
                ],
            }
        )


class ComparePlansView(LoginRequiredMixin, View):
    """Compare N plans' KPIs side by side. GET ?plans=<id>,<id>,... — loads each
    plan and returns its KPIs so the UI can stack them with deltas. The honest
    comparison is the metrics themselves (worst travel, imbalance, coverage); no
    weighted composite — that read as a black box and was removed."""

    def get(self, request, opp_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        try:
            ids = [int(x) for x in (request.GET.get("plans", "")).split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"status": "error", "detail": "plans must be comma-separated ids"}, status=400)
        if not ids:
            return JsonResponse({"status": "error", "detail": "no plans selected"}, status=400)

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        entries = []
        for pid in ids:
            try:
                p = da.get_plan(pid)
            except Exception:  # noqa: BLE001
                logger.exception("microplans compare: plan %s load failed", pid)
                continue
            entries.append(
                {
                    "plan_id": p.id,
                    "name": p.name or f"Plan {p.id}",
                    "mode": p.mode,
                    "created_at": p.created_at,
                    "kpis": plan_lib.plan_kpis(p.work_areas),
                }
            )
        if not entries:
            return JsonResponse({"status": "error", "detail": "no plans found."}, status=404)
        return JsonResponse({"status": "ok", "plans": entries})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ComparePageView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Plan comparison page: pick plans, see KPIs stacked with deltas + composite."""

    template_name = "microplans/compare.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        opp_id = kwargs.get("opp_id")
        context["opp_id"] = opp_id
        context["scope_label"] = f"Opportunity #{opp_id}"
        context["list_url"] = reverse("microplans:plan_list", args=[opp_id])
        context["compare_url"] = reverse("microplans:plan_compare", args=[opp_id])
        return context


# ============================================================================
# Program layer: a program owns a portfolio of candidate plans + plan groups.
# Plans are program-scoped; an opportunity is bound only at Deploy.
#
# Authorization: ProgramPlanDataAccess sends program_id on every read/write, so
# the production LabsRecord API enforces program membership (a non-member gets an
# empty/404 result — see CLAUDE.md "Permission Model"). Labs has no local program
# membership table to check against, so this is the only auth boundary; the HTML
# shell views render for any logged-in user, but their data fetches return nothing
# unless the user is a member. No program data leaks from rendering the shell.
# ============================================================================


def _plan_summary_row(plan):
    """Compact per-plan row for the workspace (status, region, headline KPIs)."""
    from commcare_connect.microplans.core import plan as plan_lib

    k = plan_lib.plan_kpis(plan.work_areas)
    # Travel/balance KPIs are only meaningful once areas are split across workers.
    # Pre-assignment everything collapses to one territory, so flag it so the UI can
    # show the area count instead of a misleading "1 worker / whole-region travel".
    assigned = k["dimension"] == "worker"
    return {
        "plan_id": plan.id,
        "name": plan.name or f"Plan {plan.id}",
        "region": plan.region,
        "mode": plan.mode,
        "status": plan.status,
        "status_label": plan_lib.PLAN_STATUS_LABELS.get(plan.status, plan.status),
        "opportunity_id": plan.data.get("opportunity_id"),
        "assigned": assigned,
        "work_areas": len(plan.work_areas),
        "max_spread_km": k["plan"]["max_spread_km"],
        "coverage_pct": k["coverage_pct"],
        "excluded": k["excluded"]["count"],
        "territory_count": k["plan"]["territory_count"],
        "created_at": plan.created_at,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramWorkspaceView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program workspace: the portfolio of candidate plans + plan groups."""

    template_name = "microplans/program_workspace.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["program_id"] = kwargs.get("program_id")
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        return context


class ProgramPlansAPIView(LoginRequiredMixin, View):
    """JSON: the program's plans (+ headline KPIs) and groups, for the workspace."""

    def get(self, request, program_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plans = [_plan_summary_row(p) for p in da.list_plans()]
            groups = [
                {
                    "group_id": g.id,
                    "name": g.name,
                    "plan_ids": g.plan_ids,
                    "offered_to": g.offered_to,
                    "shared": g.shared,
                }
                for g in da.list_groups()
            ]
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plans failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not load program plans."}, status=502)
        return JsonResponse(
            {
                "status": "ok",
                "plans": plans,
                "groups": groups,
                "statuses": list(plan_lib.PLAN_STATUSES),
                "status_labels": plan_lib.PLAN_STATUS_LABELS,
                "transitions": {k: sorted(v) for k, v in plan_lib.PLAN_TRANSITIONS.items()},
            }
        )


class ProgramCreatePlanView(LoginRequiredMixin, View):
    """Create a Draft plan in the program from a generated frame (region + geometry)."""

    def post(self, request, program_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            region = str(payload.get("region", "")).strip()[:255]
            name = str(payload.get("name", "") or region or "Untitled plan").strip()[:255]
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            pins = payload.get("pins") or empty_fc
            hulls = (payload.get("coverage_areas") or payload.get("hulls")) or empty_fc
            # Optional: the original draw/admin/pin areas from setup. Stored on the
            # plan so the review-page footprints overlay can re-fetch by THAT geometry
            # (which is already PG-cached from generation) instead of by the unioned
            # cells (a different hash → expensive cold Overture query).
            input_areas = payload.get("input_areas") or []
            if not isinstance(input_areas, list):
                input_areas = []
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.create_plan(region=region, name=name, mode=mode, pins=pins, hulls=hulls, input_areas=input_areas)
        except Exception:  # noqa: BLE001
            logger.exception("microplans create_plan failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not create the plan."}, status=502)
        return JsonResponse({"status": "ok", "plan_id": plan.id})


class ProgramPlanTransitionView(LoginRequiredMixin, View):
    """Advance a plan's lifecycle status (Deploy binds the live opportunity_id)."""

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            payload = json.loads(request.body)
            to = payload["to"]
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.transition_plan(
                int(plan_id), to, request.user.get_username(), opportunity_id=payload.get("opportunity_id")
            )
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans transition failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Transition failed."}, status=502)
        return JsonResponse(
            {
                "status": "ok",
                "plan_id": plan.id,
                "plan_status": plan.status,
                "opportunity_id": plan.data.get("opportunity_id"),
            }
        )


class ProgramPlanDeleteView(LoginRequiredMixin, View):
    """Hard-delete a plan record. POST → 204. Used for wiping sample data; the
    safer default for normal lifecycle is the Archive status transition."""

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_plan(plan_id)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_plan failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "plan_id": int(plan_id)})


class ProgramGroupDeleteView(LoginRequiredMixin, View):
    """Hard-delete a plan group record (sample-data wipe)."""

    def post(self, request, program_id, group_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_group(group_id)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "group_id": int(group_id)})


class ProgramGroupsAPIView(LoginRequiredMixin, View):
    """Create a plan group (a shareable subset offered to an LLO)."""

    def post(self, request, program_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            payload = json.loads(request.body)
            name = str(payload["name"]).strip()[:255]
            plan_ids = [int(p) for p in payload.get("plan_ids", [])]
            offered_to = str(payload.get("offered_to", "")).strip()[:255]
            if not name or not plan_ids:
                raise ValueError("name and at least one plan are required")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.create_group(name=name, plan_ids=plan_ids, offered_to=offered_to)
        except Exception:  # noqa: BLE001
            logger.exception("microplans create_group failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not create the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id})


class ProgramGroupUpdateView(LoginRequiredMixin, View):
    """Update a plan group (rename, change plans, set offered_to, share)."""

    def post(self, request, program_id, group_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.update_group(
                int(group_id),
                name=payload.get("name"),
                plan_ids=payload.get("plan_ids"),
                offered_to=payload.get("offered_to"),
                shared=payload.get("shared"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("microplans update_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Could not update the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id, "shared": group.shared})


class ProgramGroupShareView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """LLO-facing page for a plan group: its subset of plans + their KPIs."""

    template_name = "microplans/group_share.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        group_id = kwargs.get("group_id")
        context["program_id"] = program_id
        context["group_id"] = group_id
        da = ProgramPlanDataAccess(program_id, request=self.request)
        try:
            group = da.get_group(int(group_id))
            plans_by_id = {p.id: p for p in da.list_plans()}
            # Preserve the group's plan order — no synthetic "fit score" ranking.
            # The LLO reads the actual metrics (worst travel, imbalance, coverage)
            # and decides for themselves.
            entries = []
            for pid in group.plan_ids:
                p = plans_by_id.get(pid)
                if p is None:
                    continue
                kpis = plan_lib.plan_kpis(p.work_areas)
                entries.append(
                    {
                        "plan_id": pid,
                        "name": p.name,
                        "region": p.region,
                        "kpis": kpis,
                        "assigned": kpis["dimension"] == "worker",
                        "work_areas": len(p.work_areas),
                        "status": p.status,
                        "status_label": plan_lib.PLAN_STATUS_LABELS.get(p.status, p.status),
                        "review_url": reverse("microplans:program_review", args=[program_id, pid]),
                    }
                )
            context["group_name"] = group.name
            context["offered_to"] = group.offered_to
            context["entries"] = entries
        except Exception:  # noqa: BLE001
            logger.exception("microplans group share failed (program=%s group=%s)", program_id, group_id)
            context["error"] = "Could not load the group."
        return context


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramReviewView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program-scoped per-plan review page (reuses review.html via context URLs)."""

    template_name = "microplans/review.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id, plan_id = kwargs.get("program_id"), kwargs.get("plan_id")
        context["program_id"] = program_id
        context["plan_id"] = plan_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        context["plan_url"] = reverse("microplans:program_plan", args=[program_id, plan_id])
        context["edit_url"] = reverse("microplans:program_plan_edit", args=[program_id, plan_id])
        context["csv_url"] = reverse("microplans:program_plan_csv", args=[program_id, plan_id])
        context["footprints_url"] = reverse("microplans:program_plan_footprints", args=[program_id, plan_id])
        context["compare_url"] = reverse("microplans:program_compare_page", args=[program_id]) + f"?plans={plan_id}"
        context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


class ProgramPlanView(LoginRequiredMixin, View):
    def get(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan get failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        return JsonResponse(_plan_json(plan))


class ProgramPlanEditView(LoginRequiredMixin, View):
    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.plan import ACTIONS

        try:
            payload = json.loads(request.body)
            action = payload["action"]
            wa_ids = payload.get("wa_ids") or ([payload["wa_id"]] if payload.get("wa_id") else [])
            if action not in ACTIONS:
                raise ValueError(f"unknown action {action}")
            if not wa_ids:
                raise ValueError("no work area specified")
            if len(wa_ids) > 5000:
                raise ValueError("too many work areas in one request")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        params = {k: v for k, v in payload.items() if k not in ("action", "wa_id", "wa_ids")}
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.apply_plan_edits(
                int(plan_id), [str(w) for w in wa_ids], action, params, request.user.get_username()
            )
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan edit failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Edit failed."}, status=502)
        return JsonResponse(_plan_json(plan))


class ProgramPlanFootprintsView(LoginRequiredMixin, View):
    """Building footprints (polygons + centroids) inside a saved plan's area.

    Used by the review-page 'Show footprints' toggle. Prefers the plan's stored
    `input_areas` (the original ward boundary from setup, already PG-cached from
    generation) over re-deriving from cell unions (which would be a different
    cache hash → expensive cold Overture query)."""

    def get(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.footprints import fetch_buildings

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)

        area = _plan_lookup_area(plan)
        if area is None:
            return JsonResponse(
                {"status": "ok", "footprints": {"type": "FeatureCollection", "features": []}, "count": 0}
            )
        try:
            df = fetch_buildings(area, min_confidence=None, with_geom=True)
        except Exception:  # noqa: BLE001
            logger.exception("plan footprints fetch failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Footprints fetch failed."}, status=502)

        # Prefer the stored polygon when present; fall back to a centroid Point
        # for rows from before the polygon-on-cache rollout.
        features = []
        for _, row in df.iterrows():
            geom_json = row.get("geom_json") if hasattr(row, "get") else None
            if isinstance(geom_json, dict) and geom_json.get("type"):
                features.append({"type": "Feature", "geometry": geom_json, "properties": {}})
            else:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                        "properties": {},
                    }
                )
        return JsonResponse(
            {"status": "ok", "footprints": {"type": "FeatureCollection", "features": features}, "count": len(features)}
        )


def _plan_lookup_area(plan):
    """Best geometry to use when re-querying footprints for a plan.

    Order of preference:
      1. The plan's stored ``input_areas`` (the ward/draw/pin payload from setup;
         already PG-cached as a whole from generation → instant hit).
      2. The union of cell geometries (works but a different cache hash → cold
         miss the first time per plan).
    """
    from shapely.ops import unary_union

    from commcare_connect.microplans.core.area_input import resolve_area

    inputs = plan.data.get("input_areas") or []
    if inputs:
        try:
            return unary_union([resolve_area(a) for a in inputs])
        except Exception:  # noqa: BLE001
            logger.exception("plan footprints: input_areas resolve failed; falling back to cells")

    from shapely.geometry import shape

    geoms = []
    for w in plan.work_areas:
        g = w.get("geometry")
        if not g:
            continue
        try:
            geoms.append(shape(g))
        except Exception:  # noqa: BLE001
            continue
    return unary_union(geoms) if geoms else None


class ProgramPlanFootprintsRefreshView(LoginRequiredMixin, View):
    """Force a refresh of the footprint cache for this plan's area.

    Used to upgrade plans cached in the centroid-only era (pre-`geom_json`) to
    the new polygon-aware cache: deletes the matching `FootprintArea` row so the
    next footprints toggle re-fetches from Overture and stores polygons too.
    """

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.footprints import _area_cache_key
        from commcare_connect.microplans.models import FootprintArea

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        area = _plan_lookup_area(plan)
        if area is None:
            return JsonResponse({"status": "ok", "deleted": 0})
        deleted, _ = FootprintArea.objects.filter(area_hash=_area_cache_key(area.wkt)).delete()
        return JsonResponse({"status": "ok", "deleted": deleted})


class ProgramPlanCSVView(LoginRequiredMixin, View):
    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.workarea import to_csv_rows

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            payload = {}
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        rows = to_csv_rows(
            plan_lib.to_workarea_payloads(plan.work_areas, lga=payload.get("lga", ""), state=payload.get("state", ""))
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="microplan_program{program_id}_plan{plan_id}.csv"'
        if rows:
            writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return response


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramSetupView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Create a plan in a program: reuses the setup/generation page, but the save
    step creates a program-scoped Draft plan (rather than an opp-scoped frame).

    The preview/boundary endpoints are stateless generation that ignore the id in
    their URL, so we reuse them by passing opp_id=program_id."""

    template_name = "microplans/setup.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["opp_id"] = program_id  # harmless: preview/boundary views ignore the id
        context["program_id"] = program_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        if not settings.MAPBOX_TOKEN:
            context["error"] = "MAPBOX_TOKEN is not configured; the map cannot load."
        context["create_plan_url"] = reverse("microplans:program_create_plan", args=[program_id])
        context["program_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


class ProgramComparePlansView(LoginRequiredMixin, View):
    """Compare N program plans' KPIs side by side — program-scoped sibling of
    ComparePlansView. Returns per-plan KPIs so the UI can stack them with deltas;
    no composite score (the metrics themselves are the comparison)."""

    def get(self, request, program_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            ids = [int(x) for x in (request.GET.get("plans", "")).split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"status": "error", "detail": "plans must be comma-separated ids"}, status=400)
        if not ids:
            return JsonResponse({"status": "error", "detail": "no plans selected"}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
        entries = []
        for pid in ids:
            try:
                p = da.get_plan(pid)
            except Exception:  # noqa: BLE001
                logger.exception("microplans program compare: plan %s load failed", pid)
                continue
            entries.append(
                {
                    "plan_id": p.id,
                    "name": p.name or f"Plan {p.id}",
                    "region": p.region,
                    "mode": p.mode,
                    "created_at": p.created_at,
                    "kpis": plan_lib.plan_kpis(p.work_areas),
                }
            )
        if not entries:
            return JsonResponse({"status": "error", "detail": "no plans found."}, status=404)
        return JsonResponse({"status": "ok", "plans": entries})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramComparePageView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program-scoped plan comparison page (reuses compare.html via context URLs)."""

    template_name = "microplans/compare.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["scope_label"] = f"Program #{program_id}"
        context["list_url"] = reverse("microplans:program_plans", args=[program_id])
        context["compare_url"] = reverse("microplans:program_plan_compare", args=[program_id])
        context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


# ---------------------------------------------------------------------------
# Service-delivery GPS overlay (reusable layer; see microplans/service_delivery/)
# ---------------------------------------------------------------------------
class _ServiceDeliveryMixin:
    """Restrict posted opp_ids to opportunities the user can actually see."""

    def _allowed_opp_ids(self, request) -> set[int]:
        from commcare_connect.labs.context import get_org_data

        ids = set()
        for o in get_org_data(request).get("opportunities", []):
            if o.get("id") is not None:
                ids.add(int(o["id"]))
        return ids


class PreviewServiceDeliveryView(_ServiceDeliveryMixin, LoginRequiredMixin, View):
    """Fetch service-delivery GPS points for one or more opportunities.

    POST {opp_ids: [int], pipeline_id?: int}. Each opp's points are fetched via
    the ServiceDeliveryPoints provider (default GPS pipeline, or the given
    pipeline_id), colored per opp, and merged into one FeatureCollection. Mirrors
    PreviewFootprintsView's request/response shape so the FE layer code is uniform.
    """

    def post(self, request, opp_id):
        from commcare_connect.labs.context import get_org_data
        from commcare_connect.microplans.service_delivery.points import color_for, fetch_points, points_to_geojson

        try:
            payload = json.loads(request.body)
            opp_ids = [int(x) for x in payload.get("opp_ids") or []]
            pipeline_id = payload.get("pipeline_id")
            pipeline_id = int(pipeline_id) if pipeline_id not in (None, "", "default") else None
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        if not opp_ids:
            opp_ids = [int(opp_id)]
        allowed = self._allowed_opp_ids(request)
        opp_ids = [oid for oid in opp_ids if oid in allowed]
        if not opp_ids:
            return JsonResponse({"status": "error", "detail": "No accessible opportunities selected."}, status=403)

        names = {
            int(o["id"]): (o.get("name") or f"Opportunity #{o['id']}")
            for o in get_org_data(request).get("opportunities", [])
            if o.get("id") is not None
        }

        all_features, layers, auth_error = [], [], None
        for i, oid in enumerate(opp_ids):
            color = color_for(i)
            result = fetch_points(oid, request=request, pipeline_id=pipeline_id)
            if result.get("auth_error") and auth_error is None:
                auth_error = {
                    "auth_error": result["auth_error"],
                    "auth_error_domain": result.get("auth_error_domain"),
                    "auth_authorize_url": result.get("auth_authorize_url"),
                }
            fc = points_to_geojson(result["points"], opportunity_id=oid, color=color)
            all_features.extend(fc["features"])
            layers.append(
                {
                    "opportunity_id": oid,
                    "name": names.get(oid, f"Opportunity #{oid}"),
                    "color": color,
                    "stats": result["stats"],
                    "error": result.get("error"),
                }
            )

        body = {
            "status": "ok",
            "points": {"type": "FeatureCollection", "features": all_features},
            "layers": layers,
            "count": len(all_features),
        }
        if auth_error:
            body.update(auth_error)
        return JsonResponse(body)


class ServiceDeliveryPipelinesView(LoginRequiredMixin, View):
    """List visit-level pipelines the user can pick instead of the default GPS one."""

    def get(self, request, opp_id):
        from commcare_connect.workflow.data_access import PipelineDataAccess

        pipelines = [{"id": "default", "name": "Default — device GPS (any app)"}]
        try:
            pda = PipelineDataAccess(opportunity_id=opp_id, request=request)
            for d in pda.list_definitions(include_shared=True):
                schema = getattr(d, "schema", None) or {}
                if schema.get("terminal_stage", "aggregated") == "visit_level":
                    pipelines.append({"id": d.id, "name": d.name})
        except Exception:  # noqa: BLE001 — dropdown is best-effort
            logger.exception("microplans service_delivery_pipelines failed (opp=%s)", opp_id)
        return JsonResponse({"status": "ok", "pipelines": pipelines})


class DeriveBoundaryView(LoginRequiredMixin, View):
    """Derive a boundary polygon from a posted service-delivery point cloud.

    POST {coords: [[lon,lat],...], method?, concavity?, buffer_m?}. Returns the
    boundary as a GeoJSON Feature so the FE can drop it straight into the draw
    layer / area-input path used by sampling and coverage.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.service_delivery.hull import derive_boundary

        try:
            payload = json.loads(request.body)
            coords = payload["coords"]
            points = [{"lon": float(c[0]), "lat": float(c[1])} for c in coords]
            method = payload.get("method", "concave")
            concavity = float(payload.get("concavity", 0.3))
            buffer_m = float(payload.get("buffer_m", 25.0))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, IndexError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            geometry = derive_boundary(points, method=method, concavity=concavity, buffer_m=buffer_m)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans derive_boundary failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": "Boundary derivation failed."}, status=502)

        return JsonResponse(
            {
                "status": "ok",
                "boundary": {"type": "Feature", "geometry": geometry, "properties": {"source": "service_delivery"}},
                "point_count": len(points),
            }
        )
