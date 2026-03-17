"""JSON API endpoints for funder_dashboard."""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess

logger = logging.getLogger(__name__)


def _serialize_fund(fund):
    return {"id": fund.id, "data": fund.data}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_funds_list(request):
    try:
        da = FunderDashboardDataAccess(request=request)
    except ValueError:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    if request.method == "GET":
        status = request.GET.get("status")
        funds = da.get_funds(status=status)
        return JsonResponse({"funds": [_serialize_fund(f) for f in funds]})
    else:
        data = json.loads(request.body)
        org_id = str(request.labs_context.get("organization_id", ""))
        data["org_id"] = org_id
        fund = da.create_fund(data)
        return JsonResponse({"fund": _serialize_fund(fund)}, status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_fund_detail(request, pk):
    try:
        da = FunderDashboardDataAccess(request=request)
    except ValueError:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    if request.method == "GET":
        fund = da.get_fund_by_id(pk)
        if not fund:
            return JsonResponse({"error": "Fund not found"}, status=404)
        return JsonResponse({"fund": _serialize_fund(fund)})
    else:
        data = json.loads(request.body)
        fund = da.update_fund(pk, data)
        return JsonResponse({"fund": _serialize_fund(fund)})
