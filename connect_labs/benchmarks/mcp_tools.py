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

import json
import logging
from typing import Any, NoReturn

from connect_labs.benchmarks.auto_publish import PublishRefused, publish_run
from connect_labs.benchmarks.auto_publish import run_history as _run_history  # noqa: F401 -- tests and callers
from connect_labs.benchmarks.models import MIN_PEERS_FLOOR, BenchmarkCohort, BenchmarkCohortMember
from connect_labs.labs.access import scopes
from connect_labs.labs.access.scopes import Caller, may_use
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError, register
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import create_workflow_from_template, get_template

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


def _as_id_list(value, argument: str) -> list[str] | None:
    """A list argument that may arrive as a JSON string, coerced or refused.

    MCP clients differ on whether they coerce against the declared schema, so an
    array can land here as `'["C13"]'`. Iterating that yields its CHARACTERS,
    and for an allow-list the result is catastrophic-but-quiet: every character
    is a "permitted indicator", no real indicator matches, and the publisher
    withholds everything while reporting a snapshot that "carries no
    benchmarkable indicator" -- blaming the data for an argument-shape problem.

    So: parse a string, and refuse anything that is not a list of ids rather
    than degrading into a character set.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = [part.strip() for part in text.split(",") if part.strip()]
        value = parsed
    if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
        raise MCPToolError("INVALID_SCHEMA", f"{argument} must be a list of indicator ids, got {value!r}.")
    return [str(v) for v in value]


def _serialize_cohort(cohort: BenchmarkCohort) -> dict[str, Any]:
    return {
        "id": cohort.pk,
        "name": cohort.name,
        "organization_id": cohort.organization_id,
        "description": cohort.description,
        "auto_publish_on_completion": cohort.auto_publish_on_completion,
        "source_workflow_id": cohort.source_workflow_id,
        "min_peers": cohort.min_peers,
        "require_complete_series": cohort.require_complete_series,
        "complete_cohort": cohort.complete_cohort,
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
        f"min_peers must be >= {MIN_PEERS_FLOOR}; 1 turns the peer floor off. Below 3 a "
        "reader can see a named peer's exact value, so set 3 or more for a cohort that "
        "needs that protection."
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
            "require_complete_series": {
                "type": "boolean",
                "default": True,
                "description": (
                    "R6. True (default) publishes a trend only for peers present in EVERY period of "
                    "the window, so no line starts late, ends early or has a hole. That is the safer "
                    "rule and it is also what leaves a cohort whose members joined at different times "
                    "with almost no trend at all -- on the 12-opportunity KMC cohort it kept 5. Set "
                    "False to let a peer with fewer reports contribute the reports it has. Safe only "
                    "because a period is an opportunity's own Nth report, so an incomplete line says "
                    "'fewer reports', never a date. R1 and R5 still apply."
                ),
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
    require_complete_series: bool = True,
) -> dict[str, Any]:
    # A boolean can arrive as the STRING "false" -- MCP clients differ on
    # whether they coerce against the declared schema, and Django then refuses
    # the create with a ValidationError naming the column, which tells the
    # caller nothing about which argument it came from. Coerce here, and treat
    # anything unrecognisable as an error rather than as truthy: "false" is
    # truthy in Python, so a silent bool() would switch a disclosure rule ON
    # when the caller asked for it OFF.
    if isinstance(require_complete_series, str):
        lowered = require_complete_series.strip().lower()
        if lowered not in {"true", "false"}:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"require_complete_series must be a boolean, got {require_complete_series!r}.",
            )
        require_complete_series = lowered == "true"

    if min_peers < MIN_PEERS_FLOOR:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"min_peers must be >= {MIN_PEERS_FLOOR}: at {min_peers} a figure could be published "
            "that no opportunity contributed.",
        )
    _require_organization_access(user, organization_id, "you are creating this cohort under")
    cohort = BenchmarkCohort.objects.create(
        name=name,
        require_complete_series=require_complete_series,
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
    name="benchmarks_cohort_delete",
    description=(
        "Delete a benchmark cohort, its membership and every publication made to it. A cohort "
        "IS a read grant -- membership is what lets one opportunity see another's anonymised "
        "figures -- so being able to create one and never remove it is a gap, not a safety "
        "feature: revoking is the operation you need in a hurry. Refuses a caller who does not "
        "belong to the cohort's organisation, the same check that gates creating one. "
        "Irreversible: the published values are deleted, not archived, and rebuilding them means "
        "republishing from a completed run."
    ),
    input_schema={
        "type": "object",
        "properties": {"cohort_id": {"type": "integer"}},
        "required": ["cohort_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_delete(user, *, cohort_id: int) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"Benchmark cohort {cohort_id} not found.") from exc
    _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")
    name = cohort.name
    publications = cohort.publications.count()
    members = cohort.members.count()
    cohort.delete()
    return {
        "deleted_cohort_id": cohort_id,
        "name": name,
        "publications_deleted": publications,
        "members_removed": members,
    }


def _coerce_bool(name: str, value):
    """A boolean that may arrive as the STRING "true"/"false" (MCP clients differ).
    Anything else is refused: "false" is truthy, so a silent bool() would switch a
    disclosure rule ON when the caller asked for it OFF."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise MCPToolError("INVALID_SCHEMA", f"{name} must be a boolean, got {value!r}.")


