"""The /hq/ door.

``/hq/`` is a satellite prefix: the labs boundary leaves it alone, so everything here decides
for itself. A person gets in with a CommCare token that works, which is either the CommCare
login they used to arrive, or the CommCare connection a Connect person made earlier. Their
domains are proven to labs as they pass (``sync_identity``).
"""

from __future__ import annotations

from functools import wraps
from urllib.parse import quote

from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import redirect, render

from connect_labs.hqdata.identity import sync_identity
from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none


class ProjectNotYours(Http404):
    """The project is not one this person may use. Says the same whether or not it exists."""


def hq_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        here = quote(request.get_full_path(), safe="/")
        if not user.is_authenticated:
            return redirect(f"/hq/login/?next={here}")
        token = cchq_token_or_none(user)
        if token is None:
            if request.session.get("hq_auth"):
                # Signed in with CommCare, and the token is gone and cannot be refreshed.
                logout(request)
                return redirect(f"/hq/login/?next={here}")
            # A Connect person who has not connected CommCare yet: the existing connection flow.
            request.session["commcare_oauth_next"] = request.get_full_path()
            return redirect("/labs/commcare/initiate/")
        request.hq_token = token
        sync_identity(user, token)
        try:
            return view(request, *args, **kwargs)
        except ProjectNotYours:
            # A bare "page not found" tells someone who simply has not joined a project nothing about what to do.
            # This says the same thing whether the project exists or not, so it reveals nothing about it.
            return render(request, "hqdata/not_yours.html", {"present_mode": True}, status=404)

    return wrapper
