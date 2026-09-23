"""Serve the Labs help docs (the MkDocs build of user_docs/) inside Labs.

WHY HERE AND NOT GITHUB PAGES. The help docs were published to GitHub Pages,
which was never re-enabled when the repo moved to dimagi-internal -- so for months
the only build of them 404'd, and nobody noticed because the deploy job kept
passing. The repo is public, so Pages would also make the docs public. Serving the
build from Labs puts it where its readers already are, behind the Labs login, and
ties it to the same deploy as the features it describes.

WHY NOT WHITENOISE / STATIC. Everything under STATIC_URL is served without a
login check. This view is the login gate.

The Dockerfile builds the site (`mkdocs build`) into HELP_SITE_DIR. Locally the
directory is absent until you run `mkdocs build --site-dir help_site`, and the view
says so rather than 404ing silently.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils._os import safe_join
from django.views.decorators.http import require_GET


def _site_dir() -> Path:
    return Path(getattr(settings, "HELP_SITE_DIR", Path(settings.BASE_DIR) / "help_site"))


@login_required
@require_GET
def help_site(request, path: str = ""):
    root = _site_dir()
    if not (root / "index.html").is_file():
        return HttpResponse(
            "The Labs help site has not been built on this server. "
            "Run `mkdocs build --site-dir help_site` from the repo root.",
            status=503,
            content_type="text/plain",
        )

    # MkDocs pages are directories (`reports-with-claude/index.html`) whose links are
    # relative, so a directory URL must end in "/" or every link on the page breaks.
    try:
        target = Path(safe_join(str(root), path))
    except SuspiciousFileOperation:  # path escapes the site root
        raise Http404
    if target.is_dir():
        if path and not path.endswith("/"):
            return HttpResponseRedirect(request.path + "/")
        target = target / "index.html"
    if not target.is_file():
        raise Http404

    content_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target.open("rb"), content_type=content_type or "application/octet-stream")
