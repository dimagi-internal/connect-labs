"""Template workflows -- the FAST PATH for changing a report that many workflows show.

A template workflow holds a report's render code, config defaults and snapshot spec
as data, with a draft, published versions and a rollback. Workflows that follow it
(`render_source: {workflow: <id>, <scope>}`) show its published version on their next
load: no PR, no merge, no deploy. See connect_labs/workflow/template_workflows.py.

Everything is LabsRecords in the template's home scope (a program or an opportunity),
and every permission is that scope's LabsRecord ACL: write access there means edit,
preview, publish, roll back and change sharing; being able to read the template -- in
its scope, or anywhere once it is shared publicly -- means follow. Each call runs
under the caller's own Connect token, so Connect enforces it too.

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
    "an API) needs a deploy. A render can use only Tailwind classes the deployed CSS already contains."
)

_ACL = (
    "Permissions are the LabsRecord ACL of the template's home scope (a program or an opportunity): write access "
    "there means edit, preview, publish and roll back."
)

_SCOPE_PROPS = {
    "opportunity_id": {
        "type": "integer",
        "description": "The template workflow's home (owning) opportunity. This OR program_id.",
    },
    "program_id": {
        "type": "integer",
        "description": "The template workflow's home (owning) program. This OR opportunity_id.",
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


class _Home:
    """The template's home-scope data access under the caller's token, plus the template."""

    def __init__(self, user, workflow_id, opportunity_id, program_id):
        scope = _scope(opportunity_id, program_id)
        self.token = require_connect_token(user)
        self.wda = WorkflowDataAccess(access_token=self.token, **scope)
        try:
            self.template = tw.load_template(self.wda.labs_api, workflow_id, **scope)
        except Exception:
            self.wda.close()
            raise
        if self.template is None:
            self.wda.close()
            raise MCPToolError(
                "NOT_FOUND",
                f"workflow {workflow_id} in {scope} is not a template workflow you can read: it does not exist, is "
                "not a template workflow (workflow_template_create makes one), or is not shared with you (its "
                "owners can share it with workflow_template_set_sharing public=true).",
            )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.wda.close()

    def describe(self, **kwargs):
        return tw.describe(self.wda.labs_api, self.template, **kwargs)

    def reload(self):
        self.template = _wrap(tw.reload, self.wda, self.template)
        return self.template


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
        "Create a TEMPLATE WORKFLOW: a report whose render code, config defaults and snapshot spec live as data "
        "(LabsRecords), with a draft, published versions and rollback. Seed it from a code template (template_key, "
        "e.g. 'kmc_programme_metrics' -- after this the code template has no role for its followers) or from an "
        "existing workflow (from_workflow). It lives in -- is owned by -- the program or opportunity you name "
        "(opportunity_id / program_id); seeding from a workflow defaults to that workflow's own scope. The seed is "
        "published as version 1. " + _ACL + " public=true shares it so workflows in ANY program or opportunity "
        "can follow it. Then point workflows at it with workflow_follow_template. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE_PROPS,
            "name": {"type": "string"},
            "template_scope": {
                "type": "string",
                "description": "Where it shows in the template picker: 'global' (admin-only to set), 'org:<id>' or "
                "'program:<id>'. Does not grant access; `public` does.",
            },
            "public": {
                "type": "boolean",
                "description": "Share it publicly so any program or opportunity may follow it (default false: only "
                "people with access to its home scope can).",
            },
            "template_key": {"type": "string", "description": "Seed from this code template."},
            "from_workflow": {
                "type": "object",
                "description": "Seed from this workflow's current render/config/snapshot_inputs: "
                "{workflow_id, opportunity_id | program_id}.",
            },
            "description": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_create(
    user,
    name: str,
    template_scope: str = None,
    opportunity_id: int = None,
    program_id: int = None,
    template_key: str = None,
    from_workflow: dict = None,
    description: str = None,
    public: bool = False,
):
    from connect_labs.workflow.render_source import resolve_render_code
    from connect_labs.workflow.templates import get_template

    from .workflows import _validate_template_scope

    if template_scope:
        _validate_template_scope(template_scope, user)
    if (template_key is None) == (from_workflow is None):
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of template_key / from_workflow.")
    if opportunity_id is None and program_id is None and from_workflow:
        # The template keeps the seed workflow's own scope unless told otherwise.
        opportunity_id, program_id = from_workflow.get("opportunity_id"), from_workflow.get("program_id")
    scope = _scope(opportunity_id, program_id)
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

    home = WorkflowDataAccess(access_token=token, **scope)
    try:
        template = _wrap(
            tw.create_template,
            home,
            user=user,
            name=name,
            description=description,
            statuses=statuses,
            template_scope=template_scope,
            render_code=render_code,
            config=config,
            snapshot_inputs=snapshot_inputs,
            seeded_from=seeded_from,
            template_type=template_type,
            public=bool(public),
        )
        out = tw.describe(home.labs_api, template)
    finally:
        home.close()
    out["url"] = tw.run_page_url(template.workflow_id, scope.get("opportunity_id"), scope.get("program_id"))
    out["next"] = "workflow_follow_template to point workflows at it; then edit -> preview -> publish."
    return out


@register(
    name="workflow_template_get",
    description=(
        "Read a template workflow: who owns it (its scope) and who may edit it, whether YOU can, its sharing "
        "(who may follow it), the draft (revision, whether it differs from what is live -- shown to editors "
        "only), the version history, and its followers with each one's page URL and draft-preview URL. "
        "include_code=true adds the draft's render code, config and snapshot_inputs (editors) or the published "
        "ones (everyone else) -- read the draft here before editing it. " + _ACL + " " + _FAST_PATH
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
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        out = home.describe(include_code=include_code)
        if include_code and not tw.can_edit(home.template):
            published = tw.content_of(home.wda.labs_api, home.template, draft=False) or {}
            out["published"] = {k: published.get(k) for k in ("render_code", "config", "snapshot_inputs", "version")}
        return out


@register(
    name="workflow_template_list",
    description=(
        "List template workflows: the ones in a program or opportunity you name (opportunity_id / program_id -- "
        "with you_can_edit for each), plus every publicly shared one. " + _FAST_PATH
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer", "description": "List the templates owned by this opportunity."},
            "program_id": {"type": "integer", "description": "List the templates owned by this program."},
        },
        "additionalProperties": False,
    },
)
def workflow_template_list(user, opportunity_id: int = None, program_id: int = None):
    token = require_connect_token(user)
    found: dict[tuple, dict] = {}

    def add(record, scope, in_scope):
        if not (isinstance(record.data, dict) and isinstance(record.data.get(tw.META_KEY), dict)):
            return
        t = tw.Template(record, scope, in_scope=in_scope)
        found[(t.workflow_id, tw.scope_key(t.opportunity_id, t.program_id))] = {
            "template_workflow_id": t.workflow_id,
            "scope": dict(t.scope),
            "name": t.name,
            "template_type": t.template_type,
            "template_scope": t.template_scope,
            "public": t.public,
            "published_version": (t.published or {}).get("version"),
            "you_can_edit": in_scope,
            "followers": len(t.meta.get("followers") or []),
        }

    if opportunity_id is not None or program_id is not None:
        scope = _scope(opportunity_id, program_id)
        wda = WorkflowDataAccess(access_token=token, **scope)
        try:
            for record in wda.labs_api.get_records(experiment=tw.EXPERIMENT, type=tw.DEFINITION_TYPE):
                add(record, scope, True)
        finally:
            wda.close()
    reader = WorkflowDataAccess(access_token=token)
    try:
        for record in reader.labs_api.get_records(experiment=tw.EXPERIMENT, type=tw.DEFINITION_TYPE, public=True):
            if not (isinstance(record.data, dict) and isinstance(record.data.get(tw.META_KEY), dict)):
                continue
            home = (
                {"opportunity_id": record.opportunity_id}
                if record.opportunity_id
                else {"program_id": record.program_id} if record.program_id else None
            )
            if home is None:
                continue
            key = (record.id, tw.scope_key(home.get("opportunity_id"), home.get("program_id")))
            if key not in found:
                add(record, home, False)
    finally:
        reader.close()
    return {"templates": sorted(found.values(), key=lambda t: (t["name"] or "", t["template_workflow_id"]))}


@register(
    name="workflow_template_update_draft",
    description=(
        "Edit a template workflow's DRAFT (write access to its scope). Followers see nothing until "
        "workflow_template_publish. Change the render with `edits` ([{old, new}], each `old` matching exactly once "
        "-- best for small changes) or a whole `render_code`; `config` merges into the draft's config defaults "
        "(config_replace=true replaces them); `snapshot_inputs` replaces the snapshot spec (null clears it). A "
        "follower's own config keys still win over these defaults. expected_revision is the draft revision from "
        "workflow_template_get. Check the result with the follower's draft_preview_url, then publish. " + _FAST_PATH
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
    snapshot_set = "snapshot_inputs" in kwargs
    snapshot_inputs = kwargs.get("snapshot_inputs")
    if snapshot_set:
        _check_snapshot_inputs(snapshot_inputs)
    _check_config(config)
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        draft = _wrap(
            tw.update_draft,
            home.wda,
            home.template,
            user=user,
            expected_revision=expected_revision,
            render_code=render_code,
            edits=edits,
            config=config,
            config_replace=bool(config_replace),
            snapshot_inputs=snapshot_inputs,
            snapshot_inputs_set=snapshot_set,
        )
        home.reload()
        out = home.describe()
    out["warning"] = _render_warning(draft.get("render_code"))
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
        "Preview a template workflow's draft before publishing (write access to its scope): returns, for every "
        "follower, the URL that renders it WITH THE DRAFT (`draft_preview_url` -- add &run_id=<id> for a saved run; "
        "only people with write access to the template's scope see the draft there, everyone else still sees what "
        "is published), and a diff of the draft against the published version. Followers are not affected. "
        + _FAST_PATH
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
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        _wrap(tw.require_edit, home.template)
        api = home.wda.labs_api
        published = tw.content_of(api, home.template, draft=False) or {
            "render_code": "",
            "config": {},
            "snapshot_inputs": None,
        }
        draft = tw.content_of(api, home.template, draft=True) or {}
        diff = list(
            difflib.unified_diff(
                (published.get("render_code") or "").splitlines(),
                (draft.get("render_code") or "").splitlines(),
                fromfile=f"published v{published.get('version')}",
                tofile=f"draft r{draft.get('draft_revision')}",
                lineterm="",
                n=2,
            )
        )
        p_config, d_config = published.get("config") or {}, draft.get("config") or {}
        out = home.describe()
        out["render_diff"] = diff[:max_diff_lines]
        out["render_diff_truncated"] = len(diff) > max_diff_lines
        out["config_changed_keys"] = sorted(
            k for k in set(p_config) | set(d_config) if p_config.get(k) != d_config.get(k)
        )
        out["snapshot_inputs_changed"] = published.get("snapshot_inputs") != draft.get("snapshot_inputs")
        t = home.template
        out["self_preview_url"] = tw.run_page_url(t.workflow_id, t.opportunity_id, t.program_id, draft=True)
        return out


@register(
    name="workflow_template_publish",
    description=(
        "Publish a template workflow's draft as its next version (write access to its scope). Every follower shows "
        "it on its next page load -- no deploy. The previous version stays in the history for "
        "workflow_template_rollback. " + _FAST_PATH
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
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        _wrap(tw.require_edit, home.template)
        if expected_revision is not None:
            draft = tw.read_draft(home.wda.labs_api, home.template)
            revision = (draft.data or {}).get("revision") if draft else None
            if revision != expected_revision:
                raise MCPToolError("VERSION_CONFLICT", f"draft is at revision {revision}, not {expected_revision}")
        version = _wrap(tw.publish_draft, home.wda, home.template, user=user, note=note)
        home.reload()
        out = home.describe()
    out["published_now"] = version["number"]
    return out


@register(
    name="workflow_template_rollback",
    description=(
        "Roll a template workflow back (write access to its scope): publishes a COPY of an earlier version as the "
        "next version, so every follower shows it on its next load and the history records the rollback. "
        "reset_draft=true also resets the draft to that version (otherwise the draft keeps your unpublished edits). "
        + _FAST_PATH
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
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        version = _wrap(
            tw.rollback,
            home.wda,
            home.template,
            user=user,
            to_version=to_version,
            note=note,
            reset_draft=bool(reset_draft),
        )
        home.reload()
        out = home.describe()
    out["published_now"] = version["number"]
    out["restores_version"] = to_version
    return out


@register(
    name="workflow_template_set_sharing",
    description=(
        "Change who may FOLLOW a template workflow (write access to its scope). public=true shares it -- the "
        "template and its published versions become public LabsRecords (the same flag workflow sharing uses), so "
        "workflows in any program or opportunity can follow it; public=false limits it to people with access to "
        "its home scope (existing followers outside it then fall back to their stored copy, with a warning). "
        "template_scope only changes where it shows in the template picker. Who may EDIT it is not set here: it "
        "is the scope's own LabsRecord write access."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "template_workflow_id": {"type": "integer"},
            **_SCOPE_PROPS,
            "public": {"type": "boolean"},
            "template_scope": {"type": "string"},
        },
        "required": ["template_workflow_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_template_set_sharing(
    user,
    template_workflow_id: int,
    opportunity_id: int = None,
    program_id: int = None,
    public: bool = None,
    template_scope: str = None,
):
    from .workflows import _validate_template_scope

    if public is None and template_scope is None:
        raise MCPToolError("INVALID_SCHEMA", "Pass public and/or template_scope.")
    if template_scope:
        _validate_template_scope(template_scope, user)
    with _Home(user, template_workflow_id, opportunity_id, program_id) as home:
        home.template = _wrap(tw.set_sharing, home.wda, home.template, public=public, template_scope=template_scope)
        return home.describe()


@register(
    name="workflow_follow_template",
    description=(
        "Point a workflow at a template workflow (follow=true, the default) or fork it off one (follow=false). "
        "A follower renders the template's PUBLISHED render and inherits its config defaults and snapshot_inputs; "
        "keys the follower sets itself win (e.g. its own enrollment_targets). On follow, any follower key EQUAL to "
        "the template's value is dropped so it stays inherited -- the result lists them under `now_inherited` and "
        "the real overrides under `overrides`. You must be able to READ the template: have access to its home "
        "scope, or it is shared publicly (workflow_template_set_sharing). The workflow must have the same "
        "templateType. follow=false forks: the published render becomes the workflow's stored copy and inherited "
        "values are written into its own config, so the page does not change. " + _FAST_PATH
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
    return follow_template(
        user,
        workflow_id=workflow_id,
        scope=_scope(opportunity_id, program_id),
        template_workflow_id=template_workflow_id,
        template_scope=(
            _scope(template_opportunity_id, template_program_id)
            if (template_opportunity_id is not None or template_program_id is not None)
            else None
        ),
        follow=follow,
        expected_version=expected_version,
    )


def follow_template(user, *, workflow_id, scope, template_workflow_id, template_scope, follow, expected_version):
    from connect_labs.workflow.render_source import followed_template_workflow, resolve_render_code

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
            rs = tw.source_of(getattr(current, "own_data", current.data))
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
            listed = _with_template_home(
                token, rs, lambda home, t: tw.forget_follower(home, t, workflow_id=workflow_id, scope=scope)
            )
            return {
                "workflow_id": workflow_id,
                "follows": None,
                "forked": True,
                "new_version": version + 1,
                "removed_from_template_list": bool(listed),
            }

        if template_workflow_id is None or template_scope is None:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "template_workflow_id and one of template_opportunity_id / template_program_id are required to follow",
            )
        reader = WorkflowDataAccess(access_token=token, **template_scope)
        try:
            template = tw.load_template(reader.labs_api, template_workflow_id, **template_scope)
            if template is None:
                raise MCPToolError(
                    "PERMISSION_DENIED",
                    f"you cannot read template workflow {template_workflow_id} in {template_scope}, so you cannot "
                    "follow it. Following needs access to that scope, or the template shared publicly: its sharing "
                    "is set by its owners (workflow_template_set_sharing public=true).",
                )
            own_type = (current.data.get("config") or {}).get("templateType") or ""
            if template.template_type and own_type != template.template_type:
                raise MCPToolError(
                    "INVALID_SCHEMA",
                    f"workflow {workflow_id} is a '{own_type}' workflow; this template renders "
                    f"'{template.template_type}' and reads that template's pipelines, config and snapshot contract",
                )
            if tw.content_of(reader.labs_api, template, draft=False) is None:
                raise MCPToolError("INVALID_SCHEMA", "the template has no published version yet; publish it first")

            before = getattr(current, "own_data", current.data)
            data = dict(before)
            data["render_source"] = template.render_source()
            data["version"] = version + 1
            updated = wda.update_definition(workflow_id, data)
            listed = tw.record_follower(reader, template, workflow_id=workflow_id, scope=scope)
        finally:
            reader.close()
    finally:
        wda.close()

    after = (updated.data if updated else data) or {}
    b_cfg, a_cfg = before.get("config") or {}, after.get("config") or {}
    b_in = before.get("snapshot_inputs") if isinstance(before.get("snapshot_inputs"), dict) else {}
    a_in = after.get("snapshot_inputs") if isinstance(after.get("snapshot_inputs"), dict) else {}
    return {
        "workflow_id": workflow_id,
        "follows": template.render_source(),
        "published_version": (template.published or {}).get("version"),
        "listed_on_template": listed,
        **(
            {}
            if listed
            else {
                "note": "you lack write access to the template's scope, so it does not list this follower; the "
                "follow itself (this workflow's render_source) works regardless"
            }
        ),
        "now_inherited": {
            "config": sorted(set(b_cfg) - set(a_cfg)),
            "snapshot_inputs": sorted(set(b_in) - set(a_in)),
        },
        "overrides": {"config": sorted(a_cfg), "snapshot_inputs": sorted(a_in)},
        "url": tw.run_page_url(workflow_id, scope.get("opportunity_id"), scope.get("program_id")),
        "draft_preview_url": tw.run_page_url(
            workflow_id, scope.get("opportunity_id"), scope.get("program_id"), draft=True
        ),
        "new_version": version + 1,
    }


def _with_template_home(token, rs, fn):
    """Run `fn(home, template)` against the template `rs` names, if it is readable."""
    scope = tw._scope_from_source(rs) if rs else None
    if not scope:
        return None
    home = WorkflowDataAccess(access_token=token, **scope)
    try:
        template = tw.load_template(home.labs_api, rs["workflow"], **scope)
        return fn(home, template) if template else None
    finally:
        home.close()