def _coerce_int(name: str, value) -> int:
    if isinstance(value, bool):
        raise MCPToolError("INVALID_SCHEMA", f"{name} must be an integer, got {value!r}.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MCPToolError("INVALID_SCHEMA", f"{name} must be an integer, got {value!r}.") from exc


@register(
    name="benchmarks_cohort_update",
    description=(
        "Change a benchmark cohort's name, description, disclosure settings (min_peers, "
        "min_denominator, require_complete_series, complete_cohort) or automatic publishing "
        "(source_workflow_id + auto_publish_on_completion: saving a run of that workflow, or "
        "finishing a history rebuild of it, republishes the cohort). Pass only what should change. "
        f"min_peers must be >= {MIN_PEERS_FLOOR}; min_peers=1 with min_denominator=0 and "
        "require_complete_series=false publishes every figure the rules otherwise withhold "
        "for being thin. Existing publications are NOT re-graded: the new settings apply to "
        "the next benchmarks_publish, so republish to see them. Refuses a caller who does not "
        "belong to the cohort's organisation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cohort_id": {"type": "integer"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "min_peers": {"type": "integer", "description": f"Must be >= {MIN_PEERS_FLOOR}."},
            "min_denominator": {"type": "integer", "description": "Must be >= 0."},
            "require_complete_series": {"type": "boolean"},
            "complete_cohort": {
                "type": "boolean",
                "description": (
                    "Every member organisation on every non-count indicator, withheld figures kept "
                    "with their band (too few babies, not credible, not collected) instead of dropped."
                ),
            },
            "source_workflow_id": {
                "type": "integer",
                "description": "The report this cohort is published from. 0 clears it.",
            },
            "auto_publish_on_completion": {
                "type": "boolean",
                "description": "Republish automatically when source_workflow_id saves a run or finishes a rebuild.",
            },
        },
        "required": ["cohort_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def benchmarks_cohort_update(
    user,
    *,
    cohort_id: int,
    name: str | None = None,
    description: str | None = None,
    min_peers: int | None = None,
    min_denominator: int | None = None,
    require_complete_series: bool | None = None,
    complete_cohort: bool | None = None,
    source_workflow_id: int | None = None,
    auto_publish_on_completion: bool | None = None,
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=_coerce_int("cohort_id", cohort_id))
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"Benchmark cohort {cohort_id} not found.") from exc
    _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")

    changed: list[str] = []
    if min_peers is not None:
        min_peers = _coerce_int("min_peers", min_peers)
        if min_peers < MIN_PEERS_FLOOR:
            raise MCPToolError("INVALID_SCHEMA", f"min_peers must be >= {MIN_PEERS_FLOOR}, got {min_peers}.")
        cohort.min_peers = min_peers
        changed.append("min_peers")
    if min_denominator is not None:
        min_denominator = _coerce_int("min_denominator", min_denominator)
        if min_denominator < 0:
            raise MCPToolError("INVALID_SCHEMA", f"min_denominator must be >= 0, got {min_denominator}.")
        cohort.min_denominator = min_denominator
        changed.append("min_denominator")
    if require_complete_series is not None:
        cohort.require_complete_series = _coerce_bool("require_complete_series", require_complete_series)
        changed.append("require_complete_series")
    if complete_cohort is not None:
        cohort.complete_cohort = _coerce_bool("complete_cohort", complete_cohort)
        changed.append("complete_cohort")
    if source_workflow_id is not None:
        source_workflow_id = _coerce_int("source_workflow_id", source_workflow_id)
        cohort.source_workflow_id = source_workflow_id or None
        changed.append("source_workflow_id")
    if auto_publish_on_completion is not None:
        cohort.auto_publish_on_completion = _coerce_bool("auto_publish_on_completion", auto_publish_on_completion)
        changed.append("auto_publish_on_completion")
    if cohort.auto_publish_on_completion and not cohort.source_workflow_id:
        raise MCPToolError(
            "INVALID_SCHEMA",
            "auto_publish_on_completion needs a source_workflow_id: without one no save can trigger it.",
        )
    if name is not None:
        cohort.name = name
        changed.append("name")
    if description is not None:
        cohort.description = description
        changed.append("description")
    if changed:
        cohort.save(update_fields=changed + ["updated_at"])
    return {**_serialize_cohort(cohort), "changed": changed}


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
            "benchmarkable_indicator_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional. Publish exactly these indicators, instead of what the registry's "
                    "`meta.benchmarkable` and the rate-shaped unit rule resolve to. For a caller "
                    "who has decided indicator by indicator -- a one-off or exploratory "
                    "publication that should not change a shared registry definition, which is a "
                    "durable policy other reports inherit. A `kind: count` is still refused: "
                    "widening what may be published must never become the way to publish an "
                    "opportunity's size."
                ),
            },
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
    benchmarkable_indicator_ids: list[str] | None = None,
) -> dict[str, Any]:
    try:
        cohort = BenchmarkCohort.objects.get(pk=cohort_id)
    except BenchmarkCohort.DoesNotExist as exc:
        raise MCPToolError("NOT_FOUND", f"Benchmark cohort {cohort_id} not found.") from exc

    explicit_ids = _as_id_list(benchmarkable_indicator_ids, "benchmarkable_indicator_ids")
    token, _ = _require_organization_access(user, cohort.organization_id, f"owns cohort {cohort_id}")

    wda = WorkflowDataAccess(access_token=token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        run = wda.get_run(run_id)
        if run is None:
            raise MCPToolError("NOT_FOUND", f"Run {run_id} not found.")
        try:
            publication = publish_run(
                cohort,
                wda,
                workflow_id,
                run,
                run_id=run_id,
                published_by=getattr(user, "username", "") or "",
                benchmarkable_indicator_ids=(set(explicit_ids) if explicit_ids else None),
            )
        except PublishRefused as exc:
            raise MCPToolError("INVALID_SCHEMA", str(exc)) from exc
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
