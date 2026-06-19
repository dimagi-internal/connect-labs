from django.http import HttpRequest, JsonResponse
from django.views.generic import TemplateView


def ping(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True})


class AppView(TemplateView):
    template_name = "campaign/app.html"
