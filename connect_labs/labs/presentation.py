"""Presentation ("present") mode for shared labs pages.

Workflow run pages are increasingly used as funder- and partner-facing
dashboards, not just internal authoring surfaces. Everything a workflow
author controls lives inside ``render_code``; the *application shell* around
it — the breadcrumb, the multi-opp "Opportunities: N selected / Edit"
control, the header context selector with its raw database ids — is rendered
by Django templates outside the runner mount point, so no amount of authoring
work can remove it. See dimagi-internal/connect-labs#1295.

Presentation mode is the opt-in that suppresses that shell. It is a URL query
parameter rather than a workflow ``config`` key on purpose: a link that has
already been sent to people opts in by appending one parameter, with no
workflow edited or re-saved, and different recipients of the same workflow can
get different chrome.

Scope, deliberately: this is a *chrome* concern only. It does not suppress
write affordances inside the workflow's own render output — some funder-facing
pages are still meant to be interactive for the person holding the link, and
real write-protection is owned by the saved-run immutability rules
(WORKFLOW_REFERENCE.md §9). It is also not an access-control mechanism: it
hides internal identifiers from the *rendered page*, but the viewer still
authenticates normally and the underlying APIs are unchanged.
"""

PRESENT_PARAM = "present"

# Accepted truthy spellings. Anything else — including the parameter's absence
# — is normal mode, so a typo degrades to today's behaviour rather than to a
# half-suppressed page.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_present_mode(request) -> bool:
    """True when ``?present=1`` (or another accepted truthy spelling) is set.

    Tolerant of a request object with no ``GET`` (some internal render paths
    pass a bare stub), which reads as normal mode.
    """
    params = getattr(request, "GET", None)
    if not params:
        return False
    raw = params.get(PRESENT_PARAM)
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUTHY
