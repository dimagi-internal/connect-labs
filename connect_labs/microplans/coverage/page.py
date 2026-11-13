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
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller
from connect_labs.labsdata import service, spaces
from connect_labs.labsdata.engine import QueryError
from connect_labs.labsdata.scope import ScopeError
from connect_labs.microplans.coverage import check, read
from connect_labs.microplans.coverage.request import BadRequest, build_spec

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


def run(request, body: dict) -> dict:
    """Run the check as the person asking and shape the answer for the page. Reads what ``load`` committed."""
    spec, labels, opp_ids = build_spec(body)
    caller = _caller(request)
    space = spaces.from_request(caller, opportunity_ids=opp_ids)
    rows = read.read_households(caller, space, spec, agent=False)
    result = check.run_check(rows, spec)
    for area in result["areas"]:
        area["district"] = labels.get(area["group"])
    result["total"] = check.total_row(result)
    result["rollup"] = check.rollup(result, labels) if labels else []
    result["opportunities"] = [o.id for o in space.selection.opportunities]
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
