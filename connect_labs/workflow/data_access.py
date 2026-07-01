"""
Data Access Layer for Workflows and Pipelines.

This layer uses LabsRecordAPIClient to interact with production LabsRecord API.
It handles:
1. Managing workflow definitions, render code, instances, and chat history
2. Managing pipeline definitions, render code, and chat history
3. Fetching pipeline data for workflows that reference pipelines as sources
4. Sharing workflows and pipelines (making them available to others)
5. Fetching worker data dynamically from Connect OAuth APIs

This is a pure API client with no local database storage.
"""

import logging
from datetime import datetime, timezone

import httpx
from django.conf import settings
from django.http import HttpRequest

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord

logger = logging.getLogger(__name__)


class PipelineCacheMiss(Exception):
    """Raised by the cached-only pipeline read when a required pipeline has no
    usable processed cache for an opportunity. Callers (run completion) should
    surface this as "reload the dashboard, then retry" rather than silently
    re-executing the pipeline."""

    def __init__(self, alias: str, opportunity_id: int, pipeline_name: str = ""):
        self.alias = alias
        self.opportunity_id = opportunity_id
        self.pipeline_name = pipeline_name
        super().__init__(f"No cached data for pipeline {pipeline_name or alias!r} (opportunity {opportunity_id})")


# =============================================================================
# Proxy Models for LabsRecords
# =============================================================================


class WorkflowDefinitionRecord(LocalLabsRecord):
    """Proxy model for workflow definition LabsRecords."""

    @property
    def name(self):
        return self.data.get("name", "Untitled Workflow")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def version(self):
        return self.data.get("version", 1)

    @property
    def render_code_id(self):
        return self.data.get("render_code_id")

    @property
    def pipeline_sources(self) -> list[dict]:
        """List of pipeline sources: [{"pipeline_id": 123, "alias": "visits"}]"""
        return self.data.get("pipeline_sources", [])

    @property
    def opportunity_ids(self) -> list[int]:
        """List of opportunity IDs this workflow pulls data from.

        Empty list means legacy single-opp behavior; callers should fall back
        to [primary_opportunity_id] in that case.
        """
        return self.data.get("opportunity_ids", []) or []

    @property
    def template_type(self) -> str:
        return self.data.get("config", {}).get("templateType", "")

    @property
    def snapshot_inputs(self) -> dict | None:
        """Instance-owned completion-snapshot manifest, or None when this
        definition still relies on the template registry (legacy instances
        and hook templates)."""
        value = self.data.get("snapshot_inputs")
        return value if isinstance(value, dict) else None

    @property
    def multi_opp(self) -> bool:
        """Whether this workflow was created from a multi-opp template."""
        return bool(self.data.get("config", {}).get("multi_opp", False))

    @property
    def is_shared(self) -> bool:
        return self.data.get("is_shared", False)

    @property
    def shared_scope(self) -> str:
        return self.data.get("shared_scope", "global")


class WorkflowRenderCodeRecord(LocalLabsRecord):
    """Proxy model for workflow render code LabsRecords."""

    @property
    def definition_id(self):
        return self.data.get("definition_id")

    @property
    def component_code(self):
        return self.data.get("component_code", "")

    @property
    def version(self):
        return self.data.get("version", 1)


# Workflow run status enum. Two states only — abandoned runs are
# indistinguishable from in-progress, so the user explicitly marking a run
# completed is the only terminal transition. See docs/plans/2026-05-04-run-state-final.md.
RUN_STATUS_IN_PROGRESS = "in_progress"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUSES = frozenset({RUN_STATUS_IN_PROGRESS, RUN_STATUS_COMPLETED})

# Defensive mapping: any prod row that the (now-reverted) 04-30 migration
# touched will read back through the proxy as the canonical value.
_LEGACY_STATUS_MAP = {
    "active": RUN_STATUS_IN_PROGRESS,
    "frozen": RUN_STATUS_COMPLETED,
}


class WorkflowRunRecord(LocalLabsRecord):
    """Proxy model for workflow run LabsRecords."""

    @property
    def definition_id(self):
        return self.data.get("definition_id")

    @property
    def period_start(self):
        top = self.data.get("period_start")
        if top:
            return top
        return self.data.get("state", {}).get("period_start")

    @property
    def period_end(self):
        top = self.data.get("period_end")
        if top:
            return top
        return self.data.get("state", {}).get("period_end")

    @property
    def status(self):
        """Run status. Two values only: in_progress, completed.

        Defaults to in_progress for missing/unknown values so render code never
        sees a third state. Legacy `active`/`frozen` (from a brief vocabulary
        detour) map back to in_progress/completed defensively, so any row a
        deleted migration may have touched still reads correctly.
        """
        top = self.data.get("status") or self.data.get("state", {}).get("status")
        if top in RUN_STATUSES:
            return top
        if top in _LEGACY_STATUS_MAP:
            return _LEGACY_STATUS_MAP[top]
        return RUN_STATUS_IN_PROGRESS

    @property
    def is_completed(self) -> bool:
        return self.status == RUN_STATUS_COMPLETED

    @property
    def completed_at(self):
        # Some legacy rows may have stamped `frozen_at` instead.
        return self.data.get("completed_at") or self.data.get("frozen_at")

    @property
    def state(self):
        return self.data.get("state", {})

    @property
    def snapshot(self):
        return self.data.get("snapshot")

    @property
    def created_at(self):
        return self.data.get("created_at", "")

    @property
    def selected_count(self) -> int:
        state = self.data.get("state", {})
        if "selected_workers" in state:
            selected = state.get("selected_workers", [])
            return len(selected) if isinstance(selected, list) else 0
        if "selected_flws" in state:
            selected = state.get("selected_flws", [])
            return len(selected) if isinstance(selected, list) else 0
        if "flw_count" in state:
            return state.get("flw_count", 0)
        return 0


class WorkflowChatHistoryRecord(LocalLabsRecord):
    """Proxy model for workflow chat history LabsRecords."""

    @property
    def definition_id(self):
        return self.data.get("definition_id")

    @property
    def messages(self):
        return self.data.get("messages", [])


class PipelineDefinitionRecord(LocalLabsRecord):
    """Proxy model for pipeline definition LabsRecords."""

    @property
    def name(self):
        return self.data.get("name", "Untitled Pipeline")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def version(self):
        return self.data.get("version", 1)

    @property
    def render_code_id(self):
        return self.data.get("render_code_id")

    @property
    def schema(self) -> dict:
        """Get the pipeline schema (fields, grouping, etc.)."""
        return self.data.get("schema", {})

    @property
    def is_shared(self) -> bool:
        return self.data.get("is_shared", False)

    @property
    def shared_scope(self) -> str:
        return self.data.get("shared_scope", "global")


class PipelineRenderCodeRecord(LocalLabsRecord):
    """Proxy model for pipeline render code LabsRecords."""

    @property
    def definition_id(self):
        return self.data.get("definition_id")

    @property
    def component_code(self):
        return self.data.get("component_code", "")

    @property
    def version(self):
        return self.data.get("version", 1)


class PipelineChatHistoryRecord(LocalLabsRecord):
    """Proxy model for pipeline chat history LabsRecords."""

    @property
    def definition_id(self):
        return self.data.get("definition_id")

    @property
    def messages(self):
        return self.data.get("messages", [])


# =============================================================================
# Base Data Access Class
# =============================================================================


class BaseDataAccess:
    """Base class with shared functionality for data access."""

    def __init__(
        self,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
        program_id: int | None = None,
        user=None,
        request: HttpRequest | None = None,
        access_token: str | None = None,
    ):
        """
        Initialize data access layer.

        Args:
            opportunity_id: Optional opportunity ID for scoped API requests
            organization_id: Optional organization ID for scoped API requests
            program_id: Optional program ID for scoped API requests
            user: Django User object (for OAuth token extraction)
            request: HttpRequest object (for extracting token and org context)
            access_token: OAuth token for Connect production APIs
        """
        self.opportunity_id = opportunity_id
        self.organization_id = organization_id
        self.program_id = program_id
        self.user = user
        self.request = request

        # Use labs_context from middleware if available
        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not opportunity_id and "opportunity_id" in labs_context:
                self.opportunity_id = labs_context["opportunity_id"]
            if not program_id and "program_id" in labs_context:
                self.program_id = labs_context["program_id"]
            if not organization_id and "organization_id" in labs_context:
                self.organization_id = labs_context["organization_id"]

        # Get OAuth token
        if not access_token and request:
            if hasattr(request, "session") and "labs_oauth" in request.session:
                access_token = request.session["labs_oauth"].get("access_token")
            elif user:
                # allauth SocialAccount was removed during labs simplification.
                # Non-labs users won't have Connect tokens via this path.
                pass

        if not access_token:
            raise ValueError("OAuth access token required for data access")

        self.access_token = access_token
        self.production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")

        # Initialize HTTP client with Bearer token
        self.http_client = httpx.Client(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=120.0,
        )

        # Initialize Labs API client
        self.labs_api = LabsRecordAPIClient(
            access_token,
            opportunity_id=self.opportunity_id,
            organization_id=self.organization_id,
            program_id=self.program_id,
        )

    def close(self):
        """Close HTTP client. Safe to call multiple times (idempotent)."""
        if self.http_client:
            self.http_client.close()
            self.http_client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _call_connect_api(self, endpoint: str) -> httpx.Response:
        """Call Connect production API with OAuth token."""
        url = f"{self.production_url}{endpoint}"
        response = self.http_client.get(url)
        response.raise_for_status()
        return response


