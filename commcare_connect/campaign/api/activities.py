import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from commcare_connect.campaign.auth.decorators import require_perm
from commcare_connect.campaign.models import Activity, Campaign
from commcare_connect.campaign.services import activity_actions, audit, serializers


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}


def _campaign():
    return Campaign.objects.order_by("id").first()


@require_perm("activities", "create")
def activity_create(request):
    data = _body(request)
    if not (data.get("name") or "").strip():
        return JsonResponse({"error": "name required"}, status=400)
    c = _campaign()
    a = activity_actions.create_activity(c, data, bool(data.get("sync")))
    audit.record(request, f"Created activity {a.activity_id} ({a.name})", "Activities", c)
    return JsonResponse({"activity": serializers._activity(a)})


@require_perm("activities", "create")
def activity_sync(request, activity_id):
    a = get_object_or_404(Activity, campaign=_campaign(), activity_id=activity_id)
    a = activity_actions.sync_activity(a)
    return JsonResponse({"activity": serializers._activity(a)})
