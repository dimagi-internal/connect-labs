"""What a restricted MCP caller may reach: "no user visit data".

A caller is restricted when it comes in on the ``/mcp/no_user_visit/`` endpoint, or
holds a restricted credential (a ``no-uservisit-data`` Personal Access Token, or an
OAuth sign-in with the ``mcp:no-uservisit-data`` scope). Either is enough; the rule
is the same.

The rule: a restricted caller never sees REAL user visit data -- no visit rows or
per-visit values, raw or computed by a pipeline, a workflow run, a snapshot or a
report. Opportunity-level counts and dates, and contact details of people who
submitted as an organisation, are on the allowed side of that line. It may profile
real opportunities server-side (a profile is aggregate statistics) and generate
synthetic data from a profile, because generated data is never real.

Three sets, all deny-by-default -- a tool added to the catalogue reaches no
restricted caller until someone adds it here on purpose:

* ``NO_USERVISIT_DATA_TOOLS`` -- reachable outright: definitions, directories,
  targeting, and the synthetic profile-and-generate flow.
* ``GENERATED_ONLY_TOOLS`` -- tools that read visit data, reachable only when every
  opportunity the call reads holds GENERATED data (``connect_labs.mcp.visit_access``,
  on ``connect_labs.labs.synthetic.provenance``).
* ``USERVISIT_DATA_TOOLS`` -- read tools that return visit data. None of them may be
  reachable outright; a test pins it.
"""

from __future__ import annotations

FULL = "full"
NO_USERVISIT_DATA = "no-uservisit-data"

SCOPE_CHOICES = [
    (FULL, "Full access"),
    (NO_USERVISIT_DATA, "No user visit data"),
]

#: The scope string stamped on a resolved PAT, which is what the tool gate reads.
TOKEN_SCOPE_STRINGS = {
    FULL: "connect_labs:user",
    NO_USERVISIT_DATA: "connect_labs:no-uservisit-data",
}

#: The OAuth scope an MCP sign-in through the restricted endpoint asks for.
OAUTH_NO_USERVISIT_SCOPE = "mcp:no-uservisit-data"

#: Scope strings that make a caller restricted, whichever way it authenticated.
RESTRICTED_SCOPE_STRINGS = frozenset({TOKEN_SCOPE_STRINGS[NO_USERVISIT_DATA], OAUTH_NO_USERVISIT_SCOPE})

#: The endpoint marker ``config/asgi.py`` puts on a request to ``/mcp/no_user_visit/``.
NO_USER_VISIT_ENDPOINT = "no_user_visit"

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
        # The organisation directory and EOI rounds. Contacts are people who
        # submitted as an organisation; a round's applicant outcome is dated
        # from the org's first visit (a date, not a visit).
        "marketplace_orgs_get",
        "marketplace_rounds_list",
        # Targeting: population and burden data, not visits.
        "targeting_indicators",
        "targeting_select",
        "targeting_methodology",
        "targeting_scenario",
        "targeting_admin_levels",
        "targeting_research",
        "targeting_compare_criteria",
        # Microplans. A plan handed off from WA Revisit carries per-ward
        # figures computed from visits (children per building) -- aggregates,
        # not visits, which is on the allowed side of the line.
        "microplans_list_plans",
        "microplans_plan_work_areas",
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


#: The synthetic flow: profile a real opp server-side, keep the profile, generate a
#: synthetic set from it. A profile is aggregate statistics (distributions, real
#: min/max) by design; generation writes only generated data, which the generator
#: marks as such. Reaching these needs no check on the target opp.
SYNTHETIC_TOOLS: frozenset[str] = frozenset(
    {
        # The starting point and its status: clone an opportunity in one call. The
        # step-by-step profile/generate tools stay on the full address for the people
        # who build with them; a restricted caller gets the one flow that is enough.
        "synthetic_clone_opp",
        "synthetic_job_status",
        "synthetic_profile_status",
        # Score a clone against its source (aggregate statistics only).
        "synthetic_fidelity_vs_source",
        # Demo environments (fresh=true is refused: visit_access.synthetic_denied_reason).
        "synthetic_env_ensure",
        "synthetic_set_my_visibility",
        "synthetic_local_records_count",
        # Compiles a pipeline to SQL text; executes nothing.
        "pipeline_sql",
    }
)

#: Tools that read visit data, or change an opp's registry row, reachable only when
#: every opportunity the call reads holds generated data. Each has a resolver in
#: ``connect_labs.mcp.visit_access.RESOLVERS``; a test pins that they match.
GENERATED_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "pipeline_preview",
        "custom_analysis_run",
        "synthetic_local_record_dump",
        "synthetic_reload_fixtures",
        "synthetic_disable",
        "synthetic_set_allowed_domains",
        "task_create_synthetic",
        "workflow_run_context",
        "workflow_run_indicators",
        "workflow_indicator_explain",
        "workflow_action_status",
        "workflow_preview_snapshot",
        "workflow_history_runs",
        "workflow_preview_as_of",
    }
)

#: Read tools that return visit data (real, unless the opp is generated). None may
#: be reachable outright.
USERVISIT_DATA_TOOLS: frozenset[str] = frozenset(
    {
        "pipeline_preview",
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
        # Reads a bundle at any server path it is given.
        "synthetic_fidelity_report",
    }
)

#: Everything a restricted caller can list. The generated-only tools are listed and
#: then checked per call, on the opportunities that call reads.
RESTRICTED_TOOLS: frozenset[str] = NO_USERVISIT_DATA_TOOLS | SYNTHETIC_TOOLS | GENERATED_ONLY_TOOLS


def is_restricted(scopes) -> bool:
    """True when any of these scope strings makes the caller restricted."""
    return bool(RESTRICTED_SCOPE_STRINGS & set(scopes or []))
