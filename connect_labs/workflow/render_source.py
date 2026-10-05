"""Where a workflow's render code comes from: its own stored copy, or the template it follows.

Every workflow holds a COPY of its render code. A copy is right when a workflow is a
fork someone will edit. It is wrong when several workflows are meant to be the same
report: each copy has to be pushed after every deploy (`workflow_sync_from_deployed_
template`), and one that is missed quietly shows an older report. On 2026-09-11 the
real and synthetic KMC reports held four copies of the same render between them.

`render_source: {"template": "<key>"}` on a definition makes it FOLLOW the deployed
template instead: the page renders the template's code as it is on the server, so a
deploy reaches every following workflow at once and there is nothing to sync. Its
stored copy is left alone, and edits to it are refused rather than silently
invisible. Setting `render_source` to null forks: the template's current code
becomes the stored copy, so the page does not jump.

`render_source: {"workflow": <id>, "opportunity_id" | "program_id": <scope>}` follows a
TEMPLATE WORKFLOW instead: data, not code, with a draft, published versions and a
rollback, so a render change reaches every follower with no deploy. See
workflow/template_workflows.py. It is set with the `workflow_follow_template` MCP tool
(which checks the caller may read the template -- the LabsRecord ACL), never by a raw
definition patch.
"""

from __future__ import annotations


class RenderFollowsTemplate(Exception):
    """An edit to the stored render of a workflow that follows a template."""

    def __init__(self, definition_id, template_key: str):
        self.template_key = template_key
        super().__init__(
            f"workflow {definition_id} follows the deployed '{template_key}' template, so its render changes "
            "when that template is deployed and an edit here would never be shown. Edit the template -- or set "
            "render_source to null to fork a stored copy you can edit."
        )


class RenderFollowsTemplateWorkflow(RenderFollowsTemplate):
    """An edit to the stored render of a workflow that follows a template WORKFLOW."""

    def __init__(self, definition_id, template_workflow_id: int):
        self.template_key = None
        self.template_workflow_id = template_workflow_id
        Exception.__init__(
            self,
            f"workflow {definition_id} follows template workflow {template_workflow_id}, so its render is that "
            "template's published version and an edit here would never be shown. Edit the template's draft "
            "(workflow_template_update_draft), preview it, then publish it (workflow_template_publish) -- or "
            "unfollow (workflow_follow_template with follow=false) to fork a stored copy.",
        )


def render_source_of(definition) -> dict:
    return dict(((getattr(definition, "data", None) or {}).get("render_source")) or {})


def followed_template(definition) -> str | None:
    """The template key this workflow follows, if it follows one that exists."""
    from connect_labs.workflow.templates import get_template

    key = render_source_of(definition).get("template")
    return key if key and get_template(key) else None


def followed_template_workflow(definition) -> int | None:
    """The template WORKFLOW id this workflow follows, if it follows one."""
    from connect_labs.workflow.template_workflows import source_of

    rs = source_of(getattr(definition, "data", None))
    return rs["workflow"] if rs else None


def resolve_render_code(data_access, definition) -> tuple[str | None, dict]:
    """`(component_code, source)` -- the code the page should render, and where it came from."""
    from connect_labs.workflow.template_workflows import content_for_data, source_of
    from connect_labs.workflow.templates import get_template

    rs = source_of(getattr(definition, "data", None))
    if rs:
        template, content = content_for_data(data_access.labs_api, definition.data)
        if content and content.get("render_code"):
            return content["render_code"], {
                "source": "template_workflow",
                "template_workflow": rs["workflow"],
                "version": content["version"],
                "draft": content["draft"],
                **({"draft_revision": content["draft_revision"]} if content["draft"] else {}),
            }
        # A template that was deleted or never published: show the stored copy rather
        # than a blank page, and say so.
        code, src = _stored(data_access, definition)
        return code, {
            **src,
            "warning": f"follows template workflow {rs['workflow']}, which "
            + ("has no published version" if template else "is not readable here (not found, or not shared with you)")
            + "; showing this workflow's stored copy",
        }

    key = followed_template(definition)
    if key:
        return get_template(key)["render_code"], {"source": "template", "template": key}
    return _stored(data_access, definition)


def _stored(data_access, definition) -> tuple[str | None, dict]:
    record = data_access.get_render_code(definition.id)
    if not record:
        return None, {"source": "stored", "version": None}
    # The stored record's `data` holds the code (`component_code` is a property over it).
    data = getattr(record, "data", None)
    code = data.get("component_code") if isinstance(data, dict) else record.component_code
    version = getattr(record, "version", None)
    return code, {"source": "stored", "version": version if isinstance(version, int) else None}


def refuse_edit_if_following(definition) -> None:
    template_workflow = followed_template_workflow(definition)
    if template_workflow is not None:
        raise RenderFollowsTemplateWorkflow(getattr(definition, "id", "?"), template_workflow)
    key = followed_template(definition)
    if key:
        raise RenderFollowsTemplate(getattr(definition, "id", "?"), key)


def validate_render_source(value, definition) -> dict | None:
    """A render_source a definition may take: null (its stored copy) or its own template.

    Only the workflow's OWN template (config.templateType): following another one would
    render code written against a different config, pipelines and snapshot contract.
    """
    from connect_labs.workflow.templates import get_template

    if value is None:
        return None
    if isinstance(value, dict) and "workflow" in value:
        raise ValueError(
            "follow a template workflow with workflow_follow_template, which checks you may read it and "
            "records the follower; render_source cannot be patched to it directly"
        )
    if not isinstance(value, dict) or set(value) != {"template"} or not isinstance(value["template"], str):
        raise ValueError('render_source must be null or {"template": "<template key>"}')
    key = value["template"]
    if not get_template(key):
        raise ValueError(f"no deployed template '{key}'")
    own = ((getattr(definition, "data", None) or {}).get("config") or {}).get("templateType")
    if own != key:
        raise ValueError(
            f"a workflow can follow only its own template ('{own}'), not '{key}': the render is written against "
            "that template's config, pipelines and snapshot contract"
        )
    return {"template": key}
