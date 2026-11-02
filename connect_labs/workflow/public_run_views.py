"""The page behind a public link to a saved run. No login.

It draws only the copy made when the link was created (public_shares.py), in the same
sandboxed frame every report draws in. It reads nothing from Connect, and whoever opens
it is anonymous here, signed in to labs or not.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.views import View

from connect_labs.audit_trail.context import get_audit_context
from connect_labs.workflow import public_shares, sandbox

REDACTED_TOKEN = "redacted"


def _private(response):
    add_never_cache_headers(response)
    response["X-Robots-Tag"] = "noindex, nofollow"
    # The token is in the URL, and the frame loads third-party scripts.
    response["Referrer-Policy"] = "no-referrer"
    return response


def _gone():
    # The same bytes for a link that never existed, expired or was ended.
    return _private(HttpResponse(render_to_string("workflow/public_run_gone.html"), status=404))


class PublicRunView(View):
    http_method_names = ["get", "head", "options"]

    def dispatch(self, request, *args, **kwargs):
        # A signed-in visitor is anonymous here: nothing downstream sees their session,
        # and the audit trail does not name them.
        request.user = AnonymousUser()
        # The audit trail is append-only, so a token written there could not be taken out.
        audit = get_audit_context()
        if audit is not None:
            audit.path = reverse("labs:workflow:public_run", kwargs={"token": REDACTED_TOKEN})
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, token):
        share = public_shares.find_usable(token)
        if share is None:
            return _gone()
        public_shares.note_view(share)
        origin = sandbox.labs_origin(request)
        response = render(
            request,
            "workflow/public_run.html",
            {
                "share": share,
                "completed_at": (share.payload.get("instance") or {}).get("completed_at"),
                "public_run_data": {
                    "labsOrigin": origin,
                    "mapboxToken": settings.MAPBOX_TOKEN or "",
                    "payload": share.payload,
                },
            },
        )
        # The frame may be navigated only to its shell, so it cannot leave with data in a URL.
        response["Content-Security-Policy"] = sandbox.host_frame_src(origin)
        response.csp_extra_policy = True
        return _private(response)
