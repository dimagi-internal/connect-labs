"""Page code runs in a sandbox: the frame document, its policy, and the host page's.

Design: docs/superpowers/specs/2026-10-12-page-sandbox-design.md.

A page's render code (a workflow definition with ``kind: "page"``) is author-written
JavaScript. It used to be compiled and run on labs' own origin, inside the viewer's
session. It now runs in an ``<iframe sandbox="allow-scripts">`` with no
``allow-same-origin``: its origin is opaque, so it holds no labs cookies, cannot read
the host page, and its requests are cross-site. The trusted page around it (the
*host*, ``components/workflow/sandbox/host.ts``) holds the data, passes it in, and
answers a short allowlist of requests over a private ``MessageChannel``.

This module is the server half:

* ``frame_policy`` -- the frame document's ENFORCED Content-Security-Policy. No labs
  origin in ``connect-src`` and no wildcard: page code reaches labs only through the
  bridge, and has no arbitrary host to send data to.
* ``host_frame_src`` -- the second, enforced policy the host page carries: the frame
  may be navigated only to the shell (and the canopy panel's own embed path).
* ``sandbox_render`` -- ``GET /labs/sandbox/render/``: a fixed, data-free shell,
  identical for every viewer. No login, no redirect, no CSRF token, query and suffix
  ignored (a ``frame-src`` path match does not survive a redirect).
* ``should_sandbox`` -- THE decision, made once from the consuming definition's
  ``kind`` in ``WorkflowRunView``.

``LABS_PAGE_SANDBOX`` (default on) exists only as an emergency switch back.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

SHELL_PATH = "/labs/sandbox/render/"

# Pinned CDN URLs, named in full in the frame's script-src. The run page loads the
# same versions (templates/workflow/_runner_scripts.html), so a render sees the same
# libraries in both places.
BABEL_URL = "https://unpkg.com/@babel/standalone@7.23.5/babel.min.js"
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
CHART_ADAPTER_URL = (
    "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"
)
LEAFLET_JS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
LEAFLET_CSS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_PREFIX = "https://unpkg.com/leaflet@1.9.4/"
MAPBOX_GL_JS_URL = "https://api.mapbox.com/mapbox-gl-js/v3.13.0/mapbox-gl.js"
MAPBOX_GL_CSS_URL = "https://api.mapbox.com/mapbox-gl-js/v3.13.0/mapbox-gl.css"
GOOGLE_FONTS_CSS = "https://fonts.googleapis.com/css2"
GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"
GOOGLE_FONTS_CSS_URL = f"{GOOGLE_FONTS_CSS}?family=Work+Sans:wght@400;500;600;700&display=swap"

#: The Mapbox PATHS GL needs -- never the whole of api.mapbox.com, whose datasets and
#: uploads APIs would be a two-way channel to whoever holds a token of their own.
MAPBOX_PATHS = (
    "https://api.mapbox.com/styles/v1/mapbox/",
    "https://api.mapbox.com/fonts/v1/mapbox/",
    "https://api.mapbox.com/v4/",
    "https://*.tiles.mapbox.com",
    "https://events.mapbox.com",
)


def sandbox_enabled() -> bool:
    return bool(getattr(settings, "LABS_PAGE_SANDBOX", True))


def should_sandbox(definition) -> bool:
    """Whether this definition's render runs in the sandbox: every page, on every route.

    Decided from the consuming definition's own ``kind``, whatever its
    ``render_source``: a template workflow's render is just as author-controlled.
    """
    from connect_labs.workflow.page_mode import is_page

    return sandbox_enabled() and definition is not None and is_page(definition)


def _origin_of(url: str) -> str:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def labs_origin(request=None) -> str:
    """Labs' public origin: ``LABS_PUBLIC_URL``, else the request's own (ALLOWED_HOSTS-checked) host."""
    origin = _origin_of(getattr(settings, "LABS_PUBLIC_URL", "") or "")
    if origin:
        return origin
    if request is not None:
        return f"{request.scheme}://{request.get_host()}"
    return ""


def static_prefix(origin: str) -> str:
    """The static prefix as a full URL: in an opaque document ``'self'`` is no guarantee."""
    static_url = settings.STATIC_URL or "/static/"
    if _origin_of(static_url):
        return static_url
    return origin + "/" + static_url.lstrip("/")


def frame_policy(origin: str) -> str:
    """The frame document's enforced CSP, as one string (tested as a string)."""
    static = static_prefix(origin)
    script_src = [
        static,
        BABEL_URL,
        MAPBOX_GL_JS_URL,
        CHART_JS_URL,
        CHART_ADAPTER_URL,
        LEAFLET_JS_URL,
        "'unsafe-eval'",  # Babel's output is eval'd
        "'unsafe-inline'",
    ]
    style_src = [static, "https://api.mapbox.com/mapbox-gl-js/", LEAFLET_PREFIX, GOOGLE_FONTS_CSS, "'unsafe-inline'"]
    parts = [
        # Even opened top-level, the document is opaque-origin and may only run script.
        "sandbox allow-scripts",
        "default-src 'none'",
        "script-src " + " ".join(script_src),
        "style-src " + " ".join(style_src),
        f"font-src {static} {GOOGLE_FONTS_FILES}",
        "img-src data: blob: " + " ".join(MAPBOX_PATHS),
        "connect-src " + " ".join(MAPBOX_PATHS),
        "worker-src blob:",
        "frame-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "object-src 'none'",
        f"frame-ancestors {origin}",
    ]
    return "; ".join(parts)


