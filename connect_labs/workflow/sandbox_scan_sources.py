"""Which workflow definitions ``workflow_sandbox_scan`` reads, and their effective render code.

Three sources, and what each one covers:

1. **Labs-local records** (``LabsLocalRecord``, the labs DB): every definition of every
   owner on a labs-only (synthetic) opportunity or program, read through the ORM. No
   token is needed and nothing is left out.
2. **Real-opportunity records** live on production Connect behind the LabsRecord API.
   There is no cross-owner listing there: a read is scoped to one opportunity, program
   or organization and answered only for a member of it. So they are read AS a user,
   with that user's stored Connect token (``UserConnectToken``), across every scope in
   that user's org tree (``/export/opp_org_program_list/``) -- the same calls the
   ``workflow_list`` MCP tool makes, one per scope, deduplicated by definition id.
   Anything no scanned user can read is NOT covered, and the report says so.
3. **Deployed seed templates** (``TEMPLATES``): their own RENDER_CODE, reported apart.

"Effective" render code is what the run page draws, as ``render_source.resolve_render_code``
decides it: the template's code for a follower of a deployed template
(``render_source: {"template": key}``), the PUBLISHED version for a follower of a template
workflow (``render_source: {"workflow": id, ...}``), the stored copy otherwise.

Getting a user's token may refresh it (and so rotate their refresh token), exactly as
the MCP server does; nothing else is written anywhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

from connect_labs.workflow.sandbox import deprecated_exempt, should_sandbox, workflow_sandbox_mode
from connect_labs.workflow.sandbox_scan import Finding, scan_render_code, worst

logger = logging.getLogger(__name__)

EXPERIMENT = "workflow"
DEFINITION_TYPE = "workflow_definition"
RENDER_CODE_TYPE = "workflow_render_code"


@dataclass
class Target:
    """One definition (or seed template) and what the scan found in its effective render."""

    origin: str  # local | connect | template
    id: object  # definition id, or template key
    name: str
    owner: str  # opportunity:123 | program:25 | organization:<id> | template
    kind: str  # workflow | page
    template_type: str
    render_source: str  # stored | template:<key> | template_workflow:<id>@v<n> | ...
    sandboxed_now: bool
    sandboxed_all: bool
    code_found: bool
    findings: list[Finding] = field(default_factory=list)
    note: str = ""

    @property
    def worst(self) -> str | None:
        return worst(self.findings)

    def as_dict(self) -> dict:
        return {
            "origin": self.origin,
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "kind": self.kind,
            "template_type": self.template_type,
            "render_source": self.render_source,
            "sandboxed_now": self.sandboxed_now,
            "sandboxed_all": self.sandboxed_all,
            "code_found": self.code_found,
            "note": self.note,
            "worst": self.worst,
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass
class Coverage:
    local_definitions: int = 0
    users_requested: list[str] = field(default_factory=list)
    users_scanned: list[str] = field(default_factory=list)
    users_without_token: list[str] = field(default_factory=list)
    scopes_read: int = 0
    scopes_refused: int = 0
    scopes_failed: int = 0
    connect_definitions: int = 0
    real_opps_covered: bool = False

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# -----------------------------------------------------------------------------
# Shared
# -----------------------------------------------------------------------------


def _owner(opportunity_id=None, program_id=None, organization_id=None) -> str:
    if opportunity_id is not None:
        return f"opportunity:{opportunity_id}"
    if program_id is not None:
        return f"program:{program_id}"
    if organization_id is not None:
        return f"organization:{organization_id}"
    return "unscoped"


def _target(origin, def_id, data: dict, owner: str, code, render_source: str, note: str = "") -> Target:
    from connect_labs.workflow.page_mode import is_page

    data = data if isinstance(data, dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    opp = owner.split(":", 1)[1] if owner.startswith("opportunity:") else None
    definition = SimpleNamespace(id=def_id, data=data, opportunity_id=int(opp) if opp and opp.isdigit() else None)
    page = is_page(definition)
    if deprecated_exempt(definition):
        note = (note + "; " if note else "") + "deprecated: pinned out of the sandbox under `all` (sandbox.py)"
    return Target(
        origin=origin,
        id=def_id,
        name=str(data.get("name") or ""),
        owner=owner,
        kind="page" if page else "workflow",
        template_type=str(config.get("templateType") or data.get("templateType") or ""),
        render_source=render_source,
        sandboxed_now=should_sandbox(definition, mode=workflow_sandbox_mode()),
        sandboxed_all=should_sandbox(definition, mode="all"),
        code_found=bool(code),
        findings=scan_render_code(code, is_page=page),
        note=note,
    )


def _template_label(source: dict) -> str:
    if source.get("source") == "template":
        return f"template:{source.get('template')}"
    if source.get("source") == "template_workflow":
        version = "draft" if source.get("draft") else f"v{source.get('version')}"
        return f"template_workflow:{source.get('template_workflow')}@{version}"
    return "stored"


# -----------------------------------------------------------------------------
# 1. Labs-local records (every owner, through the ORM)
# -----------------------------------------------------------------------------


def local_targets() -> list[Target]:
    from connect_labs.labs.synthetic.models import LabsLocalRecord
    from connect_labs.workflow.template_workflows import META_KEY, VERSION_TYPE, merge_inherited, source_of
    from connect_labs.workflow.templates import get_template

    definitions = list(LabsLocalRecord.objects.filter(experiment=EXPERIMENT, type=DEFINITION_TYPE).order_by("id"))
    by_id = {d.id: d for d in definitions}
    # Newest record per definition wins, as get_render_code reads the local backend
    # (LabsLocalRecord's ordering is -id).
    stored: dict[int, str] = {}
    for rec in LabsLocalRecord.objects.filter(experiment=EXPERIMENT, type=RENDER_CODE_TYPE).order_by("id"):
        data = rec.data if isinstance(rec.data, dict) else {}
        try:
            stored[int(data.get("definition_id"))] = data.get("component_code") or ""
        except (TypeError, ValueError):
            continue

    out = []
    for d in definitions:
        data = d.data if isinstance(d.data, dict) else {}
        owner = _owner(d.opportunity_id, d.program_id, d.organization_id)
        rs = source_of(data)
        if rs:
            template = by_id.get(rs["workflow"])
            meta = (template.data or {}).get(META_KEY) if template is not None else None
            published = meta.get("published") if isinstance(meta, dict) else None
            version = None
            if isinstance(published, dict) and published.get("record_id"):
                version = LabsLocalRecord.objects.filter(id=published["record_id"], type=VERSION_TYPE).first()
            if version is not None and (version.data or {}).get("render_code"):
                effective = merge_inherited(
                    data,
                    {"config": published.get("config") or {}, "snapshot_inputs": published.get("snapshot_inputs")},
                )
                label = f"template_workflow:{rs['workflow']}@v{published.get('version')}"
                out.append(_target("local", d.id, effective, owner, version.data["render_code"], label))
            else:
                why = "has no published version" if template is not None else "is not a labs-local record"
                out.append(
                    _target(
                        "local",
                        d.id,
                        data,
                        owner,
                        stored.get(d.id),
                        f"template_workflow:{rs['workflow']} (unresolved)",
                        note=f"template workflow {why}; its stored copy was scanned, as the run page would show it",
                    )
                )
            continue
        key = (
            (data.get("render_source") or {}).get("template") if isinstance(data.get("render_source"), dict) else None
        )
        template = get_template(key) if isinstance(key, str) else None
        if template:
            out.append(_target("local", d.id, data, owner, template.get("render_code"), f"template:{key}"))
        else:
            out.append(_target("local", d.id, data, owner, stored.get(d.id), "stored"))
    return out


# -----------------------------------------------------------------------------
# 2. Real-opportunity records, as a user, scope by scope
# -----------------------------------------------------------------------------


class _IndexedRenderCode:
    """What ``resolve_render_code`` needs of a data access, with render code read once per scope."""

    def __init__(self, labs_api, records):
        self.labs_api = labs_api
        self._by_def = {}
        for rec in records:
            def_id = (rec.data or {}).get("definition_id")
            if def_id is not None and def_id not in self._by_def:
                self._by_def[def_id] = rec  # the first, as get_render_code picks

    def get_render_code(self, definition_id):
        return self._by_def.get(definition_id)


def user_scopes(org_data: dict) -> list[dict]:
    """Every real (not labs-only) scope in a user's org tree, as WorkflowDataAccess kwargs."""
    scopes = []
    for opp in org_data.get("opportunities") or []:
        if opp.get("id") is not None and not opp.get("labs_only"):
            scopes.append({"opportunity_id": opp["id"]})
    for prog in org_data.get("programs") or []:
        if prog.get("id") is not None and not prog.get("labs_only"):
            scopes.append({"program_id": prog["id"]})
    for org in org_data.get("organizations") or []:
        if org.get("id") is not None and not org.get("labs_only"):
            scopes.append({"organization_id": org["id"]})
    return scopes


