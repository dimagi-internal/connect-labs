"""The CommCare pages used to live at /hq/; they are at /commcare/ now."""

from __future__ import annotations

from django.http import HttpResponseRedirect


def hq_legacy_redirect(request, rest: str = ""):
    """/hq/<anything> -> /commcare/<anything>, keeping the query. A POST is sent on as a POST (307)."""
    target = f"/commcare/{rest}"
    if request.META.get("QUERY_STRING"):
        target += "?" + request.META["QUERY_STRING"]
    response = HttpResponseRedirect(target)
    if request.method not in ("GET", "HEAD"):
        response.status_code = 307
    return response
