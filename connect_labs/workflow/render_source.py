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


def render_source_of(definition) -> dict:
    return dict(((getattr(definition, "data", None) or {}).get("render_source")) or {})


def followed_template(definition) -> str | None:
    """The template key this workflow follows, if it follows one that exists."""
    from connect_labs.workflow.templates import get_template

    key = render_source_of(definition).get("template")
    return key if key and get_template(key) else None


def resolve_render_code(data_access, definition) -> tuple[str | None, dict]:
    """`(component_code, source)` -- the code the page should render, and where it came from."""
    from connect_labs.workflow.templates import get_template

    key = followed_template(definition)
    if key:
        return get_template(key)["render_code"], {"source": "template", "template": key}
    record = data_access.get_render_code(definition.id)
    if not record:
        return None, {"source": "stored", "version": None}
    # The stored record's `data` holds the code (`component_code` is a property over it).
    data = getattr(record, "data", None)
    code = data.get("component_code") if isinstance(data, dict) else record.component_code
    version = getattr(record, "version", None)
    return code, {"source": "stored", "version": version if isinstance(version, int) else None}


def refuse_edit_if_following(definition) -> None:
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
