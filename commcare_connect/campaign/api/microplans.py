import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from commcare_connect.campaign.auth.decorators import current_campaign_user, require_perm
from commcare_connect.campaign.models import Campaign, Microplan
from commcare_connect.campaign.services import audit, microplan_actions, serializers


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}


def _campaign():
    return Campaign.objects.order_by("id").first()


def _get(mid):
    return get_object_or_404(Microplan, campaign=_campaign(), microplan_id=mid)


@require_perm("planning", "create")
def microplan_create(request):
    cu = current_campaign_user(request)
    c = _campaign()
    m = microplan_actions.create_microplan(c, _body(request), cu.name or cu.commcare_username)
    audit.record(request, f"Created microplan {m.microplan_id} ({m.lga})", "Microplanning", c)
    return JsonResponse({"microplan": serializers._microplan(m)})


@require_perm("planning", "edit")
def microplan_update(request, microplan_id):
    m = microplan_actions.update_microplan(_get(microplan_id), _body(request))
    return JsonResponse({"microplan": serializers._microplan(m)})


@require_perm("planning", "edit")
def microplan_target(request, microplan_id):
    d = _body(request)
    m = microplan_actions.set_target(_get(microplan_id), d.get("target"), d.get("goalPct"))
    return JsonResponse({"microplan": serializers._microplan(m)})


@require_perm("planning", "edit")
def microplan_budget(request, microplan_id):
    m = microplan_actions.set_budget(_get(microplan_id), _body(request).get("budget"))
    return JsonResponse({"microplan": serializers._microplan(m)})
