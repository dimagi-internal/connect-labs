"""Content-Security-Policy, report-only to begin with.

The audit called CSP "the single highest-leverage systemic backstop for every
current and future HTML-injection bug in this PHI-adjacent app" (#1032 item H),
and there was none anywhere. Every XSS fix so far has been per-sink: the
solicitation markdown, the `</script>` breakout, the fund report. Each of those
needed someone to find the sink first. A CSP catches the next one nobody found.

It ships **report-only**, deliberately. This app loads scripts from three CDNs
and uses inline `<script>` and inline event handlers extensively, so an enforcing
policy written blind would break pages — and a CSP that breaks pages gets
reverted, which is worse than not having one. Report-only produces the evidence
needed to write the enforcing version: turn on `CSP_REPORT_URI`, collect for a
week, tighten, then flip `CSP_REPORT_ONLY` to False.

Everything is env-overridable so the policy can be tightened without a deploy of
this file.
"""

from __future__ import annotations

from django.conf import settings

# Hosts the app genuinely loads from today. Narrowing this list is the point of
# the report-only period — these are observed, not endorsed.
DEFAULT_SCRIPT_SRC = (
    "'self'",
    "'unsafe-inline'",  # inline <script> blocks and on* handlers, pervasive today
    "'unsafe-eval'",  # Babel transpiles workflow JSX in the browser
    "https://cdn.jsdelivr.net",
    "https://unpkg.com",
    "https://api.mapbox.com",
    "https://cdn.tailwindcss.com",
)
DEFAULT_STYLE_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://cdn.jsdelivr.net",
    "https://unpkg.com",
    "https://api.mapbox.com",
    "https://fonts.googleapis.com",
)
DEFAULT_IMG_SRC = ("'self'", "data:", "blob:", "https:")
DEFAULT_CONNECT_SRC = ("'self'", "https:", "wss:")
DEFAULT_FONT_SRC = ("'self'", "data:", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net")
DEFAULT_FRAME_ANCESTORS = ("'none'",)


def build_policy() -> str:
    def directive(name: str, default: tuple[str, ...]) -> str:
        key = "CSP_" + name.replace("-", "_").upper()
        values = getattr(settings, key, None) or default
        return f"{name} {' '.join(values)}"

    parts = [
        directive("default-src", ("'self'",)),
        directive("script-src", DEFAULT_SCRIPT_SRC),
        directive("style-src", DEFAULT_STYLE_SRC),
        directive("img-src", DEFAULT_IMG_SRC),
        directive("connect-src", DEFAULT_CONNECT_SRC),
        directive("font-src", DEFAULT_FONT_SRC),
        # Not overridable and not merely cosmetic: `object-src 'none'` and
        # `base-uri 'self'` close two injection routes that survive an otherwise
        # tight script-src (a plugin object, and a rewritten <base> that
        # re-points every relative script URL).
        "object-src 'none'",
        "base-uri 'self'",
        directive("frame-ancestors", DEFAULT_FRAME_ANCESTORS),
    ]
    report_uri = getattr(settings, "CSP_REPORT_URI", None)
    if report_uri:
        parts.append(f"report-uri {report_uri}")
    return "; ".join(parts)


class ContentSecurityPolicyMiddleware:
    """Attach a CSP header. Report-only unless ``CSP_REPORT_ONLY`` is False.

    Skips non-HTML responses: a policy on a JSON API response or a CCZ download
    is pure header weight, and streaming responses (the SSE dashboards) should
    not be touched at all.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "CSP_ENABLED", True)
        self.report_only = getattr(settings, "CSP_REPORT_ONLY", True)
        self.policy = build_policy()

    def __call__(self, request):
        response = self.get_response(request)
        if not self.enabled:
            return response
        content_type = response.get("Content-Type", "")
        if not content_type.startswith("text/html"):
            return response
        header = "Content-Security-Policy-Report-Only" if self.report_only else "Content-Security-Policy"
        if header not in response:
            response[header] = self.policy
        return response