# =============================================================================
# Workflow Data Access
# =============================================================================


class WorkflowDataAccess(BaseDataAccess):
    """
    Data access layer for workflows.

    Handles workflow definitions, render code, instances, chat history,
    and fetching pipeline data for workflows that reference pipelines.
    """

    EXPERIMENT = "workflow"

    # -------------------------------------------------------------------------
    # Workflow Definition Methods
    # -------------------------------------------------------------------------

    def list_definitions(self, include_shared: bool = False) -> list[WorkflowDefinitionRecord]:
        """
        List workflow definitions.

        Args:
            include_shared: If True, also include shared workflows from others

        Returns:
            List of WorkflowDefinitionRecord instances
        """
        # Get user's own workflows
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            model_class=WorkflowDefinitionRecord,
        )

        if include_shared:
            # Also get shared workflows (public=True)
            shared_records = self.labs_api.get_records(
                experiment=self.EXPERIMENT,
                type="workflow_definition",
                model_class=WorkflowDefinitionRecord,
                public=True,
            )
            # Merge, avoiding duplicates
            seen_ids = {r.id for r in records}
            for r in shared_records:
                if r.id not in seen_ids:
                    records.append(r)

        return records

    def get_definition(self, definition_id: int) -> WorkflowDefinitionRecord | None:
        """Get a workflow definition by ID."""
        return self.labs_api.get_record_by_id(
            record_id=definition_id,
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            model_class=WorkflowDefinitionRecord,
        )

    def create_definition(self, name: str, description: str, **kwargs) -> WorkflowDefinitionRecord:
        """
        Create a new workflow definition.

        Args:
            name: Workflow name
            description: Workflow description
            **kwargs: Additional data fields (statuses, config, pipeline_sources, opportunity_ids)

        Returns:
            Created WorkflowDefinitionRecord
        """
        data = {
            "name": name,
            "description": description,
            "version": 1,
            "statuses": kwargs.get(
                "statuses",
                [
                    {"id": "pending", "label": "Pending", "color": "gray"},
                    {"id": "reviewed", "label": "Reviewed", "color": "green"},
                ],
            ),
            "config": kwargs.get("config", {"showSummaryCards": True, "showFilters": True}),
            "pipeline_sources": kwargs.get("pipeline_sources", []),
            "opportunity_ids": kwargs.get("opportunity_ids", []),
            "is_shared": False,
            "shared_scope": "global",
        }
        # Instance-owned completion contract — only present when explicitly
        # provided (an empty dict is meaningful: "capture everything").
        if kwargs.get("snapshot_inputs") is not None:
            data["snapshot_inputs"] = kwargs["snapshot_inputs"]

        record = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            data=data,
        )

        return WorkflowDefinitionRecord(
            {
                "id": record.id,
                "experiment": record.experiment,
                "type": record.type,
                "data": record.data,
                "opportunity_id": record.opportunity_id,
            }
        )

    def update_definition(self, definition_id: int, data: dict) -> WorkflowDefinitionRecord | None:
        """Update a workflow definition."""
        result = self.labs_api.update_record(
            record_id=definition_id,
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            data=data,
        )
        if result:
            return WorkflowDefinitionRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
        return None

    def update_opportunity_ids(
        self, definition_id: int, opportunity_ids: list[int]
    ) -> WorkflowDefinitionRecord | None:
        """Replace the opportunity_ids list on a workflow definition.

        Other fields in `data` are preserved.
        """
        existing = self.get_definition(definition_id)
        if not existing:
            return None

        updated_data = {**existing.data, "opportunity_ids": list(opportunity_ids)}
        return self.update_definition(definition_id, updated_data)

    def _definition_opportunity_ids(self, definition) -> list[int]:
        """Opportunities a definition's audits may span: the WDA's own opp plus
        the definition's multi-opp ``opportunity_ids``."""
        opp_ids: list[int] = []
        if self.opportunity_id:
            opp_ids.append(self.opportunity_id)
        if definition is not None:
            for oid in definition.data.get("opportunity_ids") or []:
                if oid and oid not in opp_ids:
                    opp_ids.append(oid)
        return opp_ids

    def _scoped_audit_session_ids(self, run_id: int, opportunity_ids: list[int]) -> list[int]:
        """AuditSession record ids linked to ``run_id`` across every given
        opportunity.

        Audit sessions carry ``labs_record_id=run_id`` but are scoped per
        opportunity — the labs API injects the client's ``opportunity_id`` into
        every GET, so a single-opp query misses a multi-opp run's sessions in
        the other opps. Querying each opportunity with its own scoped client
        gathers them all; deletion is membership-based, so one
        ``delete_records`` call removes sessions across several opps at once.
        """
        from connect_labs.audit.data_access import AuditDataAccess

        token = self.access_token
        if not token and self.request is not None:
            token = (self.request.session.get("labs_oauth", {}) or {}).get("access_token")

        ids: list[int] = []
        seen: set[int] = set()
        for opp_id in opportunity_ids:
            if not opp_id:
                continue
            ada = AuditDataAccess(access_token=token, opportunity_id=opp_id)
            try:
                for session in ada.get_sessions_by_workflow_run(run_id):
                    if session.id not in seen:
                        seen.add(session.id)
                        ids.append(session.id)
            except Exception as e:
                logger.warning("Failed to query audit sessions for run %s opp %s: %s", run_id, opp_id, e)
            finally:
                try:
                    ada.close()
                except Exception:
                    pass
        return ids

    def delete_definition(self, definition_id: int, delete_linked: bool = False) -> dict:
        """Delete a workflow definition and optionally related records.

        Args:
            definition_id: ID of the workflow definition to delete
            delete_linked: If True, also delete runs and their linked audit sessions.
                          Render code and chat history are always deleted with the definition.

        Returns:
            dict with counts of deleted records:
            {"definition": 1, "render_code": N, "runs": N, "audit_sessions": N, "chat_history": N}
        """
        deleted_counts = {"definition": 0, "render_code": 0, "runs": 0, "audit_sessions": 0, "chat_history": 0}

        # Collect all IDs to delete in a single batch at the end
        ids_to_delete: list[int] = []

        # Fetch the definition + render code up front: both feed the safety
        # backup below, and the definition is reused to scope linked deletes.
        definition = self.get_definition(definition_id)
        render_code = self.get_render_code(definition_id)

        # Safety backup: persist the workflow (definition + render-code JSX) to
        # the labs DB BEFORE anything is deleted. Fail-closed — if the backup
        # cannot be written, the exception propagates and nothing is deleted.
        self._backup_definition(definition, render_code)

        # Always delete render code (belongs to the definition)
        if render_code:
            ids_to_delete.append(render_code.id)
            deleted_counts["render_code"] = 1

        # Always delete chat history (belongs to the definition)
        chat_history = self.get_chat_history(definition_id)
        if chat_history:
            ids_to_delete.append(chat_history.id)
            deleted_counts["chat_history"] = 1

        if delete_linked:
            # Collect all run and audit session IDs for batch deletion. Audits
            # are gathered across every opportunity the definition spans (a
            # multi-opp workflow's audits live in several opps), not just the
            # primary — otherwise non-primary opps' audits orphan on delete.
            opp_ids = self._definition_opportunity_ids(definition)
            runs = self.list_runs(definition_id)
            for run in runs:
                run_opp_ids = list(opp_ids)
                if getattr(run, "opportunity_id", None) and run.opportunity_id not in run_opp_ids:
                    run_opp_ids.append(run.opportunity_id)
                audit_ids = self._scoped_audit_session_ids(run.id, run_opp_ids)
                ids_to_delete.extend(audit_ids)
                deleted_counts["audit_sessions"] += len(audit_ids)

                ids_to_delete.append(run.id)
                deleted_counts["runs"] += 1

        # Add the definition itself
        ids_to_delete.append(definition_id)
        deleted_counts["definition"] = 1

        # Single batch delete for all collected IDs
        self.labs_api.delete_records(ids_to_delete)

        return deleted_counts

    def _backup_definition(self, definition, render_code) -> None:
        """Persist a restorable copy of a workflow to the labs DB before delete.

        Captures the definition JSON plus its render-code JSX so a deleted
        workflow can be reconstructed by hand. Runs are intentionally excluded.
        Fail-closed: any exception propagates to the caller, which aborts the
        delete. A missing definition (already gone) is a no-op — nothing to save.
        """
        if definition is None:
            return

        # Imported here to avoid a module-level dependency of the API-backed
        # data-access layer on a labs-DB Django model.
        from connect_labs.labs.models import DeletedWorkflowBackup

        DeletedWorkflowBackup.objects.create(
            definition_id=definition.id,
            opportunity_id=getattr(definition, "opportunity_id", None) or self.opportunity_id or 0,
            name=definition.name,
            template_type=definition.template_type,
            definition_data=definition.data,
            render_code=(render_code.component_code if render_code else ""),
            deleted_by=(self.user.username if getattr(self, "user", None) else ""),
        )
        logger.info(
            "[WorkflowBackup] backed up definition=%s opp=%s name=%r by=%s",
            definition.id,
            getattr(definition, "opportunity_id", None),
            definition.name,
            (self.user.username if getattr(self, "user", None) else ""),
        )

    # -------------------------------------------------------------------------
    # Workflow Render Code Methods
    # -------------------------------------------------------------------------

    def get_render_code(self, definition_id: int) -> WorkflowRenderCodeRecord | None:
        """Get render code for a workflow definition."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="workflow_render_code",
            model_class=WorkflowRenderCodeRecord,
        )
        for record in records:
            if record.data.get("definition_id") == definition_id:
                return record
        return None

    def save_render_code(self, definition_id: int, component_code: str, version: int = 1) -> WorkflowRenderCodeRecord:
        """Save render code for a workflow definition.

        After writing the render_code record, repoints the workflow
        definition's `render_code_id` at it. Without that step, repeated
        saves create orphan records and the runner reads stale (or null)
        code — exactly the bug that hit MBW v3 when push-render appeared
        to succeed but the dashboard kept rendering blank.
        """
        existing = self.get_render_code(definition_id)

        data = {
            "definition_id": definition_id,
            "component_code": component_code,
            "version": version,
        }

        if existing:
            result = self.labs_api.update_record(
                record_id=existing.id,
                experiment=self.EXPERIMENT,
                type="workflow_render_code",
                data=data,
            )
        else:
            result = self.labs_api.create_record(
                experiment=self.EXPERIMENT,
                type="workflow_render_code",
                data=data,
            )

        # Repoint the workflow's render_code_id at the (possibly new) record
        # so the runner's `get_render_code` finds it. Initial creation via
        # `create_from_template` sets this once; subsequent saves used to
        # leak orphan records.
        definition = self.get_definition(definition_id)
        if definition and definition.data.get("render_code_id") != result.id:
            updated_data = {**definition.data, "render_code_id": result.id}
            self.labs_api.update_record(
                record_id=definition_id,
                experiment=self.EXPERIMENT,
                type="workflow_definition",
                data=updated_data,
            )

        return WorkflowRenderCodeRecord(
            {
                "id": result.id,
                "experiment": result.experiment,
                "type": result.type,
                "data": result.data,
                "opportunity_id": result.opportunity_id,
            }
        )

    # -------------------------------------------------------------------------
    # Workflow Run Methods
    # -------------------------------------------------------------------------

    def list_runs(self, definition_id: int | None = None) -> list[WorkflowRunRecord]:
        """List workflow runs."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="workflow_run",
            model_class=WorkflowRunRecord,
        )
        if definition_id:
            records = [r for r in records if r.data.get("definition_id") == definition_id]
        return records

    def get_run(self, run_id: int) -> WorkflowRunRecord | None:
        """Get a workflow run by ID."""
        return self.labs_api.get_record_by_id(
            record_id=run_id,
            experiment=self.EXPERIMENT,
            type="workflow_run",
            model_class=WorkflowRunRecord,
        )

    def create_run(
        self,
        definition_id: int,
        opportunity_id: int,
        period_start: str,
        period_end: str,
        initial_state: dict | None = None,
    ) -> WorkflowRunRecord:
        """
        Create a new workflow run.

        Args:
            definition_id: ID of the workflow definition
            opportunity_id: ID of the opportunity
            period_start: Start date of the period (ISO format)
            period_end: End date of the period (ISO format)
            initial_state: Optional initial state dict

        Returns:
            Created WorkflowRunRecord
        """
        data = {
            "definition_id": definition_id,
            "period_start": period_start,
            "period_end": period_end,
            "status": RUN_STATUS_IN_PROGRESS,
            "state": initial_state or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        record = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="workflow_run",
            data=data,
        )

        return WorkflowRunRecord(
            {
                "id": record.id,
                "experiment": record.experiment,
                "type": record.type,
                "data": record.data,
                "opportunity_id": record.opportunity_id,
            }
        )

    def delete_run(self, run_id: int, delete_linked: bool = True) -> dict:
        """Delete a workflow run and optionally its linked records.

        Args:
            run_id: ID of the workflow run to delete
            delete_linked: If True, also delete linked audit sessions

        Returns:
            dict with counts of deleted records:
            {"run": 1, "audit_sessions": N}
        """
        deleted_counts = {"run": 0, "audit_sessions": 0}

        ids_to_delete: list[int] = []

        if delete_linked:
            # Audits are gathered across every opportunity the run spans (a
            # multi-opp run creates audits in several opps), not just the
            # primary — otherwise non-primary opps' audits orphan on delete.
            run = self.get_run(run_id)
            definition = self.get_definition(run.definition_id) if (run and run.definition_id) else None
            opp_ids = self._definition_opportunity_ids(definition)
            if run is not None and getattr(run, "opportunity_id", None) and run.opportunity_id not in opp_ids:
                opp_ids.append(run.opportunity_id)
            audit_ids = self._scoped_audit_session_ids(run_id, opp_ids)
            ids_to_delete.extend(audit_ids)
            deleted_counts["audit_sessions"] = len(audit_ids)
            if audit_ids:
                logger.info(
                    "Deleting %d audit session(s) linked to run %s across opps %s", len(audit_ids), run_id, opp_ids
                )

        # Add the run itself
        ids_to_delete.append(run_id)
        deleted_counts["run"] = 1

        # Single batch delete
        self.labs_api.delete_records(ids_to_delete)

        return deleted_counts

    def update_run_state(
        self, run_id: int, new_state: dict, run: WorkflowRunRecord | None = None
    ) -> WorkflowRunRecord | None:
        """Merge new_state into run.data['state']. In-progress runs only.

        Returns None if the run doesn't exist or is already completed (caller
        view turns that into a 409). Status and period_* are not accepted in
        new_state — those are managed by create_run / complete_run.
        """
        if run is None:
            run = self.get_run(run_id)
        if not run:
            return None
        if run.is_completed:
            # Caller is responsible for translating this into a 409 response.
            return None

        # Strip protected keys silently — render code occasionally piggybacks
        # them and we want a clean separation between state writes and status
        # transitions. Status changes go through complete_run only.
        sanitized = {k: v for k, v in new_state.items() if k not in {"status", "period_start", "period_end"}}

        current_state = run.data.get("state", {})
        merged_state = {**current_state, **sanitized}
        updated_data = {**run.data, "state": merged_state}

        result = self.labs_api.update_record(
            record_id=run_id,
            experiment=self.EXPERIMENT,
            type="workflow_run",
            data=updated_data,
            current_record=run,
        )
        if result:
            return WorkflowRunRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
        return None

    def complete_run(
        self,
        run_id: int,
        snapshot: dict,
        run: WorkflowRunRecord | None = None,
    ) -> WorkflowRunRecord | None:
        """Atomic in_progress → completed transition: persist the snapshot,
        flip status, stamp completed_at — single LabsRecord write.

        The caller (the completion API endpoint) builds the snapshot via the
        template's build_snapshot hook before calling this. If the build
        raises, the caller never invokes complete_run and the run stays
        in_progress.

        Returns None if the run is missing or already completed.
        """
        if run is None:
            run = self.get_run(run_id)
        if not run:
            return None
        if run.is_completed:
            # Idempotency: completed is terminal. Caller should 409 rather than
            # silently re-write a snapshot the user can't re-derive.
            return None

        completed_at = datetime.now(timezone.utc).isoformat()
        updated_data = {
            **run.data,
            "status": RUN_STATUS_COMPLETED,
            "completed_at": completed_at,
            "snapshot": snapshot,
        }

        result = self.labs_api.update_record(
            record_id=run_id,
            experiment=self.EXPERIMENT,
            type="workflow_run",
            data=updated_data,
            current_record=run,
        )
        if result:
            return WorkflowRunRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
        return None

    # -------------------------------------------------------------------------
    # Pipeline Source Methods
    # -------------------------------------------------------------------------

    def add_pipeline_source(self, definition_id: int, pipeline_id: int, alias: str) -> WorkflowDefinitionRecord | None:
        """Add a pipeline as a data source for a workflow."""
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        sources = definition.data.get("pipeline_sources", [])
        # Check if already exists
        for source in sources:
            if source.get("alias") == alias:
                source["pipeline_id"] = pipeline_id
                break
        else:
            sources.append({"pipeline_id": pipeline_id, "alias": alias})

        updated_data = {**definition.data, "pipeline_sources": sources}
        return self.update_definition(definition_id, updated_data)

    def remove_pipeline_source(self, definition_id: int, alias: str) -> WorkflowDefinitionRecord | None:
        """Remove a pipeline source from a workflow."""
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        sources = definition.data.get("pipeline_sources", [])
        sources = [s for s in sources if s.get("alias") != alias]

        updated_data = {**definition.data, "pipeline_sources": sources}
        return self.update_definition(definition_id, updated_data)

    def get_pipeline_data(self, definition_id: int, opportunity_id: int) -> dict[str, dict]:
        """
        Fetch data from all pipeline sources defined in a workflow.

        If the workflow has a non-empty `opportunity_ids` list, each pipeline is
        executed once per opp and rows are concatenated with an `opportunity_id`
        tag on every row. Otherwise, falls back to [opportunity_id] (the primary
        opp passed in), preserving legacy single-opp behavior.

        Returns:
            Dict mapping alias to pipeline result:
                {
                    "visits": {
                        "rows": [{...fields, "opportunity_id": int}, ...],
                        "metadata": {
                            "opportunity_ids": [int, ...],
                            "per_opp": {opp_id: {...per-opp metadata or {"error": str}}},
                            "row_count": int,
                        },
                    },
                }
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return {}

        sources = definition.pipeline_sources
        if not sources:
            return {}

        opp_ids = definition.opportunity_ids or [opportunity_id]

        results = {}
        pipeline_access = PipelineDataAccess(
            request=self.request,
            access_token=self.access_token,
            opportunity_id=opportunity_id,
            organization_id=self.organization_id,
            program_id=self.program_id,
        )

        # Pre-resolve cross-pipeline JOIN config_hashes and topologically sort
        # so dependencies run before dependents. Mirrors what the SSE pipeline
        # stream view does — keeps celery-driven and SSE-driven paths consistent.
        # Without this, the visits pipeline (which JOINs registrations) errors
        # out with "resolved_config_hash not set" before any SQL runs.
        from connect_labs.labs.analysis.utils import resolve_join_hashes
        from connect_labs.workflow.views import _resolve_pipeline_sources_for_run

        ordered_sources, configs_by_alias = _resolve_pipeline_sources_for_run(pipeline_access, sources)
        if configs_by_alias:
            resolve_join_hashes(configs_by_alias)

        try:
            for source in ordered_sources:
                pipeline_id = source.get("pipeline_id")
                alias = source.get("alias")
                if not pipeline_id or not alias:
                    continue

                merged_rows: list[dict] = []
                # Keys are stringified because JSON serialization coerces dict
                # keys to strings. Using str keys here matches what JS clients
                # see, so `metadata.per_opp[String(oppId)]` works end-to-end.
                per_opp_meta: dict[str, dict] = {}
                for opp_id in opp_ids:
                    try:
                        pipeline_result = pipeline_access.execute_pipeline(
                            pipeline_id, opp_id, config=configs_by_alias.get(alias)
                        )
                        merged_rows.extend(
                            {**row, "opportunity_id": opp_id} for row in pipeline_result.get("rows", [])
                        )
                        per_opp_meta[str(opp_id)] = pipeline_result.get("metadata", {})
                    except Exception as e:
                        logger.exception("Pipeline %s failed for opp %s", pipeline_id, opp_id)
                        per_opp_meta[str(opp_id)] = {"error": str(e)}

                results[alias] = {
                    "rows": merged_rows,
                    "metadata": {
                        # pipeline_id is the same across opp_ids (it's the
                        # pipeline definition id, not opp-specific). Surfaced
                        # at alias level so the V2 job handler can look up
                        # full forms in RawVisitCache by (opp, pipeline_id)
                        # without digging into per_opp metadata.
                        "pipeline_id": pipeline_id,
                        "opportunity_ids": list(opp_ids),
                        "per_opp": per_opp_meta,
                        "row_count": len(merged_rows),
                    },
                }
        finally:
            pipeline_access.close()

        return results

    def get_cached_pipeline_data(
        self,
        definition_id: int,
        opportunity_id: int,
        aliases: list[str] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict[str, dict]:
        """Cache-only counterpart of `get_pipeline_data`, scoped to a manifest.

        Used by run completion: the snapshot must freeze the data the user was
        already looking at, so this never executes a pipeline — it only reads
        the processed cache the runner page populated. `aliases` limits the
        read to the pipelines a snapshot contract actually captures (None
        means all of the workflow's sources; the caller should skip calling
        this entirely for an empty manifest).

        When `period_start`/`period_end` are given (a saved run that carries a
        period), any source whose pipeline schema sets `period_scoped: true` is
        re-aggregated to that half-open `[period_start, period_end)` visit-date
        window instead of returning the all-time cache (ace#764) — so each
        weekly snapshot freezes its own slice rather than the whole-program
        total. This still reads from the existing cache (no download/recompute);
        sources without the flag are unaffected.

        Raises PipelineCacheMiss if any required pipeline has no usable cache
        for any of the workflow's opportunities — completion should fail fast
        with "reload the dashboard" rather than snapshot partial data.
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return {}

        sources = definition.pipeline_sources
        wanted = None if aliases is None else set(aliases)
        if not sources or (wanted is not None and not any(s.get("alias") in wanted for s in sources)):
            return {}

        opp_ids = definition.opportunity_ids or [opportunity_id]

        pipeline_access = PipelineDataAccess(
            request=self.request,
            access_token=self.access_token,
            opportunity_id=opportunity_id,
            organization_id=self.organization_id,
            program_id=self.program_id,
        )

        # Same JOIN-hash resolution as the execute path: cache keys are
        # config-hash-addressed, so the cached read must build configs the
        # exact same way or it will look up the wrong (missing) hash. Resolve
        # over ALL sources (a captured pipeline may JOIN an uncaptured one);
        # the `wanted` filter applies only to what gets read and returned.
        from connect_labs.labs.analysis.utils import resolve_join_hashes
        from connect_labs.workflow.views import _resolve_pipeline_sources_for_run

        ordered_sources, configs_by_alias = _resolve_pipeline_sources_for_run(pipeline_access, sources)
        if configs_by_alias:
            resolve_join_hashes(configs_by_alias)

        want_period = bool(period_start and period_end)

        results: dict[str, dict] = {}
        try:
            for source in ordered_sources:
                pipeline_id = source.get("pipeline_id")
                alias = source.get("alias")
                if not pipeline_id or not alias:
                    continue
                if wanted is not None and alias not in wanted:
                    continue

                # Period-scope this source only when the run carries a period
                # AND the pipeline's own schema opts in via `period_scoped`.
                period_scoped = False
                if want_period:
                    pdef = pipeline_access.get_definition(pipeline_id)
                    period_scoped = bool(pdef and (pdef.schema or {}).get("period_scoped"))

                merged_rows: list[dict] = []
                per_opp_meta: dict[str, dict] = {}
                for opp_id in opp_ids:
                    if period_scoped:
                        cached = pipeline_access.get_period_scoped_pipeline_result(
                            pipeline_id,
                            opp_id,
                            period_start,
                            period_end,
                            config=configs_by_alias.get(alias),
                        )
                    else:
                        cached = pipeline_access.get_cached_pipeline_result(
                            pipeline_id, opp_id, config=configs_by_alias.get(alias)
                        )
                    if cached is None:
                        raise PipelineCacheMiss(alias, opp_id, source.get("name", ""))
                    merged_rows.extend({**row, "opportunity_id": opp_id} for row in cached.get("rows", []))
                    per_opp_meta[str(opp_id)] = cached.get("metadata", {})

                results[alias] = {
                    "rows": merged_rows,
                    "metadata": {
                        "pipeline_id": pipeline_id,
                        "opportunity_ids": list(opp_ids),
                        "per_opp": per_opp_meta,
                        "row_count": len(merged_rows),
                    },
                }
        finally:
            pipeline_access.close()

        return results

    # -------------------------------------------------------------------------
    # Sharing Methods
    # -------------------------------------------------------------------------

    def share_workflow(self, definition_id: int, scope: str = "global") -> WorkflowDefinitionRecord | None:
        """Share a workflow (make it available to others).

        Sets both the data.is_shared metadata flag AND the record-level public flag
        to make the workflow queryable by others without scope parameters.
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        updated_data = {**definition.data, "is_shared": True, "shared_scope": scope}

        # Update the record with public=True so others can query it
        result = self.labs_api.update_record(
            record_id=definition_id,
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            data=updated_data,
            public=True,  # Set ACL flag to make record publicly queryable
        )

        if result:
            return WorkflowDefinitionRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
        return None

    def unshare_workflow(self, definition_id: int) -> WorkflowDefinitionRecord | None:
        """Unshare a workflow (make it private again).

        Sets both the data.is_shared metadata flag to False AND the record-level
        public flag to False to restrict visibility.
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        updated_data = {**definition.data, "is_shared": False, "shared_scope": None}

        # Update the record with public=False to restrict access
        result = self.labs_api.update_record(
            record_id=definition_id,
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            data=updated_data,
            public=False,  # Set ACL flag to make record private
        )

        if result:
            return WorkflowDefinitionRecord(
                {
                    "id": result.id,
                    "experiment": result.experiment,
                    "type": result.type,
                    "data": result.data,
                    "opportunity_id": result.opportunity_id,
                }
            )
        return None

    def list_shared_workflows(self, scope: str = "global") -> list[WorkflowDefinitionRecord]:
        """List workflows shared by others."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            model_class=WorkflowDefinitionRecord,
            public=True,
        )
        # Filter by scope and is_shared flag
        return [r for r in records if r.is_shared and r.shared_scope == scope]

    def copy_workflow(
        self, definition_id: int, new_name: str | None = None, source_is_public: bool = False
    ) -> WorkflowDefinitionRecord | None:
        """Create a copy of a workflow definition.

        Args:
            definition_id: ID of the workflow to copy
            new_name: Optional new name for the copy (defaults to "Copy of {original_name}")
            source_is_public: If True, fetch the source from public records (for copying shared workflows)

        Returns:
            The newly created workflow definition, or None if source not found
        """
        # Fetch the source definition
        if source_is_public:
            # Fetch from public records (for copying shared workflows)
            records = self.labs_api.get_records(
                experiment=self.EXPERIMENT,
                type="workflow_definition",
                model_class=WorkflowDefinitionRecord,
                public=True,
            )
            source = next((r for r in records if r.id == definition_id), None)
        else:
            source = self.get_definition(definition_id)

        if not source:
            return None

        # Prepare data for the copy (reset sharing flags)
        copied_data = {
            "name": new_name or f"Copy of {source.name}",
            "description": source.description,
            "version": 1,
            "statuses": source.data.get("statuses", []),
            "config": source.data.get("config", {}),
            "pipeline_sources": source.data.get("pipeline_sources", []),
            "opportunity_ids": source.data.get("opportunity_ids", []),
            "is_shared": False,
            "shared_scope": "global",
        }

        # Create the new definition (private by default)
        result = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="workflow_definition",
            data=copied_data,
            public=False,
        )

        new_definition = WorkflowDefinitionRecord(
            {
                "id": result.id,
                "experiment": result.experiment,
                "type": result.type,
                "data": result.data,
                "opportunity_id": result.opportunity_id,
            }
        )

        # Copy render code if exists
        if source_is_public:
            # Fetch render code from public records
            render_records = self.labs_api.get_records(
                experiment=self.EXPERIMENT,
                type="workflow_render_code",
                model_class=WorkflowRenderCodeRecord,
                public=True,
            )
            source_render = next((r for r in render_records if r.data.get("definition_id") == definition_id), None)
        else:
            source_render = self.get_render_code(definition_id)

        if source_render:
            self.save_render_code(new_definition.id, source_render.component_code)

        return new_definition

    # -------------------------------------------------------------------------
    # Chat History Methods
    # -------------------------------------------------------------------------

    def get_chat_history(self, definition_id: int) -> WorkflowChatHistoryRecord | None:
        """Get chat history for a workflow definition."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="workflow_chat_history",
            model_class=WorkflowChatHistoryRecord,
        )
        definition_id_int = int(definition_id)
        for record in records:
            record_def_id = record.data.get("definition_id")
            if record_def_id is not None and int(record_def_id) == definition_id_int:
                return record
        return None

    def get_chat_messages(self, definition_id: int) -> list[dict]:
        """Get chat messages for a workflow definition."""
        record = self.get_chat_history(definition_id)
        return record.messages if record else []

    def save_chat_history(self, definition_id: int, messages: list[dict]) -> WorkflowChatHistoryRecord:
        """Save chat history for a workflow definition."""
        now = datetime.now(timezone.utc).isoformat()
        definition_id_int = int(definition_id)
        existing = self.get_chat_history(definition_id_int)

        data = {
            "definition_id": definition_id_int,
            "messages": messages,
            "updated_at": now,
        }

        if existing:
            data["created_at"] = existing.data.get("created_at", now)
            result = self.labs_api.update_record(
                record_id=existing.id,
                experiment=self.EXPERIMENT,
                type="workflow_chat_history",
                data=data,
            )
        else:
            data["created_at"] = now
            result = self.labs_api.create_record(
                experiment=self.EXPERIMENT,
                type="workflow_chat_history",
                data=data,
            )

        return WorkflowChatHistoryRecord(
            {
                "id": result.id,
                "experiment": result.experiment,
                "type": result.type,
                "data": result.data,
                "opportunity_id": result.opportunity_id,
            }
        )

    def add_chat_message(self, definition_id: int, role: str, content: str) -> bool:
        """Add a single message to the chat history."""
        messages = self.get_chat_messages(definition_id)
        messages.append({"role": role, "content": content})
        self.save_chat_history(definition_id, messages)
        return True

    def clear_chat_history(self, definition_id: int) -> bool:
        """Clear chat history for a workflow definition."""
        existing = self.get_chat_history(definition_id)
        if existing:
            self.save_chat_history(definition_id, [])
            return True
        return False

    # -------------------------------------------------------------------------
    # Worker Data Methods
    # -------------------------------------------------------------------------

    def get_workers(self, opportunity_id: int) -> list[dict]:
        """
        Get workers for an opportunity from Connect API.

        Returns:
            List of worker dicts with username, name, visit_count, last_active.
            Note: visit_count is not in the v2 serializer; it will default to 0
            unless the serializer is extended on the production side.
        """
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        endpoint = f"/export/opportunity/{opportunity_id}/user_data/"
        try:
            with get_export_client(
                opportunity_id=opportunity_id,
                access_token=self.access_token,
                timeout=60.0,
            ) as client:
                records = client.fetch_all(endpoint)
        except ExportAPIError as e:
            logger.error(f"Failed to fetch workers for opp {opportunity_id}: {e}")
            return []

        workers = []
        for row in records:
            username = row.get("username")
            if not username:
                continue
            worker = {
                "username": str(username),
                "name": str(row.get("name") or username),
                "visit_count": int(row.get("total_visits") or 0),
                "last_active": str(row["last_active"]) if row.get("last_active") else None,
            }
            # Pass through any other fields the v2 serializer happens to include
            for key in ("phone_number", "approved_visits", "flagged_visits", "rejected_visits", "email"):
                if row.get(key) is not None:
                    worker[key] = row[key]
            workers.append(worker)

        return workers


