"""The buildings-reached page: ``/microplans/buildings-reached/``.

A person (or a workflow link) names the opportunities, the facilities and the distance; the page runs
``check.run_check`` over the campaign's recorded households as THAT person, through the Data query path, and shows
per district (or facility) and cycle the share of buildings reached, with the data-quality account and the caveats
that make the number honest. It never shows a household or a point.

The settings ride in the URL, so a result is a link: ``?opps=10184,10185&facilities=D1-01,7.089,13.51,D1;...``.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller
from connect_labs.labsdata import service, spaces
from connect_labs.labsdata.engine import QueryError
from connect_labs.labsdata.scope import ScopeError
from connect_labs.microplans.coverage import check, population, read, upload
from connect_labs.microplans.coverage.request import (
    BadRequest,
    build_spec,
    parse_ids,
    parse_iso,
    parse_targets,
)

logger = logging.getLogger(__name__)


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def load(request, body: dict) -> dict:
    """Load the visits of any named opportunity that has nothing cached, as the person asking.

    This is its own request on purpose: the loader writes the cache inside the request's transaction
    (ATOMIC_REQUESTS), and the Data query reads over its own connection, which cannot see rows that are not
    committed yet. Loading and reading in one request therefore read an empty campaign on a cold cache (seen on the
    live deploy). The page calls this, waits for it to answer (the commit), then calls ``run``."""
    opp_ids = parse_ids(body.get("opps"))
    caller = _caller(request)
    space = spaces.from_request(caller, opportunity_ids=opp_ids)
    loaded = service.warm(caller, space.selection.opportunities)
    failed = [r for r in loaded if r.get("error")]
    if failed:
        raise BadRequest(
            "could not load " + "; ".join(f"opportunity {r['opportunity_id']}: {r['error']}" for r in failed)
        )
    return {"opportunities": [o.id for o in space.selection.opportunities], "loaded": loaded}


def compute_run(rows, spec, labels) -> dict:
    """The buildings lens from household rows, wherever they came from (Connect visits or an uploaded file)."""
    result = check.run_check(rows, spec)
    for area in result["areas"]:
        area["district"] = labels.get(area["group"])
    result["total"] = check.total_row(result)
    result["rollup"] = check.rollup(result, labels) if labels else []
    return result


def compute_population(rows, served, spec, labels, iso, targets) -> dict:
    """The population lens and its denominators panel from household rows and children served."""
    placed, quality = check.placed_households(rows, spec)
    if not len(placed):
        raise BadRequest("no household has a usable point, so there is nothing to place on the grid")
    units, units_error = [], None
    try:
        from connect_labs.labs.admin_boundaries import placement

        units = placement.load_units(placed, iso=iso, levels=(2,))
    except Exception as e:  # noqa: BLE001 -- boundaries are an extra denominator, never a reason to fail the lens
        logger.warning("buildings reached: no admin units for %s", iso, exc_info=True)
        units_error = str(e)[:160]
    x0, y0, x1, y1 = population.bounds_of(placed)
    for u in units:
        ux0, uy0, ux1, uy1 = u.geometry.bounds
        x0, y0, x1, y1 = min(x0, ux0), min(y0, uy0), max(x1, ux1), max(y1, uy1)
    try:
        grid = population.fetch_grid(iso, (x0, y0, x1, y1))
    except ValueError as e:
        raise BadRequest(str(e)) from e
    result = population.population_lens(placed, served, grid, labels)
    result["quality"] = quality
    result["iso"] = iso
    result["denominators"] = population.denominators(placed, served, grid, units, result, labels, targets)
    result["denominators"]["unit_level"] = "ADM2" if units else None
    if units_error or not units:
        result["denominators"]["unit_note"] = units_error or "no admin boundaries found for these households"
    return result


def run(request, body: dict) -> dict:
    """Run the check as the person asking and shape the answer for the page. Reads what ``load`` committed."""
    spec, labels, opp_ids = build_spec(body)
    caller = _caller(request)
    space = spaces.from_request(caller, opportunity_ids=opp_ids)
    rows = read.read_households(caller, space, spec, agent=False)
    result = compute_run(rows, spec, labels)
    result["opportunities"] = [o.id for o in space.selection.opportunities]
    return result


def population_lens(request, body: dict) -> dict:
    """Children served against the population grid, per 1 km cell and district. Reads what ``load`` committed."""
    spec, labels, opp_ids = build_spec(body)
    iso = parse_iso(body.get("iso"))
    caller = _caller(request)
    space = spaces.from_request(caller, opportunity_ids=opp_ids)
    rows = read.read_households(caller, space, spec, agent=False)
    served = check.parse_served(read.read_served(caller, space, spec, agent=False))
    return compute_population(rows, served, spec, labels, iso, parse_targets(body.get("facilities")))


def from_upload(request, body: dict, data: bytes) -> dict:
    """Either lens from an uploaded household CSV: read in memory for this request, never stored."""
    spec, labels, _ = build_spec(body, need_opps=False)
    try:
        rows, served, summary = upload.parse(data)
    except upload.UploadError as e:
        raise BadRequest(str(e)) from e
    if str(body.get("lens")) == "population":
        result = compute_population(
            rows, served, spec, labels, parse_iso(body.get("iso")), parse_targets(body.get("facilities"))
        )
    else:
        result = compute_run(rows, spec, labels)
        result["households_total"] = result.get("households_total", len(rows))
    result["upload"] = summary
    return result


@method_decorator(ensure_csrf_cookie, name="dispatch")
class BuildingsReachedPage(LoginRequiredMixin, TemplateView):
    template_name = "microplans/buildings_reached.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The public Mapbox token the other microplans maps use; without one the page shows its tables only.
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        return context


class BuildingsReachedRun(LoginRequiredMixin, View):
    action = staticmethod(run)

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
            if not isinstance(body, dict):
                raise BadRequest("the body must be a JSON object")
            return JsonResponse(self.action(request, body))
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_json", "message": "Body must be JSON."}, status=400)
        except BadRequest as e:
            return JsonResponse({"error": "bad_request", "message": str(e)}, status=400)
        except read.RowCapExceeded as e:
            return JsonResponse({"error": "row_cap", "message": str(e)}, status=413)
        except ScopeError as e:
            status = 503 if e.code == "scopes_unavailable" else 403 if e.code == "not_held" else 400
            return JsonResponse({"error": e.code, "message": e.message}, status=status)
        except spaces.SpaceNotFound as e:
            return JsonResponse({"error": e.code, "message": e.message}, status=404)
        except QueryError as e:
            return JsonResponse(e.as_dict(), status=400)


class BuildingsReachedLoad(BuildingsReachedRun):
    """Same answers and errors as the run, for the load step."""

    action = staticmethod(load)


class BuildingsReachedPopulation(BuildingsReachedRun):
    """Same answers and errors as the run, for the population lens."""

    action = staticmethod(population_lens)


def _failure(e: Exception) -> JsonResponse:
    """The JSON answer for the errors every endpoint shares."""
    if isinstance(e, BadRequest):
        return JsonResponse({"error": "bad_request", "message": str(e)}, status=400)
    if isinstance(e, read.RowCapExceeded):
        return JsonResponse({"error": "row_cap", "message": str(e)}, status=413)
    raise e


class BuildingsReachedUpload(LoginRequiredMixin, View):
    """Either lens from an uploaded household CSV (multipart: ``file`` plus the same settings as the run)."""

    def post(self, request):
        data_file = request.FILES.get("file")
        if data_file is None:
            return JsonResponse({"error": "bad_request", "message": "Choose a CSV file to upload."}, status=400)
        if data_file.size > upload.MAX_BYTES:
            return JsonResponse(
                {
                    "error": "too_large",
                    "message": f"The file is over {upload.MAX_BYTES // (1024 * 1024)} MB; split it by district.",
                },
                status=413,
            )
        try:
            return JsonResponse(from_upload(request, request.POST.dict(), data_file.read()))
        except (BadRequest, read.RowCapExceeded) as e:
            return _failure(e)


class BuildingsReachedTemplate(LoginRequiredMixin, View):
    """The CSV template: the columns, with example rows marked as such."""

    def get(self, request):
        response = HttpResponse(upload.TEMPLATE, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="buildings-reached-template.csv"'
        return response
