from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.generic import TemplateView

from commcare_connect.campaign.auth.decorators import CampaignLoginRequiredMixin, current_campaign_user
from commcare_connect.campaign.services import rbac


def ping(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True})


class AppView(CampaignLoginRequiredMixin, TemplateView):
    template_name = "campaign/app.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cu = current_campaign_user(self.request)
        perms_matrix = {
            module: {verb: rbac.can(cu.role, module, verb) for verb in rbac.VERBS} for module in rbac.MODULES
        }
        ctx["bootstrap"] = {
            "user": {
                "name": cu.name,
                "role": cu.role,
            },
            "campaign": {
                "name": "Campaign Utility Tool",
            },
            "perms_matrix": perms_matrix,
        }
        ctx["mapbox_token"] = getattr(settings, "MAPBOX_TOKEN", None) or ""
        return ctx
