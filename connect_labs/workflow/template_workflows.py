"""Template workflows: a report's render, config defaults and snapshot spec as DATA.

Before this, the only way for many workflows to stay the same report was to follow a
CODE template (`render_source: {"template": "<key>"}`), so every change to that report
-- a section reordered, a divider removed -- was a PR, a merge and a production deploy.
A template workflow moves everything the snapshot builder and the APIs already
provide into data that can be edited and published in seconds:

    render_source: {"workflow": <template workflow id>, "opportunity_id" | "program_id": <its scope>}

A follower renders the template's PUBLISHED render code and inherits its config
defaults and `snapshot_inputs`; any key the follower sets itself wins (KMC's
`enrollment_targets`, a follower's own `flw_review`). The template carries one
editable DRAFT; publishing it writes an immutable version that every follower shows
on its next load, and a rollback publishes a copy of an older version.

THE ENGINE BOUNDARY. A render can only read what the snapshot builder and the labs
APIs give it. A change to what they compute -- a new count in `semantic_snapshot`,
a new endpoint -- is still code, and still a deploy. Everything downstream of that
(layout, order, charts, labels, which config a report reads) is template data.

Where the content lives: `workflow.models.TemplateWorkflow` (draft + published
pointer), `TemplateWorkflowVersion` (history) and `TemplateWorkflowFollower`. The
labs DB, not the template's LabsRecord, so that a follower in another scope can read
it with no cross-scope call and a page load never waits on production for it.

How a follower sees it:
- `WorkflowDataAccess.get_definition` / `list_definitions` hand back the EFFECTIVE
  definition (`effective_data`): config and snapshot_inputs with the template's
  values underneath the follower's own. The follower's raw record is `own_data`.
- Every definition write through `WorkflowDataAccess` passes `strip_inherited`
  first, so a read-modify-write never bakes inherited values into the follower. The
  rule that falls out: a follower key EQUAL to the published template's value is
  not an override, and is dropped on the next write (it would read the same anyway).
- `render_source.resolve_render_code` renders the published version (or, for an
  owner previewing, the draft).

Draft preview: inside `preview_drafts(user)` (the run page with `?template_draft=1`),
a template the user owns resolves to its DRAFT -- render, config and snapshot spec --
for that request only. Followers and other viewers keep seeing the published version.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import logging

logger = logging.getLogger(__name__)

TEMPLATE_SCOPE_PATTERN = r"^(global|org:\d+|program:\d+)$"

#: The user whose OWNED templates resolve to their draft for this request (preview).
_preview_user: contextvars.ContextVar = contextvars.ContextVar("template_preview_user", default=None)


class TemplateWorkflowError(Exception):
    """A refused template-workflow operation. `code` maps onto an MCP error code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# -----------------------------------------------------------------------------
# Identity
# -----------------------------------------------------------------------------


def scope_key(opportunity_id=None, program_id=None) -> str:
    if (opportunity_id is None) == (program_id is None):
        raise TemplateWorkflowError("INVALID_SCHEMA", "exactly one of opportunity_id / program_id")
    return f"opp:{int(opportunity_id)}" if opportunity_id is not None else f"program:{int(program_id)}"


def source_of(data) -> dict | None:
    """The `{"workflow": id, scope}` render_source on definition data, if it has one."""
    rs = (data or {}).get("render_source") if isinstance(data, dict) else None
    if isinstance(rs, dict) and isinstance(rs.get("workflow"), int):
        return rs
    return None


def render_source_for(template) -> dict:
    out = {"workflow": template.workflow_id}
    if template.opportunity_id is not None:
        out["opportunity_id"] = template.opportunity_id
    else:
        out["program_id"] = template.program_id
    return out


def find_template(workflow_id, opportunity_id=None, program_id=None):
    from connect_labs.workflow.models import TemplateWorkflow

    try:
        key = scope_key(opportunity_id, program_id)
    except TemplateWorkflowError:
        return None
    return TemplateWorkflow.objects.filter(workflow_id=int(workflow_id), scope_key=key).first()


def template_for_source(rs: dict | None):
    if not rs:
        return None
    return find_template(rs["workflow"], rs.get("opportunity_id"), rs.get("program_id"))


# -----------------------------------------------------------------------------
# Permissions
# -----------------------------------------------------------------------------


