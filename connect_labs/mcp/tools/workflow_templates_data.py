"""Template workflows -- the FAST PATH for changing a report that many workflows show.

A template workflow holds a report's render code, config defaults and snapshot spec
as data, with a draft, published versions and a rollback. Workflows that follow it
(`render_source: {workflow: <id>, <scope>}`) show its published version on their next
load: no PR, no merge, no deploy. See connect_labs/workflow/template_workflows.py.

Only a change to what the ENGINE computes (the snapshot builder, an API) still needs
a deploy -- a render can only read what those provide.
"""

from __future__ import annotations

import difflib

from connect_labs.workflow import template_workflows as tw
from connect_labs.workflow.data_access import WorkflowDataAccess

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

_FAST_PATH = (
    "FAST PATH: a template workflow's render, config defaults and snapshot spec are DATA. Edit the draft, "
    "preview it on a follower, publish -- every follower shows it on its next load with no deploy. Prefer this "
    "over editing a code template in the repo; only a change to what the engine computes (the snapshot builder, "
    "an API) needs a deploy."
)

_SCOPE_PROPS = {
    "opportunity_id": {
        "type": "integer",
        "description": "The template workflow's owning opportunity. This OR program_id.",
    },
    "program_id": {
        "type": "integer",
        "description": "The template workflow's owning program. This OR opportunity_id.",
    },
}


