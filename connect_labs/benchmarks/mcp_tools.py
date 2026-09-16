"""Cohort administration -- the missing write path for Plan 1's benchmark store.

Plan 1 shipped models, disclosure rules, a publisher, and a read API, but no
way to create a cohort or add opportunities to one, so nothing could ever be
published and the read API returned ``{}`` forever. These are plain local
ORM writes against ``BenchmarkCohort`` / ``BenchmarkCohortMember`` -- the labs
DB is the system of record for benchmarks (see models.py), so unlike most
labs apps there is no Connect token or LabsRecord API involved here.

Who-is-this-caller used to be answered here twice -- once for organisation
membership, once for opportunity holding -- reimplementing what
``connect_labs.labs.access.scopes`` (``may_use``) now answers once, for every
REAL-scoped app that keeps its own data in local Postgres (``data_access.py``
consults the same module for the web read path). That module owns the
network fetch and its TTL cache; this module keeps only what is genuinely its
own. The labs-only synthetic-opp merge used to live here too -- it is now
inside ``scopes`` itself, because the token surface needed it just as much as
the web one and only the web one had it.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from connect_labs.benchmarks.models import MIN_PEERS_FLOOR, BenchmarkCohort, BenchmarkCohortMember
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.labs.access import scopes
from connect_labs.labs.access.scopes import Caller, may_use
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError, register
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import create_workflow_from_template, get_template, resolve_snapshot_contract

logger = logging.getLogger(__name__)


def _raise_for_denial(reason: str | None, *, what: str | None = None) -> None:
    """Map a `may_use` refusal onto this registry's error codes.

    `may_use` returns the one named string ``scopes.UNKNOWABLE`` when it could
    not REACH Connect to check -- a network blip, not "you belong to
    nothing". ``workflows.py`` draws exactly that
    line (UPSTREAM_ERROR vs PERMISSION_DENIED) so an authorised publisher
    hitting a blip is not told they lack permission. Both branches still fail
    CLOSED -- this raises either way, and nothing is written.
    """
    if reason is None:
        return
    if reason == scopes.UNKNOWABLE:
        _raise_upstream()
    raise MCPToolError("PERMISSION_DENIED", f"{reason}, which {what}." if what else reason)


def _raise_upstream() -> NoReturn:
    raise MCPToolError(
        "UPSTREAM_ERROR",
        "Could not reach production Connect to check your access, so nothing could be "
        "verified. Nothing was written -- retry.",
    )


def _caller_opportunity_ids(caller: Caller) -> set[int]:
    """Opportunity ids the caller holds -- production's and labs-only alike.

    The labs-only merge now happens inside the shared policy (which the web
    surface got for free via ``get_org_data`` and the token surface did not),
    so this is a straight delegation plus the one thing a set cannot carry:
    ``ScopesUnavailable`` means Connect could not be REACHED. Returning an
    empty set there would tell an authorised caller "you do not hold
    opportunity N" because of a network blip -- the exact misdiagnosis
    ``_raise_for_denial``'s UPSTREAM_ERROR branch exists to prevent. This is a
    SECOND resolution (the organisation gate made the first), so the window
    between them is real: a TTL expiry plus a blip lands exactly here.
    """
    try:
        return scopes.opportunity_ids(caller)
    except scopes.ScopesUnavailable:
        _raise_upstream()


def _require_organization_access(user, organization_id: str, what: str) -> tuple[str, Caller]:
    """The single organisation gate for every write in this module.

    Membership of a cohort IS the read permission (models.py), so a caller who
    can add an opportunity they hold to someone else's cohort has granted
    themselves that cohort's published peer figures without ever calling
    publish. Every write therefore passes through here, gated by the same
    policy `data_access.py`'s read path consults -- and the resolved caller is
    returned so an opportunity-level check costs no extra round trip (Connect's
    own TTL cache absorbs the repeat fetch).
    """
    token = require_connect_token(user)
    caller = Caller(user=user, access_token=token)
    _raise_for_denial(may_use(caller, organization_id=organization_id), what=what)
    return token, caller


def _run_history(wda, workflow_id: int, state_key: str) -> list[dict]:
    """Every completed run of `workflow_id`, oldest first, projected to `byOpp`.

    A failure here costs the SERIES and nothing else, so it is logged and
    swallowed rather than taking the publication down with it: the point values
    are the publication's substance and they come from the snapshot already in
    hand. A publication with no series is a visible, recoverable state; a
    refused publication because a history read timed out is not.
    """
    # ONE POINT PER PERIOD, not per run. A period can hold several completed
    # runs -- a hand-saved one and the one `workflow_rebuild_history` generated
    # for the same week, or seven re-runs of the same week while something was
    # being fixed -- and they do not agree, because each was computed from what
    # was cached when it ran. Taking all of them made consecutive points
    # alternate between two unrelated figures for the whole length of the
    # series, which renders as a violently oscillating indicator rather than as
    # the duplication it is. The LATEST completion of a period wins: a
    # recomputation supersedes what it recomputed.
    latest: dict[str, tuple] = {}
    try:
        # The iteration is inside the guard, not just the call: `list_runs`
        # resolves lazily, so the upstream failure surfaces on the first `for`.
        for run in wda.list_runs(definition_id=workflow_id):
            if not getattr(run, "is_completed", False):
                continue
            payload = ((run.snapshot or {}).get("state") or {}).get(state_key) or {}
            by_opp = {}
            for name, block in [("C", payload)] + sorted((payload.get("series") or {}).items()):
                cells = {}
                for entry in (block or {}).get("byOpp") or []:
                    if entry.get("opp") is not None:
                        cells[int(entry["opp"])] = entry.get("ind") or {}
                if cells:
                    by_opp[name] = cells
            if not by_opp:
                continue
            period = str(run.period_end or run.completed_at or "")[:10]
            stamp = str(run.completed_at or "")
            if period not in latest or stamp >= latest[period][0]:
                latest[period] = (stamp, {"date": period, "byOpp": by_opp})
    except Exception:
        logger.warning("benchmark publication could not read run history for workflow %s", workflow_id, exc_info=True)
        return []
    return [entry for _, entry in sorted((p, e) for p, (_, e) in latest.items())]


def _serialize_cohort(cohort: BenchmarkCohort) -> dict[str, Any]:
    return {
        "id": cohort.pk,
        "name": cohort.name,
        "organization_id": cohort.organization_id,
        "description": cohort.description,
        "auto_publish_on_completion": cohort.auto_publish_on_completion,
        "min_peers": cohort.min_peers,
        "min_denominator": cohort.min_denominator,
        "opportunity_ids": sorted(cohort.opportunity_ids),
    }


@register(
    name="benchmarks_cohort_create",
    description=(
        "Create a benchmark cohort -- a named set of opportunities that may be "
        "benchmarked against each other. Membership IS the grant: an opportunity "
        "sees benchmarks for the cohorts it belongs to and nothing else, so the "
        "caller must belong to the organisation the cohort is created under. "
        f"min_peers must be >= {MIN_PEERS_FLOOR} (below that, the reader is one "
        "of the contributors, so the one remaining bar is a named peer's exact "
        "value)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "organization_id": {
                "type": "string",
                "description": "Connect organisation slug that owns and may publish this cohort (e.g. 'dimagi-kmc').",
            },
            "description": {"type": "string", "default": ""},
            "min_peers": {
                "type": "integer",
                "default": 5,
                "description": (
                    "Disclosure floor: minimum contributing peers required to publish a figure. "
                    f"Must be >= {MIN_PEERS_FLOOR}."
                ),
            },
            "min_denominator": {
                "type": "integer",
                "default": 25,
                "description": "Disclosure floor: minimum denominator required to publish a rate.",
            },
        },
        "required": ["name", "organization_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_create(
    user,
    *,
    name: str,
    organization_id: str,
    description: str = "",
    min_peers: int = 5,
    min_denominator: int = 25,
) -> dict[str, Any]:
    if min_peers < MIN_PEERS_FLOOR:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"min_peers must be >= {MIN_PEERS_FLOOR}: at {min_peers} the reader is one of the "
            "contributors, so the one remaining bar is a named peer's exact value.",
        )
    _require_organization_access(user, organization_id, "you are creating this cohort under")
    cohort = BenchmarkCohort.objects.create(
        name=name,
        organization_id=organization_id,
        description=description,
        min_peers=min_peers,
        min_denominator=min_denominator,
    )
    return _serialize_cohort(cohort)


@register(
    name="benchmarks_cohort_add_opportunities",
    description=(
        "Add opportunities to a benchmark cohort. Refuses a caller who does not "
        "belong to the cohort's organisation, or who does not hold one of the "
        "opportunities being added -- membership IS the read grant, so adding an "
        "opportunity to a cohort hands it that cohort's peer figures. Idempotent "
        "-- re-adding an opportunity already in the cohort is a no-op. Returns "
        "the cohort's full membership after the add."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
        },
        "required": ["cohort_id", "opportunity_ids"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_add_opportunities(
    user,
    *,
    cohort_id: int,
    opportunity_ids: list[int],
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"No cohort with id {cohort_id}") from exc

    _, caller = _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")

    # The org gate alone would still let a member of org A add an opportunity
    # belonging to org B into A's cohort, which publishes B's figures into a
    # cohort B never joined. The caller's opportunity list came back on the
    # SAME fetch the org gate already made, so this costs no extra round trip.
    held = _caller_opportunity_ids(caller)
    unheld = sorted({int(oid) for oid in opportunity_ids} - held)
    if unheld:
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"You do not hold opportunities {unheld}, so you cannot add them to cohort {cohort_id}.",
            details={"unheld_opportunity_ids": unheld},
        )

    for opportunity_id in opportunity_ids:
        BenchmarkCohortMember.objects.get_or_create(cohort=cohort, opportunity_id=int(opportunity_id))

    return _serialize_cohort(cohort)


@register(
    name="benchmarks_cohort_list",
    description=(
        "List benchmark cohorts owned by an organization, with membership counts. Refuses "
        "a caller who does not belong to that organisation: a cohort id is what an "
        "add-opportunities call needs to target, so listing is the enumeration step of "
        "the grant, not a neutral read."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "organization_id": {"type": "string"},
        },
        "required": ["organization_id"],
        "additionalProperties": False,
    },
)
def benchmarks_cohort_list(user, *, organization_id: str) -> dict[str, Any]:
    # Filtering is not authorisation. `.filter(organization_id=...)` scopes the
    # query to what was ASKED FOR and says nothing about whether the asker may
    # have it -- so without this gate the tool hands any authenticated caller
    # every cohort id in any organisation, which is precisely the target list
    # `benchmarks_cohort_add_opportunities` is now gated against.
    _require_organization_access(user, organization_id, "you are listing cohorts for")
    cohorts = BenchmarkCohort.objects.filter(organization_id=organization_id).order_by("name")
    return {
        "cohorts": [
            {
                "id": cohort.pk,
                "name": cohort.name,
                "organization_id": cohort.organization_id,
                "opportunity_count": cohort.members.count(),
                "min_peers": cohort.min_peers,
                "min_denominator": cohort.min_denominator,
            }
            for cohort in cohorts
        ]
    }


@register(
    name="benchmarks_publish",
    description=(
        "Publish a completed workflow run's graded figures to a benchmark cohort. Refuses "
        "a run that is not completed -- its figures are still moving -- refuses a caller "
        "who lacks access to the cohort's organisation, refuses a run that does not belong "
        "to the workflow_id given (that would record false provenance), and refuses a run "
        "that carries no as-of date. Everything the publisher's own "
        "disclosure rules withhold (non-rate indicators, cohorts below min_peers/min_denominator "
        "after the rules run) is withheld and reported back, never silently dropped. If the "
        "run's snapshot is not the shape this publisher reads -- or yields no benchmarkable "
        "observations at all -- the call fails loudly rather than writing an empty publication, "
        "so a caller must not treat a raised error as 'nothing to publish'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "workflow_id": {
                "type": "integer",
                "description": "The workflow definition id the run belongs to.",
            },
            "run_id": {"type": "integer"},
            "opportunity_id": {
                "type": "integer",
                "description": "Scope for loading the run, if it is opportunity-owned.",
            },
            "program_id": {
                "type": "integer",
                "description": "Scope for loading the run, if it is program-owned (multi-opp report).",
            },
        },
        "required": ["cohort_id", "workflow_id", "run_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_publish(
    user,
    *,
    cohort_id: int,
    workflow_id: int,
    run_id: int,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"Benchmark cohort {cohort_id} not found.") from exc

    token, _ = _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"Run {run_id} not found.")
        # A run loads fine under a workflow_id that is not its own, and the
        # mismatch would be invisible: the state_key would be resolved from a
        # FOREIGN definition's contract and `source_workflow_id` -- the
        # provenance an anonymised figure's defensibility rests on -- would be
        # written false.
        if run.definition_id and int(run.definition_id) != int(workflow_id):
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Run {run_id} belongs to workflow {int(run.definition_id)}, not {workflow_id}. "
                "Publishing it under the wrong workflow would record false provenance.",
            )
        if not run.is_completed:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Run {run_id} is not completed -- its figures are still moving and cannot be published.",
            )

        # The graded payload a saved run stores is one level down from
        # `run.snapshot`, under `["state"][<state_key>]` --
        # `workflow/snapshot_builders.wrap_for_runner` wraps it there so the
        # runner's `view.state.<key>` contract resolves, and `state_key`
        # defaults to "snapshot" but is spec-driven per workflow (see
        # `workflow/history_rebuild.py`'s identical resolution). Verified
        # against `connect_labs/semantic/snapshot.py::build` and
        # `connect_labs/benchmarks/publish.py`'s own docstring, both of which
        # name this exact path.
        definition = wda.get_definition(workflow_id)
        state_key = "snapshot"
        if definition is not None:
            contract = resolve_snapshot_contract(definition)
            if contract.get("ok"):
                state_key = (contract.get("snapshot_inputs") or {}).get("state_key") or "snapshot"

        state = (run.snapshot or {}).get("state") or {}
        graded_payload = state.get(state_key) or {}

        meta = graded_payload.get("meta") or {}
        registry_id = (meta.get("registry") or {}).get("registry_id")
        as_of = (
            meta.get("as_of")
            or (str(run.period_end)[:10] if run.period_end else None)
            or (str(run.completed_at)[:10] if run.completed_at else None)
        )
        if not as_of:
            # `as_of` is NOT NULL on BenchmarkPublication, so without this the
            # refusal is a raw psycopg IntegrityError naming a column, which
            # tells a caller nothing about which of the three sources it should
            # have populated.
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"Run {run_id} carries no as-of date: its snapshot has no meta.as_of, and the run "
                "has neither a period_end nor a completed_at to fall back on. A publication must "
                "be dated, so there is nothing to publish.",
            )

        # The SERIES comes from the workflow's completed runs, not from anything
        # inside the one being published: a saved run is one point of a trend,
        # each computed as of its own period end by the same builder, which is
        # what the programme report's own trend charts are drawn from.
        history = _run_history(wda, workflow_id, state_key)

        publication = publish_benchmark(
            cohort,
            snapshot=graded_payload,
            history=history,
            source_workflow_id=workflow_id,
            source_run_id=run_id,
            registry_id=registry_id,
            as_of=as_of,
            published_by=getattr(user, "username", "") or "",
        )
    finally:
        wda.close()

    return {
        "publication_id": publication.pk,
        "cohort_id": cohort.pk,
        "as_of": str(publication.as_of),
        "value_count": publication.values.count(),
        "withheld_indicator_ids": publication.withheld_indicator_ids,
    }


# =============================================================================
# Fan-out -- one opportunity report per cohort member
# =============================================================================
#
# A cohort is twelve opportunities, and each of them wants the SAME report over
# its own scope. Twelve copies of a workflow is twelve things to keep in step,
# and this repo has already paid for that: a render edited on one instance, a
# config flag that reached none of them, a pipeline copied per scope and then
# drifting. So the fan-out creates twelve instances that own as little as
# possible:
#
#   render      `render_source: {"template": <key>}` -- the deployed template IS
#               the render, so one deploy updates all twelve and editing an
#               instance's stored copy is refused (workflow/render_source.py).
#   config      resolved from the template on read
#               (templates.with_inherited_config_flags), so a key added to the
#               template tomorrow reaches instances created today.
#   pipelines   the source report's records, REFERENCED via `home_scope` rather
#               than copied -- one pipeline read where it lives, twelve readers.
#   indicators  the source report's bound registry record, so every instance
#               computes from the same indicator definitions.
#
# Everything in that list needs a SOURCE workflow to inherit from, which is why
# the sharing arguments exist. Without one, each instance falls back to the
# template's own creation path (its own pipeline records, its own seeded
# registry) -- still correct, but twelve copies again, so the tool says which it
# did.


def _shared_inheritance(
    user,
    token: str,
    caller: Caller,
    template_key: str,
    source_workflow_id,
    source_opportunity_id,
    source_program_id,
):
    """The pipeline sources and registry binding a fan-out inherits from one report.

    Returns ``(pipeline_sources, registry_source)``, both ``None`` when no source
    workflow was named. The rules are ``workflow_clone(linked=True)``'s, reusing
    its helper rather than a second copy of them: a source keeps whatever home
    scope it already had, else gains ``{"public": True}`` when the pipeline
    record really is shared, else the source workflow's own scope.

    The source scope is CALLER-SUPPLIED, so it is gated like every other scope
    this module accepts. Filtering a query is not authorising the requester: for
    a labs-only opportunity (``id >= 10_000``) ``LabsRecordAPIClient``
    short-circuits to the local backend, which performs no permission checks at
    all, so an unchecked ``source_opportunity_id`` would let a caller who
    legitimately owns one cohort read any labs-only workflow's
    ``pipeline_sources`` / ``registry_source`` and stamp those pointers into
    twelve instances of their own.
    """
    if source_workflow_id is None:
        if source_opportunity_id is not None or source_program_id is not None:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "source_opportunity_id / source_program_id only mean something alongside "
                "source_workflow_id, which was not given.",
            )
        return None, None
    if (source_opportunity_id is None) == (source_program_id is None):
        raise MCPToolError(
            "INVALID_SCHEMA",
            "Naming source_workflow_id requires exactly one of source_opportunity_id / "
            "source_program_id -- a workflow record is only readable from its own scope.",
        )

    if source_opportunity_id is not None:
        # The same held-opportunities check every sibling write in this module
        # makes, off the SAME fetch, so it costs no extra round trip.
        if int(source_opportunity_id) not in _caller_opportunity_ids(caller):
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"You do not hold opportunity {int(source_opportunity_id)}, so you cannot read a "
                "workflow from its scope. Nothing was created.",
                details={"unheld_opportunity_ids": [int(source_opportunity_id)]},
            )
    else:
        # Program scope splits in two, because only one half is unguarded.
        #
        # A LABS-ONLY program (>= 10_000) takes the same local-backend
        # short-circuit a labs-only opportunity does -- no HTTP, no permission
        # check -- so this is the only gate there will be, and it is made here
        # against the synthetic programs the caller can actually see.
        #
        # A REAL program id goes to production Connect, which checks the token's
        # user has membership of the owning entity and 404s otherwise. That is a
        # deliberate residual: `may_use(caller, program_id=...)` would be cheap
        # to call here, but its exact semantics (owning org vs. delivery
        # partner) are production's to define, and mirroring them here would
        # refuse legitimate callers for a check production already makes.
        from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id

        program_id = int(source_program_id)
        if is_labs_only_program_id(program_id) and program_id not in scopes.labs_only_program_ids(user):
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"You cannot see labs-only program {program_id}, so you cannot read a workflow "
                "from its scope. Nothing was created.",
                details={"unheld_program_ids": [program_id]},
            )

    # Lazy: connect_labs.mcp.tools.__init__ imports this module (via its wrapper),
    # so a module-level import of a sibling tool module would close an import cycle.
    from connect_labs.mcp.tools.workflows import _linked_sources

    source_scope = (
        {"opportunity_id": int(source_opportunity_id)}
        if source_opportunity_id is not None
        else {"program_id": int(source_program_id)}
    )
    wda = WorkflowDataAccess(access_token=token, **source_scope)
    try:
        source = wda.get_definition(int(source_workflow_id))
    finally:
        wda.close()
    if source is None:
        raise MCPToolError(
            "NOT_FOUND",
            f"No workflow {source_workflow_id} readable in scope {source_scope} -- nothing was created.",
        )

    pipeline_sources = _linked_sources(source.data.get("pipeline_sources"), source_scope, token)

    registry_source = None
    binding = dict(source.data.get("registry_source") or {})
    if binding.get("registry_id") is not None and (get_template(template_key) or {}).get("semantic_registry"):
        if not binding.get("public") and not {k for k in binding if k != "registry_id"}:
            # The record lives in the source's scope; say so, or an instance in
            # another opportunity's scope cannot read it.
            binding.update(source_scope)
        registry_source = binding
    return pipeline_sources, registry_source


@register(
    name="benchmarks_create_opp_reports",
    description=(
        "Create one opportunity-scoped report workflow per cohort member that does not "
        "already have one. Idempotent: an opportunity that already holds an instance of "
        "this template is skipped and reported, so a second run creates nothing and a run "
        "that failed part-way is safe to repeat. Every instance FOLLOWS the deployed "
        "template's render (render_source), so one deploy updates them all and editing an "
        "instance's stored render is refused; its config resolves from the template on "
        "read, so a key added to the template later still reaches it. Name a source "
        "workflow (with its scope) to have every instance reference that report's pipeline "
        "records via home_scope and bind to its registry record -- one pipeline read where "
        "it lives and one set of indicator definitions, instead of a copy per opportunity. "
        "Refuses a caller who does not belong to the cohort's organisation, does not hold "
        "every opportunity in it (creating a workflow inside an opportunity's scope is a "
        "write into that opportunity), or does not hold the scope the source workflow is "
        "read from."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "template_key": {
                "type": "string",
                "default": "kmc_opp_report",
                "description": "Workflow template to instantiate once per cohort member.",
            },
            "source_workflow_id": {
                "type": "integer",
                "description": (
                    "Optional. The report whose pipeline records and registry binding every "
                    "instance should share. Omit and each instance creates its own."
                ),
            },
            "source_opportunity_id": {
                "type": "integer",
                "description": "Scope the source workflow is readable in, if it is opportunity-owned.",
            },
            "source_program_id": {
                "type": "integer",
                "description": "Scope the source workflow is readable in, if it is program-owned.",
            },
        },
        "required": ["cohort_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_create_opp_reports(
    user,
    *,
    cohort_id: int,
    template_key: str = "kmc_opp_report",
    source_workflow_id: int | None = None,
    source_opportunity_id: int | None = None,
    source_program_id: int | None = None,
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"No cohort with id {cohort_id}") from exc

    if get_template(template_key) is None:
        # Up front, so a typo cannot create eleven workflows and then fail.
        raise MCPToolError("NOT_FOUND", f"Unknown workflow template {template_key!r}. Nothing was created.")

    # The same gate every other write in this module passes through. It is not
    # bookkeeping here either: a cohort's membership IS the read grant for its
    # published peer figures, so a tool that creates the very workflows which
    # read them, against someone else's cohort, is another way in.
    token, caller = _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")

    opportunity_ids = sorted(cohort.opportunity_ids)
    if not opportunity_ids:
        return {"created": [], "skipped": [], "shared": False}

    # Creating a workflow inside an opportunity's scope is a write into that
    # opportunity, so holding it is required -- the same check, off the same
    # fetch, as benchmarks_cohort_add_opportunities.
    unheld = sorted(set(opportunity_ids) - _caller_opportunity_ids(caller))
    if unheld:
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"You do not hold opportunities {unheld}, so reports cannot be created in them. " "Nothing was created.",
            details={"unheld_opportunity_ids": unheld},
        )

    pipeline_sources, registry_source = _shared_inheritance(
        user, token, caller, template_key, source_workflow_id, source_opportunity_id, source_program_id
    )

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for opportunity_id in opportunity_ids:
        wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id)
        try:
            existing = next(
                (d for d in wda.list_definitions() if d.template_type == template_key),
                None,
            )
            if existing is not None:
                skipped.append(
                    {
                        "opportunity_id": opportunity_id,
                        "workflow_id": existing.id,
                        "reason": "already_has_one",
                    }
                )
                continue
            definition, _render, _pipeline = create_workflow_from_template(
                data_access=wda,
                template_key=template_key,
                request=None,
                registry_source=registry_source,
                pipeline_sources_override=pipeline_sources,
                # One deploy updates every instance, and an edit to any
                # instance's stored render is refused rather than silently
                # forking the twelve apart.
                render_source={"template": template_key},
            )
            created.append({"opportunity_id": opportunity_id, "workflow_id": definition.id})
        finally:
            wda.close()

    return {
        "created": created,
        "skipped": skipped,
        # False means each created instance made its own pipeline records and
        # seeded its own registry, because no source workflow was named.
        "shared": pipeline_sources is not None,
    }
