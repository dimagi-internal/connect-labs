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

STORAGE: LabsRecords, like every other workflow record (connect-labs#2236). Three
record types in experiment "workflow", all in the template's HOME scope (a program
or an opportunity):

- `workflow_definition` -- the template workflow's own definition IS the template.
  `data.template_workflow` holds its metadata: the template type, where it was
  seeded from, the published pointer (version number, version record id, and a copy
  of that version's config / snapshot_inputs so a follower's effective definition
  costs no extra read), the draft record id and the known followers.
- `workflow_template_draft` -- the one editable draft (render, config, snapshot
  spec, revision). Child of the definition (`labs_record_id`). Never public.
- `workflow_template_version` -- one immutable record per published version. Child
  of the definition. Public exactly when the template is.

PERMISSIONS ARE THE LABSRECORD ACL -- there is no template-specific permission.

- OWNERSHIP (edit the draft, preview it, publish, roll back, change sharing) is
  write access to the home scope. Connect checks reads and writes of a scoped
  record with the same membership test (`data_export/views.py`: the program's
  managing org for a program, the opportunity's orgs for an opportunity), so "can
  read it IN its scope" is "can write it"; every write also goes out under the
  caller's own token, so Connect enforces it again. A labs-only (synthetic) scope
  has no membership to check, as for any other record there.
- USE (follow, and render as a follower) is a read of the template: in its home
  scope, or -- when its owner has shared it (`public`, the same flag
  `share_workflow` sets) -- as a public record from any scope. That fallback is the
  registry fix of #2219 (#2216 was this failure for registries): a follower whose
  viewer is not a member of the template's scope reads it as public.

Draft preview: inside `preview_drafts()` (the run page with `?template_draft=1`), a
template whose draft the viewer can read IN SCOPE resolves to that draft -- render,
config and snapshot spec -- for that request only. The draft record is never public,
so only people with write access to the template's scope ever see it.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import difflib
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

EXPERIMENT = "workflow"
DEFINITION_TYPE = "workflow_definition"
DRAFT_TYPE = "workflow_template_draft"
VERSION_TYPE = "workflow_template_version"
META_KEY = "template_workflow"

TEMPLATE_SCOPE_PATTERN = r"^(global|org:\d+|program:\d+)$"

#: Set while a run page renders with `?template_draft=1`.
_preview: contextvars.ContextVar = contextvars.ContextVar("template_draft_preview", default=False)


class TemplateWorkflowError(Exception):
    """A refused template-workflow operation. `code` maps onto an MCP error code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# -----------------------------------------------------------------------------
# Identity
# -----------------------------------------------------------------------------


def scope_of(opportunity_id=None, program_id=None) -> dict:
    if (opportunity_id is None) == (program_id is None):
        raise TemplateWorkflowError("INVALID_SCHEMA", "exactly one of opportunity_id / program_id")
    return {"opportunity_id": int(opportunity_id)} if opportunity_id is not None else {"program_id": int(program_id)}


def scope_key(opportunity_id=None, program_id=None) -> str:
    s = scope_of(opportunity_id, program_id)
    return f"opp:{s['opportunity_id']}" if "opportunity_id" in s else f"program:{s['program_id']}"


def source_of(data) -> dict | None:
    """The `{"workflow": id, scope}` render_source on definition data, if it has one."""
    rs = (data or {}).get("render_source") if isinstance(data, dict) else None
    if isinstance(rs, dict) and isinstance(rs.get("workflow"), int):
        return rs
    return None


def _scope_from_source(rs: dict) -> dict | None:
    try:
        return scope_of(rs.get("opportunity_id"), rs.get("program_id"))
    except TemplateWorkflowError:
        return None


def run_page_url(workflow_id, opportunity_id=None, program_id=None, *, draft=False) -> str:
    qs = f"opportunity_id={opportunity_id}" if opportunity_id is not None else f"program_id={program_id}"
    return f"/labs/workflow/{workflow_id}/run/?{qs}" + ("&template_draft=1" if draft else "")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _username(user) -> str | None:
    return getattr(user, "username", None) if user is not None else None


# -----------------------------------------------------------------------------
# Reading LabsRecords: in scope, then public
# -----------------------------------------------------------------------------


def _read_in_scope(labs_api, record_id: int, type_: str, scope: dict):
    """The record read in `scope` with the caller's own token, or None if they cannot."""
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError

    try:
        return labs_api.get_record_by_id(int(record_id), experiment=EXPERIMENT, type=type_, **scope)
    except LabsAPIError as exc:
        if exc.status_code in (403, 404):
            return None
        raise


def _read_public(labs_api, record_id: int, type_: str):
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError

    try:
        return labs_api.get_public_record_by_id(int(record_id), experiment=EXPERIMENT, type=type_)
    except LabsAPIError:
        logger.info("template record %s (%s) not readable as public", record_id, type_)
        return None


def _read_shared(labs_api, record_id: int, type_: str, scope: dict):
    """`(record, in_scope)`: in the home scope, else as a public record (#2216 pattern)."""
    record = _read_in_scope(labs_api, record_id, type_, scope)
    if record is not None:
        return record, True
    return _read_public(labs_api, record_id, type_), False


def _cache(labs_api) -> dict:
    """A per-client memo: one client lives for one request (or one MCP call)."""
    store = getattr(labs_api, "_template_workflow_cache", None)
    if not isinstance(store, dict):
        store = {}
        try:
            labs_api._template_workflow_cache = store
        except AttributeError:  # pragma: no cover -- a client that refuses attributes
            pass
    return store


def forget_template_reads(labs_api) -> None:
    store = getattr(labs_api, "_template_workflow_cache", None)
    if isinstance(store, dict):
        store.clear()


class Template:
    """A template workflow as read by one caller: its definition record, and whether
    that caller read it IN its home scope (= may write it) or only as public."""

    def __init__(self, record, scope: dict, *, in_scope: bool):
        self.record = record
        self.workflow_id = int(record.id)
        self.scope = dict(scope)
        self.in_scope = bool(in_scope)
        self.data = record.data if isinstance(record.data, dict) else {}
        self.meta = dict(self.data.get(META_KEY) or {})

    @property
    def opportunity_id(self):
        return self.scope.get("opportunity_id")

    @property
    def program_id(self):
        return self.scope.get("program_id")

    @property
    def public(self) -> bool:
        return bool(getattr(self.record, "public", False))

    @property
    def name(self) -> str:
        return self.data.get("name") or ""

    @property
    def template_type(self) -> str:
        return self.meta.get("template_type") or ""

    @property
    def template_scope(self) -> str | None:
        return self.data.get("template_scope")

    @property
    def published(self) -> dict | None:
        p = self.meta.get("published")
        return p if isinstance(p, dict) and p.get("record_id") else None

    def render_source(self) -> dict:
        return {"workflow": self.workflow_id, **self.scope}


def load_template(labs_api, workflow_id, opportunity_id=None, program_id=None) -> Template | None:
    """The template workflow `workflow_id` in its home scope, as this caller can read it.

    None when it is unreadable to them, or the record is not a template workflow.
    """
    scope = scope_of(opportunity_id, program_id)
    key = ("def", int(workflow_id), tuple(sorted(scope.items())))
    memo = _cache(labs_api)
    if key in memo:
        return memo[key]
    record, in_scope = _read_shared(labs_api, workflow_id, DEFINITION_TYPE, scope)
    template = None
    if record is not None and isinstance(record.data, dict) and isinstance(record.data.get(META_KEY), dict):
        template = Template(record, scope, in_scope=in_scope)
    memo[key] = template
    return template


def template_for_source(labs_api, rs: dict | None) -> Template | None:
    if not rs:
        return None
    scope = _scope_from_source(rs)
    if scope is None:
        return None
    return load_template(labs_api, rs["workflow"], **scope)


# -----------------------------------------------------------------------------
# Permissions: the LabsRecord ACL of the template's home scope
# -----------------------------------------------------------------------------


def describe_scope(template: Template) -> str:
    if template.program_id is not None:
        return f"program {template.program_id}"
    return f"opportunity {template.opportunity_id}"


def editors_rule(template: Template) -> str:
    if template.program_id is not None:
        return (
            f"members of the organization that manages program {template.program_id} "
            "(write access to its LabsRecords)"
        )
    return (
        f"people with access to opportunity {template.opportunity_id} -- members of its organization, its "
        "supervising organization, or its program's organizations (write access to its LabsRecords)"
    )


def can_edit(template: Template) -> bool:
    return template.in_scope


def require_edit(template: Template) -> None:
    if not can_edit(template):
        raise TemplateWorkflowError(
            "PERMISSION_DENIED",
            f"template workflow {template.workflow_id} belongs to {describe_scope(template)}; editing, previewing, "
            f"publishing and rolling it back need write access there: {editors_rule(template)}. You can read it "
            "only because it is shared publicly.",
        )


def sharing_of(template: Template) -> dict:
    return {
        "public": template.public,
        "template_scope": template.template_scope,
        "who_can_follow": (
            "anyone signed in to labs, from any program or opportunity (the template is public)"
            if template.public
            else f"only people with access to {describe_scope(template)} (the template is not shared; an owner "
            "can share it with workflow_template_set_sharing public=true)"
        ),
    }


# -----------------------------------------------------------------------------
# Content: published, or draft under preview
# -----------------------------------------------------------------------------


@contextlib.contextmanager
def preview_drafts(_user=None):
    """Resolve templates to their DRAFT for the duration -- where the viewer can read
    the draft, i.e. has write access to the template's scope."""
    token = _preview.set(True)
    try:
        yield
    finally:
        _preview.reset(token)


def read_draft(labs_api, template: Template):
    """The draft record, read IN SCOPE only (it is never public). None if unreadable."""
    draft_id = template.meta.get("draft_record_id")
    if not draft_id:
        return None
    key = ("draft", int(draft_id))
    memo = _cache(labs_api)
    if key not in memo:
        memo[key] = _read_in_scope(labs_api, draft_id, DRAFT_TYPE, template.scope)
    return memo[key]


def list_versions(labs_api, template: Template) -> list:
    """Every published version record, newest first (read in the caller's reach)."""
    records = []
    if template.in_scope:
        records = labs_api.get_records(
            experiment=EXPERIMENT, type=VERSION_TYPE, labs_record_id=template.workflow_id, **_scope_kwargs(template)
        )
    else:
        records = labs_api.get_records(
            experiment=EXPERIMENT, type=VERSION_TYPE, labs_record_id=template.workflow_id, public=True
        )
    records = [r for r in records or [] if (r.data or {}).get("template_workflow_id") == template.workflow_id]
    return sorted(records, key=lambda r: int((r.data or {}).get("number") or 0), reverse=True)


def _scope_kwargs(template: Template) -> dict:
    # get_records takes an opportunity override and a program filter.
    if template.opportunity_id is not None:
        return {"opportunity_id": template.opportunity_id}
    return {"program_id": template.program_id}


def read_version(labs_api, template: Template, record_id: int):
    key = ("version", int(record_id))
    memo = _cache(labs_api)
    if key not in memo:
        memo[key], _ = _read_shared(labs_api, record_id, VERSION_TYPE, template.scope)
    return memo[key]


def content_of(labs_api, template: Template, *, draft: bool | None = None, with_code: bool = True) -> dict | None:
    """`{render_code, config, snapshot_inputs, version, draft}` for a template.

    `draft=None` means: the draft when a preview is on and this caller can read it,
    else the published version. None when nothing is published and no draft is in
    view. `with_code=False` skips the version-record read (the published config and
    snapshot spec are copied onto the definition).
    """
    use_draft = _preview.get() if draft is None else draft
    if use_draft:
        record = read_draft(labs_api, template)
        if record is not None:
            d = record.data or {}
            return {
                "render_code": d.get("render_code") or "",
                "config": d.get("config") or {},
                "snapshot_inputs": d.get("snapshot_inputs"),
                "version": None,
                "draft_revision": d.get("revision"),
                "draft": True,
            }
        if draft:
            return None
    published = template.published
    if published is None:
        return None
    out = {
        "render_code": None,
        "config": published.get("config") or {},
        "snapshot_inputs": published.get("snapshot_inputs"),
        "version": published.get("version"),
        "draft": False,
    }
    if with_code:
        record = read_version(labs_api, template, published["record_id"])
        if record is None:
            return None
        out["render_code"] = (record.data or {}).get("render_code") or ""
    return out


def content_for_data(labs_api, data, *, with_code: bool = True):
    """`(template, content)` for definition data that follows a template workflow."""
    rs = source_of(data)
    if not rs:
        return None, None
    template = template_for_source(labs_api, rs)
    if template is None:
        return None, None
    return template, content_of(labs_api, template, with_code=with_code)


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


def effective_data(labs_api, data):
    """The definition data a follower is read as. Returns `data` itself when it follows nothing."""
    if labs_api is None or not source_of(data):
        return data
    try:
        _template, content = content_for_data(labs_api, data, with_code=False)
    except Exception:  # noqa: BLE001 -- an unreadable template must not break a definition read
        logger.warning("template workflow unreadable while reading a follower", exc_info=True)
        return data
    if not content:
        return data
    return merge_inherited(data, content)


def strip_inherited(labs_api, data):
    """Definition data fit to WRITE: inherited values removed so they stay inherited.

    Compared against the PUBLISHED version (never a draft under preview), key by key:
    a follower config / snapshot_inputs key equal to the template's is dropped. A
    snapshot_inputs left empty is dropped too -- for a follower it would read as the
    template's spec anyway.
    """
    rs = source_of(data)
    if labs_api is None or not rs:
        return data
    template = template_for_source(labs_api, rs)
    content = content_of(labs_api, template, draft=False, with_code=False) if template else None
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


def apply_to_record(labs_api, record):
    """Turn a definition record into its effective view, keeping the raw data as `own_data`."""
    if record is None:
        return record
    raw = record.data
    record.own_data = raw
    if isinstance(raw, dict):
        record.data = effective_data(labs_api, raw)
    return record


def apply_to_records(labs_api, records):
    for record in records or []:
        apply_to_record(labs_api, record)
    return records


# -----------------------------------------------------------------------------
# Lifecycle: create, edit draft, publish, roll back, share, follow
# -----------------------------------------------------------------------------
#
# Every write takes `home`: a WorkflowDataAccess opened in the template's HOME scope
# with the caller's own token, so Connect applies the scope's write ACL to it.


def _write_meta(home, template: Template, **changes) -> Template:
    data = copy.deepcopy(template.data)
    meta = dict(data.get(META_KEY) or {})
    meta.update(changes)
    data[META_KEY] = meta
    record = home.labs_api.update_record(
        record_id=template.workflow_id, experiment=EXPERIMENT, type=DEFINITION_TYPE, data=data
    )
    if record is None or not isinstance(getattr(record, "data", None), dict):
        record = template.record
        record.data = data
    _cache(home.labs_api).clear()
    return Template(record, template.scope, in_scope=True)


def create_template(
    home,
    *,
    user,
    name,
    description,
    statuses=None,
    template_scope=None,
    render_code,
    config,
    snapshot_inputs,
    seeded_from,
    template_type="",
    public=False,
    publish=True,
    note="",
    draft_revision=1,
) -> Template:
    """A new template workflow in `home`'s scope: its definition, its draft, and (if
    `publish`) version 1. Whoever can write that scope owns it; nobody is listed."""
    scope = scope_of(home.opportunity_id if home.program_id is None else None, home.program_id)
    template_type = template_type or (config or {}).get("templateType", "") or ""
    created = home.create_definition(
        name=name,
        description=description or "",
        **({"statuses": statuses} if statuses else {}),
        config={"templateType": template_type} if template_type else {},
    )
    draft = home.labs_api.create_record(
        experiment=EXPERIMENT,
        type=DRAFT_TYPE,
        data={
            "template_workflow_id": created.id,
            "render_code": render_code or "",
            "config": copy.deepcopy(config or {}),
            "snapshot_inputs": copy.deepcopy(snapshot_inputs) if isinstance(snapshot_inputs, dict) else None,
            "revision": int(draft_revision),
            "updated_by": _username(user),
            "updated_at": _now(),
        },
        labs_record_id=created.id,
    )
    data = dict(created.data)
    data.update(
        {
            "is_template": True,
            **({"template_scope": template_scope} if template_scope else {}),
            # The record follows ITSELF, so opening it shows the published version
            # (or, with ?template_draft=1 and write access, the draft).
            "render_source": {"workflow": created.id, **scope},
            META_KEY: {
                "template_type": template_type,
                "seeded_from": seeded_from,
                "draft_record_id": draft.id,
                "published": None,
                "followers": [],
            },
        }
    )
    record = home.labs_api.update_record(
        record_id=created.id, experiment=EXPERIMENT, type=DEFINITION_TYPE, data=data, public=bool(public)
    )
    template = Template(record, scope, in_scope=True)
    if publish:
        publish_draft(home, template, user=user, note=note or f"seeded from {seeded_from}")
        template = reload(home, template)
    return template


def reload(home, template: Template) -> Template:
    _cache(home.labs_api).clear()
    fresh = load_template(home.labs_api, template.workflow_id, **template.scope)
    if fresh is None:
        raise TemplateWorkflowError("NOT_FOUND", f"template workflow {template.workflow_id} is gone")
    return fresh


def update_draft(
    home,
    template: Template,
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
    require_edit(template)
    draft = read_draft(home.labs_api, template)
    if draft is None:
        raise TemplateWorkflowError("NOT_FOUND", f"template workflow {template.workflow_id} has no draft record")
    d = dict(draft.data or {})
    revision = int(d.get("revision") or 1)
    if expected_revision != revision:
        raise TemplateWorkflowError(
            "VERSION_CONFLICT", f"draft is at revision {revision}, not {expected_revision}; re-read and retry"
        )
    if render_code is not None and edits:
        raise TemplateWorkflowError("INVALID_SCHEMA", "pass render_code OR edits, not both")
    code = d.get("render_code") or ""
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
    d["render_code"] = code
    if config is not None:
        if not isinstance(config, dict):
            raise TemplateWorkflowError("INVALID_SCHEMA", "config must be an object")
        d["config"] = dict(config) if config_replace else {**(d.get("config") or {}), **config}
    if snapshot_inputs_set:
        if snapshot_inputs is not None and not isinstance(snapshot_inputs, dict):
            raise TemplateWorkflowError("INVALID_SCHEMA", "snapshot_inputs must be an object or null")
        d["snapshot_inputs"] = snapshot_inputs
    d["revision"] = revision + 1
    d["updated_by"] = _username(user)
    d["updated_at"] = _now()
    home.labs_api.update_record(record_id=draft.id, experiment=EXPERIMENT, type=DRAFT_TYPE, data=d)
    _cache(home.labs_api).clear()
    return d


def publish_draft(home, template: Template, *, user, note="", restores_version=None, source=None):
    """Publish the draft (or `source`, a version record to restore) as the next version.

    Version numbers come from the version records already there. LabsRecords have no
    row lock, so two publishes in the same instant could pick one number; the later
    write still wins the published pointer and both stay in the history.
    """
    require_edit(template)
    if source is not None:
        content = dict(source.data or {})
    else:
        draft = read_draft(home.labs_api, template)
        content = dict((draft.data if draft else None) or {})
        if not (content.get("render_code") or "").strip():
            raise TemplateWorkflowError("INVALID_SCHEMA", "the draft has no render code to publish")
    existing = list_versions(home.labs_api, template)
    number = max([int((r.data or {}).get("number") or 0) for r in existing] or [0]) + 1
    version_data = {
        "template_workflow_id": template.workflow_id,
        "number": number,
        "render_code": content.get("render_code") or "",
        "config": copy.deepcopy(content.get("config") or {}),
        "snapshot_inputs": copy.deepcopy(content.get("snapshot_inputs")),
        "note": note or "",
        "restores_version": restores_version,
        "published_by": _username(user),
        "published_at": _now(),
    }
    record = home.labs_api.create_record(
        experiment=EXPERIMENT,
        type=VERSION_TYPE,
        data=version_data,
        labs_record_id=template.workflow_id,
        public=template.public,
    )
    _write_meta(
        home,
        template,
        published={
            "version": number,
            "record_id": record.id,
            "config": version_data["config"],
            "snapshot_inputs": version_data["snapshot_inputs"],
        },
    )
    return version_data | {"record_id": record.id}


def rollback(home, template: Template, *, user, to_version: int, note="", reset_draft=False):
    """Publish a copy of version `to_version`. History grows; nothing is deleted."""
    require_edit(template)
    source = next(
        (
            r
            for r in list_versions(home.labs_api, template)
            if int((r.data or {}).get("number") or 0) == int(to_version)
        ),
        None,
    )
    if source is None:
        raise TemplateWorkflowError("NOT_FOUND", f"template has no version {to_version}")
    version = publish_draft(
        home,
        template,
        user=user,
        note=note or f"rollback to v{to_version}",
        restores_version=int(to_version),
        source=source,
    )
    if reset_draft:
        draft = read_draft(home.labs_api, template)
        if draft is not None:
            d = dict(draft.data or {})
            d.update(
                {
                    "render_code": (source.data or {}).get("render_code") or "",
                    "config": copy.deepcopy((source.data or {}).get("config") or {}),
                    "snapshot_inputs": copy.deepcopy((source.data or {}).get("snapshot_inputs")),
                    "revision": int(d.get("revision") or 1) + 1,
                    "updated_by": _username(user),
                    "updated_at": _now(),
                }
            )
            home.labs_api.update_record(record_id=draft.id, experiment=EXPERIMENT, type=DRAFT_TYPE, data=d)
    _cache(home.labs_api).clear()
    return version


def set_sharing(home, template: Template, *, public: bool | None = None, template_scope=None) -> Template:
    """Change who may USE the template. `public` is the LabsRecord flag (the same one
    `share_workflow` sets) on the definition and every version; the draft stays private."""
    require_edit(template)
    if public is not None:
        for record in list_versions(home.labs_api, template):
            if bool(getattr(record, "public", False)) != bool(public):
                home.labs_api.update_record(
                    record_id=record.id,
                    experiment=EXPERIMENT,
                    type=VERSION_TYPE,
                    data=record.data,
                    public=bool(public),
                )
    data = copy.deepcopy(template.data)
    if template_scope is not None:
        data["template_scope"] = template_scope
    home.labs_api.update_record(
        record_id=template.workflow_id,
        experiment=EXPERIMENT,
        type=DEFINITION_TYPE,
        data=data,
        **({"public": bool(public)} if public is not None else {}),
    )
    return reload(home, template)


def _follower_entry(workflow_id, scope: dict) -> dict:
    return {"workflow_id": int(workflow_id), **scope}


def record_follower(home, template: Template, *, workflow_id, scope: dict) -> bool:
    """Add a follower to the template's list. Only someone who can write the template's
    scope can; a follower outside it is still a follower (its own `render_source` is
    the follow), it just is not listed. Returns whether the list was written."""
    if not can_edit(template):
        return False
    entry = _follower_entry(workflow_id, scope)
    followers = [f for f in template.meta.get("followers") or [] if f != entry]
    _write_meta(home, template, followers=[*followers, entry])
    return True


def forget_follower(home, template: Template, *, workflow_id, scope: dict) -> bool:
    if not can_edit(template):
        return False
    entry = _follower_entry(workflow_id, scope)
    followers = [f for f in template.meta.get("followers") or [] if f != entry]
    if followers != list(template.meta.get("followers") or []):
        _write_meta(home, template, followers=followers)
    return True


def code_template_path(template_type: str | None) -> str | None:
    """The repo path of the code template's render file for `template_type`.

    Templates read their render from a sibling `.js` file (or inline it); the file
    is found by content, so this needs no per-template mapping. None when the
    render is inline or the type is not a deployed template.
    """
    from pathlib import Path

    from connect_labs.workflow.templates import TEMPLATES

    code = (TEMPLATES.get(template_type or "") or {}).get("render_code")
    if not code:
        return None
    root = Path(__file__).resolve().parent / "templates"
    repo = root.parents[2]
    for f in sorted(root.rglob("*.js")):
        if "__tests__" in f.parts:
            continue
        try:
            if f.read_text() == code:
                return str(f.relative_to(repo))
        except OSError:
            continue
    return None


def export(labs_api, template: Template, *, version: int | None = None, base: int = 1, draft: bool = False) -> dict:
    """A template's render at one version (or its draft) beside a BASE version.

    The way back from data to code. A template workflow is seeded from a code
    template (version 1 is that seed, verbatim) and then edited as data; this
    returns what changed since the base, with every version note in between, so the
    changes can be merged into the code template as a three-way merge -- base,
    template, repo -- rather than retyped (tools/promote_template_workflow.py).
    """
    versions = {int((r.data or {}).get("number") or 0): r for r in list_versions(labs_api, template)}
    if not versions:
        raise TemplateWorkflowError("NOT_FOUND", "the template has no published version")
    if base not in versions:
        raise TemplateWorkflowError("NOT_FOUND", f"version {base} is not in this template's history")
    if draft:
        require_edit(template)
        record = read_draft(labs_api, template)
        if record is None:
            raise TemplateWorkflowError("NOT_FOUND", "the template has no draft you can read")
        target_code = (record.data or {}).get("render_code") or ""
        target_label = f"draft r{(record.data or {}).get('revision')}"
        upto = max(versions)
    else:
        number = version if version is not None else (template.published or {}).get("version") or max(versions)
        if number not in versions:
            raise TemplateWorkflowError("NOT_FOUND", f"version {number} is not in this template's history")
        target_code = (versions[number].data or {}).get("render_code") or ""
        target_label = f"v{number}"
        upto = number
    base_code = (versions[base].data or {}).get("render_code") or ""
    path = code_template_path(template.template_type)
    deployed = None
    if path:
        from connect_labs.workflow.templates import TEMPLATES

        deployed = TEMPLATES[template.template_type]["render_code"]
    changes = []
    for n in sorted(versions):
        if base < n <= upto:
            d = versions[n].data or {}
            changes.append(
                {
                    "version": n,
                    "note": d.get("note"),
                    "published_at": d.get("published_at"),
                    "restores_version": d.get("restores_version"),
                }
            )
    diff = list(
        difflib.unified_diff(
            base_code.splitlines(),
            target_code.splitlines(),
            fromfile=f"v{base}",
            tofile=target_label,
            lineterm="",
        )
    )
    return {
        "template_workflow_id": template.workflow_id,
        "scope": dict(template.scope),
        "name": template.name,
        "template_type": template.template_type,
        "seeded_from": template.meta.get("seeded_from"),
        "base": {"version": base, "published_at": (versions[base].data or {}).get("published_at")},
        "target": target_label,
        "changes": changes,
        "code_template": {
            "path": path,
            # False: the code template has moved on since the base was seeded, so
            # a plain copy would undo that work -- merge, do not overwrite.
            "deployed_equals_base": None if deployed is None else deployed == base_code,
        },
        "diff": diff,
        "base_code": base_code,
        "code": target_code,
    }


def describe(labs_api, template: Template, *, include_code=False) -> dict:
    """The MCP-facing summary of a template workflow, as this caller can see it."""
    published = template.published
    versions = list_versions(labs_api, template)
    draft = read_draft(labs_api, template) if template.in_scope else None
    d = dict((draft.data if draft else None) or {})
    published_version = published.get("version") if published else None
    live_record = published.get("record_id") if published else None
    live = next((v for v in versions if v.id == live_record), None)
    out = {
        "template_workflow_id": template.workflow_id,
        "scope": dict(template.scope),
        "render_source": template.render_source(),
        "name": template.name,
        "template_type": template.template_type,
        "seeded_from": template.meta.get("seeded_from"),
        "owned_by": describe_scope(template),
        "editors": editors_rule(template),
        "you_can_edit": can_edit(template),
        "sharing": sharing_of(template),
        "template_scope": template.template_scope,
        "published_version": published_version,
        "versions": [
            {
                "number": (v.data or {}).get("number"),
                "note": (v.data or {}).get("note"),
                "restores_version": (v.data or {}).get("restores_version"),
                "published_by": (v.data or {}).get("published_by"),
                "published_at": (v.data or {}).get("published_at"),
                "live": v.id == live_record,
                "record_id": v.id,
            }
            for v in versions[:20]
        ],
        "followers": [
            {
                **f,
                "url": run_page_url(f["workflow_id"], f.get("opportunity_id"), f.get("program_id")),
                "draft_preview_url": run_page_url(
                    f["workflow_id"], f.get("opportunity_id"), f.get("program_id"), draft=True
                ),
            }
            for f in sorted(template.meta.get("followers") or [], key=lambda f: f.get("workflow_id", 0))
        ],
    }
    if draft is not None:
        live_data = (live.data if live else None) or {}
        out["draft"] = {
            "revision": d.get("revision"),
            "differs_from_published": live is None
            or live_data.get("render_code") != d.get("render_code")
            or (live_data.get("config") or {}) != (d.get("config") or {})
            or live_data.get("snapshot_inputs") != d.get("snapshot_inputs"),
            "updated_at": d.get("updated_at"),
            "updated_by": d.get("updated_by"),
            "render_code_bytes": len((d.get("render_code") or "").encode()),
            "config_keys": sorted((d.get("config") or {}).keys()),
            "has_snapshot_inputs": isinstance(d.get("snapshot_inputs"), dict),
        }
        if include_code:
            out["draft"]["render_code"] = d.get("render_code")
            out["draft"]["config"] = d.get("config")
            out["draft"]["snapshot_inputs"] = d.get("snapshot_inputs")
    else:
        out["draft"] = None
        out["note"] = (
            "the draft is visible only with write access to the template's scope; you are reading the published "
            "template"
        )
    return out
