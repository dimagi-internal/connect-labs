"""What a Personal Access Token may reach.

A PAT acts as the person who minted it. ``full`` reaches every tool, as PATs
always have. ``no-uservisit-data`` is for handing an agent a token that can read how
things are BUILT -- workflow definitions, pipeline schemas, indicator
registries, app structure, solicitations, the org directory, targeting -- but
never visit data: no rows, raw or computed from them by a pipeline, a workflow
run, a snapshot or a report. It writes nothing either.

The list is deny-by-default: a tool added to the catalogue is out of reach of a
no-uservisit-data token until someone adds it here on purpose, having checked that
nothing it returns is read from visits. ``USERVISIT_DATA_TOOLS`` names the read
tools that DO return visit data, so a test can refuse them here even if
someone reaches for them.
"""

from __future__ import annotations

FULL = "full"
NO_USERVISIT_DATA = "no-uservisit-data"

SCOPE_CHOICES = [
    (FULL, "Full access"),
    (NO_USERVISIT_DATA, "No user visit data (no writes)"),
]

#: The scope string stamped on the resolved access token, which is what the
#: tool gate in ``server.allowed_tools`` reads.
TOKEN_SCOPE_STRINGS = {
    FULL: "connect_labs:user",
    NO_USERVISIT_DATA: "connect_labs:no-uservisit-data",
}

NO_USERVISIT_DATA_TOOLS: frozenset[str] = frozenset(
    {
        # Workflow definitions: config, statuses, render code, pipeline wiring.
        "workflow_list",
        "workflow_get",
        "workflow_authoring_guide",
        "workflow_history_eligibility",
        "list_templates",
        # CommCare app structure (modules, forms, questions), never submissions.
        "get_opportunity_apps",
        # Pipeline schemas and indicator registries: the definitions, not the rows.
        "pipeline_list",
        "pipeline_get",
        "semantic_registry_list",
        "semantic_registry_get",
        "semantic_registry_validate",
        "semantic_registry_explain",
        # Who can see what.
        "labs_context",
        # Solicitations, responses, reviews and funds.
        "list_solicitations",
        "get_solicitation",
        "list_responses",
        "get_response",
        "list_reviews",
        "get_review",
        "list_funds",
        "get_fund",
        # The organisation directory and EOI rounds.
        "marketplace_orgs_get",
        # Targeting: population and burden data, not visits.
        "targeting_indicators",
        "targeting_select",
        "targeting_methodology",
        "targeting_scenario",
        "targeting_admin_levels",
        "targeting_research",
        "targeting_compare_criteria",
        # Microplans: the parameter schema only. A plan's work areas are not
        # here: a plan handed off from WA Revisit carries per-ward figures
        # computed from approved visits (mopup/core/handoff.py), and its set of
        # work areas is itself the set that failed visit-derived coverage.
        "microplans_coverage_param_schema",
        # Page and cohort definitions, synthetic env templates.
        "pages_list_providers",
        "pages_list",
        "pages_get",
        "benchmarks_cohort_list",
        "synthetic_env_list",
        "synthetic_env_get",
    }
)

#: Read tools that return visit data, raw or computed. Never reachable by a no-uservisit-data token.
USERVISIT_DATA_TOOLS: frozenset[str] = frozenset(
    {
        "pipeline_preview",
        "pipeline_sql",
        "workflow_run_context",
        "workflow_run_indicators",
        "workflow_indicator_explain",
        "workflow_action_status",
        "workflow_history_runs",
        "workflow_preview_as_of",
        "workflow_preview_snapshot",
        "custom_analysis_run",
        "get_sample_ids",
        "synthetic_local_record_dump",
        "synthetic_profile_from_prod",
        "synthetic_profile_opp",
        "synthetic_profile_opps_bulk",
        "synthetic_fidelity_report",
        "synthetic_fidelity_vs_source",
        "synthetic_clone_profile",
        # Plan work areas carry WA Revisit's visit-derived ward figures.
        "microplans_list_plans",
        "microplans_plan_work_areas",
        # include_applicants dates each org's outcome from its first visit.
        "marketplace_rounds_list",
    }
)

#: Fields a no-uservisit-data token must not see in an otherwise allowed tool's
#: result, stripped at any depth. ``labs_context`` carries each opportunity's
#: ``visit_count``, which is an aggregate of visits.
REDACTED_FIELDS: dict[str, frozenset[str]] = {
    "labs_context": frozenset({"visit_count"}),
}


def is_restricted(scopes) -> bool:
    return TOKEN_SCOPE_STRINGS[NO_USERVISIT_DATA] in (scopes or [])


def allowed_tools(scopes) -> frozenset[str] | None:
    """The tools a PAT with these scopes may reach, or ``None`` for every tool."""
    if is_restricted(scopes):
        return NO_USERVISIT_DATA_TOOLS
    return None


def redact(scopes, tool_name: str, result):
    """``result`` with ``REDACTED_FIELDS[tool_name]`` removed at any depth, for a restricted token."""
    fields = REDACTED_FIELDS.get(tool_name)
    if not fields or not is_restricted(scopes):
        return result
    return _strip(result, fields)


def _strip(value, fields: frozenset[str]):
    if isinstance(value, dict):
        return {k: _strip(v, fields) for k, v in value.items() if k not in fields}
    if isinstance(value, list):
        return [_strip(v, fields) for v in value]
    return value
