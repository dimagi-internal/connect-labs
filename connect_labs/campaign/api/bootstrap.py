# connect_labs/campaign/api/bootstrap.py
from django.http import JsonResponse

from connect_labs.campaign.auth.decorators import current_campaign_user, require_perm
from connect_labs.campaign.models import Campaign
from connect_labs.campaign.services import seed, serializers


def _select_campaign(request):
    """Pick which campaign to show: an explicit ?campaign=<code>, else a real
    CommCare-domain (national) campaign if one exists, else the first (legacy demo)."""
    code = (request.GET.get("campaign") or "").strip()
    if code:
        chosen = Campaign.objects.filter(code=code).first()
        if chosen is not None:
            return chosen
    return (
        Campaign.objects.exclude(commcare_domain="").order_by("-id").first() or Campaign.objects.order_by("id").first()
    )


@require_perm("overview", "view")
def bootstrap(request):
    campaign = _select_campaign(request)
    if campaign is None:
        campaign = seed.seed_campaign()  # lazy idempotent seed so the demo always has data
    cu = current_campaign_user(request)
    return JsonResponse(
        {
            "campaign": serializers.bootstrap_payload(
                campaign, current_username=cu.commcare_username, request=request
            ),
            "user": {"name": cu.name or cu.commcare_username, "role": cu.role},
        },
        json_dumps_params={"ensure_ascii": False},
    )
