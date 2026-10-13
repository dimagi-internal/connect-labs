"""The targeting page's three heavy answers, cached until the data changes.

The map, the selection and the costing each resolve every indicator for some
800 regions of Africa, which took about four seconds a request on labs. Their
answers depend only on the query string and the dataset -- the views show
public open data and nothing about the signed-in user (see OpenLocallyMixin) --
and the dataset changes only when it is loaded or imported. So an answer is
kept under the request's query and a version of the data, and a new load or
import gets a new version: every count, highest id and ingest run feeds it, and
the commands that rewrite rows in place bump it outright. Six hours is the
backstop for anything that slips past both.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from django.db.models import Count, Max
from django.http import HttpResponse

TTL_SECONDS = 6 * 60 * 60
_BUMP_KEY = "targeting:data-bump"


def invalidate() -> None:
    """A load or import has changed the data: drop every cached answer."""
    try:
        cache.incr(_BUMP_KEY)
    except ValueError:
        cache.set(_BUMP_KEY, 1, None)


def data_version() -> str:
    """A fingerprint of the dataset, cheap enough to take on every request."""
    from connect_labs.labs.admin_boundaries.models import AdminBoundary
    from connect_labs.labs.indicators.models import IndicatorValue, IngestRun

    values = IndicatorValue.objects.aggregate(n=Count("pk"), top=Max("pk"))
    shapes = AdminBoundary.objects.aggregate(n=Count("pk"), top=Max("pk"))
    run = IngestRun.objects.aggregate(top=Max("pk"))["top"]
    bump = cache.get(_BUMP_KEY) or 0
    return f"{values['n']}.{values['top']}-{shapes['n']}.{shapes['top']}-{run}-{bump}"


def key_for(name: str, request) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(request.GET.items()))
    digest = hashlib.sha1(f"{data_version()}|{query}".encode()).hexdigest()
    return f"targeting:{name}:{digest}"


class CachedJsonMixin:
    """Serve a GET from the cache when this query was answered on this data.

    Only a 200 is kept: an error answers one request, not the next one.
    """

    cache_name = ""

    # dispatch, not get: each view defines its own get, which would override
    # this one. Listed after the login mixin, so the sign-in check runs first.
    def dispatch(self, request, *args, **kwargs):
        if request.method != "GET":
            return super().dispatch(request, *args, **kwargs)
        key = key_for(self.cache_name, request)
        hit = cache.get(key)
        if hit is not None:
            return HttpResponse(hit, content_type="application/json")
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code == 200:
            cache.set(key, response.content, TTL_SECONDS)
        return response
