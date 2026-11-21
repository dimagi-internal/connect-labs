"""The ``connect-messaging`` site's public documents: its JWKS and its signed ``site.json``.

canopy's Host sites form reads ``site.json`` ("Fill from the site") beside the JWKS: the site's
origins, where its keys are, its MCP resource (the worker tools), the scheme of its people's
records (``person_scheme: labs-worker``) and of the links its tools return. Both answer 404
until the site's identity is configured (``conf.site_configured``).
"""

from __future__ import annotations

import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from connect_labs.messaging.canopy_bridge import conf, sdk

logger = logging.getLogger(__name__)

#: What the worker tools let an agent do, for canopy's Host sites page (descriptive only: labs
#: enforces the playbook's tool list on every call, bound to the subject canopy names).
SCOPE_LABELS = {
    "worker:read": "read the Connect Worker's own profile, timeline, tasks, learning, visits and payments",
    "worker:memory": "read and record what has been learned about the Connect Worker; never a judgement of them",
    "thread:manage": "record the conversation's outcome, hand it to a person or another approach, or close it",
}
PAGES = [
    {
        "key": "messaging:thread",
        "title": "A Connect Worker's own conversation (tools bound to that one worker)",
        "scopes": list(SCOPE_LABELS),
    }
]


def site_facts() -> dict:
    public = conf.mcp_resource().removesuffix(conf.MCP_PATH)
    return {
        "origins": [public],
        "jwks_url": conf.jwks_url(),
        "issuer": public,
        "mcp_resource": conf.mcp_resource(),
        "resource_schemes": [conf.PERSON_SCHEME],
    }


def _config_or_404():
    config = conf.host_config()
    if config is None:
        raise Http404("the connect-messaging canopy site is not configured here")
    return config


@require_GET
def jwks(request):
    config = _config_or_404()
    response = JsonResponse(config.jwks())
    response["Cache-Control"] = "max-age=300"
    return response


@require_GET
def site_document(request):
    from canopy_sdk.site import MEDIA_TYPE

    config = _config_or_404()
    try:
        token = sdk.site_document_token(
            config,
            person_scheme=conf.PERSON_SCHEME,
            site=site_facts(),
            title=conf.SITE_TITLE,
            pages=PAGES,
            scope_labels=SCOPE_LABELS,
        )
    except Exception:  # noqa: BLE001 -- a malformed key is a deployment fault, never a 500 page
        logger.exception("connect-messaging site.json could not be built")
        return JsonResponse({"error": "server_error"}, status=503)
    response = HttpResponse(token, content_type=MEDIA_TYPE)
    response["Cache-Control"] = "max-age=60"
    return response
