"""MCP tools for the labs synthetic-data system."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from django.conf import settings

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.synthetic.bundle import make_bundle_store, read_bundle
from connect_labs.labs.synthetic.clone_from_prod import (
    generate_cohort,
    generate_opp_from_bundle,
    generate_opps_bulk,
    profile_cohort,
    profile_opp_to_bundle,
    profile_opps_bulk,
)
from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.dump import _fetch_endpoint
from connect_labs.labs.synthetic.gdrive import DriveClient
from connect_labs.labs.synthetic.generator.fixtures.engine import generate as _generate
from connect_labs.labs.synthetic.generator.fixtures.fidelity import compare, compare_to_source
from connect_labs.labs.synthetic.generator.fixtures.manifest import (
    Manifest,
    ManifestValidationError,
    NormalDistribution,
    UniformDistribution,
)
from connect_labs.labs.synthetic.generator.fixtures.profiler import profile as _profile
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import FormSchema, parse_form_schema_from_app_json
from connect_labs.labs.synthetic.generator.io.uploader import upload_and_register
from connect_labs.labs.synthetic.invalidation import invalidate_synthetic_caches
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provisioning import register_labs_only_opp
from connect_labs.labs.synthetic.registry import invalidate_cache
from connect_labs.labs.synthetic.visit_count import resync_visit_count

from ..connect_token import require_connect_token
from ..progress import NULL_PROGRESS
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

# user_id -> (monotonic timestamp, accessible opportunity ids). Short-lived: long
# enough to cover one bulk operation, short enough that a real membership change
# is picked up promptly.
_ACCESS_CACHE: dict[int, tuple[float, set[int]]] = {}
_ACCESS_CACHE_TTL_SECONDS = 60
# (user_id, opportunity_id) -> (timestamp, granted). Shares the access cache's
# clock so a bulk operation costs at most one probe per opportunity. Only a
# CONFIRMED GRANT is ever stored — see _production_grants_access.
_PROBE_CACHE: dict[tuple, tuple[float, bool]] = {}
# user_id -> org labels the accessible set was derived from. Diagnostic only,
# never a permission input: it is what lets a denial say which identity's org
# tree produced the set (connect-labs#1195).
_ORG_LABEL_CACHE: dict[int, list[str]] = {}


def _accessible_opp_ids_for_user(user) -> set[int]:
    """Return the set of opportunity IDs the user has Connect access to.

    The labs UI's ``registry.accessible_opp_ids(request)`` reads the org
    data the OAuth callback stashed in the user's session. MCP tools don't
    have a request, so we fetch the same data fresh from production using
    the user's stored Connect access token. This is the same upstream
    endpoint (``/export/opp_org_program_list/``) the OAuth callback hits.

    Returns an empty set if the user has no Connect token. If the upstream call
    itself fails this raises UPSTREAM_ERROR rather than returning empty: an empty
    set means "denied", and reporting a network blip as a denial sends the caller
    hunting for an access problem that does not exist.

    The result is cached briefly per user. A bulk operation gates every opportunity
    it touches, and without this an 11-opp cohort made 11 full production org-list
    round-trips purely to authorize — 11 chances for one blip to fail the whole run.
    """
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    try:
        token = require_connect_token(user)
    except MCPToolError:
        return set()

    key = getattr(user, "id", None)
    now = time.monotonic()
    if key is not None:
        cached = _ACCESS_CACHE.get(key)
        if cached is not None and (now - cached[0]) < _ACCESS_CACHE_TTL_SECONDS:
            return cached[1]

    # require_connect_token(user) returned this token FOR this user, so the owner is proven.
    org_data = fetch_user_organization_data(token, owner=getattr(user, "username", None))
    if org_data is None:
        # Distinguish "we could not ask" from "you may not". Collapsing the two
        # made a transient upstream blip surface as a confident PERMISSION_DENIED
        # naming a specific opportunity the caller demonstrably owns.
        raise MCPToolError(
            "UPSTREAM_ERROR",
            "Could not reach production Connect to check opportunity access. "
            "This is not a permission denial — retry.",
        )

    ids = {int(o["id"]) for o in org_data.get("opportunities", []) if o.get("id") is not None}
    if key is not None:
        _ACCESS_CACHE[key] = (now, ids)
        _ORG_LABEL_CACHE[key] = [
            str(o.get("slug") or o.get("name"))
            for o in org_data.get("organizations", [])
            if o.get("slug") or o.get("name")
        ]
    return ids


def _production_grants_access(user, opportunity_id: int) -> bool:
    """Ask production Connect whether this caller may read this opportunity.

    The accessible-opportunity list is a local COPY of a decision production owns
    and enforces on every export call. When that copy is incomplete the honest move
    is to ask the authority rather than infer from the copy: a 200 here means the
    caller genuinely has access; anything else leaves the denial standing.

    This can only ever GRANT access production itself confirms — it can never
    override a denial — so it is strictly safer than trusting a partial list.

    Needed because the list really is partial: it returned 273 entries whose lowest
    id was 948, so every older opportunity looked inaccessible, while labs_context
    listed those same opportunities and production served their exports 200 OK
    (connect-labs#1195).
    """
    try:
        token = require_connect_token(user)
    except MCPToolError:
        return False

    key = (getattr(user, "id", None), opportunity_id)
    now = time.monotonic()
    cached = _PROBE_CACHE.get(key)
    if cached is not None and (now - cached[0]) < _ACCESS_CACHE_TTL_SECONDS:
        return cached[1]

    # Reuse `_fetch_endpoint` rather than hand-rolling the request: it is the exact
    # call the profiler already makes successfully against this endpoint, including
    # follow_redirects=True, which production needs (see the note in
    # integrations/connect/export_client.py — a redirect there is not a denial, but a
    # bare httpx.get reads one as a non-200 and denies a caller who does have access).
    try:
        detail = _fetch_endpoint(settings.CONNECT_PRODUCTION_URL, opportunity_id, "", token)
        granted = bool(detail)
    except Exception:  # noqa: BLE001 — an unreachable or refusing authority is not a grant
        logger.info("Access probe did not confirm opportunity_id=%s; leaving the denial in place", opportunity_id)
        granted = False

    # Cache the GRANT only. A denial is the answer we are least sure of — it is
    # what a transient 404, a redirect, or a slow upstream all look like — and
    # caching it applies one bad reply to the rest of the TTL. That is how an
    # 11-opp cohort refused all 11 opportunities the single-opp tool profiled
    # fine seconds either side, and why re-driving the run could not recover
    # inside 60s: the retry was answered from the cache, never from production
    # (connect-labs#1195). Re-probing a denial costs one request against the
    # authority; replaying it costs the whole run.
    if granted:
        _PROBE_CACHE[key] = (now, granted)
    return granted


def _require_opportunity_access(user, opportunity_id: int) -> None:
    """Raise PERMISSION_DENIED if the user has no access to ``opportunity_id``.

    Two paths are accepted:
    1. The opp is a registered labs-only ``SyntheticOpportunity`` and the user
       clears its ACCESS model (``is_accessible_to`` — scoped by ``allowed_domains``
       with Dimagi-internal + creator carve-outs). Labs-only opps route to the
       local backend with no Connect membership behind them, so this is the sole
       security boundary. The ``view_synthetic_opps`` UI toggle is intentionally
       NOT sufficient on its own — otherwise any opted-in user could reach another
       tenant's labs-only opp regardless of ``allowed_domains``.
    2. The opp is a real Connect opp in the user's live membership data. Empty set
       (no token, upstream failure) is treated as "no access" so an unauthenticated
       caller can't slip a write through.
    """
    # Path 1 — registered labs-only opp. Gated by the synthetic ACCESS model
    # (allowed_domains, with Dimagi-internal + creator carve-outs). This is the
    # security boundary for the local-backend namespace; the `view_synthetic_opps`
    # UI toggle is deliberately NOT sufficient on its own — otherwise any opted-in
    # user could reach another tenant's labs-only opp regardless of allowed_domains.
    opp = SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id, labs_only=True).first()
    if opp is not None:
        if opp.is_accessible_to(user):
            return
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"labs-only opportunity_id {opportunity_id} is not permitted for your account "
            f"(scoped by allowed_domains).",
        )

    # Path 2 — a real Connect opp the caller is a member of. (An id without a
    # registered labs-only row is not a labs-only opp, so it must clear real
    # Connect membership.)
    accessible = _accessible_opp_ids_for_user(user)
    if opportunity_id not in accessible:
        # The list is only a copy — production is the authority, and it enforces this
        # on every export call anyway. Ask it before turning a partial list into a
        # permission decision (connect-labs#1195).
        if _production_grants_access(user, opportunity_id):
            return

        # Say how big the set was. "Not in your accessible set" is indistinguishable
        # between "production says you may not touch this opportunity" and "we asked
        # and got back nothing useful" — and the second one sends people hunting for
        # a permissions problem that does not exist (connect-labs#1195, where the same
        # opportunity profiles fine through the single-opp tool seconds later).
        sample = sorted(accessible)[:8]
        # Name the identity the call actually resolved to. "Not in your accessible
        # set" is equally true when the caller lacks access and when the call ran
        # as a DIFFERENT identity than the caller believes — a second PAT, another
        # labs account — and from outside the two are indistinguishable. That
        # ambiguity, not the access decision, is what made connect-labs#1195 cost
        # days: production answered consistently for each identity throughout,
        # while the error named neither.
        who = getattr(user, "username", None) or getattr(user, "email", None) or f"user_id={getattr(user, 'id', '?')}"
        orgs = _ORG_LABEL_CACHE.get(getattr(user, "id", None), [])
        logger.warning(
            "Access denied for opportunity_id=%s as labs user %s — accessible set had %d entries "
            "(sample: %s) derived from orgs: %s",
            opportunity_id,
            who,
            len(accessible),
            sample,
            orgs or "(none reported)",
        )
        detail = (
            "your accessible set came back EMPTY, which usually means the upstream "
            "opportunity list could not be read rather than that you lack access"
            if not accessible
            else f"{len(accessible)} opportunities are visible to you"
        )
        org_note = f", via orgs: {', '.join(orgs)}" if orgs else ""
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"opportunity_id {opportunity_id} is not in your accessible set ({detail}), "
            f"and production Connect did not confirm access to it. "
            f"This call resolved to labs user {who!r}{org_note} — if that is not the account you "
            f"expected, the token in use belongs to someone else.",
        )


@register(
    name="synthetic_register",
    description=(
        "Register or update a synthetic-opportunity entry. Set enabled=True "
        "to make labs serve fixtures from the given GDrive folder for this "
        "opportunity_id; set enabled=False to disable without deleting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "gdrive_folder_id": {"type": "string"},
            "enabled": {"type": "boolean", "default": True},
            "label": {"type": ["string", "null"], "default": None},
        },
        "required": ["opportunity_id", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_register(
    user,
    *,
    opportunity_id: int,
    gdrive_folder_id: str,
    enabled: bool = True,
    label: str | None = None,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    defaults: dict[str, Any] = {
        "gdrive_folder_id": gdrive_folder_id,
        "enabled": enabled,
        "created_by": user,
    }
    if label is not None:
        defaults["label"] = label
    existing = SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id).first()
    previous_folder_id = existing.gdrive_folder_id if existing else None
    row, _created = SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults=defaults,
    )
    # Everything downstream of the fixtures, not just the registry: the fixture
    # store, and the raw/computed analysis rows keyed on (opp, pipeline) and
    # (opp, config_hash). Clearing only the registry is what made a regenerated
    # dataset unreachable from a dashboard (#1034).
    invalidate_synthetic_caches(opportunity_id)
    # The row now points at different fixtures, so the cached count describes
    # the old ones. Left alone it prints in the labs chrome next to the new
    # data (#1197). No-ops when the folder didn't actually change.
    resync_visit_count(row, previous_folder_id=previous_folder_id)
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
        "label": row.label,
        "visit_count": row.visit_count,
    }


@register(
    name="synthetic_reload_fixtures",
    description=(
        "Force an opportunity to re-read its fixtures from GDrive and drop every "
        "cached artifact derived from them: the fixture store (across all worker "
        "processes), and the raw + computed analysis rows a pipeline reads.\n\n"
        "Use this after editing fixture files IN PLACE in a registered Drive "
        "folder. Registering a new folder id, minting a new pipeline and bumping "
        "a schema version do NOT achieve this on their own -- the analysis caches "
        "are keyed on (opportunity_id, config_hash), so two pipelines with "
        "identical schemas share rows. Until this is called the dashboard renders "
        "cleanly while serving the previous dataset."
    ),
    input_schema={
        "type": "object",
        "properties": {"opportunity_id": {"type": "integer"}},
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_reload_fixtures(user, *, opportunity_id: int) -> dict[str, Any]:
    """The escape hatch that existed in code but was reachable from nowhere.

    `FixtureStore.reload` has been the right answer since it was written, but its
    only caller was a button in the labs UI -- so anyone driving labs over MCP
    (the normal way to iterate a synthetic dataset) had no way to invoke it, and
    #1034 burned an afternoon rediscovering that.
    """
    _require_opportunity_access(user, opportunity_id)
    try:
        row = SyntheticOpportunity.objects.get(opportunity_id=opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError("NOT_FOUND", f"No synthetic entry for opportunity_id={opportunity_id}")

    outcome = invalidate_synthetic_caches(opportunity_id)
    count = resync_visit_count(row, previous_folder_id=None)
    return {
        "opportunity_id": opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "invalidated": outcome,
        "visit_count": count,
    }


@register(
    name="synthetic_disable",
    description=(
        "Disable a synthetic-opportunity entry without deleting it. The "
        "GDrive folder is retained for forensics; labs reverts to real "
        "export reads for this opportunity_id on next request."
    ),
    input_schema={
        "type": "object",
        "properties": {"opportunity_id": {"type": "integer"}},
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_disable(user, *, opportunity_id: int) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    try:
        row = SyntheticOpportunity.objects.get(opportunity_id=opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError("NOT_FOUND", f"No synthetic entry for opportunity_id={opportunity_id}")
    row.enabled = False
    row.save(update_fields=["enabled", "updated_at"])
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
    }


@register(
    name="synthetic_repoint_by_source",
    description=(
        "Re-point the labs-only synthetic opportunity that was cloned from a given "
        "SOURCE opportunity to a new GDrive fixture folder. Looks up the "
        "SyntheticOpportunity by cloned_from_opportunity_id and updates its "
        "gdrive_folder_id in place (preserving its labs opp id), so a locally-generated "
        "fixture set overwrites what labs serves. This is the server-side half of the "
        "PREFERRED local clone flow: run `synthetic_generate_opps --spec <spec> "
        "--no-register` on a fast machine (heavy copula generation + GDrive upload, no "
        "DB), then call this once per printed `source_opp -> gdrive_folder_id` line to "
        "repoint the existing opps — no prod-DB connection needed on the generating box, "
        "and no slow/timeout-prone server-side generation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_opportunity_id": {
                "type": "integer",
                "description": "The real source opp id the labs opp was cloned from (its cloned_from_opportunity_id).",
            },
            "gdrive_folder_id": {
                "type": "string",
                "description": "New fixture folder id, from `synthetic_generate_opps --no-register` output.",
            },
            "enabled": {"type": "boolean", "default": True},
        },
        "required": ["source_opportunity_id", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_repoint_by_source(
    user,
    *,
    source_opportunity_id: int,
    gdrive_folder_id: str,
    enabled: bool = True,
) -> dict[str, Any]:
    try:
        row = SyntheticOpportunity.objects.get(cloned_from_opportunity_id=source_opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError(
            "NOT_FOUND",
            f"No synthetic opportunity cloned from source_opportunity_id={source_opportunity_id}",
        )
    except SyntheticOpportunity.MultipleObjectsReturned:
        raise MCPToolError(
            "CONFLICT",
            f"Multiple synthetic opportunities cloned from source_opportunity_id={source_opportunity_id}; "
            "repoint by opportunity_id with synthetic_register instead",
        )
    _require_opportunity_access(user, row.opportunity_id)
    previous = row.gdrive_folder_id
    row.gdrive_folder_id = gdrive_folder_id
    row.enabled = enabled
    row.save(update_fields=["gdrive_folder_id", "enabled", "updated_at"])
    invalidate_synthetic_caches(row.opportunity_id)
    resync_visit_count(row, previous_folder_id=previous)
    return {
        "opportunity_id": row.opportunity_id,
        "source_opportunity_id": source_opportunity_id,
        "previous_gdrive_folder_id": previous,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
        "visit_count": row.visit_count,
    }


def _load_opportunity_detail(opportunity_id: int, user) -> dict:
    """Pull live opportunity detail from prod via the user's OAuth token.

    Uses the same /export/opportunity/<id>/ endpoint that the labs admin's
    AppDownloaderDataAccess.get_opportunity_details hits, authenticated with
    the calling user's stored Connect access token.

    Falls back to a minimal stub if the user has no token, the upstream call
    fails, or the user lacks access to the opportunity. The engine tolerates
    an empty payload and still produces fixtures (no payment_units / no
    deliver_unit_id, but every visit still gets the standard 23 metadata
    fields).
    """
    fallback: dict[str, Any] = {
        "id": opportunity_id,
        "name": "(synthetic)",
        "payment_units": [],
        "deliver_units": [],
    }
    try:
        token = require_connect_token(user)
    except MCPToolError:
        logger.warning(
            "synthetic_generate_from_manifest: no Connect token for user; "
            "using empty opportunity_detail stub for opp_id=%s",
            opportunity_id,
        )
        return fallback

    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/"
        try:
            resp = client.http_client.get(url, timeout=60.0)
        except httpx.RequestError as exc:
            logger.warning(
                "synthetic_generate_from_manifest: upstream RequestError loading opp %s: %s; " "falling back to stub.",
                opportunity_id,
                exc,
            )
            return fallback
        if resp.status_code >= 400:
            logger.warning(
                "synthetic_generate_from_manifest: opp_detail GET %s returned %s; " "falling back to stub.",
                url,
                resp.status_code,
            )
            return fallback
        return resp.json()
    finally:
        client.close()


def _load_form_schema_for_opp(opportunity_id: int, user) -> FormSchema:
    """Resolve the opp's primary deliver form schema by hitting Connect's app_structure endpoint.

    Calls ``/export/opportunity/<id>/app_structure/?app_type=deliver`` (the same
    upstream the ``get_opportunity_apps`` MCP tool uses) and translates the
    deliver app's primary form into ``QuestionSpec`` entries via
    ``parse_form_schema_from_app_json``.

    Falls back to an empty FormSchema if the user has no Connect token, the
    upstream call fails, or the opp has no deliver app. The engine's field
    filler tolerates an empty schema (no per-question form_json fields are
    added, but every visit still carries the standard 23 metadata fields).
    """
    try:
        token = require_connect_token(user)
    except MCPToolError:
        logger.warning(
            "synthetic_generate_from_manifest: no Connect token for user; " "using empty form_schema for opp_id=%s",
            opportunity_id,
        )
        return FormSchema(questions=[])

    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/app_structure/"
        try:
            resp = client.http_client.get(url, params={"app_type": "deliver"}, timeout=120.0)
        except httpx.RequestError as exc:
            logger.warning(
                "synthetic_generate_from_manifest: upstream RequestError loading app_structure for opp %s: %s; "
                "falling back to empty schema.",
                opportunity_id,
                exc,
            )
            return FormSchema(questions=[])
        if resp.status_code >= 400:
            logger.warning(
                "synthetic_generate_from_manifest: app_structure GET %s returned %s; falling back to empty schema.",
                url,
                resp.status_code,
            )
            return FormSchema(questions=[])
        return parse_form_schema_from_app_json(resp.json(), app_type="deliver")
    finally:
        client.close()


@register(
    name="synthetic_generate_from_manifest",
    description=(
        "Generate the five fixture JSON files from a YAML manifest, upload "
        "them to a fresh GDrive folder, and register the opportunity as "
        "synthetic. Returns the new folder_id, a human-openable folder_url, "
        "and per-endpoint record counts so callers can verify the upload."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "manifest_yaml": {"type": "string"},
        },
        "required": ["opportunity_id", "manifest_yaml"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_generate_from_manifest(
    user,
    *,
    opportunity_id: int,
    manifest_yaml: str,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)
    try:
        manifest = Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    if manifest.opportunity_id != opportunity_id:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"manifest.opportunity_id ({manifest.opportunity_id}) != " f"tool arg opportunity_id ({opportunity_id})",
        )

    detail = _load_opportunity_detail(opportunity_id, user)
    form_schema = _load_form_schema_for_opp(opportunity_id, user)
    fixtures = _generate(manifest=manifest, opportunity_detail=detail, form_schema=form_schema)
    drive = DriveClient()
    result = upload_and_register(
        drive=drive,
        opportunity_id=opportunity_id,
        opportunity_name=manifest.opportunity_name,
        fixtures=fixtures,
    )

    task_records = fixtures.get("task_records", [])
    tasks_created = 0
    if task_records:
        # For labs-only opps the client has no Connect token; the dispatch in
        # LabsRecordAPIClient routes writes to LabsLocalRecord instead. Pass
        # token=None (won't be used) rather than require_connect_token which
        # would raise for users without a Connect membership.
        try:
            token = require_connect_token(user)
        except MCPToolError:
            token = ""
        client = LabsRecordAPIClient(access_token=token, opportunity_id=opportunity_id)
        try:
            for rec in task_records:
                # Write as Task records so the Tasks UI (experiment="tasks",
                # type="Task") picks them up. The synthetic generator already
                # produces records in the Task schema; this just registers them
                # under the right experiment/type tags.
                client.create_record(
                    experiment="tasks",
                    type="Task",
                    data=rec,
                    username=rec.get("username") or "",
                )
                tasks_created += 1
        finally:
            client.close()

    # Invalidate the labs analysis SQL cache so the next pipeline read sees the
    # fresh visits/fixtures we just uploaded — otherwise stale aggregated cache
    # from a prior fixture set keeps shadowing the new data.
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.synthetic.registry import invalidate_cache as _reg_invalidate

    SQLCacheManager.delete_all_cache(opportunity_id)
    _reg_invalidate()

    # Cache the visit count on the registry row so the labs-context picker shows the
    # real number instead of 0 (the fixtures we just generated are authoritative).
    try:
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id).update(
            visit_count=len(fixtures.get("user_visits") or [])
        )
    except Exception:  # noqa: BLE001
        logger.exception("synthetic_generate_from_manifest: visit_count cache failed for opp %s", opportunity_id)

    # Surfaced so a manifest that declares image_config but produced no images
    # (its visits carry no MUAC field to hang one off) is visible HERE, rather
    # than a week later as an image audit with nothing in it.
    image_stats = fixtures.get("image_stats")

    return {
        "folder_id": result.folder_id,
        "folder_url": result.folder_url,
        "record_counts": result.record_counts,
        "form_schema_questions": len(form_schema.questions),
        "tasks_created": tasks_created,
        "image_stats": image_stats,
    }


@register(
    name="synthetic_create_labs_only",
    description=(
        "Create a labs-only synthetic opportunity from scratch. No real Connect "
        "opp is required — opportunity_id is auto-allocated from the labs-only "
        "reserved range (10_000+). The opp is surfaced into labs_context only "
        "for users with view_synthetic_opps=True whose email domain matches one "
        "of allowed_domains. Returns the new opportunity_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "gdrive_folder_id": {"type": "string"},
            "org_name": {"type": "string", "default": "Labs Synthetic"},
            "program_name": {"type": "string", "default": "Labs Synthetic"},
            "program_id": {
                "type": ["integer", "null"],
                "default": None,
                "description": "Labs-only program this opp belongs to (reserved >= 10_000). Set it to file "
                "this opp under an existing labs-only program (e.g. a study's program) instead of giving "
                "it its own. Unset = the opp is its own program (program_id = opportunity_id).",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["@dimagi.com"],
                "description": "Email-domain allowlist (e.g. ['@dimagi.com']). Empty = any.",
            },
            "enabled": {"type": "boolean", "default": True},
            "notes": {"type": "string", "default": ""},
        },
        "required": ["label", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_create_labs_only(
    user,
    *,
    label: str,
    gdrive_folder_id: str,
    org_name: str = "Labs Synthetic",
    program_name: str = "Labs Synthetic",
    program_id: int | None = None,
    allowed_domains: list[str] | None = None,
    enabled: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    row = register_labs_only_opp(
        label=label,
        gdrive_folder_id=gdrive_folder_id,
        org_name=org_name,
        program_name=program_name,
        program_id=program_id,
        allowed_domains=allowed_domains if allowed_domains is not None else ["@dimagi.com"],
        enabled=enabled,
        created_by=user,
    )
    if notes:
        SyntheticOpportunity.objects.filter(opportunity_id=row.opportunity_id).update(notes=notes)
        row.refresh_from_db()
    return {
        "opportunity_id": row.opportunity_id,
        "label": row.label,
        "gdrive_folder_id": row.gdrive_folder_id,
        "org_name": row.org_name,
        "program_name": row.program_name,
        "program_id": row.program_id,
        "allowed_domains": list(row.allowed_domains),
        "labs_only": True,
        "enabled": row.enabled,
    }


@register(
    name="synthetic_clone_to_labs_only",
    description=(
        "Clone an existing SyntheticOpportunity (real-backed or labs-only) into a "
        "new labs-only opp. Reuses the source's gdrive_folder_id (same fixture set, "
        "new opp_id from the 10_000+ range). Open to any authenticated MCP caller: "
        "once a source has been registered as a SyntheticOpportunity it's already a "
        "labs-controlled fixture artifact, so cloning it doesn't grant any new data "
        "access — it just creates a second view onto the same GDrive fixture folder. "
        "Use this to make existing synthetic fixture data accessible to users who "
        "lack Connect membership for the original opp (e.g. ACE)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_opportunity_id": {"type": "integer"},
            "label": {
                "type": ["string", "null"],
                "default": None,
                "description": "Label for the new opp. Defaults to 'Clone of <source label>'.",
            },
            "org_name": {"type": ["string", "null"], "default": None},
            "program_name": {"type": ["string", "null"], "default": None},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["@dimagi.com", "@dimagi-ai.com"],
                "description": (
                    "Email-domain allowlist for the new labs-only opp. Default is broad "
                    "(['@dimagi.com', '@dimagi-ai.com']) so ace@dimagi-ai.com can use it."
                ),
            },
        },
        "required": ["source_opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_clone_to_labs_only(
    user,
    *,
    source_opportunity_id: int,
    label: str | None = None,
    org_name: str | None = None,
    program_name: str | None = None,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    try:
        source = SyntheticOpportunity.objects.get(opportunity_id=source_opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError(
            "NOT_FOUND",
            f"No SyntheticOpportunity for opportunity_id={source_opportunity_id}. "
            "Register the source as synthetic first via synthetic_register or "
            "synthetic_generate_from_manifest.",
        )

    # Auth: any authenticated MCP caller may clone an existing SyntheticOpportunity.
    # The source row's existence is the gate — it was registered by a human with
    # Connect access, the underlying data is already a synthetic fixture, and the
    # clone creates only a second view onto the same GDrive folder (no new data).
    # Visibility of the new opp is controlled by allowed_domains + view_synthetic_opps.
    row = register_labs_only_opp(
        label=label or f"Clone of {source.label or source.opportunity_id}",
        gdrive_folder_id=source.gdrive_folder_id,
        org_name=org_name or source.org_name or "Labs Synthetic",
        program_name=program_name or source.program_name or "Labs Synthetic",
        allowed_domains=(allowed_domains if allowed_domains is not None else ["@dimagi.com", "@dimagi-ai.com"]),
        enabled=True,
        created_by=user,
    )
    SyntheticOpportunity.objects.filter(opportunity_id=row.opportunity_id).update(
        notes=f"Cloned from opp {source_opportunity_id} via MCP."
    )
    return {
        "opportunity_id": row.opportunity_id,
        "source_opportunity_id": source_opportunity_id,
        "label": row.label,
        "gdrive_folder_id": row.gdrive_folder_id,
        "org_name": row.org_name,
        "program_name": row.program_name,
        "allowed_domains": list(row.allowed_domains),
        "labs_only": True,
    }


@register(
    name="synthetic_image_server_status",
    description=(
        "Diagnostic: report the synthetic image-server config and folder access — "
        "whether LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID is set, what filenames the "
        "service-account can see in that folder, and whether a sample stock image "
        "downloads. Used to root-cause why audit MUAC photo cards render with "
        "placeholders."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    is_write=False,
)
def synthetic_image_server_status(user) -> dict[str, Any]:
    import json as _json
    import os as _os

    from django.conf import settings

    from connect_labs.labs.synthetic.image_server import SyntheticImageServer

    # Try to expose the labs Drive service-account email so the operator
    # knows what address to share the stock-images folder with when the
    # listing comes back empty.
    sa_email = None
    raw = _os.environ.get("LABS_SYNTHETIC_GDRIVE_SA_KEY", "")
    if raw:
        try:
            if raw.strip().startswith("{"):
                sa_email = _json.loads(raw).get("client_email")
            else:
                with open(raw) as _f:
                    sa_email = _json.load(_f).get("client_email")
        except Exception:  # noqa: BLE001 — best-effort, don't fail the diagnostic
            pass

    folder_id = getattr(settings, "LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID", "") or ""
    result: dict[str, Any] = {
        "folder_id_set": bool(folder_id),
        "folder_id": folder_id,
        "service_account_email": sa_email,
        "listing_files": [],
        "listing_error": None,
        "sample_blob_id": None,
        "sample_download_ok": False,
        "sample_bytes": 0,
        "sample_download_error": None,
    }
    if not folder_id:
        return result

    server = SyntheticImageServer()
    try:
        listing = server.list_stock_folder()
        result["listing_files"] = sorted(listing.keys())
    except Exception as exc:  # noqa: BLE001 — diagnostic surfaces all errors
        result["listing_error"] = f"{type(exc).__name__}: {exc}"
        return result

    # Pick the first muac_NNN.jpg from the listing and translate to its blob_id.
    # Hardcoding "synth-muac-001" would 404 if the operator's stock folder
    # used a different numbering scheme.
    sample_blob_id = None
    for fn in result["listing_files"]:
        if fn.startswith("muac_") and fn.endswith(".jpg"):
            digits = fn[len("muac_") : -len(".jpg")]
            if digits.isdigit():
                sample_blob_id = f"synth-muac-{int(digits):03d}"
                break
    result["sample_blob_id"] = sample_blob_id
    if not sample_blob_id:
        return result

    try:
        data = server.get_image(sample_blob_id)
        result["sample_download_ok"] = bool(data)
        result["sample_bytes"] = len(data) if data else 0
    except Exception as exc:  # noqa: BLE001 — diagnostic surfaces all errors
        result["sample_download_error"] = f"{type(exc).__name__}: {exc}"

    return result


@register(
    name="synthetic_local_records_count",
    description=(
        "Diagnostic: return counts of LabsLocalRecord rows for a labs-only opp, "
        "grouped by (experiment, type). Useful for verifying that synthetic-data "
        "writes landed correctly in the labs-local backend before triaging UI gaps."
    ),
    input_schema={
        "type": "object",
        "properties": {"opportunity_id": {"type": "integer"}},
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_local_records_count(user, *, opportunity_id: int) -> dict[str, Any]:
    from django.db.models import Count

    from connect_labs.labs.synthetic.models import LabsLocalRecord

    rows = (
        LabsLocalRecord.objects.filter(opportunity_id=opportunity_id)
        .values("experiment", "type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return {
        "opportunity_id": opportunity_id,
        "groups": list(rows),
        "total": LabsLocalRecord.objects.filter(opportunity_id=opportunity_id).count(),
    }


@register(
    name="synthetic_local_record_dump",
    description=(
        "Diagnostic: return the full ``data`` JSON for a single LabsLocalRecord "
        "row, scoped to the caller's labs-only opp. Used to debug shape "
        "mismatches between the synthetic generator's emitted dict and what "
        "the labs UI reads back."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "record_id": {"type": "integer"},
        },
        "required": ["opportunity_id", "record_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_local_record_dump(user, *, opportunity_id: int, record_id: int) -> dict[str, Any]:
    from connect_labs.labs.synthetic.models import LabsLocalRecord

    try:
        rec = LabsLocalRecord.objects.get(id=record_id, opportunity_id=opportunity_id)
    except LabsLocalRecord.DoesNotExist:
        raise MCPToolError(
            "NOT_FOUND",
            f"no LabsLocalRecord with id={record_id} in opp {opportunity_id}",
        )
    return {
        "id": rec.id,
        "opportunity_id": rec.opportunity_id,
        "experiment": rec.experiment,
        "type": rec.type,
        "username": rec.username,
        "data_keys": sorted(rec.data.keys()) if isinstance(rec.data, dict) else [],
        "data": rec.data,
    }


@register(
    name="synthetic_set_my_visibility",
    description=(
        "Toggle the calling user's `view_synthetic_opps` setting. When on, "
        "labs-only synthetic opportunities whose `allowed_domains` matches the "
        "user's email domain are merged into the user's labs_context (org/"
        "program/opportunity lists). Off by default. Returns the new state."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "True to opt in to seeing labs-only opps; False to opt out.",
            },
        },
        "required": ["enabled"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_set_my_visibility(user, *, enabled: bool) -> dict[str, Any]:
    user.view_synthetic_opps = bool(enabled)
    user.save(update_fields=["view_synthetic_opps"])
    return {
        "view_synthetic_opps": user.view_synthetic_opps,
        "email": user.email,
    }


@register(
    name="synthetic_profile_from_prod",
    description=(
        "Analyze real production data for an opportunity and produce a "
        "synthetic-data manifest that reproduces the same statistical shape. "
        "Reads the five export endpoints server-side, computes per-FLW "
        "distributions (approval rates, flag rates, visit cadence), field "
        "value distributions from form_json, and timeline parameters. "
        "Returns a YAML manifest string (no PII) ready to pass to "
        "synthetic_generate_from_manifest."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "form_json_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional explicit list of form_json dot-paths to profile "
                    "(e.g. ['form.case.update.soliciter_muac_cm']). If omitted, "
                    "auto-discovers numeric fields from a sample of visits."
                ),
            },
            "mirror": {
                "type": "boolean",
                "description": (
                    "High-fidelity 'close mirror' mode. When true, the manifest carries a "
                    "de-identified per-entity transplant pool so the clone reproduces the "
                    "source's exact visits-per-case and cases-per-FLW ratios, timing, and "
                    "per-entity value trajectories (e.g. an infant growth curve) — not just "
                    "per-column means. Numerics + structure only; identifiers/text are never "
                    "copied. Default false (fast marginal mode)."
                ),
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_profile_from_prod(
    user,
    *,
    opportunity_id: int,
    form_json_paths: list[str] | None = None,
    mirror: bool = False,
) -> dict[str, Any]:
    _require_opportunity_access(user, opportunity_id)

    try:
        token = require_connect_token(user)
    except MCPToolError:
        raise MCPToolError(
            "PERMISSION_DENIED",
            "No Connect token available — cannot fetch production data.",
        )

    base_url = settings.CONNECT_PRODUCTION_URL

    logger.info("synthetic_profile_from_prod: fetching exports for opp %s", opportunity_id)
    detail = _fetch_endpoint(base_url, opportunity_id, "", token)
    user_visits = _fetch_endpoint(base_url, opportunity_id, "user_visits", token)
    user_data = _fetch_endpoint(base_url, opportunity_id, "user_data", token)

    if not isinstance(user_visits, list) or not user_visits:
        raise MCPToolError(
            "NOT_FOUND",
            f"No user_visits data for opportunity_id={opportunity_id}",
        )

    logger.info(
        "synthetic_profile_from_prod: profiling %d visits, %d users for opp %s",
        len(user_visits),
        len(user_data) if isinstance(user_data, list) else 0,
        opportunity_id,
    )

    manifest_yaml = _profile(
        opportunity_id=opportunity_id,
        user_visits=user_visits,
        user_data=user_data if isinstance(user_data, list) else [],
        opportunity_detail=detail if isinstance(detail, dict) else {},
        form_json_paths=form_json_paths,
        mirror=mirror,
    )

    return {
        "manifest_yaml": manifest_yaml,
        "mode": "mirror" if mirror else "marginal",
        "source_visit_count": len(user_visits),
        "source_flw_count": len({v.get("username") for v in user_visits if v.get("username")}),
        "source_entity_count": len({v.get("entity_id") for v in user_visits if v.get("entity_id")}),
    }


# =============================================================================
# Composite env templates (synthetic_env_*)
#
# Env manifests are first-class TEMPLATES, discovered by a registry that mirrors
# the workflow template registry. These three tools extend the synthetic_*
# family with the same naming + (user, *, ...) signature + return-dict / error
# conventions: list the available env templates, inspect one, and realize one
# server-side via the ensure engine. ``synthetic_env_ensure`` is the rename of
# the former one-off ``ensure_synthetic_env`` tool.
# =============================================================================


@register(
    name="synthetic_env_list",
    description=(
        "List the available composite synthetic ENVIRONMENT templates "
        "(checked-in manifests under connect_labs/labs/synthetic/envs/). "
        "Each entry is a summary of the env template (NOT a realization): its "
        "key (pass to synthetic_env_get / synthetic_env_ensure), declared "
        "resource kinds, and the opportunity ids it touches. Use this to "
        "discover which envs exist before realizing one."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    is_write=False,
)
def synthetic_env_list(user) -> dict[str, Any]:
    from connect_labs.labs.synthetic.ensure.registry import list_envs

    return {"envs": list_envs()}


@register(
    name="synthetic_env_get",
    description=(
        "Get the registry summary for a single composite synthetic ENV "
        "template by key (e.g. 'program-admin-report'). Returns the template's "
        "declared shape — env name, resource list (kind + opportunity ids), "
        "timeline window — NOT a realization (use synthetic_env_ensure to "
        "realize). Unknown or unsafe names raise NOT_FOUND."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "env": {
                "type": "string",
                "description": (
                    "Env template key (a single plain segment, e.g. "
                    "'program-admin-report'). Path separators and '..' are rejected."
                ),
            },
        },
        "required": ["env"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_env_get(user, *, env: str) -> dict[str, Any]:
    from connect_labs.labs.synthetic.ensure.registry import get_env

    try:
        entry = get_env(env)
    except ValueError as exc:
        raise MCPToolError("NOT_FOUND", str(exc))

    summary = entry.summary
    summary["resources"] = [
        {
            "kind": r.kind,
            "opportunity_id": getattr(r, "opportunity_id", None),
            "opportunity_ids": list(getattr(r, "opportunity_ids", None) or []),
        }
        for r in entry.manifest.resources
    ]
    return summary


@register(
    name="synthetic_env_ensure",
    description=(
        "Realize a composite synthetic ENVIRONMENT template server-side on labs "
        "(idempotent). Resolves an env template key via the registry to the "
        "checked-in manifest at connect_labs/labs/synthetic/envs/<env>.yaml "
        "and runs the ensure engine in-app, so labs-only synthetic opps are "
        "written through the local-records backend on the labs DB — the only "
        "transport that reaches labs prod for synthetic opportunities. Returns "
        "the realized id map (the ${...} vars a walkthrough spec interpolates: "
        "par_run_id, par_url, good_*/incomplete_* drill targets, wk4_*, etc.). "
        "Re-running does not duplicate or churn ids (current-week runs may reset "
        "per the manifest). Pass fresh=true to first DELETE the env's regenerable "
        "records (runs/flags/audits/tasks for its opps) and rebuild cleanly — use "
        "it when prior records no longer match the manifest (ids will churn). "
        "Use env='program-admin-report' for the PAR demo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "env": {
                "type": "string",
                "description": (
                    "Env template key (a single plain segment, e.g. "
                    "'program-admin-report'). Resolves to "
                    "connect_labs/labs/synthetic/envs/<env>.yaml. Path "
                    "separators and '..' are rejected."
                ),
            },
            "fresh": {
                "type": "boolean",
                "description": (
                    "When true, delete the env's regenerable records (workflow "
                    "runs, flags, audits, tasks) for its opportunities before "
                    "re-seeding, then rebuild. Definitions, render code, and "
                    "pipelines are preserved. Churns run/audit/task ids. Default false."
                ),
            },
        },
        "required": ["env"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_env_ensure(user, *, env: str, fresh: bool = False) -> dict[str, Any]:
    from connect_labs.labs.synthetic.ensure.engine import ensure_synthetic_data
    from connect_labs.labs.synthetic.ensure.registry import get_env_path

    try:
        env_path = get_env_path(env)
    except ValueError as exc:
        raise MCPToolError("NOT_FOUND", str(exc))
    return ensure_synthetic_data(str(env_path), fresh=fresh)


# =============================================================================
# Task 19: Two-phase clone tools (profile / generate / fidelity)
# =============================================================================


@register(
    name="synthetic_profile_opp",
    description=(
        "PHASE 1 (prod-touching). Profile one real opportunity into a self-contained "
        "profile bundle on disk (manifest.yaml + app_structure.json + scrubbed "
        "opportunity.json). Reads real exports with the caller's OAuth token and "
        "persists ONLY aggregate stats + program config — no row-level data. "
        "Run this in safe mode; Phase 2 (synthetic_generate_opp) needs no prod access."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_opportunity_id": {"type": "integer"},
            "curate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Floor flag rates and give degenerate clinical categoricals minority mass so "
                    "derived rates have variance to model (#670). Outcome fields such as child_alive "
                    "are never curated (#1189)."
                ),
            },
            "mirror": {
                "type": "boolean",
                "default": False,
                "description": (
                    "High-fidelity close mirror (#713): carry a de-identified per-entity transplant "
                    "pool so the clone reproduces the source opp's exact visits-per-case, "
                    "cases-per-FLW, timing and per-entity value trajectories rather than "
                    "re-sampling from marginals."
                ),
            },
            "out_dir": {
                "type": "string",
                "description": (
                    "Where to write the <opp_id>/ bundle: a local directory path, or "
                    "'gdrive:<folder_id>' (or bare 'gdrive:' to auto-create a run folder) "
                    "to persist it durably in Google Drive."
                ),
            },
        },
        "required": ["source_opportunity_id", "out_dir"],
        "additionalProperties": False,
    },
    is_write=False,
    wants_progress=True,
)
def synthetic_profile_opp(
    user,
    *,
    source_opportunity_id: int,
    out_dir: str,
    curate: bool = False,
    mirror: bool = False,
    progress=NULL_PROGRESS,
) -> dict[str, Any]:
    _require_opportunity_access(user, source_opportunity_id)
    try:
        token = require_connect_token(user)
    except MCPToolError:
        raise MCPToolError("PERMISSION_DENIED", "No Connect token — cannot fetch production data.")
    drive = DriveClient() if str(out_dir).startswith("gdrive:") else None
    store = make_bundle_store(out_dir, drive=drive)
    handle = profile_opp_to_bundle(
        source_opportunity_id,
        curate=curate,
        mirror=mirror,
        base_url=settings.CONNECT_PRODUCTION_URL,
        oauth_token=token,
        store=store,
        progress=progress,
    )
    resolved = f"gdrive:{store.root_folder_id}" if hasattr(store, "root_folder_id") else str(out_dir)
    return {
        "bundle_dir": str(handle),
        "bundle_root": resolved,
        "source_opportunity_id": source_opportunity_id,
    }


@register(
    name="synthetic_profile_opps_bulk",
    description=(
        "PHASE 1 (prod-touching). Profile multiple real opportunities into "
        "self-contained profile bundles. Each opp is profiled independently; "
        "failures are logged and skipped so a single bad opp doesn't abort the rest. "
        "Returns the resolved bundle_root (pass it to synthetic_generate_opps_bulk) "
        "plus the per-opp bundle handles. Use out_dir='gdrive:' for a durable, "
        "container-independent run that survives partial failures."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "List of real opportunity IDs to profile.",
            },
            "curate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Floor flag rates and give degenerate clinical categoricals minority mass so "
                    "derived rates have variance to model (#670). Outcome fields such as child_alive "
                    "are never curated (#1189)."
                ),
            },
            "mirror": {
                "type": "boolean",
                "default": False,
                "description": (
                    "High-fidelity close mirror (#713): carry a de-identified per-entity transplant "
                    "pool so the clone reproduces the source opp's exact visits-per-case, "
                    "cases-per-FLW, timing and per-entity value trajectories rather than "
                    "re-sampling from marginals."
                ),
            },
            "out_dir": {
                "type": "string",
                "description": (
                    "Where to write the bundles: a local directory path, or "
                    "'gdrive:<folder_id>' (or bare 'gdrive:' to auto-create a run folder) "
                    "to persist them durably in Google Drive."
                ),
            },
        },
        "required": ["source_opportunity_ids", "out_dir"],
        "additionalProperties": False,
    },
    is_write=False,
    wants_progress=True,
)
def synthetic_profile_opps_bulk(
    user,
    *,
    source_opportunity_ids: list[int],
    out_dir: str,
    curate: bool = False,
    mirror: bool = False,
    progress=NULL_PROGRESS,
) -> dict[str, Any]:
    for opp_id in source_opportunity_ids:
        _require_opportunity_access(user, opp_id)
    try:
        token = require_connect_token(user)
    except MCPToolError:
        raise MCPToolError("PERMISSION_DENIED", "No Connect token — cannot fetch production data.")
    drive = DriveClient() if str(out_dir).startswith("gdrive:") else None
    resolved, handles = profile_opps_bulk(
        source_opportunity_ids,
        curate=curate,
        mirror=mirror,
        base_url=settings.CONNECT_PRODUCTION_URL,
        oauth_token=token,
        bundle_root=out_dir,
        drive=drive,
        progress=progress,
    )
    return {
        "bundle_root": resolved,
        "bundle_dirs": handles,
        "succeeded": len(handles),
        "requested": len(source_opportunity_ids),
    }


@register(
    name="synthetic_fidelity_vs_source",
    description=(
        "PROD-TOUCHING. Score a clone against the REAL opportunity it was cloned from — "
        "the measurement that answers 'would an analysis run on this clone reach the same "
        "conclusion as on real data'. Regenerates the clone deterministically from its "
        "profile bundle, fetches the real opp's visits, and returns an overall 0-1 fidelity "
        "score plus its components: per-field normalized Wasserstein distance and "
        "out-of-range leakage, per-entity trajectory slope deltas (the growth-curve axis), "
        "and total-variation distance on visits-per-case and cases-per-FLW. "
        "Distinct from synthetic_fidelity_report, which only checks a clone against its OWN "
        "manifest (did generation hit its targets) and so cannot detect drift from reality. "
        "Output is aggregate statistics only — no row-level data leaves the source."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "bundle_dir": {
                "type": "string",
                "description": (
                    "The clone's profile bundle: a local path, or 'gdrive:<subfolder_id>'. "
                    "The clone is regenerated from it deterministically (same seed), so the "
                    "scored fixtures match what was registered."
                ),
            },
            "top_n_fields": {
                "type": "integer",
                "description": "Cap per-field detail in the response (default 15). The score always uses every field.",
            },
        },
        "required": ["bundle_dir"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_fidelity_vs_source(user, *, bundle_dir: str, top_n_fields: int = 15) -> dict[str, Any]:
    try:
        token = require_connect_token(user)
    except Exception:
        raise MCPToolError("PERMISSION_DENIED", "No Connect token — cannot fetch the real source opportunity.")

    drive = DriveClient() if str(bundle_dir).startswith("gdrive:") else None
    if drive is not None:
        from connect_labs.labs.synthetic.bundle import GDriveBundleStore

        bundle = GDriveBundleStore(drive, "").read(str(bundle_dir)[len("gdrive:") :])
    else:
        bundle = read_bundle(bundle_dir)

    try:
        manifest = Manifest.from_yaml(bundle.manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    source_visits = _fetch_endpoint(settings.CONNECT_PRODUCTION_URL, bundle.source_opp_id, "user_visits", token)
    if not isinstance(source_visits, list) or not source_visits:
        raise MCPToolError("UPSTREAM_ERROR", f"No user_visits returned for source opp {bundle.source_opp_id}.")

    form_schema = parse_form_schema_from_app_json(bundle.app_structure, app_type="deliver")
    clone_visits = _generate(
        manifest=manifest,
        opportunity_detail=bundle.opportunity,
        form_schema=form_schema,
        app_structure=bundle.app_structure,
    ).get("user_visits", [])

    cohort = manifest.beneficiary_cohorts[0]
    numeric_paths = {
        path
        for path, dist in cohort.field_distributions.items()
        if isinstance(dist, (NormalDistribution, UniformDistribution))
    }
    if not numeric_paths:
        raise MCPToolError("INVALID_SCHEMA", "Bundle manifest declares no numeric fields to score.")

    report = compare_to_source(source_visits, clone_visits, numeric_paths=numeric_paths)

    fields = report.get("fields", {})
    worst = sorted(fields.items(), key=lambda kv: -kv[1].get("wasserstein_norm", 0.0))[:top_n_fields]
    return {
        "source_opportunity_id": bundle.source_opp_id,
        "score": report.get("score"),
        "source_visit_count": len(source_visits),
        "clone_visit_count": len(clone_visits),
        "visits_per_case_tvd": report.get("visits_per_case_tvd"),
        "cases_per_flw_tvd": report.get("cases_per_flw_tvd"),
        "fields_scored": len(fields),
        "worst_fields": dict(worst),
        "trajectory": report.get("trajectory", {}),
    }


@register(
    name="synthetic_generate_opp",
    description=(
        "PHASE 2 (offline, no prod). Generate fixture data and register a labs-only "
        "synthetic opportunity from a profile bundle written by synthetic_profile_opp. "
        "Idempotent: if a SyntheticOpportunity cloned from the same source already "
        "exists and fresh=False, returns the existing row immediately (skipped=True). "
        "Pass fresh=True to regenerate from scratch, or target_opportunity_id to "
        "register onto a specific labs-only opp (bypasses the cloned_from lookup — "
        "how a source whose twin is claimed elsewhere gets a second, explicitly-placed twin)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "bundle_dir": {
                "type": "string",
                "description": (
                    "Path to the bundle directory (e.g. /tmp/bundles/523), or "
                    "'gdrive:<subfolder_id>' for a bundle subfolder in Drive."
                ),
            },
            "program_id": {
                "type": "integer",
                "description": "Labs-only program ID to file this opp under.",
            },
            "program_name": {"type": "string", "default": "Labs Synthetic"},
            "org_name": {"type": "string", "default": "Labs Synthetic"},
            "fresh": {
                "type": "boolean",
                "default": False,
                "description": "If True, regenerate even if a row for this source already exists.",
            },
            "target_opportunity_id": {
                "type": "integer",
                "description": (
                    "Register onto THIS labs-only opp id, bypassing the cloned_from "
                    "idempotency lookup entirely; the existing twin (if any) is untouched."
                ),
            },
        },
        "required": ["bundle_dir", "program_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_generate_opp(
    user,
    *,
    bundle_dir: str,
    program_id: int,
    program_name: str = "Labs Synthetic",
    org_name: str = "Labs Synthetic",
    fresh: bool = False,
    target_opportunity_id: int | None = None,
) -> dict[str, Any]:
    drive = DriveClient()
    result = generate_opp_from_bundle(
        bundle_dir,
        drive=drive,
        program_id=program_id,
        program_name=program_name,
        org_name=org_name,
        fresh=fresh,
        target_opportunity_id=target_opportunity_id,
    )
    return {
        "source_opportunity_id": result.source_opportunity_id,
        "opportunity_id": result.opportunity_id,
        "gdrive_folder_id": result.gdrive_folder_id,
        "folder_url": result.folder_url,
        "record_counts": result.record_counts,
        "app_structure_present": result.app_structure_present,
        "skipped": result.skipped,
        "dropped_pool_paths": list(getattr(result, "dropped_pool_paths", []) or []),
        "parity": getattr(result, "parity", {}) or {},
    }


@register(
    name="synthetic_generate_opps_bulk",
    description=(
        "PHASE 2 (offline, no prod). Generate fixtures and register labs-only "
        "synthetic opportunities for every bundle subdirectory under bundle_root. "
        "Allocates one shared program_id for the cohort. Per-opp failures are "
        "logged and skipped. Returns a list of CloneResult dicts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "bundle_root": {
                "type": "string",
                "description": "Directory whose immediate subdirectories are bundle dirs.",
            },
            "program_name": {"type": "string", "default": "Labs Synthetic"},
            "org_name": {"type": "string", "default": "Labs Synthetic"},
            "fresh": {
                "type": "boolean",
                "default": False,
                "description": "If True, regenerate even where a row already exists.",
            },
        },
        "required": ["bundle_root"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_generate_opps_bulk(
    user,
    *,
    bundle_root: str,
    program_name: str = "Labs Synthetic",
    org_name: str = "Labs Synthetic",
    fresh: bool = False,
) -> dict[str, Any]:
    drive = DriveClient()
    results = generate_opps_bulk(
        bundle_root,
        drive=drive,
        program_name=program_name,
        org_name=org_name,
        fresh=fresh,
    )
    return {
        "results": [
            {
                "source_opportunity_id": r.source_opportunity_id,
                "opportunity_id": r.opportunity_id,
                "gdrive_folder_id": r.gdrive_folder_id,
                "folder_url": r.folder_url,
                "record_counts": r.record_counts,
                "app_structure_present": r.app_structure_present,
                "skipped": r.skipped,
                # Non-empty means the clone is missing data its source records.
                "dropped_pool_paths": list(getattr(r, "dropped_pool_paths", []) or []),
                "parity": getattr(r, "parity", {}) or {},
            }
            for r in results
        ],
        "succeeded": len(results),
    }


@register(
    name="synthetic_fidelity_report",
    description=(
        "Compare the synthetic fixtures in a profile bundle against the manifest "
        "they were generated from. Reads the bundle's manifest.yaml and "
        "user_visits fixture, then computes per-field mean/std/TVD deltas and a "
        "correlation Frobenius distance. Use this to verify that a generated "
        "dataset faithfully reproduces the target statistical shape."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "bundle_dir": {
                "type": "string",
                "description": "Path to the bundle directory (e.g. /tmp/bundles/523).",
            },
        },
        "required": ["bundle_dir"],
        "additionalProperties": False,
    },
    is_write=False,
)
def synthetic_fidelity_report(user, *, bundle_dir: str) -> dict[str, Any]:
    bundle = read_bundle(bundle_dir)
    try:
        manifest = Manifest.from_yaml(bundle.manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    form_schema = parse_form_schema_from_app_json(bundle.app_structure, app_type="deliver")
    synthetic_visits = _generate(
        manifest=manifest,
        opportunity_detail=bundle.opportunity,
        form_schema=form_schema,
        app_structure=bundle.app_structure,
    ).get("user_visits", [])

    report = compare(manifest, synthetic_visits)
    return {
        "bundle_dir": bundle_dir,
        "source_opportunity_id": bundle.source_opp_id,
        "synthetic_visit_count": len(synthetic_visits),
        **report,
    }


@register(
    name="synthetic_clone_profile",
    description=(
        "PHASE 1 (safe mode) for a whole cohort described by a YAML spec. The spec "
        "names opportunity_ids, the program (id + names), and bundle_root (use "
        "'gdrive:' for durable Drive storage). Profiles every opp into bundle_root and "
        "returns the UPDATED spec_yaml with the resolved bundle_root recorded — hand "
        "that returned spec straight to synthetic_clone_generate. Persists aggregate "
        "stats only; no row-level data."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "spec_yaml": {
                "type": "string",
                "description": (
                    "Cohort spec YAML: opportunity_ids (required), program_id, "
                    "program_name, org_name, bundle_root ('gdrive:' recommended)."
                ),
            },
        },
        "required": ["spec_yaml"],
        "additionalProperties": False,
    },
    is_write=False,
    wants_progress=True,
)
def synthetic_clone_profile(user, *, spec_yaml: str, progress=NULL_PROGRESS) -> dict[str, Any]:
    try:
        spec = CohortSpec.from_yaml(spec_yaml)
    except ValueError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    for opp_id in spec.opportunity_ids:
        _require_opportunity_access(user, opp_id)
    try:
        token = require_connect_token(user)
    except MCPToolError:
        raise MCPToolError("PERMISSION_DENIED", "No Connect token — cannot fetch production data.")
    drive = DriveClient() if str(spec.bundle_root).startswith("gdrive:") else None
    spec = profile_cohort(
        spec, base_url=settings.CONNECT_PRODUCTION_URL, oauth_token=token, drive=drive, progress=progress
    )
    return {
        "spec_yaml": spec.to_yaml(),
        "bundle_root": spec.bundle_root,
        "opportunity_ids": spec.opportunity_ids,
    }


@register(
    name="synthetic_clone_generate",
    description=(
        "PHASE 2 (offline, no prod) for a whole cohort described by a YAML spec — the "
        "spec returned by synthetic_clone_profile. Reads the bundles from bundle_root "
        "and registers every opp as a labs-only opportunity under the spec's program_id "
        "(allocated + recorded back if unset) with program_name/org_name. Idempotent; "
        "pass fresh=true to regenerate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "spec_yaml": {
                "type": "string",
                "description": "The cohort spec YAML returned by synthetic_clone_profile (bundle_root resolved).",
            },
            "fresh": {"type": "boolean", "default": False},
        },
        "required": ["spec_yaml"],
        "additionalProperties": False,
    },
    is_write=True,
    wants_progress=True,
)
def synthetic_clone_generate(user, *, spec_yaml: str, fresh: bool = False, progress=NULL_PROGRESS) -> dict[str, Any]:
    try:
        spec = CohortSpec.from_yaml(spec_yaml)
    except ValueError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    drive = DriveClient()
    spec, results = generate_cohort(spec, drive=drive, fresh=fresh, progress=progress)
    return {
        "spec_yaml": spec.to_yaml(),
        "program_id": spec.program_id,
        "program_name": spec.program_name,
        "generated": sum(1 for r in results if not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
        "opportunities": [
            {
                "source_opportunity_id": r.source_opportunity_id,
                "opportunity_id": r.opportunity_id,
                "skipped": r.skipped,
                # Non-empty means the clone is missing data its source records.
                "dropped_pool_paths": list(getattr(r, "dropped_pool_paths", []) or []),
                "parity": getattr(r, "parity", {}) or {},
            }
            for r in results
        ],
    }