def connect_targets(users, coverage: Coverage, seen: set, *, stdout=None) -> list[Target]:
    """Definitions every scope of every user's org tree holds, deduplicated by id."""
    from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
    from connect_labs.labs.integrations.connect.api_client import LabsAPIError
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data
    from connect_labs.workflow.data_access import WorkflowDataAccess, WorkflowRenderCodeRecord
    from connect_labs.workflow.render_source import resolve_render_code

    out: list[Target] = []
    for user in users:
        try:
            token = get_valid_access_token(user)
        except ConnectTokenError:
            coverage.users_without_token.append(user.username)
            continue
        org_data = fetch_user_organization_data(token, owner=user.username)
        if not org_data:
            coverage.users_without_token.append(user.username)
            coverage.scopes_failed += 1
            continue
        coverage.users_scanned.append(user.username)
        for scope in user_scopes(org_data):
            try:
                da = WorkflowDataAccess(access_token=token, **scope)
            except Exception:  # noqa: BLE001
                coverage.scopes_failed += 1
                continue
            try:
                definitions = da.list_definitions()
                codes = da.labs_api.get_records(
                    experiment=EXPERIMENT, type=RENDER_CODE_TYPE, model_class=WorkflowRenderCodeRecord
                )
                coverage.scopes_read += 1
                index = _IndexedRenderCode(da.labs_api, codes)
                for d in definitions:
                    if ("connect", d.id) in seen:
                        continue
                    seen.add(("connect", d.id))
                    owner = _owner(d.opportunity_id, d.program_id, d.organization_id)
                    try:
                        code, source = resolve_render_code(index, d)
                    except Exception:  # noqa: BLE001 -- one unreadable template must not end the scan
                        logger.warning("render code for definition %s unresolved", d.id, exc_info=True)
                        code, source = None, {"source": "unresolved"}
                    label = _template_label(source)
                    note = source.get("warning", "")
                    out.append(_target("connect", d.id, d.data, owner, code, label, note=note))
                    coverage.connect_definitions += 1
            except LabsAPIError as exc:
                if exc.status_code in (403, 404):
                    coverage.scopes_refused += 1
                else:
                    coverage.scopes_failed += 1
            except Exception:  # noqa: BLE001
                logger.warning("sandbox scan: scope %s failed", scope, exc_info=True)
                coverage.scopes_failed += 1
            finally:
                da.close()
    coverage.real_opps_covered = bool(coverage.users_scanned)
    return out


# -----------------------------------------------------------------------------
# 3. Deployed seed templates
# -----------------------------------------------------------------------------


def template_targets() -> list[Target]:
    """Every deployed seed template's own RENDER_CODE, decided as a FOLLOWER of it would be."""
    from connect_labs.workflow.templates import TEMPLATES

    out = []
    for key in sorted(TEMPLATES):
        template = TEMPLATES[key]
        definition = dict(template.get("definition") or {})
        definition.setdefault("name", template.get("name") or key)
        definition["render_source"] = {"template": key}
        target = _target("template", key, definition, "template", template.get("render_code"), "seed")
        target.template_type = key
        if template.get("deprecated"):
            target.render_source = "seed (deprecated)"
        out.append(target)
    return out