def host_frame_src(origin: str) -> str:
    """The host page's second, ENFORCED policy: where a frame on it may be navigated.

    Frame navigations are checked against the embedder's ``frame-src``, so this is what
    stops the sandboxed frame navigating itself to an arbitrary address with data in
    it. The canopy panel's widget is named by its exact path: a whole canopy origin
    would let the frame navigate onto any canopy-hosted document, which keeps an opaque
    origin and so would also pass the bridge handshake.
    """
    sources = [origin + SHELL_PATH]
    canopy = (getattr(settings, "CANOPY_BASE_URL", "") or "").rstrip("/")
    if canopy:
        sources.append(canopy + "/embed/chat")
    return "frame-src " + " ".join(sources)


def host_payload(request) -> dict:
    """What the host runner needs to draw the frame (``workflow_data.sandbox``)."""
    return {"src": SHELL_PATH, "origin": labs_origin(request)}


def shell_html(origin: str) -> str:
    return render_to_string(
        "workflow/sandbox_frame.html",
        {
            "babel_url": BABEL_URL,
            "chart_js_url": CHART_JS_URL,
            "chart_adapter_url": CHART_ADAPTER_URL,
            "leaflet_js_url": LEAFLET_JS_URL,
            "leaflet_css_url": LEAFLET_CSS_URL,
            "mapbox_gl_js_url": MAPBOX_GL_JS_URL,
            "mapbox_gl_css_url": MAPBOX_GL_CSS_URL,
            "google_fonts_css_url": GOOGLE_FONTS_CSS_URL,
        },
    )


@require_http_methods(["GET", "HEAD"])
@xframe_options_exempt  # frame-ancestors in the enforced policy replaces X-Frame-Options here
def sandbox_render(request, rest=""):
    """The frame document. Fixed and data-free: no login, no CSRF, no user, no analytics.

    Rendered with no request in the template context, so nothing about the viewer can
    reach it, and it is the same bytes for everyone.
    """
    origin = labs_origin(request)
    response = HttpResponse(shell_html(origin), content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = frame_policy(origin)
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    # A URL-restricted Mapbox token needs labs' origin as the referrer.
    response["Referrer-Policy"] = "strict-origin"
    response["Cache-Control"] = "public, max-age=300"
    # The site-wide report-only policy (utils/csp.py) is not added on top: its
    # frame-ancestors 'none' would only produce noise for a document meant to be framed.
    response.csp_exempt = True
    return response