# =============================================================================
# Pipeline Data Access
# =============================================================================


class PipelineDataAccess(BaseDataAccess):
    """
    Data access layer for pipelines.

    Handles pipeline definitions, render code, chat history, and execution.
    """

    EXPERIMENT = "pipeline"

    # -------------------------------------------------------------------------
    # Pipeline Definition Methods
    # -------------------------------------------------------------------------

    def list_definitions(self, include_shared: bool = False) -> list[PipelineDefinitionRecord]:
        """List pipeline definitions."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            model_class=PipelineDefinitionRecord,
        )

        if include_shared:
            shared_records = self.labs_api.get_records(
                experiment=self.EXPERIMENT,
                type="pipeline_definition",
                model_class=PipelineDefinitionRecord,
                public=True,
            )
            seen_ids = {r.id for r in records}
            for r in shared_records:
                if r.id not in seen_ids:
                    records.append(r)

        return records

    def get_definition(self, definition_id: int) -> PipelineDefinitionRecord | None:
        """Get a pipeline definition by ID."""
        return self.labs_api.get_record_by_id(
            definition_id,
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            model_class=PipelineDefinitionRecord,
        )

    def create_definition(
        self,
        name: str,
        description: str,
        schema: dict,
        render_code: str = "",
    ) -> PipelineDefinitionRecord:
        """Create a new pipeline definition."""
        definition_data = {
            "name": name,
            "description": description,
            "version": 1,
            "schema": schema,
            "is_shared": False,
            "shared_scope": "global",
        }

        result = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=definition_data,
        )

        definition_id = result.id

        if render_code:
            render_result = self.labs_api.create_record(
                experiment=self.EXPERIMENT,
                type="pipeline_render_code",
                data={
                    "definition_id": definition_id,
                    "component_code": render_code,
                    "version": 1,
                },
            )
            definition_data["render_code_id"] = render_result.id
            self.labs_api.update_record(
                definition_id,
                experiment=self.EXPERIMENT,
                type="pipeline_definition",
                data=definition_data,
            )

        return PipelineDefinitionRecord(
            {
                "id": definition_id,
                "experiment": self.EXPERIMENT,
                "type": "pipeline_definition",
                "data": definition_data,
                "opportunity_id": self.opportunity_id,
            }
        )

    def update_definition(
        self,
        definition_id: int,
        name: str | None = None,
        description: str | None = None,
        schema: dict | None = None,
    ) -> PipelineDefinitionRecord | None:
        """Update a pipeline definition."""
        existing = self.get_definition(definition_id)
        if not existing:
            return None

        data = existing.data.copy()

        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if schema is not None:
            data["schema"] = schema
            data["version"] = data.get("version", 1) + 1

        self.labs_api.update_record(
            definition_id,
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=data,
        )

        return PipelineDefinitionRecord(
            {
                "id": definition_id,
                "experiment": self.EXPERIMENT,
                "type": "pipeline_definition",
                "data": data,
                "opportunity_id": self.opportunity_id,
            }
        )

    def delete_definition(self, definition_id: int) -> None:
        """Delete a pipeline definition."""
        self.labs_api.delete_record(definition_id)

    # -------------------------------------------------------------------------
    # Pipeline Render Code Methods
    # -------------------------------------------------------------------------

    def get_render_code(self, definition_id: int) -> PipelineRenderCodeRecord | None:
        """Get render code for a pipeline definition."""
        definition = self.get_definition(definition_id)
        if not definition or not definition.render_code_id:
            return None

        return self.labs_api.get_record_by_id(
            definition.render_code_id,
            experiment=self.EXPERIMENT,
            type="pipeline_render_code",
            model_class=PipelineRenderCodeRecord,
        )

    def save_render_code(self, definition_id: int, component_code: str) -> PipelineRenderCodeRecord:
        """Save render code for a pipeline definition."""
        definition = self.get_definition(definition_id)
        if not definition:
            raise ValueError(f"Pipeline definition {definition_id} not found")

        if definition.render_code_id:
            existing = self.labs_api.get_record_by_id(
                definition.render_code_id,
                experiment=self.EXPERIMENT,
                type="pipeline_render_code",
            )
            if existing:
                data = existing.data.copy()
                data["component_code"] = component_code
                data["version"] = data.get("version", 1) + 1

                self.labs_api.update_record(
                    definition.render_code_id,
                    experiment=self.EXPERIMENT,
                    type="pipeline_render_code",
                    data=data,
                )

                return PipelineRenderCodeRecord(
                    {
                        "id": definition.render_code_id,
                        "experiment": self.EXPERIMENT,
                        "type": "pipeline_render_code",
                        "data": data,
                        "opportunity_id": self.opportunity_id,
                    }
                )

        render_data = {
            "definition_id": definition_id,
            "component_code": component_code,
            "version": 1,
        }

        result = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="pipeline_render_code",
            data=render_data,
        )

        # Update definition with render_code_id
        def_data = definition.data.copy()
        def_data["render_code_id"] = result.id
        self.labs_api.update_record(
            definition_id,
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=def_data,
        )

        return PipelineRenderCodeRecord(
            {
                "id": result.id,
                "experiment": self.EXPERIMENT,
                "type": "pipeline_render_code",
                "data": render_data,
                "opportunity_id": self.opportunity_id,
            }
        )

    # -------------------------------------------------------------------------
    # Sharing Methods
    # -------------------------------------------------------------------------

    def share_pipeline(self, definition_id: int, scope: str = "global") -> PipelineDefinitionRecord | None:
        """Share a pipeline (make it available to others).

        Sets both the data.is_shared metadata flag AND the record-level public flag
        to make the pipeline queryable by others without scope parameters.
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        data = definition.data.copy()
        data["is_shared"] = True
        data["shared_scope"] = scope

        # Update the record with public=True so others can query it
        result = self.labs_api.update_record(
            definition_id,
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=data,
            public=True,  # Set ACL flag to make record publicly queryable
        )

        if result:
            return PipelineDefinitionRecord(
                {
                    "id": result.id,
                    "experiment": self.EXPERIMENT,
                    "type": "pipeline_definition",
                    "data": result.data,
                    "opportunity_id": self.opportunity_id,
                }
            )
        return None

    def unshare_pipeline(self, definition_id: int) -> PipelineDefinitionRecord | None:
        """Unshare a pipeline (make it private again).

        Sets both the data.is_shared metadata flag to False AND the record-level
        public flag to False to restrict visibility.
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return None

        data = definition.data.copy()
        data["is_shared"] = False
        data["shared_scope"] = None

        # Update the record with public=False to restrict access
        result = self.labs_api.update_record(
            definition_id,
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=data,
            public=False,  # Set ACL flag to make record private
        )

        if result:
            return PipelineDefinitionRecord(
                {
                    "id": result.id,
                    "experiment": self.EXPERIMENT,
                    "type": "pipeline_definition",
                    "data": result.data,
                    "opportunity_id": self.opportunity_id,
                }
            )
        return None

    def list_shared_pipelines(self, scope: str = "global") -> list[PipelineDefinitionRecord]:
        """List pipelines shared by others."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            model_class=PipelineDefinitionRecord,
            public=True,
        )
        return [r for r in records if r.is_shared and r.shared_scope == scope]

    def copy_pipeline(
        self, definition_id: int, new_name: str | None = None, source_is_public: bool = False
    ) -> PipelineDefinitionRecord | None:
        """Create a copy of a pipeline definition.

        Args:
            definition_id: ID of the pipeline to copy
            new_name: Optional new name for the copy (defaults to "Copy of {original_name}")
            source_is_public: If True, fetch the source from public records (for copying shared pipelines)

        Returns:
            The newly created pipeline definition, or None if source not found
        """
        # Fetch the source definition
        if source_is_public:
            # Fetch from public records (for copying shared pipelines)
            records = self.labs_api.get_records(
                experiment=self.EXPERIMENT,
                type="pipeline_definition",
                model_class=PipelineDefinitionRecord,
                public=True,
            )
            source = next((r for r in records if r.id == definition_id), None)
        else:
            source = self.get_definition(definition_id)

        if not source:
            return None

        # Prepare data for the copy (reset sharing flags)
        copied_data = {
            "name": new_name or f"Copy of {source.name}",
            "description": source.description,
            "version": 1,
            "schema": source.schema,
            "is_shared": False,
            "shared_scope": "global",
        }

        # Create the new definition (private by default)
        result = self.labs_api.create_record(
            experiment=self.EXPERIMENT,
            type="pipeline_definition",
            data=copied_data,
            public=False,
        )

        return PipelineDefinitionRecord(
            {
                "id": result.id,
                "experiment": self.EXPERIMENT,
                "type": "pipeline_definition",
                "data": result.data,
                "opportunity_id": self.opportunity_id,
            }
        )

    # -------------------------------------------------------------------------
    # Chat History Methods
    # -------------------------------------------------------------------------

    def get_chat_history(self, definition_id: int) -> list[dict]:
        """Get chat history for a pipeline definition."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="pipeline_chat_history",
            model_class=PipelineChatHistoryRecord,
        )
        for record in records:
            if record.data.get("definition_id") == definition_id:
                return record.data.get("messages", [])
        return []

    def add_chat_message(self, definition_id: int, role: str, content: str) -> None:
        """Add a message to chat history."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="pipeline_chat_history",
            model_class=PipelineChatHistoryRecord,
        )

        existing_record = None
        for record in records:
            if record.data.get("definition_id") == definition_id:
                existing_record = record
                break

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if existing_record:
            data = existing_record.data.copy()
            messages = data.get("messages", [])
            messages.append(message)
            data["messages"] = messages
            data["updated_at"] = datetime.now(timezone.utc).isoformat()

            self.labs_api.update_record(
                existing_record.id,
                experiment=self.EXPERIMENT,
                type="pipeline_chat_history",
                data=data,
            )
        else:
            self.labs_api.create_record(
                experiment=self.EXPERIMENT,
                type="pipeline_chat_history",
                data={
                    "definition_id": definition_id,
                    "messages": [message],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    def clear_chat_history(self, definition_id: int) -> None:
        """Clear chat history for a pipeline definition."""
        records = self.labs_api.get_records(
            experiment=self.EXPERIMENT,
            type="pipeline_chat_history",
            model_class=PipelineChatHistoryRecord,
        )

        for record in records:
            if record.data.get("definition_id") == definition_id:
                data = record.data.copy()
                data["messages"] = []
                data["updated_at"] = datetime.now(timezone.utc).isoformat()

                self.labs_api.update_record(
                    record.id,
                    experiment=self.EXPERIMENT,
                    type="pipeline_chat_history",
                    data=data,
                )
                break

    # -------------------------------------------------------------------------
    # Pipeline Execution
    # -------------------------------------------------------------------------

    def execute_pipeline(self, definition_id: int, opportunity_id: int, config=None) -> dict:
        """
        Execute a pipeline and return results.

        Contract: this method never raises. On any failure (pipeline not
        found, missing schema, analysis error) it returns the same shape
        as the success case with `metadata["error"]` populated and an empty
        `rows` list. Callers iterating over multiple opportunities should
        inspect `result["metadata"].get("error")` to detect per-opp failures
        rather than wrapping the call in try/except.

        Args:
            definition_id: Pipeline definition labs-record id.
            opportunity_id: Opportunity to scope this pipeline run to.
            config: Optional pre-built `AnalysisPipelineConfig`. When the
                caller is orchestrating multiple sibling pipelines and has
                already resolved cross-pipeline JOIN config_hashes via
                `resolve_join_hashes`, pass that config here. Without this,
                a fresh `_schema_to_config` call rebuilds the config and
                drops any resolved JOIN hashes — leading to "resolved_config_hash
                not set" SQL build errors at execute time. Falls back to
                fresh parsing when omitted (backwards-compatible).
        Returns:
            Dict with keys:
                "rows": list of row dicts (empty on failure)
                "metadata": dict with at least one of:
                    - {"row_count", "from_cache", "pipeline_name", "terminal_stage"} on success
                    - {"error": <str>} on failure
        """
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        definition = self.get_definition(definition_id)
        if not definition:
            return {"rows": [], "metadata": {"error": "Pipeline not found"}}

        schema = definition.schema
        if not schema:
            return {"rows": [], "metadata": {"error": "Pipeline has no schema"}}

        try:
            # Use the pre-resolved config if the caller passed one (multi-
            # pipeline orchestration path); otherwise build a fresh one.
            if config is None:
                config = self._schema_to_config(schema, definition_id)

            # Execute pipeline using the AnalysisPipeline.
            # Works with either a Django request (web UI path) or a bare access_token
            # (MCP server path, which has no browser session).
            if self.request is not None:
                pipeline = AnalysisPipeline(self.request)
            else:
                pipeline = AnalysisPipeline(access_token=self.access_token)
            result = pipeline.stream_analysis_ignore_events(config, opportunity_id)

            rows = self._serialize_pipeline_rows(result)
            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": getattr(result, "from_cache", False),
                    "pipeline_id": definition_id,
                    "pipeline_name": definition.name,
                    "terminal_stage": schema.get("terminal_stage", "visit_level"),
                },
            }

        except Exception as e:
            logger.exception("Pipeline execution failed")
            # Tag CCHQ auth errors specifically so the FE can show
            # "Authorize CommCare HQ" instead of a generic error message.
            from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError

            error_meta = {"error": str(e)}
            if isinstance(e, CCHQAuthError):
                error_meta["auth_error"] = "commcare_hq"
                error_meta["auth_error_domain"] = e.domain
                error_meta["auth_authorize_url"] = "/labs/commcare/initiate/"
            return {"rows": [], "metadata": error_meta}

    def get_cached_pipeline_result(self, definition_id: int, opportunity_id: int, config=None) -> dict | None:
        """Read a pipeline's processed cache without executing anything.

        The cache-only counterpart of `execute_pipeline`, used by run
        completion: snapshots must capture the data the user was already
        looking at, never trigger a fresh download/recompute inside a web
        request (a 100k-visit opp takes many minutes and has OOM-killed
        workers). Returns the same `{"rows", "metadata"}` shape as
        `execute_pipeline` on a hit, or None when nothing usable is cached
        (caller decides how to surface the miss).
        """
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        definition = self.get_definition(definition_id)
        if not definition:
            return None
        schema = definition.schema
        if not schema:
            return None

        try:
            if config is None:
                config = self._schema_to_config(schema, definition_id)
            if self.request is not None:
                pipeline = AnalysisPipeline(self.request)
            else:
                pipeline = AnalysisPipeline(access_token=self.access_token)
            result = pipeline.get_cached_result_only(config, opportunity_id)
            if result is None:
                return None
            rows = self._serialize_pipeline_rows(result)
            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": True,
                    "pipeline_id": definition_id,
                    "pipeline_name": definition.name,
                    "terminal_stage": schema.get("terminal_stage", "visit_level"),
                },
            }
        except Exception:
            logger.exception("Cached pipeline read failed for pipeline %s opp %s", definition_id, opportunity_id)
            return None

    def get_period_scoped_pipeline_result(
        self,
        definition_id: int,
        opportunity_id: int,
        date_from: str | None,
        date_to: str | None,
        config=None,
    ) -> dict | None:
        """Read a pipeline result re-aggregated to a `[date_from, date_to)`
        visit-date window from the EXISTING cache (ace#764).

        The period-scoped counterpart of `get_cached_pipeline_result`. Used by
        saved-run snapshots whose template marks a pipeline `period_scoped` so
        each run freezes its own period instead of the whole-program total. Like
        the cache read it never downloads or recomputes from source — it re-runs
        the FLW GROUP BY over the already-cached visits with a date predicate
        (cheap, bounded, writes nothing). The window is excluded from the config
        hash, so the same cache slot serves every period. Returns `{"rows",
        "metadata"}` on a hit, or None on a cache miss (caller surfaces it the
        same way as a `get_cached_pipeline_result` miss).
        """
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        definition = self.get_definition(definition_id)
        if not definition:
            return None
        schema = definition.schema
        if not schema:
            return None

        try:
            if config is None:
                config = self._schema_to_config(schema, definition_id)
            # Window is query-time only (excluded from config hash), so setting
            # it here does not move the cache key the join-resolution already
            # used — the raw-visit slot is found identically.
            config.date_from = date_from or ""
            config.date_to = date_to or ""
            if self.request is not None:
                pipeline = AnalysisPipeline(self.request)
            else:
                pipeline = AnalysisPipeline(access_token=self.access_token)
            result = pipeline.get_period_scoped_result_only(config, opportunity_id)
            if result is None:
                return None
            rows = self._serialize_pipeline_rows(result)
            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": True,
                    "period_scoped": True,
                    "date_from": config.date_from,
                    "date_to": config.date_to,
                    "pipeline_id": definition_id,
                    "pipeline_name": definition.name,
                    "terminal_stage": schema.get("terminal_stage", "visit_level"),
                },
            }
        except Exception:
            logger.exception(
                "Period-scoped pipeline read failed for pipeline %s opp %s", definition_id, opportunity_id
            )
            return None

    def execute_pipeline_from_schema(self, schema: dict, opportunity_id: int, alias: str = "pipeline") -> dict:
        """Execute a pipeline directly from a schema dict (no DB lookup).

        Used by server-side job handlers that import their own PIPELINE_SCHEMAS
        and need to fetch data without going through the browser round-trip.

        Args:
            schema: raw schema dict (same format as PIPELINE_SCHEMAS[n]["schema"])
            opportunity_id: opportunity to fetch data for
            alias: used as the cache experiment name (use the pipeline alias for stable caching)
        """
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        if not self.request:
            return {"rows": [], "metadata": {"error": "Request object required for pipeline execution"}}

        try:
            # Use a deterministic fake definition_id for cache experiment naming
            fake_id = abs(hash(alias)) % 900000 + 100000
            config = self._schema_to_config(schema, fake_id)
            pipeline = AnalysisPipeline(self.request)
            result = pipeline.stream_analysis_ignore_events(config, opportunity_id)
            rows = self._serialize_pipeline_rows(result)
            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": getattr(result, "from_cache", False),
                    "alias": alias,
                },
            }
        except Exception as e:
            logger.error(f"Pipeline schema execution failed (alias={alias}): {e}", exc_info=True)
            return {"rows": [], "metadata": {"error": str(e)}}

    def _serialize_pipeline_rows(self, result) -> list:
        """Convert pipeline result rows to serializable dicts."""

        def format_date(d):
            if d and hasattr(d, "isoformat"):
                return d.isoformat()
            return str(d) if d else None

        rows = []
        if hasattr(result, "rows"):
            for row in result.rows:
                row_dict = {
                    "id": getattr(row, "id", None),
                    "username": getattr(row, "username", None),
                    "visit_date": format_date(getattr(row, "visit_date", None)),
                    "total_visits": getattr(row, "total_visits", 0),
                    "approved_visits": getattr(row, "approved_visits", 0),
                    "pending_visits": getattr(row, "pending_visits", 0),
                    "rejected_visits": getattr(row, "rejected_visits", 0),
                    "flagged_visits": getattr(row, "flagged_visits", 0),
                    "first_visit_date": format_date(getattr(row, "first_visit_date", None)),
                    "last_visit_date": format_date(getattr(row, "last_visit_date", None)),
                    "entity_id": getattr(row, "entity_id", None),
                    "entity_name": getattr(row, "entity_name", None),
                    "status": getattr(row, "status", None),
                    "flagged": getattr(row, "flagged", None),
                }
                custom = getattr(row, "custom_fields", None) or getattr(row, "computed", None)
                if custom:
                    row_dict.update(custom)
                rows.append(row_dict)
        return rows

    def _schema_to_config(self, schema: dict, definition_id: int):
        """Convert JSON schema to AnalysisPipelineConfig."""
        from connect_labs.labs.analysis.config import (
            AnalysisPipelineConfig,
            CacheStage,
            DataSourceConfig,
            FieldComputation,
            HistogramComputation,
            JoinConfig,
        )

        # Transform registry
        transform_registry = {
            "kg_to_g": lambda x: (
                int(float(x) * 1000) if x and str(x).replace(".", "").replace("-", "").isdigit() else None
            ),
            "float": lambda x: float(x) if x else None,
            "int": lambda x: int(float(x)) if x else None,
            "date": None,
            "string": lambda x: str(x) if x else None,
            # GPS-string parsing for "lat lon altitude accuracy" packed format.
            # The MBW data model packs all four into one form field; v3's window
            # fields need lat/lon as separate float columns.
            "gps_lat": lambda x: float(x.split()[0]) if x and isinstance(x, str) and len(x.split()) >= 2 else None,
            "gps_lon": lambda x: float(x.split()[1]) if x and isinstance(x, str) and len(x.split()) >= 2 else None,
        }

        def get_transform(name):
            if not name:
                return None
            return transform_registry.get(name)

        # Extractor registry — multi-path / multi-input field computations
        # that the path/transform machinery can't express. Schemas reference
        # by name (string); only the cchq cache loader currently consumes
        # extractors (SQL builders ignore them on aggregated queries).
        from datetime import date

        def _v1_mbw_age(visit_dict: dict) -> str:
            """v1-fidelity mother age: DOB-derived if mother_dob is parseable,
            else fall back to recorded age fields. Mirrors
            `extract_mother_metadata_from_forms` line 615-629 in v1.

            Receives the visit_dict wrapper (form_json + base fields), same
            shape produced by both cchq_cache_loader and
            SQLBackend._process_visit_level. The actual cchq form payload
            sits under `form_json`.
            """
            form_json = visit_dict.get("form_json", {}) if isinstance(visit_dict, dict) else {}
            form = form_json.get("form", {}) if isinstance(form_json, dict) else {}
            md = form.get("mother_details", {}) if isinstance(form, dict) else {}
            if not isinstance(md, dict):
                return ""
            mother_dob = md.get("mother_dob") or ""
            if mother_dob:
                try:
                    dob = date.fromisoformat(str(mother_dob)[:10])
                    today = date.today()
                    age_years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    return str(age_years)
                except (ValueError, TypeError):
                    pass
            return md.get("age_in_years_rounded") or md.get("mothers_age") or ""

        # MBW visit-type create flags + completion flags. v1 uses these to
        # determine which visits a mother is scheduled for and which were
        # completed. JS-side follow-up classification consumes the schedules
        # list shape produced by `_mbw_visit_schedules`.
        _MBW_VISIT_CREATE_FLAGS = {
            "ANC Visit": "create_antenatal_visit",
            "Postnatal Delivery Visit": "create_postnatal_visit",
            "1 Week Visit": "create_one_two_visit",
            "1 Month Visit": "create_one_month_visit",
            "3 Month Visit": "create_three_month_visit",
            "6 Month Visit": "create_six_month_visit",
        }

        def _mbw_visit_schedules(visit_dict: dict) -> list:
            """v1-fidelity expected-visits extraction. Walks var_visit_1..6
            on the registration form, filters out blocks where the create
            flag isn't set, and returns a list of schedule dicts the JS
            follow-up adapter can match against actual visits.

            Receives the visit_dict wrapper (form_json + base fields). The
            actual cchq form payload sits under `form_json`.
            """
            form_json = visit_dict.get("form_json", {}) if isinstance(visit_dict, dict) else {}
            form = form_json.get("form", {}) if isinstance(form_json, dict) else {}
            if not isinstance(form, dict):
                return []
            schedules = []
            for i in range(1, 7):
                var_visit = form.get(f"var_visit_{i}")
                if not isinstance(var_visit, dict):
                    continue
                visit_type = var_visit.get("visit_type", "")
                create_flag_name = _MBW_VISIT_CREATE_FLAGS.get(visit_type)
                if create_flag_name and str(var_visit.get(create_flag_name, "")) != "1":
                    continue
                schedules.append(
                    {
                        "visit_type": visit_type,
                        "visit_date_scheduled": var_visit.get("visit_date_scheduled", ""),
                        "visit_expiry_date": var_visit.get("visit_expiry_date", ""),
                        "mother_case_id": var_visit.get("mother_case_id", ""),
                    }
                )
            return schedules

        extractor_registry = {
            "v1_mbw_age": _v1_mbw_age,
            "mbw_visit_schedules": _mbw_visit_schedules,
        }

        def get_extractor(name):
            if not name:
                return None
            return extractor_registry.get(name)

        fields = []
        for field_def in schema.get("fields", []):
            fields.append(
                FieldComputation(
                    name=field_def["name"],
                    path=field_def.get("path", ""),
                    paths=field_def.get("paths"),
                    aggregation=field_def.get("aggregation", "first"),
                    transform=get_transform(field_def.get("transform")),
                    description=field_def.get("description", ""),
                    default=field_def.get("default"),
                    filter_path=field_def.get("filter_path", ""),
                    filter_paths=field_def.get("filter_paths"),
                    filter_value=field_def.get("filter_value", ""),
                    filter_op=field_def.get("filter_op", "eq"),
                    pre_aggregate_by=field_def.get("pre_aggregate_by", ""),
                    pre_aggregation=field_def.get("pre_aggregation", "first"),
                    pre_aggregate_attribute_to=field_def.get("pre_aggregate_attribute_to", ""),
                    extractor=get_extractor(field_def.get("extractor")),
                )
            )

        histograms = []
        for hist_def in schema.get("histograms", []):
            histograms.append(
                HistogramComputation(
                    name=hist_def["name"],
                    path=hist_def.get("path", ""),
                    paths=hist_def.get("paths"),
                    lower_bound=hist_def["lower_bound"],
                    upper_bound=hist_def["upper_bound"],
                    num_bins=hist_def["num_bins"],
                    bin_name_prefix=hist_def.get("bin_name_prefix", ""),
                    transform=get_transform(hist_def.get("transform")),
                    description=hist_def.get("description", ""),
                    include_out_of_range=hist_def.get("include_out_of_range", True),
                )
            )

        terminal_stage = CacheStage.VISIT_LEVEL
        if schema.get("terminal_stage") == "aggregated":
            terminal_stage = CacheStage.AGGREGATED
        elif schema.get("terminal_stage") == "entity":
            terminal_stage = CacheStage.ENTITY

        # Parse data source config
        data_source_dict = schema.get("data_source") or {}
        data_source = DataSourceConfig(
            type=data_source_dict.get("type", "connect_csv"),
            form_name=data_source_dict.get("form_name", ""),
            app_id=data_source_dict.get("app_id", ""),
            app_id_source=data_source_dict.get("app_id_source", ""),
            gs_app_id=data_source_dict.get("gs_app_id", ""),
            experiment_id=data_source_dict.get("experiment_id", ""),
            api_key=data_source_dict.get("api_key", ""),
            endpoint=data_source_dict.get("endpoint", ""),
            case_type=data_source_dict.get("case_type", ""),
        )

        # Window fields (e.g., distance_from_prev_case_visit_m via lag_haversine).
        # Each entry references already-extracted fields by name — config-level
        # validation in AnalysisPipelineConfig.__post_init__ catches dangling refs.
        from connect_labs.labs.analysis.config import WindowFieldComputation

        window_fields = []
        for wf_def in schema.get("window_fields", []):
            window_fields.append(
                WindowFieldComputation(
                    name=wf_def["name"],
                    operation=wf_def.get("operation", "lag_haversine"),
                    partition_by=wf_def.get("partition_by", ""),
                    order_by=wf_def.get("order_by", ""),
                    lat_field=wf_def.get("lat_field", ""),
                    lon_field=wf_def.get("lon_field", ""),
                    description=wf_def.get("description", ""),
                )
            )

        # Cross-pipeline joins. Each entry pulls fields from a sibling
        # pipeline's computed cache. `resolved_config_hash` stays empty here —
        # the orchestrator must populate it via `resolve_join_hashes` (or an
        # equivalent walk) once sibling configs are constructed, because
        # resolution requires knowing the sibling's full config to hash it.
        joins = []
        for j_def in schema.get("joins", []):
            joins.append(
                JoinConfig(
                    from_alias=j_def["from_alias"],
                    local_key=j_def["local_key"],
                    remote_key_field=j_def["remote_key_field"],
                    fields=list(j_def.get("fields", [])),
                )
            )

        return AnalysisPipelineConfig(
            grouping_key=schema.get("grouping_key", "username"),
            fields=fields,
            histograms=histograms,
            filters=schema.get("filters", {}),
            date_field=schema.get("date_field", "visit_date"),
            experiment=f"pipeline_{definition_id}",
            terminal_stage=terminal_stage,
            linking_field=schema.get("linking_field", "entity_id"),
            data_source=data_source,
            window_fields=window_fields,
            extracted_filters=schema.get("extracted_filters", []),
            joins=joins,
            # Discriminate the raw-visit cache by pipeline id so multiple
            # pipelines for the same opp don't clobber each other (#116).
            pipeline_id=definition_id,
        )


