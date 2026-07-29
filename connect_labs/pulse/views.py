"""Page views for Pulse.

Three entry points matching the three delivery modes: an authenticated index,
an authenticated display (kiosk / presenter), and a public token-scoped link.

The public view is deliberately the only unauthenticated surface in the app,
and it is read-only. It exposes no Connect credentials and no drill-through to
raw records.
"""

from __future__ import annotations

import secrets

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from connect_labs.pulse.models import PulseOpportunity, PulsePublicToken, PulseScalar

# Registered layouts. A layout is an arrangement of cards; adding one is a
# template plus an entry here, which is the point of the card/layout split.
LAYOUTS = {
    "nightmap": {
        "label": "Night map",
        "blurb": "Services ignite as points of light. The geography draws itself out of the work.",
    },
    "mission": {
        "label": "Mission control",
        "blurb": "Dense multi-panel telemetry — everything at once.",
    },
    "financial": {
        "label": "Financial view",
        "blurb": "Funds flow: committed, accrued, paid, invoiced.",
    },
}

DEFAULT_LAYOUT = "nightmap"


def _display_context(layout: str, *, public: bool, show_partner_names: bool = True) -> dict:
    from django.conf import settings

    scope = PulseScalar.objects.filter(key="scope").first()
    return {
        # Real basemap via the shared ConnectMap module, so the map carries
        # coastlines and country borders instead of asking a viewer to infer
        # geography from dots alone.
        "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
        "layout": layout,
        "layout_meta": LAYOUTS.get(layout, LAYOUTS[DEFAULT_LAYOUT]),
        "layouts": LAYOUTS,
        "is_public": public,
        "show_partner_names": show_partner_names,
        "scope": scope.value if scope else {},
        "opportunity_count": PulseOpportunity.objects.count(),
    }


class PulseIndexView(LoginRequiredMixin, View):
    """Layout picker plus ingest status and link management."""

    def get(self, request):
        from connect_labs.pulse.api import _ingest_state

        return render(
            request,
            "pulse/index.html",
            {
                "layouts": LAYOUTS,
                "ingest": _ingest_state(),
                "scope": (PulseScalar.objects.filter(key="scope").first() or PulseScalar(value={})).value,
                "tokens": PulsePublicToken.objects.filter(revoked=False).order_by("-created_at")[:20],
            },
        )


class PulseDisplayView(LoginRequiredMixin, View):
    def get(self, request, layout=DEFAULT_LAYOUT):
        if layout not in LAYOUTS:
            raise Http404("Unknown layout")
        return render(request, "pulse/display.html", _display_context(layout, public=False))


class PulsePublicView(View):
    """Unauthenticated, token-scoped, revocable.

    Tokens are individually scoped so a link given to one funder can be killed
    without breaking anyone else's — a single shared public URL would be a
    one-way door.
    """

    def get(self, request, token):
        row = PulsePublicToken.objects.filter(token=token).first()
        if row is None or not row.is_usable:
            # Same response for unknown and revoked, so a revoked link cannot be
            # distinguished from a wrong guess.
            raise Http404("No such display")

        PulsePublicToken.objects.filter(pk=row.pk).update(last_viewed_at=timezone.now(), view_count=row.view_count + 1)

        context = _display_context(row.layout_slug, public=True, show_partner_names=row.show_partner_names)
        context["public_token"] = row.token
        response = render(request, "pulse/display.html", context)
        # Labs already serves Disallow: / — belt and braces for a public URL.
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def mint_public_token(user, *, label: str = "", layout: str = DEFAULT_LAYOUT, show_partner_names: bool = True):
    return PulsePublicToken.objects.create(
        token=secrets.token_urlsafe(24),
        label=label,
        layout_slug=layout,
        show_partner_names=show_partner_names,
        created_by=user,
    )
