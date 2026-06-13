"""MCP tool: realize a composite synthetic ENV manifest server-side on labs.

``ensure_synthetic_env(env)`` is the server-side counterpart to running
``python -m commcare_connect.labs.synthetic.ensure <env>`` locally. It maps an
env NAME (e.g. ``"program-admin-report"``) to the checked-in manifest at
``commcare_connect/labs/synthetic/envs/<env>.yaml`` (resolved off the package
dir, not the cwd) and hands it to the ensure engine.

WHY this exists: the ensure engine writes through Django ORM into whatever DB
the process runs against. A walkthrough ``setup:`` command runs LOCALLY (the
recorder's machine), so a local ``python -m ...ensure`` would seed the LOCAL
dev DB — but the recorder drives labs PROD. Running ensure as an MCP tool makes
it execute *inside* the deployed labs app, where labs-only synthetic opps live
and ``LabsRecordAPIClient`` short-circuits to the local-records backend on the
labs DB. Same rationale as ``program_admin_demo_seed``; this is the
env-manifest-driven successor.

Thin ``@register`` shim — env resolution + realization live in the
``labs/synthetic/ensure`` package. Returns the realized id map over the wire
(no ``out`` file written server-side; the caller persists ``realized.json``).
"""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.synthetic.ensure.engine import ensure_synthetic_data, resolve_env_path

from ..tool_registry import MCPToolError, register


@register(
    name="ensure_synthetic_env",
    description=(
        "Realize a composite synthetic ENVIRONMENT manifest server-side on labs "
        "(idempotent). Maps an env NAME to the checked-in manifest at "
        "commcare_connect/labs/synthetic/envs/<env>.yaml and runs the ensure "
        "engine in-app, so labs-only synthetic opps are written through the "
        "local-records backend on the labs DB — the only transport that reaches "
        "labs prod for synthetic opportunities. Returns the realized id map (the "
        "${...} vars a walkthrough spec interpolates: par_run_id, par_url, "
        "good_*/incomplete_* drill targets, wk4_*, etc.). Re-running does not "
        "duplicate or churn ids (current-week runs may reset per the manifest). "
        "Use env='program-admin-report' for the PAR demo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "env": {
                "type": "string",
                "description": (
                    "Env manifest name (a single plain segment, e.g. "
                    "'program-admin-report'). Resolves to "
                    "commcare_connect/labs/synthetic/envs/<env>.yaml. Path "
                    "separators and '..' are rejected."
                ),
            },
        },
        "required": ["env"],
        "additionalProperties": False,
    },
    is_write=True,
)
def ensure_synthetic_env(user, *, env: str) -> dict[str, Any]:
    try:
        env_path = resolve_env_path(env)
    except ValueError as exc:
        raise MCPToolError("NOT_FOUND", str(exc))
    return ensure_synthetic_data(str(env_path))
