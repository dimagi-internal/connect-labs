# commcare_connect/campaign/api/bootstrap.py
from django.http import JsonResponse

from commcare_connect.campaign.auth.decorators import current_campaign_user, require_perm
from commcare_connect.campaign.models import Campaign
from commcare_connect.campaign.services import seed, serializers


@require_perm("overview", "view")
def bootstrap(request):
    campaign = Campaign.objects.order_by("id").first()
    if campaign is None:
        campaign = seed.seed_campaign()  # lazy idempotent seed so the demo always has data
    cu = current_campaign_user(request)
    return JsonResponse(
        {
            "campaign": serializers.bootstrap_payload(campaign),
            "user": {"name": cu.name or cu.commcare_username, "role": cu.role},
        },
        json_dumps_params={"ensure_ascii": False},
    )
