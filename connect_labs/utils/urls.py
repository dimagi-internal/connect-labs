"""Shared absolute-URL helper for contexts with no live HTTP request
(Celery tasks, SMS callbacks, background AI-review/duplicate-detection).

Single home for what used to be three near-identical private copies
(connect_labs/program/tasks.py, connect_labs/utils/sms.py,
connect_labs/audit/link_helpers.py) of the same
django.contrib.sites-based fallback -- a replacement for
allauth.utils.build_absolute_uri, removed during labs simplification.
"""

from django.contrib.sites.models import Site


def build_absolute_url(path: str) -> str:
    """Return an absolute https:// URL for `path` using the current Site's domain.

    Falls back to "localhost" if the Site framework/DB is unavailable (e.g. an
    unmarked test with no DB access) rather than raising.
    """
    try:
        domain = Site.objects.get_current().domain
    except Exception:
        domain = "localhost"
    return f"https://{domain}{path}"
