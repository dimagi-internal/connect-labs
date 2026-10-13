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
* ``should_sandbox`` -- THE decision, made once from the consuming definition in
  ``WorkflowRunView``: every page, and a workflow that is switched on (phase 2).

``LABS_PAGE_SANDBOX`` (default on) exists only as an emergency switch back, for
pages and workflows alike.

Phase 2 (workflows) is rolled out per workflow, because a workflow's render code
used to reach labs directly and 40-odd templates and their live copies have to be
checked in the frame first. ``LABS_WORKFLOW_SANDBOX`` says how far it has gone:

* ``"off"`` -- no workflow is sandboxed (pages still are).
* ``"opt_in"`` (the default) -- a workflow is sandboxed when its definition says
  ``config.sandbox: true`` (written on every new instance of a template that has
  been checked, since that template's DEFINITION carries it), or when its render
  FOLLOWS a deployed template that has been checked (``TEMPLATE["sandboxed"]``),
  or when the address asks for it with ``?sandbox=1`` (which can only turn the
  sandbox on, never off -- a link must not be able to take a workflow out of it).
* ``"all"`` -- every workflow, whatever its definition says, except the few live
  copies of a DEPRECATED template named in ``DEPRECATED_UNSANDBOXED`` below (by id,
  on their opportunity: an author cannot add one).

There is no per-workflow opt-out (an author must not be able to take a workflow out of
the frame): ``config.sandbox: false`` means nothing.
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

#: Raster tiles Leaflet maps draw (OpenStreetMap, CARTO): image GETs to tile
#: servers nobody here controls, so not a channel out.
TILE_HOSTS = (
    "https://*.tile.openstreetmap.org",
    "https://*.basemaps.cartocdn.com",
)

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


WORKFLOW_SANDBOX_MODES = ("off", "opt_in", "all")


def workflow_sandbox_mode() -> str:
    mode = str(getattr(settings, "LABS_WORKFLOW_SANDBOX", "opt_in") or "opt_in").strip().lower()
    # An unknown value is read as the default, never as "off".
    return mode if mode in WORKFLOW_SANDBOX_MODES else "opt_in"


#: Under ``all``, these definitions stay out of the frame: live copies of the
#: deprecated MBW monitoring templates (``mbw_monitoring``, and the v2 that was removed
#: from the repo), whose render code calls their own ``/custom_analysis/`` endpoints and
#: an ``EventSource`` the route table will not answer. They are kept renderable for
#: the record (templates/mbw_monitoring/DEPRECATED.md); no new one can be made, so the
#: list only shrinks. Named here, by ``(definition id, opportunity id)`` on production
#: Connect, and not by anything a definition says about itself: ``templateType`` is
#: author-writable, so exempting by it would be the per-workflow opt-out this module
#: refuses. A definition must ALSO still say it is one of these templates, so a pin
#: can only narrow. Ids from the 2026-10-13 ``workflow_sandbox_scan``; spec, "Rollout
#: to all". ``?sandbox=1`` still draws any of them sandboxed.
DEPRECATED_TEMPLATE_KEYS = frozenset({"mbw_monitoring", "mbw_monitoring_v2", "mbw_monitoring_v3"})
DEPRECATED_UNSANDBOXED: frozenset[tuple[int, int]] = frozenset(
    {
        (897, 765),
        (1035, 740),
        (1079, 740),
        (1097, 740),
        (1215, 740),
        (1236, 740),
        (1246, 740),
        (1265, 740),
        (1324, 1167),
        (1327, 1167),
        (1904, 1167),
        (2408, 1167),
        (2411, 709),
        (2449, 740),
        (2642, 765),
        (2695, 740),
    }
)


def deprecated_exempt(definition) -> bool:
    """Whether ``definition`` is one of the pinned live copies of a deprecated template."""
    if definition is None:
        return False
    data = getattr(definition, "data", None) or {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    key = config.get("templateType") or data.get("templateType")
    if key not in DEPRECATED_TEMPLATE_KEYS:
        return False
    try:
        pin = (int(getattr(definition, "id", None)), int(getattr(definition, "opportunity_id", None)))
    except (TypeError, ValueError):
        return False
    return pin in DEPRECATED_UNSANDBOXED


def _template_is_sandboxed(key) -> bool:
    from connect_labs.workflow.templates import TEMPLATES

    template = TEMPLATES.get(key) if isinstance(key, str) else None
    return bool(template and template.get("sandboxed"))


def workflow_wants_sandbox(definition, request=None, mode: str | None = None) -> bool:
    """Whether a WORKFLOW (not a page) runs in the sandbox, per the rollout above.

    ``mode`` asks the question for a rollout mode other than the configured one
    (``workflow_sandbox_scan`` reports both "now" and "under all").
    """
    mode = mode if mode in WORKFLOW_SANDBOX_MODES else workflow_sandbox_mode()
    if mode == "all":
        if request is not None and request.GET.get("sandbox") == "1":
            return True
        return not deprecated_exempt(definition)
    if mode == "off":
        return False
    data = getattr(definition, "data", None) or {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    if config.get("sandbox") is True:
        return True
    if request is not None and request.GET.get("sandbox") == "1":
        return True
    # There is no per-workflow opt-out: `config.sandbox: false` is read as unset. An
    # author must not be able to take a workflow that follows a checked template out
    # of the frame; the emergency switch is LABS_WORKFLOW_SANDBOX=off (or
    # LABS_PAGE_SANDBOX=false), for everything at once.
    render_source = data.get("render_source")
    if isinstance(render_source, dict) and _template_is_sandboxed(render_source.get("template")):
        return True
    return False


def should_sandbox(definition, page_mode: bool = False, request=None, mode: str | None = None) -> bool:
    """Whether this definition's render runs in the sandbox.

    Every page, on every route: decided from the consuming definition as read for this
    render (its ``kind``, or the page template it was made from), whatever its
    ``render_source`` -- a template workflow's render is just as author-controlled --
    and whenever the route draws it in page mode (a page's address, a supply tab),
    whatever the definition says: what a route draws as a page is sandboxed, fail closed.

    A workflow, when the phase-2 rollout says so (``workflow_wants_sandbox``).
    """
    from connect_labs.workflow.page_mode import is_page

    if not sandbox_enabled() or definition is None:
        return False
    if bool(page_mode) or is_page(definition):
        return True
    return workflow_wants_sandbox(definition, request, mode=mode)


def _origin_of(url: str) -> str:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def labs_origin(request=None) -> str:
    """The origin this request reached labs on: its scheme and (ALLOWED_HOSTS-checked) host.

    The request's own, not ``LABS_PUBLIC_URL``: the host page's ``frame-src``, the
    shell's ``frame-ancestors`` and the bridge's navigate check must all name the origin
    the browser is actually on, or a host that serves labs under another name (a second
    allowed host, ``127.0.0.1`` locally) draws a blank page. ``LABS_PUBLIC_URL`` is only
    the fallback with no request.
    """
    if request is not None:
        return f"{request.scheme}://{request.get_host()}"
    return _origin_of(getattr(settings, "LABS_PUBLIC_URL", "") or "")


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
        "img-src data: blob: " + " ".join(MAPBOX_PATHS + TILE_HOSTS),
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


def host_payload(request, mode: str = "page") -> dict:
    """What the host runner needs to draw the frame (``workflow_data.sandbox``).

    ``mode`` is ``"page"`` or ``"workflow"``: the host answers a workflow's phase-2
    requests (components/workflow/sandbox/routes.ts) only in workflow mode.
    """
    return {"src": SHELL_PATH, "origin": labs_origin(request), "mode": "workflow" if mode == "workflow" else "page"}


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
