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

"No user visit data" is about what a caller SEES, not whether it may change things.
A restricted caller may edit workflow and indicator definitions and trigger
server-side computation over visits (saved runs, snapshots, history rebuilds, cache
warms, hand-downs, benchmark publications), provided the tool's RESPONSE carries no
visit-derived values. The visits are read on the server; what they compute stays
there. Each tool's own permission checks (membership, home scope, organisation
access) still apply -- the scope only decides what is reachable.

The endpoint and the credential are two ways to ask for the SAME thing and resolve to
the same tool set: ``server.restricted_call`` is true for either, and
``server.allowed_tools`` then answers ``RESTRICTED_TOOLS`` whichever made it true. A
test pins that the two list identical tools.

Four sets, all deny-by-default -- a tool added to the catalogue reaches no
restricted caller until someone adds it here on purpose:

* ``NO_USERVISIT_DATA_TOOLS`` -- reachable outright: definitions, directories,
  targeting, and the synthetic profile-and-generate flow.
* ``GENERATED_ONLY_TOOLS`` -- tools that read visit data, reachable only when every
  opportunity the call reads holds GENERATED data. Render code edits
  (``workflow_update_render_code``, ``workflow_patch_render_code``) are here too,
  although they return nothing: render code executes over real data in a
  full-access viewer's browser, so a restricted caller may edit it only on a
  workflow whose every opportunity is generated (``connect_labs.mcp.visit_access``,
  on ``connect_labs.labs.synthetic.provenance``).
* ``DEFINITION_WRITE_TOOLS`` -- writes reachable outright: tools that change a
  definition (a workflow, its opportunity list, a semantic registry)
  or start server-side computation, and whose response is ids, versions, statuses,
  dates and opportunity-level counts only. A tool whose response carries a snapshot
  payload, indicator values, rows, or per-worker or per-case data never belongs here
  (``workflow_preview_snapshot``, ``workflow_preview_as_of`` and
  ``workflow_history_runs`` stay generated-only). Destructive tools that are not
  definition edits (``workflow_delete``, ``pipeline_delete`` ...) are not in it.
* ``USERVISIT_DATA_TOOLS`` -- read tools that return visit data. None of them may be
  reachable outright; a test pins it, and that the write set is disjoint from it.

A residual risk the response line does not cover, recorded so nobody mistakes the
write set for "cannot learn anything about visits in any way":

* Error text. ``workflow_ensure_visit_cache`` and ``workflow_rebuild_history`` report a
  failing pipeline or snapshot build by its exception message, and
  ``workflow_hand_down`` its per-opportunity errors. Those are configuration and
  infrastructure messages, but a pipeline's error (a failed cast, say) could quote a
  value it choked on.
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
        # Modelled deaths averted from public rates and caller-supplied costs.
        "targeting_cost_effectiveness",
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

#: Tools that read visit data, change an opp's registry row, or write render code that
#: will run over visit data, reachable only when
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
        # WRITES, gated like the readers: render code executes over real data in a
        # full-access viewer's browser, so planting it is reading visits by proxy.
        # Not in USERVISIT_DATA_TOOLS -- they return no data.
        "workflow_update_render_code",
        "workflow_patch_render_code",
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

#: Writes a restricted caller reaches outright. Every response here is ids, versions,
#: statuses, dates and opportunity-level counts; see the module docstring for the rule
#: and its residuals. Each entry's return value was read before it was added.
DEFINITION_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # Indicator registries: the definitions (returns id/name/version/counts).
        "semantic_registry_create",
        "semantic_registry_update",
        "semantic_registry_set_indicator_meta",
        # Workflow definitions and opportunity list (returns versions;
        # update_opportunity_ids also the ids it set, each checked against the
        # caller's own opportunities). The generated-only resolvers read the
        # definition's opportunity_ids live, so pointing a workflow at a real
        # opportunity makes its visit readers refuse, not leak.
        "workflow_update_definition",
        "workflow_update_opportunity_ids",
        # Runs and snapshots, computed and stored server-side. create_run returns the
        # run's id and period; save_snapshot the run id, name, captured_at and
        # opportunity ids -- the snapshot itself is never returned
        # (workflow_preview_snapshot is the reader, and stays generated-only).
        "workflow_create_run",
        "workflow_save_snapshot",
        # Per opportunity and pipeline: raw visit COUNT, computed row COUNT, held or
        # refreshed, hold-until, error.
        "workflow_ensure_visit_cache",
        # Per period: run id, action, error. dry_run writes nothing.
        "workflow_rebuild_history",
        # Run ids, periods, statuses of the runs it deleted (or would delete).
        "workflow_prune_history",
        # Created / replaced / unchanged / failed counts, and per-report errors.
        "workflow_hand_down",
        # Publication id, cohort id, as-of date, value count, withheld indicator ids.
        "benchmarks_publish",
    }
)

#: Everything a restricted caller can list. The generated-only tools are listed and
#: then checked per call, on the opportunities that call reads.
RESTRICTED_TOOLS: frozenset[str] = (
    NO_USERVISIT_DATA_TOOLS | SYNTHETIC_TOOLS | GENERATED_ONLY_TOOLS | DEFINITION_WRITE_TOOLS
)


def is_restricted(scopes) -> bool:
    """True when any of these scope strings makes the caller restricted."""
    return bool(RESTRICTED_SCOPE_STRINGS & set(scopes or []))