def _scope(opportunity_id, program_id) -> dict:
    if (opportunity_id is None) == (program_id is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
    return {"opportunity_id": opportunity_id} if opportunity_id is not None else {"program_id": program_id}


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tw.TemplateWorkflowError as exc:
        raise MCPToolError(exc.code, str(exc)) from exc


def _template(workflow_id, opportunity_id, program_id):
    _scope(opportunity_id, program_id)
    template = tw.find_template(workflow_id, opportunity_id, program_id)
    if template is None:
        raise MCPToolError(
            "NOT_FOUND",
            f"workflow {workflow_id} in that scope is not a template workflow (workflow_template_create makes one)",
        )
    return template


def _check_snapshot_inputs(value):
    if value is None:
        return
    from .workflows import _validate_snapshot_inputs

    _validate_snapshot_inputs(value)


def _check_config(value):
    if isinstance(value, dict) and "actions" in value:
        from connect_labs.workflow.actions import declaration_problems

        problems = declaration_problems(value["actions"])
        if problems:
            raise MCPToolError("INVALID_SCHEMA", "; ".join(problems))


@register(
    name="workflow_template_create",
    description=(
        "Create a TEMPLATE WORKFLOW: a report whose render code, config defaults and snapshot spec live as data, "
        "with a draft, published versions and rollback. Seed it from a code template (template_key, e.g. "
        "'kmc_programme_metrics' -- after this the code template has no role for its followers) or from an "
        "existing workflow (from_workflow). The seed is published as version 1 and you become its owner. Then "
        "point workflows at it with workflow_follow_template. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE_PROPS,
            "name": {"type": "string"},
            "template_scope": {
                "type": "string",
                "description": "Who may follow it: 'global' (admin-only to set), 'org:<id>' or 'program:<id>'.",
            },
            "template_key": {"type": "string", "description": "Seed from this code template."},
            "from_workflow": {
                "type": "object",
                "description": "Seed from this workflow's current render/config/snapshot_inputs: "
                "{workflow_id, opportunity_id | program_id}.",
            },
            "description": {"type": "string"},
        },
        "required": ["name", "template_scope"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_create(
    user,
    name: str,
    template_scope: str,
    opportunity_id: int = None,
    program_id: int = None,
    template_key: str = None,
    from_workflow: dict = None,
    description: str = None,
):
    from connect_labs.workflow.render_source import resolve_render_code
    from connect_labs.workflow.templates import get_template

    from .workflows import _validate_template_scope

    scope = _scope(opportunity_id, program_id)
    _validate_template_scope(template_scope, user)
    if (template_key is None) == (from_workflow is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of template_key / from_workflow.")
    token = require_connect_token(user)

    statuses = None
    if template_key:
        seed = get_template(template_key)
        if not seed:
            raise MCPToolError("NOT_FOUND", f"no code template '{template_key}'")
        definition = seed.get("definition") or {}
        render_code = seed.get("render_code") or ""
        config = dict(definition.get("config") or {})
        snapshot_inputs = seed.get("snapshot_inputs") if isinstance(seed.get("snapshot_inputs"), dict) else None
        statuses = definition.get("statuses")
        description = description or definition.get("description") or seed.get("description", "")
        seeded_from = f"code:{template_key}"
    else:
        src_scope = _scope(from_workflow.get("opportunity_id"), from_workflow.get("program_id"))
        src = WorkflowDataAccess(access_token=token, **src_scope)
        try:
            source = src.get_definition(int(from_workflow["workflow_id"]))
            if source is None:
                raise MCPToolError("NOT_FOUND", f"No workflow {from_workflow['workflow_id']} in {src_scope}")
            render_code, _ = resolve_render_code(src, source)
        finally:
            src.close()
        config = dict(source.data.get("config") or {})
        snapshot_inputs = (
            source.data.get("snapshot_inputs") if isinstance(source.data.get("snapshot_inputs"), dict) else None
        )
        statuses = source.data.get("statuses")
        description = description or source.description
        seeded_from = f"workflow:{from_workflow['workflow_id']}"
    if not (render_code or "").strip():
        raise MCPToolError("INVALID_SCHEMA", "the seed has no render code")
    template_type = config.get("templateType") or template_key or ""

    wda = WorkflowDataAccess(access_token=token, **scope)
    try:
        # The template's own record is thin: its content lives in the template store,
        # and the record follows ITSELF, so opening it shows the published version
        # (or, to an owner with ?template_draft=1, the draft).
        created = wda.create_definition(
            name=name,
            description=description or "",
            **({"statuses": statuses} if statuses else {}),
            config={"templateType": template_type} if template_type else {},
        )
        template = _wrap(
            tw.create_template,
            user=user,
            workflow_id=created.id,
            name=name,
            template_scope=template_scope,
            render_code=render_code,
            config=config,
            snapshot_inputs=snapshot_inputs,
            seeded_from=seeded_from,
            template_type=template_type,
            **{"opportunity_id": opportunity_id, "program_id": program_id},
        )
        data = dict(created.data)
        data.update(
            {"is_template": True, "template_scope": template_scope, "render_source": tw.render_source_for(template)}
        )
        wda.update_definition(created.id, data)
    finally:
        wda.close()
    out = tw.describe(template)
    out["url"] = tw.run_page_url(created.id, opportunity_id, program_id)
    out["next"] = "workflow_follow_template to point workflows at it; then edit -> preview -> publish."
    return out


@register(
    name="workflow_template_get",
    description=(
        "Read a template workflow: owners, the draft (revision, whether it differs from what is live), the "
        "version history, and its followers with each one's page URL and draft-preview URL. include_code=true "
        "adds the draft's render code, config and snapshot_inputs -- read the draft here before editing it. "
        + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "include_code": {"type": "boolean"},
        },
        "required": ["template_workflow_id"],
        "additionalProperties": False,
    },
)
def workflow_template_get(
    user, template_workflow_id: int, opportunity_id: int = None, program_id: int = None, include_code: bool = False
):
    template = _template(template_workflow_id, opportunity_id, program_id)
    if include_code and not tw.is_owner(template, user):
        # The draft is unpublished work; the published version is what followers read.
        out = tw.describe(template)
        published = tw.content_of(template, draft=False) or {}
        out["published"] = {k: published.get(k) for k in ("render_code", "config", "snapshot_inputs", "version")}
        out["note"] = "draft content is shown to owners only; this is the published version"
        return out
    return tw.describe(template, include_code=include_code)


@register(
    name="workflow_template_list",
    description="List the template workflows you own, plus every global one. " + _FAST_PATH,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def workflow_template_list(user):
    from django.db.models import Q

    from connect_labs.workflow.models import TemplateWorkflow

    rows = TemplateWorkflow.objects.filter(Q(owners=user) | Q(template_scope="global")).distinct()
    return {
        "templates": [
            {
                "template_workflow_id": t.workflow_id,
                "scope": (
                    {"opportunity_id": t.opportunity_id}
                    if t.opportunity_id is not None
                    else {"program_id": t.program_id}
                ),
                "name": t.name,
                "template_type": t.template_type,
                "template_scope": t.template_scope,
                "published_version": t.published.number if t.published else None,
                "owner": tw.is_owner(t, user),
                "followers": t.followers.count(),
            }
            for t in rows.order_by("name")
        ]
    }


@register(
    name="workflow_template_update_draft",
    description=(
        "Edit a template workflow's DRAFT (owners only). Followers see nothing until workflow_template_publish. "
        "Change the render with `edits` ([{old, new}], each `old` matching exactly once -- best for small changes) "
        "or a whole `render_code`; `config` merges into the draft's config defaults (config_replace=true replaces "
        "them); `snapshot_inputs` replaces the snapshot spec (null clears it). A follower's own config keys still "
        "win over these defaults. expected_revision is the draft revision from workflow_template_get. Check the "
        "result with the follower's draft_preview_url, then publish. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "expected_revision": {"type": "integer"},
            "render_code": {"type": "string"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                    "required": ["old", "new"],
                },
            },
            "config": {"type": "object"},
            "config_replace": {"type": "boolean"},
            "snapshot_inputs": {"type": ["object", "null"]},
        },
        "required": ["template_workflow_id", "expected_revision"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_update_draft(
    user,
    template_workflow_id: int,
    expected_revision: int,
    opportunity_id: int = None,
    program_id: int = None,
    render_code: str = None,
    edits: list = None,
    config: dict = None,
    config_replace: bool = False,
    **kwargs,
):
    template = _template(template_workflow_id, opportunity_id, program_id)
    snapshot_set = "snapshot_inputs" in kwargs
    snapshot_inputs = kwargs.get("snapshot_inputs")
    if snapshot_set:
        _check_snapshot_inputs(snapshot_inputs)
    _check_config(config)
    _wrap(
        tw.update_draft,
        template,
        user=user,
        expected_revision=expected_revision,
        render_code=render_code,
        edits=edits,
        config=config,
        config_replace=bool(config_replace),
        snapshot_inputs=snapshot_inputs,
        snapshot_inputs_set=snapshot_set,
    )
    out = tw.describe(template)
    out["warning"] = _render_warning(template.draft_render_code)
    return out


def _render_warning(code):
    try:
        from connect_labs.workflow.render_code_lint import render_code_warning

        return render_code_warning(code)
    except Exception:  # noqa: BLE001 -- advisory only
        return None


@register(
    name="workflow_template_preview",
    description=(
        "Preview a template workflow's draft before publishing (owners only): returns, for every follower, the URL "
        "that renders it WITH THE DRAFT (`draft_preview_url` -- add &run_id=<id> for a saved run; only owners see the "
        "draft there, everyone else still sees what is published), and a diff of the draft against the published "
        "version. Followers are not affected. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "max_diff_lines": {"type": "integer", "minimum": 0, "maximum": 2000},
        },
        "required": ["template_workflow_id"],
        "additionalProperties": False,
    },
)
def workflow_template_preview(
    user, template_workflow_id: int, opportunity_id: int = None, program_id: int = None, max_diff_lines: int = 200
):
    template = _template(template_workflow_id, opportunity_id, program_id)
    _wrap(tw.require_owner, template, user)
    published = tw.content_of(template, draft=False) or {"render_code": "", "config": {}, "snapshot_inputs": None}
    diff = list(
        difflib.unified_diff(
            (published.get("render_code") or "").splitlines(),
            (template.draft_render_code or "").splitlines(),
            fromfile=f"published v{published.get('version')}",
            tofile=f"draft r{template.draft_revision}",
            lineterm="",
            n=2,
        )
    )
    p_config, d_config = published.get("config") or {}, template.draft_config or {}
    out = tw.describe(template)
    out["render_diff"] = diff[:max_diff_lines]
    out["render_diff_truncated"] = len(diff) > max_diff_lines
    out["config_changed_keys"] = sorted(k for k in set(p_config) | set(d_config) if p_config.get(k) != d_config.get(k))
    out["snapshot_inputs_changed"] = published.get("snapshot_inputs") != template.draft_snapshot_inputs
    out["self_preview_url"] = tw.run_page_url(
        template.workflow_id, template.opportunity_id, template.program_id, draft=True
    )
    return out


@register(
    name="workflow_template_publish",
    description=(
        "Publish a template workflow's draft as its next version (owners only). Every follower shows it on its "
        "next page load -- no deploy. The previous version stays in the history for workflow_template_rollback. "
        + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "note": {"type": "string", "description": "What changed, for the version history."},
            "expected_revision": {
                "type": "integer",
                "description": "Optional: refuse if the draft moved past this revision since you previewed it.",
            },
        },
        "required": ["template_workflow_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_publish(
    user,
    template_workflow_id: int,
    opportunity_id: int = None,
    program_id: int = None,
    note: str = "",
    expected_revision: int = None,
):
    template = _template(template_workflow_id, opportunity_id, program_id)
    if expected_revision is not None and expected_revision != template.draft_revision:
        raise MCPToolError(
            "VERSION_CONFLICT", f"draft is at revision {template.draft_revision}, not {expected_revision}"
        )
    version = _wrap(tw.publish_draft, template, user=user, note=note)
    out = tw.describe(template)
    out["published_now"] = version.number
    return out


@register(
    name="workflow_template_rollback",
    description=(
        "Roll a template workflow back (owners only): publishes a COPY of an earlier version as the next version, so "
        "every follower shows it on its next load and the history records the rollback. reset_draft=true also "
        "resets the draft to that version (otherwise the draft keeps your unpublished edits). " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "to_version": {"type": "integer"},
            "note": {"type": "string"},
            "reset_draft": {"type": "boolean"},
        },
        "required": ["template_workflow_id", "to_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_rollback(
    user,
    template_workflow_id: int,
    to_version: int,
    opportunity_id: int = None,
    program_id: int = None,
    note: str = "",
    reset_draft: bool = False,
):
    template = _template(template_workflow_id, opportunity_id, program_id)
    version = _wrap(tw.rollback, template, user=user, to_version=to_version, note=note, reset_draft=bool(reset_draft))
    out = tw.describe(template)
    out["published_now"] = version.number
    out["restores_version"] = to_version
    return out


@register(
    name="workflow_template_set_owners",
    description=(
        "Add or remove owners of a template workflow (owners, or labs admins). Owners edit the draft, preview, "
        "publish and roll back; anyone who can read the template may follow it. A template keeps at least one owner."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "add": {"type": "array", "items": {"type": "string"}, "description": "Usernames to add."},
            "remove": {"type": "array", "items": {"type": "string"}, "description": "Usernames to remove."},
        },
        "required": ["template_workflow_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_set_owners(
    user,
    template_workflow_id: int,
    opportunity_id: int = None,
    program_id: int = None,
    add: list = None,
    remove: list = None,
):
    from django.contrib.auth import get_user_model

    template = _template(template_workflow_id, opportunity_id, program_id)
    if not (tw.is_owner(template, user) or getattr(user, "is_staff", False)):
        _wrap(tw.require_owner, template, user)
    User = get_user_model()
    for username in add or []:
        person = User.objects.filter(username=username).first()
        if person is None:
            raise MCPToolError("NOT_FOUND", f"no labs user '{username}' (they must have signed in to labs once)")
        template.owners.add(person)
    for username in remove or []:
        template.owners.remove(*User.objects.filter(username=username))
    if not template.owners.exists():
        template.owners.add(user)
        raise MCPToolError("INVALID_SCHEMA", "a template workflow must keep at least one owner")
    return tw.describe(template)


@register(
    name="workflow_follow_template",
    description=(
        "Point a workflow at a template workflow (follow=true, the default) or fork it off one (follow=false). "
        "A follower renders the template's PUBLISHED render and inherits its config defaults and snapshot_inputs; "
        "keys the follower sets itself win (e.g. its own enrollment_targets). On follow, any follower key EQUAL to "
        "the template's value is dropped so it stays inherited -- the result lists them under `now_inherited` and "
        "the real overrides under `overrides`. You must be able to read the template (global, or its scope), and "
        "the workflow must have the same templateType. follow=false forks: the published render becomes the "
        "workflow's stored copy and inherited values are written into its own config, so the page does not "
        "change. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer", "description": "The FOLLOWER's owning opportunity."},
            "program_id": {"type": "integer", "description": "The FOLLOWER's owning program."},
            "template_workflow_id": {"type": "integer"},
            "template_opportunity_id": {"type": "integer"},
            "template_program_id": {"type": "integer"},
            "follow": {"type": "boolean"},
            "expected_version": {"type": "integer", "description": "Optional: the follower definition's version."},
        },
        "required": ["workflow_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_follow_template(
    user,
    workflow_id: int,
    opportunity_id: int = None,
    program_id: int = None,
    template_workflow_id: int = None,
    template_opportunity_id: int = None,
    template_program_id: int = None,
    follow: bool = True,
    expected_version: int = None,
):
    from connect_labs.workflow.render_source import followed_template_workflow, resolve_render_code

    scope = _scope(opportunity_id, program_id)
    token = require_connect_token(user)
    wda = WorkflowDataAccess(access_token=token, **scope)
    try:
        current = wda.get_definition(workflow_id)
        if current is None:
            raise MCPToolError("NOT_FOUND", f"No workflow {workflow_id} in {scope}")
        version = current.data.get("version", 1)
        if expected_version is not None and expected_version != version:
            raise MCPToolError(
                "VERSION_CONFLICT", f"workflow definition is at version {version}, not {expected_version}"
            )

        if not follow:
            if followed_template_workflow(current) is None:
                raise MCPToolError("INVALID_SCHEMA", f"workflow {workflow_id} does not follow a template workflow")
            code, _src = resolve_render_code(wda, current)
            # `current.data` is the EFFECTIVE definition: dropping render_source and
            # writing it bakes every inherited value into the workflow's own record.
            data = dict(current.data)
            data.pop("render_source", None)
            data["version"] = version + 1
            wda.update_definition(workflow_id, data)
            stored = wda.get_render_code(workflow_id)
            if code:
                wda.save_render_code(workflow_id, code, version=(stored.version if stored else 0) + 1)
            tw.forget_follower(workflow_id, **scope)
            return {"workflow_id": workflow_id, "follows": None, "forked": True, "new_version": version + 1}

        if template_workflow_id is None:
            raise MCPToolError("INVALID_SCHEMA", "template_workflow_id is required to follow")
        t_scope = _scope(template_opportunity_id, template_program_id)
        template = _template(template_workflow_id, template_opportunity_id, template_program_id)

        def read_template_definition():
            reader = WorkflowDataAccess(access_token=token, **t_scope)
            try:
                return reader.get_definition(template_workflow_id)
            finally:
                reader.close()

        if not tw.can_follow(template, user, read_template_definition):
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"you cannot read template workflow {template_workflow_id} (scope {template.template_scope}), so "
                "you cannot follow it",
            )
        own_type = (current.data.get("config") or {}).get("templateType") or ""
        if template.template_type and own_type != template.template_type:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"workflow {workflow_id} is a '{own_type}' workflow; this template renders '{template.template_type}' "
                "and reads that template's pipelines, config and snapshot contract",
            )
        if tw.content_of(template, draft=False) is None:
            raise MCPToolError("INVALID_SCHEMA", "the template has no published version yet; publish it first")

        before = getattr(current, "own_data", current.data)
        data = dict(before)
        data["render_source"] = tw.render_source_for(template)
        data["version"] = version + 1
        updated = wda.update_definition(workflow_id, data)
        tw.record_follower(template, user=user, workflow_id=workflow_id, **scope)
    finally:
        wda.close()

    after = (updated.data if updated else data) or {}
    b_cfg, a_cfg = before.get("config") or {}, after.get("config") or {}
    b_in = before.get("snapshot_inputs") if isinstance(before.get("snapshot_inputs"), dict) else {}
    a_in = after.get("snapshot_inputs") if isinstance(after.get("snapshot_inputs"), dict) else {}
    return {
        "workflow_id": workflow_id,
        "follows": tw.render_source_for(template),
        "published_version": template.published.number if template.published else None,
        "now_inherited": {
            "config": sorted(set(b_cfg) - set(a_cfg)),
            "snapshot_inputs": sorted(set(b_in) - set(a_in)),
        },
        "overrides": {"config": sorted(a_cfg), "snapshot_inputs": sorted(a_in)},
        "url": tw.run_page_url(workflow_id, opportunity_id, program_id),
        "draft_preview_url": tw.run_page_url(workflow_id, opportunity_id, program_id, draft=True),
        "new_version": version + 1,
    }
