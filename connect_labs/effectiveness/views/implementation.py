"""The Implementations pages: every delivered opportunity valued with the models, and one opportunity in full.

Total cost and people reached come from Pulse; the arithmetic is ``effectiveness.implementation`` and the models.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.labs.chrome import NOT_USED, page_chrome

CONTEXT_NOT_USED = method_decorator(page_chrome(labs_context=NOT_USED), name="dispatch")


@CONTEXT_NOT_USED
class ImplementationOppView(LoginRequiredMixin, View):
    """One opportunity's cost-effectiveness, end to end (`pulse.implementation.opportunity_detail`).

    Where its visits happened, the baseline there and where each figure came from,
    the model's other inputs, its observed people and cost, and the result.
    ``?format=json`` returns the same structure.
    """

    def get(self, request, opp_id):
        from django.http import Http404, JsonResponse

        from connect_labs.effectiveness import implementation

        detail = implementation.opportunity_detail(opp_id)
        if detail is None:
            raise Http404("No such opportunity")
        if request.GET.get("format") == "json":
            return JsonResponse(detail, json_dumps_params={"default": str})
        return render(request, "effectiveness/implementation_opp.html", {"d": detail})


@CONTEXT_NOT_USED
class ImplementationDataView(LoginRequiredMixin, View):
    """The Implementation view as JSON: what-if and filters in the query string, the ranked, rolled-up rows back.

    The page's own parameters (``basis``, ``rank``, ``bar``, ``<model>_<extra|effect|reach>``, ``models``,
    ``country``, ``active``, ``modelled``, ``min_spend``, ``q``); see ``implementation.view``.
    """

    def get(self, request):
        from django.http import JsonResponse

        from connect_labs.effectiveness import implementation

        return JsonResponse(implementation.view(request.GET))


@CONTEXT_NOT_USED
class ImplementationView(LoginRequiredMixin, View):
    """Every opportunity ranked against Connect's cost-effectiveness models.

    Total cost and people reached come from Pulse; the per-country multiples come
    from Connect's CEA models (`effectiveness/implementation.py`). ``?format=json`` and ``?format=csv``
    return the same rows the page draws.
    """

    CSV_COLS = [
        "rank",
        "id",
        "name",
        "org_slug",
        "service_name",
        "model_label",
        "status_text",
        "country_name",
        "lat",
        "lon",
        "total_usd",
        "fixed_usd",
        "reached",
        "reach_basis",
        "works_per_person",
        "cost_per_person",
        "model_cost_per_person",
        "model_x",
        "x",
        "clears_bar",
        "deaths_averted",
        "cost_per_death",
        "active",
        "end_date",
    ]

    def get(self, request):
        import csv

        from django.http import HttpResponse, JsonResponse

        from connect_labs.effectiveness import implementation

        data = implementation.ranking()
        fmt = request.GET.get("format")
        if fmt == "json":
            return JsonResponse(data)
        if fmt == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="connect-cea-ranking.csv"'
            writer = csv.writer(response)
            writer.writerow(self.CSV_COLS)
            for r in data["rows"]:
                writer.writerow(["" if r.get(c) is None else r.get(c) for c in self.CSV_COLS])
            return response
        from django.conf import settings

        # The page draws a view the server worked out (what-if, filters, ranking, roll-up) and asks for a new one
        # when either changes; every row is not sent twice, so the base ranking's rows stay out of the page data.
        page = {k: v for k, v in data.items() if k != "rows"}
        page["countries"] = sorted({r["country_name"] for r in data["rows"]})
        page["view"] = implementation.view(request.GET, data)
        page["view_url"] = reverse("cea:implementation_view")
        return render(
            request,
            "effectiveness/implementation.html",
            {"data": data, "page": page, "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or ""},
        )
