from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views import View

from connect_labs.pages.data_access import resolve_surface
from connect_labs.pages.providers import get_provider


def ping(request):
    return JsonResponse({"ok": True})


def _access_token(request) -> str:
    return (request.session or {}).get("labs_oauth", {}).get("access_token", "")


def _context(request) -> dict:
    return getattr(request, "labs_context", {}) or {}


def _resolve(request, slug):
    return resolve_surface(_access_token(request), _context(request), slug)


class SurfacePageView(LoginRequiredMixin, View):
    def get(self, request, slug):
        surface = _resolve(request, slug)
        if surface is None:
            # Soft not-found: stay in chrome, offer the context switcher.
            return render(request, "pages/surface_not_found.html", {"slug": slug})

        shells = []
        for index, card in enumerate(surface["cards"]):
            provider = get_provider(card.get("provider"))
            if provider is None:
                continue
            if not provider.entitled(request, card.get("target", {})):
                continue
            shells.append({"index": index, "provider": card.get("provider"), "options": card.get("options", {})})

        return render(request, "pages/surface.html", {"surface": surface, "slug": slug, "cards": shells})


class CardDataView(LoginRequiredMixin, View):
    def get(self, request, slug, index):
        surface = _resolve(request, slug)
        if surface is None:
            raise Http404("Surface not found")
        cards = surface["cards"]
        if index >= len(cards):
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
