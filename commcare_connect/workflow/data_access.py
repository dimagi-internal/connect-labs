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
import os
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
from django.conf import settings
from django.http import HttpRequest

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.models import LocalLabsRecord

logger = logging.getLogger(__name__)


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
    def template_type(self) -> str:
        return self.data.get("config", {}).get("templateType", "")

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
        top = self.data.get("status")
        if top:
            return top
        return self.data.get("state", {}).get("status", "in_progress")

    @property
    def state(self):
        return self.data.get("state", {})

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
        """Close HTTP client."""
        if self.http_client:
            self.http_client.close()

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
            **kwargs: Additional data fields (statuses, config, pipeline_sources)

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
            "is_shared": False,
            "shared_scope": "global",
        }

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

        # Always delete render code (belongs to the definition)
        render_code = self.get_render_code(definition_id)
        if render_code:
            ids_to_delete.append(render_code.id)
            deleted_counts["render_code"] = 1

        # Always delete chat history (belongs to the definition)
        chat_history = self.get_chat_history(definition_id)
        if chat_history:
            ids_to_delete.append(chat_history.id)
            deleted_counts["chat_history"] = 1

        if delete_linked:
            # Collect all run and audit session IDs for batch deletion
            runs = self.list_runs(definition_id)
            for run in runs:
                try:
                    audit_sessions = self.labs_api.get_records(
                        experiment="audit",
                        type="AuditSession",
                        labs_record_id=run.id,
                    )
                    for session in audit_sessions:
                        ids_to_delete.append(session.id)
                        deleted_counts["audit_sessions"] += 1
                except Exception as e:
                    logger.warning(f"Failed to query audit sessions for run {run.id}: {e}")

                ids_to_delete.append(run.id)
                deleted_counts["runs"] += 1

        # Add the definition itself
        ids_to_delete.append(definition_id)
        deleted_counts["definition"] = 1

        # Single batch delete for all collected IDs
        self.labs_api.delete_records(ids_to_delete)

        return deleted_counts

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
        """Save render code for a workflow definition."""
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

    def list_instances(self, definition_id: int | None = None) -> list[WorkflowRunRecord]:
        """Alias for list_runs (deprecated)."""
        return self.list_runs(definition_id)

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
            "status": "in_progress",
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
            try:
                audit_sessions = self.labs_api.get_records(
                    experiment="audit",
                    type="AuditSession",
                    labs_record_id=run_id,
                )
                for session in audit_sessions:
                    ids_to_delete.append(session.id)
                    deleted_counts["audit_sessions"] += 1
                if deleted_counts["audit_sessions"] > 0:
                    logger.info(f"Deleting {deleted_counts['audit_sessions']} audit sessions linked to run {run_id}")
            except Exception as e:
                logger.warning(f"Failed to query audit sessions for run {run_id}: {e}")

        # Add the run itself
        ids_to_delete.append(run_id)
        deleted_counts["run"] = 1

        # Single batch delete
        self.labs_api.delete_records(ids_to_delete)

        return deleted_counts

    def get_instance(self, instance_id: int) -> WorkflowRunRecord | None:
        """Alias for get_run (deprecated)."""
        return self.get_run(instance_id)

    def get_or_create_run(self, definition_id: int, opportunity_id: int) -> WorkflowRunRecord:
        """Get or create a workflow run for the current week."""
        today = datetime.now(timezone.utc).date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        runs = self.list_runs(definition_id)
        for run in runs:
            if run.opportunity_id == opportunity_id and run.data.get("period_start") == week_start.isoformat():
                return run

        data = {
            "definition_id": definition_id,
            "period_start": week_start.isoformat(),
            "period_end": week_end.isoformat(),
            "status": "in_progress",
            "state": {},
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

    def get_or_create_instance(self, definition_id: int, opportunity_id: int) -> WorkflowRunRecord:
        """Alias for get_or_create_run (deprecated)."""
        return self.get_or_create_run(definition_id, opportunity_id)

    def update_run_state(
        self, run_id: int, new_state: dict, run: WorkflowRunRecord | None = None
    ) -> WorkflowRunRecord | None:
        """Update workflow run state (merge with existing).

        Args:
            run_id: The workflow run ID.
            new_state: Dict of state keys to merge.
            run: Optional pre-fetched run record (avoids redundant API call).
        """
        if run is None:
            run = self.get_run(run_id)
        if not run:
            return None

        current_state = run.data.get("state", {})
        merged_state = {**current_state, **new_state}
        updated_data = {**run.data, "state": merged_state}

        # Promote certain fields from state to top-level data so WorkflowRunRecord
        # properties (which check top-level first) reflect the latest values.
        for key in ("status", "period_start", "period_end"):
            if key in new_state:
                updated_data[key] = new_state[key]

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

    def update_instance_state(self, instance_id: int, new_state: dict) -> WorkflowRunRecord | None:
        """Alias for update_run_state (deprecated)."""
        return self.update_run_state(instance_id, new_state)

    def save_run_snapshot(self, run_id: int, snapshot: dict) -> WorkflowRunRecord | None:
        """Save a data snapshot on the run (writes to run.data['snapshot']).

        Unlike update_run_state() which merges into run.data['state'],
        this writes directly to run.data['snapshot'] as a sibling key.
        """
        run = self.get_run(run_id)
        if not run:
            return None

        updated_data = {**run.data, "snapshot": snapshot}

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
        overall_result: str = "completed",
        notes: str = "",
        run: WorkflowRunRecord | None = None,
    ) -> WorkflowRunRecord | None:
        """Mark a workflow run as completed.

        Updates run.data.status to 'completed' and stores overall_result/notes
        in the state.

        Args:
            run_id: The workflow run ID.
            overall_result: Completion result string.
            notes: Completion notes.
            run: Optional pre-fetched run record (avoids redundant API call).

        Returns:
            Updated WorkflowRunRecord, or None if not found.
        """
        if run is None:
            run = self.get_run(run_id)
        if not run:
            return None

        current_state = run.data.get("state", {})
        updated_data = {
            **run.data,
            "status": "completed",
            "state": {
                **current_state,
                "overall_result": overall_result,
                "notes": notes,
            },
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

        Returns:
            Dict mapping alias to pipeline result: {"visits": {"rows": [...], "metadata": {...}}}
        """
        definition = self.get_definition(definition_id)
        if not definition:
            return {}

        sources = definition.pipeline_sources
        if not sources:
            return {}

        results = {}
        pipeline_access = PipelineDataAccess(
            request=self.request,
            access_token=self.access_token,
            opportunity_id=opportunity_id,
            organization_id=self.organization_id,
            program_id=self.program_id,
        )

        for source in sources:
            pipeline_id = source.get("pipeline_id")
            alias = source.get("alias")

            if not pipeline_id or not alias:
                continue

            try:
                pipeline_result = pipeline_access.execute_pipeline(pipeline_id, opportunity_id)
                results[alias] = pipeline_result
            except Exception as e:
                logger.error(f"Failed to execute pipeline {pipeline_id}: {e}")
                results[alias] = {"rows": [], "metadata": {"error": str(e)}}

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
            List of worker dicts with username, name, visit_count, last_active
        """
        endpoint = f"/export/opportunity/{opportunity_id}/user_data/"
        response = self._call_connect_api(endpoint)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(response.content)

            df = pd.read_csv(tmp_path)

            workers = []
            for idx, row in df.iterrows():
                username = str(row["username"]) if pd.notna(row.get("username")) else None
                if username:
                    worker = {
                        "username": username,
                        "name": str(row.get("name", username)) if pd.notna(row.get("name")) else username,
                        "visit_count": int(row.get("total_visits", 0)) if pd.notna(row.get("total_visits")) else 0,
                        "last_active": str(row.get("last_active")) if pd.notna(row.get("last_active")) else None,
                    }

                    optional_fields = ["phone_number", "approved_visits", "flagged_visits", "rejected_visits", "email"]
                    for field in optional_fields:
                        if field in row and pd.notna(row[field]):
                            worker[field] = str(row[field]) if not isinstance(row[field], (int, float)) else row[field]

                    workers.append(worker)

            return workers

        finally:
            os.unlink(tmp_path)


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

    def execute_pipeline(self, definition_id: int, opportunity_id: int) -> dict:
        """
        Execute a pipeline and return results.

        Returns:
            Dict with rows and metadata
        """
        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

        definition = self.get_definition(definition_id)
        if not definition:
            return {"rows": [], "metadata": {"error": "Pipeline not found"}}

        schema = definition.schema
        if not schema:
            return {"rows": [], "metadata": {"error": "Pipeline has no schema"}}

        # Require request object for analysis pipeline
        if not self.request:
            return {"rows": [], "metadata": {"error": "Request object required for pipeline execution"}}

        try:
            # Convert schema to pipeline config
            config = self._schema_to_config(schema, definition_id)

            # Execute pipeline using the AnalysisPipeline
            pipeline = AnalysisPipeline(self.request)
            result = pipeline.stream_analysis_ignore_events(config, opportunity_id)

            # Convert result to dict format
            rows = []
            if hasattr(result, "rows"):
                for row in result.rows:
                    # Format dates consistently
                    def format_date(d):
                        if d and hasattr(d, "isoformat"):
                            return d.isoformat()
                        return str(d) if d else None

                    row_dict = {
                        "id": getattr(row, "id", None),
                        "username": getattr(row, "username", None),
                        "visit_date": format_date(getattr(row, "visit_date", None)),
                        # Built-in FLW aggregation fields
                        "total_visits": getattr(row, "total_visits", 0),
                        "approved_visits": getattr(row, "approved_visits", 0),
                        "pending_visits": getattr(row, "pending_visits", 0),
                        "rejected_visits": getattr(row, "rejected_visits", 0),
                        "flagged_visits": getattr(row, "flagged_visits", 0),
                        "first_visit_date": format_date(getattr(row, "first_visit_date", None)),
                        "last_visit_date": format_date(getattr(row, "last_visit_date", None)),
                    }
                    # Add computed fields (custom fields from config)
                    # FLWRow uses custom_fields, VisitRow uses computed
                    custom = getattr(row, "custom_fields", None) or getattr(row, "computed", None)
                    if custom:
                        row_dict.update(custom)
                    rows.append(row_dict)

            return {
                "rows": rows,
                "metadata": {
                    "row_count": len(rows),
                    "from_cache": getattr(result, "from_cache", False),
                    "pipeline_name": definition.name,
                    "terminal_stage": schema.get("terminal_stage", "visit_level"),
                },
            }

        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}", exc_info=True)
            return {"rows": [], "metadata": {"error": str(e)}}

    def _schema_to_config(self, schema: dict, definition_id: int):
        """Convert JSON schema to AnalysisPipelineConfig."""
        from commcare_connect.labs.analysis.config import (
            AnalysisPipelineConfig,
            CacheStage,
            DataSourceConfig,
            FieldComputation,
            HistogramComputation,
        )

        # Transform registry
        transform_registry = {
            "kg_to_g": lambda x: int(float(x) * 1000)
            if x and str(x).replace(".", "").replace("-", "").isdigit()
            else None,
            "float": lambda x: float(x) if x else None,
            "int": lambda x: int(float(x)) if x else None,
            "date": None,
            "string": lambda x: str(x) if x else None,
        }

        def get_transform(name):
            if not name:
                return None
            return transform_registry.get(name)

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
                    filter_value=field_def.get("filter_value", ""),
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

        # Parse data source config
        data_source_dict = schema.get("data_source") or {}
        data_source = DataSourceConfig(
            type=data_source_dict.get("type", "connect_csv"),
            form_name=data_source_dict.get("form_name", ""),
            app_id=data_source_dict.get("app_id", ""),
            app_id_source=data_source_dict.get("app_id_source", ""),
            gs_app_id=data_source_dict.get("gs_app_id", ""),
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
        )
