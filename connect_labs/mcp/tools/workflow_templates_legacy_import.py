"""ONE-OFF: move a template workflow out of the retired labs-DB tables into LabsRecords.

connect-labs#2236. #2231 stored template workflows in `workflow.TemplateWorkflow` /
`TemplateWorkflowVersion` / `TemplateWorkflowFollower` with their own owners list.
They now live as LabsRecords under the LabsRecord ACL (workflow/template_workflows.py).
This tool recreates one legacy template in a scope the caller can write -- every
version, with its number, note, author and date; the draft at its revision; the live
version -- then repoints its followers at it. Delete it with the legacy models once
the data has moved (the follow-up migration drops the tables).
"""

from __future__ import annotations

import copy

from connect_labs.workflow import template_workflows as tw
from connect_labs.workflow.data_access import WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


@register(
    name="workflow_template_import_legacy",
    description=(
        "ONE-OFF MIGRATION (connect-labs#2236): recreate a template workflow from the retired labs-DB template "
        "tables as LabsRecords in the program or opportunity you name (you need write access there), carrying "
        "every published version and the draft, then repoint its followers. Caller must be one of the legacy "
        "template's owners. dry_run=true reports what would happen."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "legacy_template_workflow_id": {"type": "integer"},
            "legacy_opportunity_id": {"type": "integer"},
            "legacy_program_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "The NEW home (owning) opportunity."},
            "program_id": {"type": "integer", "description": "The NEW home (owning) program."},
            "public": {"type": "boolean", "description": "Share the new template publicly (default true)."},
            "name": {"type": "string", "description": "Optional new name (default: the legacy name)."},
            "repoint_followers": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
        },
        "required": ["legacy_template_workflow_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_import_legacy(
    user,
    legacy_template_workflow_id: int,
    legacy_opportunity_id: int = None,
    legacy_program_id: int = None,
    opportunity_id: int = None,
    program_id: int = None,
    public: bool = True,
    name: str = None,
    repoint_followers: bool = True,
    dry_run: bool = False,
):
    from connect_labs.workflow.models import TemplateWorkflow

    try:
        legacy_key = tw.scope_key(legacy_opportunity_id, legacy_program_id)
        target = tw.scope_of(opportunity_id, program_id)
    except tw.TemplateWorkflowError as exc:
        raise MCPToolError(exc.code, f"legacy and new scope: {exc}") from exc
    legacy = TemplateWorkflow.objects.filter(workflow_id=legacy_template_workflow_id, scope_key=legacy_key).first()
    if legacy is None:
        raise MCPToolError("NOT_FOUND", f"no legacy template workflow {legacy_template_workflow_id} in {legacy_key}")
    if not (legacy.owners.filter(pk=user.pk).exists() or getattr(user, "is_staff", False)):
        raise MCPToolError("PERMISSION_DENIED", "only an owner of the legacy template may move it")
    versions = list(legacy.versions.order_by("number"))
    followers = list(legacy.followers.order_by("workflow_id"))
    plan = {
        "legacy": {"template_workflow_id": legacy.workflow_id, "scope": legacy_key},
        "new_scope": target,
        "versions": [v.number for v in versions],
        "live_version": legacy.published.number if legacy.published else None,
        "draft_revision": legacy.draft_revision,
        "followers": [
            {"workflow_id": f.workflow_id, **tw.scope_of(f.opportunity_id, f.program_id)} for f in followers
        ],
    }
    if dry_run:
        return {"dry_run": True, **plan}

    token = require_connect_token(user)
    legacy_scope = tw.scope_of(legacy_opportunity_id, legacy_program_id)
    old = WorkflowDataAccess(access_token=token, **legacy_scope)
    try:
        old_def = old.labs_api.get_record_by_id(legacy.workflow_id, experiment=tw.EXPERIMENT, type=tw.DEFINITION_TYPE)
    finally:
        old.close()
    old_data = (old_def.data if old_def else None) or {}

    home = WorkflowDataAccess(access_token=token, **target)
    try:
        template = tw.create_template(
            home,
            user=user,
            name=name or old_data.get("name") or legacy.name,
            description=old_data.get("description") or "",
            statuses=old_data.get("statuses"),
            template_scope=legacy.template_scope,
            render_code=legacy.draft_render_code,
            config=legacy.draft_config,
            snapshot_inputs=legacy.draft_snapshot_inputs,
            seeded_from=legacy.seeded_from,
            template_type=legacy.template_type,
            public=bool(public),
            publish=False,
            draft_revision=legacy.draft_revision,
        )
        records = {}
        for v in versions:
            record = home.labs_api.create_record(
                experiment=tw.EXPERIMENT,
                type=tw.VERSION_TYPE,
                data={
                    "template_workflow_id": template.workflow_id,
                    "number": v.number,
                    "render_code": v.render_code,
                    "config": copy.deepcopy(v.config or {}),
                    "snapshot_inputs": copy.deepcopy(v.snapshot_inputs),
                    "note": v.note,
                    "restores_version": v.restores_version,
                    "published_by": v.published_by.username if v.published_by else None,
                    "published_at": v.published_at.isoformat() if v.published_at else None,
                    "migrated_from": f"labs-db template {legacy.workflow_id} ({legacy_key})",
                },
                labs_record_id=template.workflow_id,
                public=bool(public),
            )
            records[v.number] = (record, v)
        if legacy.published is not None:
            record, v = records[legacy.published.number]
            template = tw._write_meta(
                home,
                template,
                published={
                    "version": v.number,
                    "record_id": record.id,
                    "config": copy.deepcopy(v.config or {}),
                    "snapshot_inputs": copy.deepcopy(v.snapshot_inputs),
                },
            )

        repointed = []
        if repoint_followers:
            for f in followers:
                f_scope = tw.scope_of(f.opportunity_id, f.program_id)
                wda = WorkflowDataAccess(access_token=token, **f_scope)
                try:
                    current = wda.get_definition(f.workflow_id)
                    if current is None:
                        repointed.append({"workflow_id": f.workflow_id, **f_scope, "error": "not readable"})
                        continue
                    own = dict(getattr(current, "own_data", current.data))
                    before_config = copy.deepcopy(own.get("config"))
                    own["render_source"] = template.render_source()
                    updated = wda.update_definition(f.workflow_id, own)
                    after_config = (updated.data if updated else own).get("config")
                    repointed.append(
                        {
                            "workflow_id": f.workflow_id,
                            **f_scope,
                            "render_source": template.render_source(),
                            "own_config_unchanged": before_config == after_config,
                        }
                    )
                finally:
                    wda.close()
                template = tw.reload(home, template)
                tw.record_follower(home, template, workflow_id=f.workflow_id, scope=f_scope)
        template = tw.reload(home, template)
        out = tw.describe(home.labs_api, template)
    finally:
        home.close()
    out["migrated"] = plan
    out["repointed"] = repointed
    out["url"] = tw.run_page_url(template.workflow_id, template.opportunity_id, template.program_id)
    return out
