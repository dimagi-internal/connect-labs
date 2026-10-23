"""Who may frame what: per-client ``frame-ancestors`` on the frame, and the sandbox shell.

Everywhere else labs keeps ``X-Frame-Options: DENY`` and ``frame-ancestors 'none'``.
"""

from __future__ import annotations

from django.core.cache import cache

_ANCESTORS_CACHE_KEY = "embed-all-ancestor-origins"
_ANCESTORS_CACHE_SECONDS = 60


def allow_framing(response, origins) -> None:
    """Let ``origins`` (and only they) frame this response.

    An enforced ``frame-ancestors`` is added as a policy of its own beside any the view
    set (a comma separates two policies; both apply), ``X-Frame-Options`` is left off
    (it cannot name an origin), and the site-wide report-only policy is told to name
    the same ancestors instead of reporting ``'none'`` on every load (utils/csp.py).
    """
    origins = tuple(origins or ())
    policy = "frame-ancestors " + (" ".join(origins) if origins else "'none'")
    existing = response.get("Content-Security-Policy")
    response["Content-Security-Policy"] = f"{existing}, {policy}" if existing else policy
    response.csp_extra_policy = True
    response.csp_frame_ancestors = origins or ("'none'",)
    response.xframe_options_exempt = True


def embed_ancestor_origins() -> tuple[str, ...]:
    """Every active client's origins, for the sandbox shell.

    A sandboxed workflow's shell is framed by the labs page, which is framed by the
    client: ``frame-ancestors`` is checked against EVERY ancestor, so the shell must name
    the client too. It is fixed and data-free, the same bytes for everyone, so naming
    every client's origins exposes nothing.
    """
    cached = cache.get(_ANCESTORS_CACHE_KEY)
    if isinstance(cached, tuple):
        return cached
    from connect_labs.embed.models import EmbedClient, valid_origin

    origins: set[str] = set()
    for allowed in EmbedClient.objects.filter(active=True).values_list("allowed_origins", flat=True):
        origins.update(o for o in (allowed or []) if valid_origin(o))
    result = tuple(sorted(origins))
    cache.set(_ANCESTORS_CACHE_KEY, result, _ANCESTORS_CACHE_SECONDS)
    return result
