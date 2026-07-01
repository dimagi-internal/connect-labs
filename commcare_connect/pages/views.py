from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.pages.data_access import SurfaceDataAccess
from commcare_connect.pages.providers import get_provider


def ping(request):
    return JsonResponse({"ok": True})


def _access_token(request) -> str:
    return (request.session or {}).get("labs_oauth", {}).get("access_token", "")


def _load_surface(request, slug: str) -> dict:
    da = SurfaceDataAccess(access_token=_access_token(request))
    surface = da.get_surface_by_slug(slug)
    if surface is None:
        raise Http404("Surface not found")
    return surface


class SurfacePageView(LoginRequiredMixin, TemplateView):
    template_name = "pages/surface.html"

    def get_context_data(self, slug: str, **kwargs):
        context = super().get_context_data(**kwargs)
        surface = _load_surface(self.request, slug)

        shells = []
        for index, card in enumerate(surface["cards"]):
            provider = get_provider(card.get("provider"))
            if provider is None:
                continue
            if not provider.entitled(self.request, card.get("target", {})):
                continue
            shells.append(
                {
                    "index": index,
                    "provider": card.get("provider"),
                    "options": card.get("options", {}),
                }
            )

        context["surface"] = surface
        context["slug"] = slug
        context["cards"] = shells
        return context


class CardDataView(LoginRequiredMixin, View):
    def get(self, request, slug: str, index: int):
        surface = _load_surface(request, slug)
        cards = surface["cards"]
        if index < 0 or index >= len(cards):
            raise Http404("Card index out of range")

        card = cards[index]
        provider = get_provider(card.get("provider"))
        if provider is None:
            raise Http404("Unknown provider")

        target = card.get("target", {})
        if not provider.entitled(request, target):
            return JsonResponse({"error": "not entitled"}, status=403)

        payload = provider.get_card_data(request, target, card.get("options", {}))
        return JsonResponse(payload.to_dict())