# =============================================================================
# Cross-workflow reader for program_admin_report (spec §5.5)
# =============================================================================


def get_saved_runs_for_program_report(
    wda: "WorkflowDataAccess | None" = None,
    *,
    watched_sources: list[dict],
    window_start,
    window_end,
    access_token: str | None = None,
    request=None,
) -> list[dict]:
    """For each (opp_id, workflow_definition_id) pair in ``watched_sources``,
    return every completed run whose ``completed_at`` falls within
    ``[window_start, window_end]``.

    Returns a list of ``{opportunity_id, workflow_definition_id, runs:
    list[WorkflowRunRecord]}`` entries. A source with no completed runs in
    the window still appears with ``runs=[]`` so the caller can render a
    "no run" cell.

    The window is inclusive on both ends. ``completed_at`` is compared as
    parsed datetime; bad/missing timestamps are skipped.

    Window bounds are coerced to UTC if naive so comparison against
    Connect's TZ-aware ``completed_at`` doesn't raise TypeError.
    """
    from datetime import datetime, timezone

    def _to_utc(dt_):
        if dt_ is None:
            return None
        if dt_.tzinfo is None:
            return dt_.replace(tzinfo=timezone.utc)
        return dt_.astimezone(timezone.utc)

    ws = _to_utc(window_start)
    we = _to_utc(window_end)

    def _within(completed_at_str):
        if not completed_at_str:
            return False
        try:
            ts = datetime.fromisoformat(completed_at_str.replace("Z", "+00:00"))
        except ValueError:
            return False
        ts = _to_utc(ts)
        return ws <= ts <= we

    # Per-source WDA so list_runs is scoped to the watched opp. A single
    # opp-less WDA would hit the LabsRecord API with no scope param and
    # return only public records, which workflow_runs are not — silently
    # producing an empty rollup. Caller can pass ``wda`` (with no opp scope)
    # for the legacy single-source path; if so we still re-scope per source
    # by minting a fresh WDA per loop iteration.
    if access_token is None and wda is not None:
        access_token = getattr(wda, "access_token", None)

    out = []
    for source in watched_sources:
        opp_id = source["opportunity_id"]
        def_id = source["workflow_definition_id"]
        scoped_wda = WorkflowDataAccess(
            request=request,
            access_token=access_token,
            opportunity_id=opp_id,
        )
        try:
            all_runs = scoped_wda.list_runs(definition_id=def_id)
        finally:
            scoped_wda.close()
        matched = [
            r for r in all_runs if r.opportunity_id == opp_id and r.status == "completed" and _within(r.completed_at)
        ]
        out.append(
            {
                "opportunity_id": opp_id,
                "workflow_definition_id": def_id,
                "runs": matched,
            }
        )
    return out
