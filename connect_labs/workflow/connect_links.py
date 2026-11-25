"""Links from a workflow page to Connect's own screens, through a Labs address.

A workflow page may open only Labs pages (components/workflow/sandbox/protocol.ts,
``WORKFLOW_NAVIGATE_PREFIXES``), so a report links here and this view sends the person on
to Connect. The page never names a Connect address itself, and this view can lead
nowhere but Connect's worker Tasks screen for an opportunity the person can access.

``GET /labs/workflow/connect/worker-tasks/?opportunity_id=<id>&username=<username>``
redirects to Connect's worker page, Tasks tab:
``<CONNECT>/a/<org slug>/opportunity/<id>/user_tasks/?user=<Connect user id>``
(commcare-connect ``opportunity/urls.py`` ``user_tasks_list``; ``WorkerPageView`` finds the
worker by ``user__user_id``; Connect takes the numeric opportunity id as well as its uuid).

- The worker's Connect user id is read from the visit cache Labs already holds for the
  opportunity (``RawVisitCache.user_id``, from Connect's ``user_visits`` export). A worker
  with no cached visit goes to the opportunity's Task List instead.
- The organization slug is one the PERSON belongs to: Connect's ``/a/<org slug>/`` must be
  their own organization. A program manager reaches an opportunity through the program's
  managing organization (MBW: ``tnyirenda``), a member of the delivering organization
  through that one (``solina``); the owner's slug sends a program manager to a 404
  (found live on 25 Nov). Read from the person's Connect org tree (``labs.context.get_org_data``),
  falling back to the owner from ``/export/opportunity/<id>/``.
- Connect still decides what the person may see there; this only builds the address.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, HttpResponseRedirect
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

#: Connect usernames are short slugs of letters and digits.
_USERNAME = re.compile(r"^[A-Za-z0-9_.@+-]{1,150}$")
#: An organization slug as Connect makes them.
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ORG_SLUG_CACHE_SECONDS = 24 * 3600


def _connect_base() -> str:
    return settings.CONNECT_PRODUCTION_URL.rstrip("/")


def worker_connect_user_id(opportunity_id: int, username: str) -> str | None:
    """The worker's Connect user id, from any visit of theirs Labs has cached for the opportunity."""
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    value = (
        RawVisitCache.objects.filter(opportunity_id=opportunity_id, username=username)
        .exclude(user_id="")
        .values_list("user_id", flat=True)
        .first()
    )
    value = str(value or "").strip()
    return value if _UUID.match(value) else None


def opportunity_org_slug(opportunity_id: int, access_token: str) -> str | None:
    """The slug of the organization that owns the opportunity, as Connect's export names it."""
    key = f"connect_links:org_slug:{int(opportunity_id)}"
    hit = cache.get(key)
    if hit:
        return hit
    try:
        response = httpx.get(
            f"{_connect_base()}/export/opportunity/{int(opportunity_id)}/",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=20.0,
        )
    except httpx.HTTPError:
        logger.warning("connect_links: could not read opportunity %s from Connect", opportunity_id, exc_info=True)
        return None
    if response.status_code != 200:
        return None
    slug = str((response.json() or {}).get("organization") or "")
    if not _SLUG.match(slug):
        return None
    cache.set(key, slug, ORG_SLUG_CACHE_SECONDS)
    return slug


def viewer_org_slug(org_data: dict, opportunity_id: int) -> str | None:
    """The organization the person reaches this opportunity through: the program's managing
    organization, else the opportunity's own, else its supervising one, the first the person
    belongs to (Connect's org tree: opportunities carry ``program``, ``organization`` and
    ``supervising_organization`` slugs, programs their ``organization``)."""
    orgs = {str(o.get("slug")) for o in (org_data.get("organizations") or []) if o.get("slug")}
    opp = next(
        (o for o in (org_data.get("opportunities") or []) if str(o.get("id")) == str(int(opportunity_id))),
        None,
    )
    if opp is None:
        return None
    program = next(
        (p for p in (org_data.get("programs") or []) if p.get("id") is not None and p.get("id") == opp.get("program")),
        None,
    )
    for slug in ((program or {}).get("organization"), opp.get("organization"), opp.get("supervising_organization")):
        if slug and str(slug) in orgs and _SLUG.match(str(slug)):
            return str(slug)
    return None


def worker_tasks_url(opportunity_id: int, org_slug: str, user_id: str | None) -> str:
    base = f"{_connect_base()}/a/{org_slug}/opportunity/{int(opportunity_id)}"
    if user_id:
        return f"{base}/user_tasks/?{urlencode({'user': user_id})}"
    return f"{base}/assigned_tasks/"


@login_required
@require_GET
def connect_worker_tasks(request):
    """Send the person to one worker's Tasks in Connect (see the module docstring)."""
    from connect_labs.workers.access import _token, accessible_opportunity_ids

    try:
        opportunity_id = int(request.GET.get("opportunity_id") or "")
    except ValueError as e:
        raise Http404("opportunity_id is required") from e
    username = (request.GET.get("username") or "").strip()
    if not _USERNAME.match(username):
        raise Http404("username is required")

    allowed, _known = accessible_opportunity_ids(request.user, [opportunity_id], request=request)
    if opportunity_id not in allowed:
        # The same answer as an opportunity that does not exist: nothing about it is told.
        raise Http404("Not found")
    from connect_labs.labs.context import get_org_data

    slug = viewer_org_slug(get_org_data(request), opportunity_id)
    if not slug:
        token = _token(request.user, request)
        slug = opportunity_org_slug(opportunity_id, token) if token else None
    if not slug:
        raise Http404("Could not find this opportunity in Connect")
    return HttpResponseRedirect(
        worker_tasks_url(opportunity_id, slug, worker_connect_user_id(opportunity_id, username))
    )
