"""A Python mirror of the sandbox's route table, for static checks on the server.

The table itself is ``components/workflow/sandbox/routes.ts``: what a sandboxed
workflow's ``fetch`` may make labs do, decided by the HOST page in the browser. The
server never enforces it; ``workflow_sandbox_scan`` only needs to know, before the
rollout goes to ``all``, which labs paths a workflow's render code asks for that the
host would REFUSE.

Why a mirror, and not reading routes.ts at run time: the scan runs as a one-off
Fargate task (``run-labs-command.yml``) in the deployed image. routes.ts happens to be
in that image today (the Dockerfile copies the whole tree), but nothing promises it,
and a TypeScript file is only parseable here with a regex parser that would fail at
run time, on prod, the day someone reformats it. So the mirror below is plain data,
and ``test_workflow_sandbox_scan.py::test_route_mirror_matches_routes_ts`` parses
routes.ts in CI and fails when the two differ -- a route added, removed, re-gated or
given another method, or FETCH_DENIED changed. Change routes.ts, and this file with it.

Patterns are kept in routes.ts's own (JavaScript) syntax so the comparison is literal;
``compiled()`` turns ``(?<name>`` into Python's ``(?P<name>``. Only methods, path and
gate are mirrored: body checks and id binding depend on the workflow being drawn and
cannot be checked statically (the scan says so).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

GET = ("GET", "HEAD")
POST = ("POST",)


@dataclass(frozen=True)
class Route:
    methods: tuple[str, ...]
    path: str  # the anchored pattern, in routes.ts's syntax
    gate: str  # read | own | effect


ROUTES: tuple[Route, ...] = (
    # Reads: workflow runs, history, indicators
    Route(GET, r"^/labs/workflow/api/run/(\d+)/$", "read"),
    Route(GET, r"^/labs/workflow/api/run/(\d+)/snapshot/(preview/)?$", "read"),
    Route(GET, r"^/labs/workflow/api/(\d+)/runs/history/$", "read"),
    Route(GET, r"^/labs/workflow/api/(\d+)/(indicator-definitions|semantic)/$", "read"),
    Route(GET, r"^/labs/workflow/api/run/(?<run>\d+)/flags/list/$", "read"),
    Route(
        GET,
        r"^/labs/workflow/api/(?<def>\d+)/"
        r"(pipeline-rows|pipeline-rows/stream|case-states|pipeline-data|supply-data|workflow-data)/$",
        "read",
    ),
    Route(POST, r"^/labs/workflow/api/(?<def>\d+)/(pipeline-query|supply-query|workflow-query)/$", "read"),
    Route(GET, r"^/labs/workflow/api/auth-status/$", "read"),
    Route(
        GET,
        r"^/labs/workflow/api/(workers|open-tasks|worker-tasks|chatbot-status|prev-categories|open-run-state"
        r"|run-category-history|flw-audit-report-history|flw-daily-indicator-history|flw-daily-summary-history)/$",
        "read",
    ),
    Route(POST, r"^/labs/workflow/api/opportunity-flws/$", "read"),
    Route(GET, r"^/labs/workflow/api/ocs/(status|bots)/$", "read"),
    Route(GET, r"^/labs/workflow/api/job/([A-Za-z0-9_.:-]+)/(status/|status\.json)$", "read"),
    Route(GET, r"^/labs/workflow/api/image/(?<opp>\d+)/([^/]+)/$", "read"),
    Route(GET, r"^/labs/workflow/api/(?<opp>\d+)/visit-images/$", "read"),
    Route(GET, r"^/audit/image/(?<opp>\d+)/([^/]+)/$", "read"),
    # Reads: audits, tasks, benchmarks, boundaries
    Route(
        GET,
        r"^/audit/api/opportunity/(?<opp>\d+)/(image-questions|field-questions|deliver-unit-types|sessions-summary)/$",
        "read",
    ),
    Route(GET, r"^/audit/api/program/(?<prog>\d+)/sessions-summary/$", "read"),
    Route(GET, r"^/audit/api/scope-context/$", "read"),
    Route(GET, r"^/audit/api/ai-agents/$", "read"),
    Route(GET, r"^/audit/api/workflow/(\d+)/sessions/$", "read"),
    Route(GET, r"^/audit/api/(\d+)/bulk-data/$", "read"),
    Route(POST, r"^/audit/api/audit/preview/$", "read"),
    Route(GET, r"^/audit/api/audit/task/([A-Za-z0-9_.:-]+)/(status|stream)/$", "read"),
    Route(GET, r"^/tasks/api/(\d+)/$", "read"),
    Route(GET, r"^/tasks/(\d+)/ai/(sessions|transcript)/$", "read"),
    Route(GET, r"^/labs/benchmarks/api/(?<opp>\d+)/$", "read"),
    Route(GET, r"^/microplans/boundaries/(viewport|countries)/$", "read"),
    Route(GET, r"^/microplans/(?<opp>\d+)/boundaries/(areas|geometry|population_by_name)/$", "read"),
    # Own: this run's and this workflow's own records
    Route(
        POST, r"^/labs/workflow/api/run/(?<run>\d+)/(state|worker-result|flags|rename|reconcile-generation)/$", "own"
    ),
    Route(POST, r"^/labs/workflow/api/(?<def>\d+)/audit-batch-config/$", "own"),
    Route(POST, r"^/labs/workflow/api/job/(?<job>[A-Za-z0-9_.:-]+)/cancel/$", "own"),
    # Effect: past this run; the host asks first
    Route(POST, r"^/labs/workflow/api/run/(?<run>\d+)/job/start/$", "effect"),
    Route(POST, r"^/labs/workflow/api/run/(?<run>\d+)/complete/$", "effect"),
    Route(POST, r"^/labs/workflow/api/run/(?<run>\d+)/delete/$", "effect"),
    Route(POST, r"^/labs/workflow/api/run/(?<run>\d+)/(manager-audit|manager-coaching)/$", "effect"),
    Route(POST, r"^/tasks/api/single-create/$", "effect"),
    Route(POST, r"^/tasks/api/(?<rec>\d+)/(update|comment)/$", "effect"),
    Route(POST, r"^/tasks/(?<rec>\d+)/ai/initiate/$", "effect"),
    Route(POST, r"^/tasks/(?<rec>\d+)/ai/save-transcript/$", "effect"),
    Route(POST, r"^/audit/api/audit/(create|create-async)/$", "effect"),
    Route(POST, r"^/audit/api/audit/task/(?<rec>[A-Za-z0-9_.:-]+)/cancel/$", "effect"),
    Route(POST, r"^/audit/api/(?<rec>\d+)/delete/$", "effect"),
)

#: Never reachable, whatever a route says (routes.ts FETCH_DENIED).
FETCH_DENIED: tuple[str, ...] = ("/o/", "/accounts/", "/admin/", "/labs/mcp/", "/mcp/", "/labs/sandbox/")


@lru_cache(maxsize=1)
def compiled() -> tuple[tuple[Route, re.Pattern], ...]:
    return tuple((r, re.compile(r.path.replace("(?<", "(?P<"))) for r in ROUTES)


@dataclass(frozen=True)
class RouteVerdict:
    allowed: bool
    reason: str  # "" when allowed
    gate: str | None = None
    needs_run: bool = False  # the route binds a run id: refused on a page (it has none)


def check_path(path: str, method: str = "GET") -> RouteVerdict:
    """Whether the host would answer ``method path`` at all (ignoring id binding)."""
    method = (method or "GET").upper()
    if any(path.startswith(d) for d in FETCH_DENIED):
        return RouteVerdict(False, "never reachable from a workflow (FETCH_DENIED)")
    for route, pattern in compiled():
        match = pattern.match(path)
        if match:
            if method not in route.methods:
                return RouteVerdict(False, f"{method} is not allowed on this route (only {'/'.join(route.methods)})")
            return RouteVerdict(True, "", route.gate, needs_run="run" in match.groupdict())
    return RouteVerdict(False, "no route in the sandbox table")
