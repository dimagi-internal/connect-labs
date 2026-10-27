"""Labs' panel mint: the canopy SDK's ``panel_token``, plus one record labs needs.

The SDK's view (``canopy_sdk.django.views.panel_token``) mints a canopy token for the
signed-in person and, on a registered page, an ID-JAG for that page's scopes. Labs mints
through this copy of it so that, in the same request, it can record WHICH page the grant
is for (``labsdata.page_holds.record_mint``): the selection labs signed onto the panel's
token URL when it drew the page. At labs' MCP the ``data_*`` tools then hold every call
under that grant to that page. Everything else is the SDK's, step for step: the subject is
``request.user``, ``?page=`` only selects among the registered pages, the page's scopes
(and so an ID-JAG) ride only a mint whose body is ``{"grant": true}`` -- which the panel
sends once the person has sent a message in it, never on page load -- and a failed mint
says nothing the visitor could act on.
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

log = logging.getLogger(__name__)


def _json(body: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(body, status=status)


@login_required
@require_POST
def panel_token(request):
    from canopy_sdk.django import conf
    from canopy_sdk.django import views as sdk_views
    from canopy_sdk.host.signing import MintFailed, arrival_payload

    from connect_labs.labsdata import page_holds

    if not conf.is_configured():
        return _json({"error": "the canopy panel is not configured here"}, 503)
    try:
        config = conf.get_host_config()
        page = conf.page_for(request.GET.get("page"), request.user)
    except Exception:  # noqa: BLE001 - HostNotConfigured, a malformed key or registry
        log.exception("CANOPY_HOST could not be read for the panel")
        return _json({"error": "the canopy panel is not configured here"}, 503)
    chosen = page.agent if page is not None else ""
    try:
        payload = arrival_payload(
            config,
            conf.subject_for_user(request.user),
            # The SDK's own reading of the body: a grant only when the person acted.
            scopes=page.scopes if page is not None and sdk_views._wants_grant(request) else (),
            agent_slug=chosen or conf.agent_slug(),
            bind_agent=bool(chosen),
            runner_requirements=(page.runner_requirements if page is not None else ()),
            **conf.user_claims(request.user),
        )
        # Before canopy can redeem the ID-JAG: the grant's page, as labs drew it.
        page_holds.record_mint(request, page, payload)
        # The SDK's own sender (looked up on its module, so it stays the one function).
        vouched = sdk_views.mint_contact_token(config, payload)
    except MintFailed as exc:
        log.warning("canopy declined to mint a token for user %s: %s", request.user.pk, exc)
        return _json({"error": "could not reach the agent service"}, 502)
    return _json(
        {
            "token": vouched["token"],
            "expires_at": vouched.get("expires_at", ""),
            "kind": vouched.get("kind", "contact"),
        }
    )