def is_owner(template, user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return template.owners.filter(pk=user.pk).exists()


def require_owner(template, user) -> None:
    if not is_owner(template, user):
        names = ", ".join(sorted(template.owners.values_list("username", flat=True))) or "nobody"
        raise TemplateWorkflowError(
            "PERMISSION_DENIED",
            f"only the owners of template workflow {template.workflow_id} ({names}) can edit, publish, "
            "roll back or preview its draft",
        )


def can_follow(template, user, read_definition) -> bool:
    """Anyone who can READ the template may follow it.

    `global` templates are readable by every labs user. Otherwise reading means what
    it means everywhere else in labs: the user's own token can fetch the template's
    definition record in its home scope (production enforces the org / program /
    opportunity membership; a labs-only scope has no membership to check).
    `read_definition()` does that fetch and returns the record or None.
    """
    if is_owner(template, user):
        return True
    if template.template_scope == "global":
        return True
    try:
        return read_definition() is not None
    except Exception:  # noqa: BLE001 -- unreadable is "cannot follow", not a crash
        logger.info("template %s unreadable for %s", template.pk, getattr(user, "username", None))
        return False


# -----------------------------------------------------------------------------
# Content: published, or draft under preview
# -----------------------------------------------------------------------------


@contextlib.contextmanager
def preview_drafts(user):
    """Resolve the templates `user` owns to their DRAFT for the duration."""
    token = _preview_user.set(user)
    try:
        yield
    finally:
        _preview_user.reset(token)


def _draft_visible(template) -> bool:
    user = _preview_user.get()
    return user is not None and is_owner(template, user)


def content_of(template, *, draft: bool | None = None) -> dict | None:
    """`{render_code, config, snapshot_inputs, version, draft}` for a template.

    `draft=None` means: the draft when the current preview user owns it, else the
    published version. None when nothing is published and no draft is in view.
    """
    use_draft = _draft_visible(template) if draft is None else draft
    if use_draft:
        return {
            "render_code": template.draft_render_code,
            "config": template.draft_config or {},
            "snapshot_inputs": template.draft_snapshot_inputs,
            "version": None,
            "draft_revision": template.draft_revision,
            "draft": True,
        }
    version = template.published
    if version is None:
        return None
    return {
        "render_code": version.render_code,
        "config": version.config or {},
        "snapshot_inputs": version.snapshot_inputs,
        "version": version.number,
        "draft": False,
    }


def content_for_data(data, _cache=None) -> tuple[object, dict] | tuple[None, None]:
    rs = source_of(data)
    if not rs:
        return None, None
    key = (rs["workflow"], rs.get("opportunity_id"), rs.get("program_id"))
    if _cache is not None and key in _cache:
        template = _cache[key]
    else:
        template = template_for_source(rs)
        if _cache is not None:
            _cache[key] = template
    if template is None:
        return None, None
    content = content_of(template)
    return (template, content) if content else (template, None)


# -----------------------------------------------------------------------------
# Inheritance: read (effective) and write (strip)
# -----------------------------------------------------------------------------


def merge_inherited(own: dict, content: dict) -> dict:
    """`own` definition data with the template content's config / snapshot_inputs underneath."""
    out = dict(own)
    t_config = content.get("config") or {}
    if t_config:
        own_config = own.get("config") if isinstance(own.get("config"), dict) else {}
        out["config"] = {**copy.deepcopy(t_config), **own_config}
    t_inputs = content.get("snapshot_inputs")
    if isinstance(t_inputs, dict):
        own_inputs = own.get("snapshot_inputs") if isinstance(own.get("snapshot_inputs"), dict) else {}
        out["snapshot_inputs"] = {**copy.deepcopy(t_inputs), **own_inputs}
    return out


def effective_data(data, _cache=None):
    """The definition data a follower is read as. Returns `data` itself when it follows nothing."""
    _template, content = content_for_data(data, _cache)
    if not content:
        return data
    return merge_inherited(data, content)


def strip_inherited(data):
    """Definition data fit to WRITE: inherited values removed so they stay inherited.

    Compared against the PUBLISHED version (never a draft under preview), key by key:
    a follower config / snapshot_inputs key equal to the template's is dropped. A
    snapshot_inputs left empty is dropped too -- for a follower it would read as the
    template's spec anyway.
    """
    rs = source_of(data)
    if not rs:
        return data
    template = template_for_source(rs)
    content = content_of(template, draft=False) if template else None
    if not content:
        return data
    out = dict(data)
    t_config = content.get("config") or {}
    if isinstance(out.get("config"), dict) and t_config:
        out["config"] = {k: v for k, v in out["config"].items() if not (k in t_config and t_config[k] == v)}
    t_inputs = content.get("snapshot_inputs")
    if isinstance(out.get("snapshot_inputs"), dict) and isinstance(t_inputs, dict):
        kept = {k: v for k, v in out["snapshot_inputs"].items() if not (k in t_inputs and t_inputs[k] == v)}
        if kept:
            out["snapshot_inputs"] = kept
        else:
            out.pop("snapshot_inputs")
    return out


def apply_to_record(record, _cache=None):
    """Turn a definition record into its effective view, keeping the raw data as `own_data`."""
    if record is None:
        return record
    raw = record.data
    record.own_data = raw
    if isinstance(raw, dict):
        record.data = effective_data(raw, _cache)
    return record


def apply_to_records(records):
    cache = {}
    for record in records or []:
        apply_to_record(record, cache)
    return records


# -----------------------------------------------------------------------------
# Lifecycle: create, edit draft, publish, roll back, follow
# -----------------------------------------------------------------------------


def create_template(
    *,
    user,
    workflow_id,
    opportunity_id,
    program_id,
    name,
    template_scope,
    render_code,
    config,
    snapshot_inputs,
    seeded_from,
    template_type="",
    publish=True,
    note="",
):
    """A template workflow row for an existing definition, its draft seeded, optionally published as v1."""
    from django.db import transaction

    from connect_labs.workflow.models import TemplateWorkflow

    with transaction.atomic():
        template = TemplateWorkflow.objects.create(
            workflow_id=int(workflow_id),
            scope_key=scope_key(opportunity_id, program_id),
            opportunity_id=opportunity_id,
            program_id=program_id,
            name=name,
            template_scope=template_scope,
            template_type=template_type or (config or {}).get("templateType", "") or "",
            seeded_from=seeded_from,
            draft_render_code=render_code or "",
            draft_config=copy.deepcopy(config or {}),
            draft_snapshot_inputs=copy.deepcopy(snapshot_inputs) if isinstance(snapshot_inputs, dict) else None,
            draft_updated_by=user,
        )
        template.owners.add(user)
        if publish:
            publish_draft(template, user=user, note=note or f"seeded from {seeded_from}")
    return template


def update_draft(
    template,
    *,
    user,
    expected_revision,
    render_code=None,
    edits=None,
    config=None,
    config_replace=False,
    snapshot_inputs=None,
    snapshot_inputs_set=False,
):
    """Edit the draft. Followers see nothing until a publish."""
    require_owner(template, user)
    if expected_revision != template.draft_revision:
        raise TemplateWorkflowError(
            "VERSION_CONFLICT",
            f"draft is at revision {template.draft_revision}, not {expected_revision}; re-read and retry",
        )
    if render_code is not None and edits:
        raise TemplateWorkflowError("INVALID_SCHEMA", "pass render_code OR edits, not both")
    code = template.draft_render_code
    if render_code is not None:
        if not render_code.strip():
            raise TemplateWorkflowError("INVALID_SCHEMA", "render_code is empty")
        code = render_code
    for i, edit in enumerate(edits or []):
        old, new = edit.get("old"), edit.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise TemplateWorkflowError("INVALID_SCHEMA", f"edit {i}: needs non-empty 'old' and a 'new' string")
        hits = code.count(old)
        if hits != 1:
            raise TemplateWorkflowError(
                "INVALID_SCHEMA", f"edit {i}: 'old' must match exactly once in the draft, matched {hits} times"
            )
        code = code.replace(old, new)
    if len(code.encode()) > 512 * 1024:
        raise TemplateWorkflowError("INVALID_SCHEMA", "render code over 512 KB")
    template.draft_render_code = code
    if config is not None:
        if not isinstance(config, dict):
            raise TemplateWorkflowError("INVALID_SCHEMA", "config must be an object")
        template.draft_config = dict(config) if config_replace else {**(template.draft_config or {}), **config}
    if snapshot_inputs_set:
        if snapshot_inputs is not None and not isinstance(snapshot_inputs, dict):
            raise TemplateWorkflowError("INVALID_SCHEMA", "snapshot_inputs must be an object or null")
        template.draft_snapshot_inputs = snapshot_inputs
    template.draft_revision += 1
    template.draft_updated_by = user
    from django.utils import timezone

    template.draft_updated_at = timezone.now()
    template.save()
    return template


def publish_draft(template, *, user, note="", restores_version=None, source=None):
    """Publish the draft (or `source`, a version to restore) as the next version."""
    from django.db import transaction
    from django.db.models import Max

    from connect_labs.workflow.models import TemplateWorkflowVersion

    require_owner(template, user)
    if source is None and not (template.draft_render_code or "").strip():
        raise TemplateWorkflowError("INVALID_SCHEMA", "the draft has no render code to publish")
    with transaction.atomic():
        # Lock the template row so two publishes cannot take the same number.
        locked = type(template).objects.select_for_update().get(pk=template.pk)
        number = (locked.versions.aggregate(n=Max("number"))["n"] or 0) + 1
        version = TemplateWorkflowVersion.objects.create(
            template=locked,
            number=number,
            render_code=source.render_code if source else locked.draft_render_code,
            config=copy.deepcopy(source.config if source else locked.draft_config) or {},
            snapshot_inputs=copy.deepcopy(source.snapshot_inputs if source else locked.draft_snapshot_inputs),
            note=note or "",
            restores_version=restores_version,
            published_by=user,
        )
        locked.published = version
        locked.save(update_fields=["published"])
    template.refresh_from_db()
    return version


def rollback(template, *, user, to_version: int, note="", reset_draft=False):
    """Publish a copy of version `to_version`. History grows; nothing is deleted."""
    require_owner(template, user)
    source = template.versions.filter(number=int(to_version)).first()
    if source is None:
        raise TemplateWorkflowError("NOT_FOUND", f"template has no version {to_version}")
    version = publish_draft(
        template, user=user, note=note or f"rollback to v{to_version}", restores_version=source.number, source=source
    )
    if reset_draft:
        template.draft_render_code = source.render_code
        template.draft_config = copy.deepcopy(source.config) or {}
        template.draft_snapshot_inputs = copy.deepcopy(source.snapshot_inputs)
        template.draft_revision += 1
        template.draft_updated_by = user
        template.save()
    return version


def record_follower(template, *, user, workflow_id, opportunity_id=None, program_id=None):
    from connect_labs.workflow.models import TemplateWorkflowFollower

    TemplateWorkflowFollower.objects.update_or_create(
        template=template,
        workflow_id=int(workflow_id),
        scope_key=scope_key(opportunity_id, program_id),
        defaults={"opportunity_id": opportunity_id, "program_id": program_id, "followed_by": user},
    )


def forget_follower(workflow_id, opportunity_id=None, program_id=None):
    from connect_labs.workflow.models import TemplateWorkflowFollower

    TemplateWorkflowFollower.objects.filter(
        workflow_id=int(workflow_id), scope_key=scope_key(opportunity_id, program_id)
    ).delete()


def run_page_url(workflow_id, opportunity_id=None, program_id=None, *, draft=False) -> str:
    qs = f"opportunity_id={opportunity_id}" if opportunity_id is not None else f"program_id={program_id}"
    return f"/labs/workflow/{workflow_id}/run/?{qs}" + ("&template_draft=1" if draft else "")


def describe(template, *, include_code=False) -> dict:
    """The MCP-facing summary of a template workflow."""
    published = template.published
    versions = list(template.versions.order_by("-number")[:20])
    draft_differs = (
        published is None
        or published.render_code != template.draft_render_code
        or (published.config or {}) != (template.draft_config or {})
        or published.snapshot_inputs != template.draft_snapshot_inputs
    )
    out = {
        "template_workflow_id": template.workflow_id,
        "scope": (
            {"opportunity_id": template.opportunity_id}
            if template.opportunity_id is not None
            else {"program_id": template.program_id}
        ),
        "render_source": render_source_for(template),
        "name": template.name,
        "template_scope": template.template_scope,
        "template_type": template.template_type,
        "seeded_from": template.seeded_from,
        "owners": sorted(template.owners.values_list("username", flat=True)),
        "published_version": published.number if published else None,
        "draft": {
            "revision": template.draft_revision,
            "differs_from_published": draft_differs,
            "updated_at": template.draft_updated_at.isoformat() if template.draft_updated_at else None,
            "updated_by": template.draft_updated_by.username if template.draft_updated_by else None,
            "render_code_bytes": len((template.draft_render_code or "").encode()),
            "config_keys": sorted((template.draft_config or {}).keys()),
            "has_snapshot_inputs": isinstance(template.draft_snapshot_inputs, dict),
        },
        "versions": [
            {
                "number": v.number,
                "note": v.note,
                "restores_version": v.restores_version,
                "published_by": v.published_by.username if v.published_by else None,
                "published_at": v.published_at.isoformat() if v.published_at else None,
                "live": published is not None and v.pk == published.pk,
            }
            for v in versions
        ],
        "followers": [
            {
                "workflow_id": f.workflow_id,
                "opportunity_id": f.opportunity_id,
                "program_id": f.program_id,
                "url": run_page_url(f.workflow_id, f.opportunity_id, f.program_id),
                "draft_preview_url": run_page_url(f.workflow_id, f.opportunity_id, f.program_id, draft=True),
            }
            for f in template.followers.order_by("workflow_id")
        ],
    }
    if include_code:
        out["draft"]["render_code"] = template.draft_render_code
        out["draft"]["config"] = template.draft_config
        out["draft"]["snapshot_inputs"] = template.draft_snapshot_inputs
    return out
